"""Views for the roulette feature (E7.3): personal and group scope.

Wraps the pure random-selection engine in :mod:`app.roulette` with the pool
queries (pending titles only), personal/group discard exclusion, the
genre/duration/game-mode filters and the "Start" action, which changes status
via the existing group service (respecting Dropped members) for the group
scope, or a plain personal status update otherwise. A draw never mutates
anything (docs/research/ruleta-ui.md).
"""

from __future__ import annotations

import logging
import random
import re
from collections import defaultdict

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from app.discards import discarded_item_ids
from app.models import Item, MediaTypes, Status
from app.providers import services
from app.roulette import pick_random
from groups.discards import group_discarded_item_ids
from groups.models import Group, GroupItem
from groups.services import (
    apply_status_to_user,
    get_group_progress,
    mark_group_item_status,
)

logger = logging.getLogger(__name__)

# Media types the roulette can offer: seasons/episodes are never drawn at
# random (docs/research/ruleta-ui.md, acuerdo 30).
ROULETTE_MEDIA_TYPES = (
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
    MediaTypes.GAME.value,
    MediaTypes.BOOK.value,
    MediaTypes.COMIC.value,
    MediaTypes.BOARDGAME.value,
)

# Number of decoy posters flashed before the result settles (E7.3 "vistoso").
_DECOY_COUNT = 4


def _personal_pool(user):
    """Return the user's pending items, minus their own discards."""
    discarded = discarded_item_ids(user)
    items = []
    for media_type in ROULETTE_MEDIA_TYPES:
        model = apps.get_model(app_label="app", model_name=media_type)
        rows = model.objects.filter(
            user=user,
            status=Status.PLANNING.value,
        ).select_related("item")
        items.extend(row.item for row in rows if row.item_id not in discarded)
    return items


def _group_pool(group):
    """Return the group's pending items, minus the group's own discards."""
    discarded = group_discarded_item_ids(group)
    rows = group.group_items.filter(status=Status.PLANNING.value).select_related("item")
    return [
        gi.item
        for gi in rows
        if gi.item.media_type in ROULETTE_MEDIA_TYPES and gi.item_id not in discarded
    ]


def _available_media_types(items):
    """Return the distinct media types present in the pool, sorted."""
    return sorted({item.media_type for item in items})


def _bulk_genre_names(items):
    """Return ``{item_id: [genre names]}`` for a batch of items, one query."""
    item_ids = [item.id for item in items]
    if not item_ids:
        return {}
    through = Item.genres.through
    rows = through.objects.filter(item_id__in=item_ids).values_list(
        "item_id", "genre__name"
    )
    names = defaultdict(list)
    for item_id, name in rows:
        names[item_id].append(name)
    return names


def _genre_rows(items, genres_by_item):
    """Return sorted ``{name, count}`` rows and the count of items with no genres."""
    counts = defaultdict(int)
    unknown = 0
    for item in items:
        names = genres_by_item.get(item.id) or []
        if not names:
            unknown += 1
            continue
        for name in names:
            counts[name] += 1
    rows = [{"name": name, "count": count} for name, count in counts.items()]
    rows.sort(key=lambda row: (-row["count"], row["name"]))
    return rows, unknown


def _filter_by_genres(items, genres_by_item, selected_genres):
    """Keep items sharing at least one of ``selected_genres`` (case-insensitive)."""
    if not selected_genres:
        return items
    wanted = {g.strip().casefold() for g in selected_genres if g.strip()}
    return [
        item
        for item in items
        if wanted & {g.casefold() for g in (genres_by_item.get(item.id) or [])}
    ]


def _fetch_metadata(item):
    """Best-effort provider metadata lookup; ``None`` on any failure."""
    try:
        return services.get_media_metadata(item.media_type, item.media_id, item.source)
    except Exception:  # a slow/broken provider must not break the roulette
        logger.exception("Failed to load roulette metadata for %s", item)
        return None


_RUNTIME_RE = re.compile(r"(?:(?P<hours>\d+)h\s*)?(?:(?P<minutes>\d+)m)?")
_DURATION_BUCKETS = ("short", "medium", "long")
_DURATION_LABELS = {"short": "< 90 min", "medium": "90-120 min", "long": "> 120 min"}


def _parse_runtime_minutes(runtime_label):
    """Parse tmdb's readable "Xh Ym" duration back into total minutes."""
    if not runtime_label:
        return None
    match = _RUNTIME_RE.fullmatch(runtime_label.strip())
    if not match or (not match.group("hours") and not match.group("minutes")):
        return None
    return int(match.group("hours") or 0) * 60 + int(match.group("minutes") or 0)


