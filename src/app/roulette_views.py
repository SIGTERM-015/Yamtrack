"""Views for the roulette feature (E7.3): personal and group scope.

Wraps the pure random-selection engine in :mod:`app.roulette` with the pool
queries (pending titles only), personal/group discard exclusion and the
"Start" action, which changes status via the existing group service
(respecting Dropped members) for the group scope, or a plain personal status
update otherwise. A draw never mutates anything (docs/research/ruleta-ui.md).
"""

from __future__ import annotations

import logging

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from app.discards import discarded_item_ids
from app.models import Item, MediaTypes, Status
from app.roulette import pick_random
from groups.discards import group_discarded_item_ids
from groups.models import Group, GroupItem
from groups.services import apply_status_to_user, mark_group_item_status

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


def _available_genres(items, media_type):
    """Return the distinct genre names available for one media type."""
    genres = set()
    for item in items:
        if media_type and item.media_type != media_type:
            continue
        genres.update(str(genre) for genre in item.genres.all())
    return sorted(genres)


def _require_membership(request, group):
    """Raise 404 when the current user is not a member of ``group``."""
    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)


@login_required
@require_GET
def roulette(request):
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
    selected_genres = [g for g in request.GET.getlist("genres") if g]

    session_exclude_raw = request.GET.get("session_exclude", "")
    session_excluded_ids = {
        int(pk) for pk in session_exclude_raw.split(",") if pk.strip().isdigit()
    }

    pool = [
        item for item in items if media_type is None or item.media_type == media_type
    ]
    remaining_pool = [item for item in pool if item.id not in session_excluded_ids]

    result = None
    empty_reason = None
    if request.GET.get("draw"):
        result = pick_random(remaining_pool, genres=selected_genres or None)
        if result is None:
            if not pool:
                empty_reason = "No pending titles match this media type yet."
            elif not remaining_pool:
                empty_reason = (
                    "You've gone through every pending title in this round. "
                    "Widen the filters to see them again."
                )
            else:
                empty_reason = "No pending title matches these genres."

    new_session_excluded = sorted(
        session_excluded_ids | ({result.id} if result else set()),
    )

    context = {
        "group": group,
        "pool_count": len(pool),
        "media_types": _available_media_types(items),
        "media_type": media_type,
        "available_genres": _available_genres(items, media_type),
        "selected_genres": selected_genres,
        "result": result,
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