def _duration_bucket(minutes):
    """Classify a runtime in minutes into the short/medium/long buckets."""
    if minutes is None:
        return None
    if minutes < 90:  # noqa: PLR2004
        return "short"
    if minutes <= 120:  # noqa: PLR2004
        return "medium"
    return "long"


_GAME_MODE_BUCKETS = {
    "single": {"single player"},
    "co-op": {"co-operative"},
    "competitive": {
        "multiplayer",
        "split screen",
        "massively multiplayer online (mmo)",
        "battle royale",
    },
}
_GAME_MODE_LABELS = {
    "single": "Single player",
    "co-op": "Co-op",
    "competitive": "Competitive",
}
_GAME_MODE_ORDER = ("single", "co-op", "competitive")


def _game_mode_buckets(raw_modes):
    """Map IGDB's ``game_modes`` names to the single/co-op/competitive axis.

    IGDB's separate "multiplayer_modes" entity (local vs. online split) is not
    queried, so that finer axis is not offered here -- see the delivery
    report for why.
    """
    if not raw_modes:
        return set()
    lowered = {str(mode).strip().casefold() for mode in raw_modes}
    return {bucket for bucket, names in _GAME_MODE_BUCKETS.items() if lowered & names}


def _duration_filter(request, media_type, items):
    """Return (filtered items, bucket rows, unknown count, selected bucket)."""
    if media_type != MediaTypes.MOVIE.value:
        return items, [], 0, None

    selected = request.GET.get("duration") or None
    if selected not in _DURATION_BUCKETS:
        selected = None

    minutes_by_item = {
        item.id: _parse_runtime_minutes(
            ((_fetch_metadata(item) or {}).get("details") or {}).get("runtime"),
        )
        for item in items
    }
    bucket_counts = defaultdict(int)
    unknown = 0
    for item in items:
        bucket = _duration_bucket(minutes_by_item.get(item.id))
        if bucket is None:
            unknown += 1
        else:
            bucket_counts[bucket] += 1

    rows = [
        {
            "key": bucket,
            "label": _DURATION_LABELS[bucket],
            "count": bucket_counts[bucket],
        }
        for bucket in _DURATION_BUCKETS
    ]
    if selected is None:
        return items, rows, unknown, selected
    filtered = [
        item
        for item in items
        if _duration_bucket(minutes_by_item.get(item.id)) == selected
    ]
    return filtered, rows, unknown, selected


def _game_mode_filter(request, media_type, items):
    """Return (filtered items, bucket rows, unknown count, selected buckets)."""
    if media_type != MediaTypes.GAME.value:
        return items, [], 0, []

    selected = [m for m in request.GET.getlist("game_mode") if m in _GAME_MODE_BUCKETS]

    modes_by_item = {
        item.id: _game_mode_buckets(
            ((_fetch_metadata(item) or {}).get("details") or {}).get("game_modes"),
        )
        for item in items
    }
    bucket_counts = defaultdict(int)
    unknown = 0
    for item in items:
        buckets = modes_by_item.get(item.id) or set()
        if not buckets:
            unknown += 1
        for bucket in buckets:
            bucket_counts[bucket] += 1

    rows = [
        {
            "key": bucket,
            "label": _GAME_MODE_LABELS[bucket],
            "count": bucket_counts[bucket],
        }
        for bucket in _GAME_MODE_ORDER
    ]
    if not selected:
        return items, rows, unknown, selected
    wanted = set(selected)
    filtered = [item for item in items if modes_by_item.get(item.id) & wanted]
    return filtered, rows, unknown, selected


def _group_seen(group, item):
    """Return ``{"seen": x, "total": y}`` when at least one member has engaged.

    "Seen" means the member already has a personal record for the item beyond
    Planning (Completed, In progress or Dropped), even though the group's own
    copy is still Planning -- e.g. they watched it before the group tracked it.
    """
    progress = get_group_progress(group)
    entry = progress.get(item.id)
    if not entry:
        return None
    seen = sum(
        1
        for member in entry["members"].values()
        if member["status"] not in (None, Status.PLANNING.value)
    )
    if not seen:
        return None
    return {"seen": seen, "total": entry["total_members"]}


def _require_membership(request, group):
    """Raise 404 when the current user is not a member of ``group``."""
    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)


@login_required
@require_GET
def roulette(request):  # noqa: PLR0915
    """Roulette screen: personal scope, or group scope with ``?group=<id>``.

    A draw only proposes a title (no mutation); filters never relax
    automatically, and "not enough candidates" is explained rather than
    silently widening the pool (acuerdo 31, acuerdo 35).
    """
    group_id = request.GET.get("group")
    group = None
    if group_id:
        group = get_object_or_404(Group, id=group_id)
        _require_membership(request, group)
        items = _group_pool(group)
    else:
        items = _personal_pool(request.user)

    media_type = request.GET.get("media_type") or None
    if media_type and media_type not in ROULETTE_MEDIA_TYPES:
        media_type = None

    pool_by_type = [
        item for item in items if media_type is None or item.media_type == media_type
    ]
    genres_by_item = _bulk_genre_names(pool_by_type)
    genre_rows, unknown_genre_count = _genre_rows(pool_by_type, genres_by_item)

    strict = request.GET.get("strict") == "1"
    pool_after_strict = (
        [item for item in pool_by_type if genres_by_item.get(item.id)]
        if strict
        else pool_by_type
    )

    selected_genres = [g for g in request.GET.getlist("genres") if g]
    pool_after_genres = _filter_by_genres(
        pool_after_strict, genres_by_item, selected_genres
    )

    pool_after_duration, duration_rows, unknown_duration_count, selected_duration = (
        _duration_filter(request, media_type, pool_after_genres)
    )
    pool_after_modes, mode_rows, unknown_mode_count, selected_modes = _game_mode_filter(
        request, media_type, pool_after_duration
    )

    session_exclude_raw = request.GET.get("session_exclude", "")
    session_excluded_ids = {
        int(pk) for pk in session_exclude_raw.split(",") if pk.strip().isdigit()
    }
    remaining_pool = [
        item for item in pool_after_modes if item.id not in session_excluded_ids
    ]

    result = None
    empty_reason = None
    decoys = []
    group_seen = None
    if request.GET.get("draw"):
        result = pick_random(remaining_pool) if remaining_pool else None
        if result is None:
            if not pool_by_type:
                empty_reason = "No pending titles match this media type yet."
            elif not pool_after_modes:
                empty_reason = "No pending title matches these filters."
            else:
                empty_reason = (
                    "You've gone through every pending title in this round. "
                    "Widen the filters to see them again."
                )
        else:
            others = [
                item for item in pool_after_modes if item.id != result.id and item.image
            ]
            decoys = [
                {"image": item.image, "title": item.title}
                for item in random.sample(others, min(_DECOY_COUNT, len(others)))
            ]
            if group:
                group_seen = _group_seen(group, result)

    new_session_excluded = sorted(
        session_excluded_ids | ({result.id} if result else set()),
    )

    result_genres = genres_by_item.get(result.id) if result else None
    result_year = None
    if result and media_type == MediaTypes.MOVIE.value:
        metadata = _fetch_metadata(result) or {}
        release_date = (metadata.get("details") or {}).get("release_date")
        if release_date:
            result_year = str(release_date)[:4]

    context = {
        "group": group,
        "pool_count": len(pool_after_modes),
        "media_types": _available_media_types(items),
        "media_type": media_type,
        "genre_rows": genre_rows,
        "unknown_genre_count": unknown_genre_count,
        "selected_genres": selected_genres,
        "strict": strict,
        "duration_rows": duration_rows,
        "selected_duration": selected_duration,
        "unknown_duration_count": unknown_duration_count,
        "mode_rows": mode_rows,
        "selected_modes": selected_modes,
        "unknown_mode_count": unknown_mode_count,
        "result": result,
        "result_genres": result_genres,
        "result_year": result_year,
        "group_seen": group_seen,
        "decoys": decoys,
        "empty_reason": empty_reason,
        "session_exclude": ",".join(str(pk) for pk in new_session_excluded),
        "has_drawn": bool(request.GET.get("draw")),
    }
    return render(request, "app/roulette.html", context)


@login_required
@require_POST
def roulette_start(request):
    """Confirm "Start": change status per the scope's rules (acuerdo 12)."""
    item = get_object_or_404(Item, id=request.POST.get("item_id"))
    group_id = request.POST.get("group_id")

    if group_id:
        group = get_object_or_404(Group, id=group_id)
        _require_membership(request, group)
        group_item = get_object_or_404(GroupItem, group=group, item=item)
        mark_group_item_status(group_item, Status.IN_PROGRESS)
        messages.success(request, f'"{item.title}" is now in progress for the group.')
    else:
        apply_status_to_user(item, request.user, Status.IN_PROGRESS)
        messages.success(request, f'"{item.title}" is now in progress.')

    next_url = request.POST.get("next") or "/roulette"
    if not next_url.startswith("/"):
        next_url = "/roulette"
    return redirect(next_url)
