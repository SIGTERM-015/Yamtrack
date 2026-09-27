from collections import defaultdict

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.utils import timezone

from app import providers
from app.models import Item, MediaTypes, Status
from groups.models import Group, GroupEpisodeWatch, GroupItem

# Minimum number of submitted scores needed to report a comparison difference.
_MIN_SCORES_FOR_DIFFERENCE = 2


def get_group_progress(group: Group) -> dict:
    """
    Get aggregated progress for all items tracked by a group.

    Args:
        group: The Group instance.

    Returns:
        A dictionary mapping item_id (int) to a dictionary containing:
            - 'item_id': the item ID.
            - 'media_type': the media type.
            - 'completed_count' (int): Number of members who have completed the item.
            - 'total_members' (int): Total number of members in the group.
            - 'members' (dict): A dictionary mapping user_id (int) to:
                - 'status' (str|None): The tracking status, e.g., 'Completed',
                  or None if not tracked.
                - 'progress' (int): The current progress (calculated for TV,
                  raw for others).
    """
    group_items = list(group.group_items.select_related("item"))
    members = list(group.members.all())

    result = {}
    for gi in group_items:
        result[gi.item.id] = {
            "item_id": gi.item.id,
            "media_type": gi.item.media_type,
            "completed_count": 0,
            "total_members": len(members),
            "members": {m.id: {"status": None, "progress": 0} for m in members},
        }

    items_by_type = defaultdict(list)
    for gi in group_items:
        items_by_type[gi.item.media_type].append(gi.item.id)

    member_ids = [m.id for m in members]

    for media_type, item_ids in items_by_type.items():
        # Dynamically load the media model
        model = apps.get_model("app", media_type)

        qs = model.objects.filter(item_id__in=item_ids, user_id__in=member_ids)

        # If TV show, we must calculate progress from the related episodes,
        # explicitly excluding season 0.
        if media_type.lower() == "tv":
            qs = qs.annotate(
                calculated_progress=models.Count(
                    "seasons__episodes",
                    filter=models.Q(seasons__item__season_number__gt=0),
                )
            ).values("item_id", "user_id", "status", "calculated_progress")
        else:
            qs = qs.values("item_id", "user_id", "status", "progress")

        for row in qs:
            item_id = row["item_id"]
            user_id = row["user_id"]
            status = row["status"]

            # Map the right progress field
            if media_type.lower() == "tv":
                progress = row.get("calculated_progress", 0)
            else:
                progress = row.get("progress", 0)

            result[item_id]["members"][user_id] = {
                "status": status,
                "progress": progress,
            }
            if status == Status.COMPLETED.value:
                result[item_id]["completed_count"] += 1

    return result


def apply_status_to_group_members(group: Group, item, status) -> dict:
    """
    Apply a status to every member of a group without overwriting existing data.

    Only members who do not already have the item in ``status`` are touched.
    Members who already hold that exact status keep their dates and notes
    intact; members with a different status only have the status field changed.

    The operation is idempotent: running it a second time changes nothing.

    Args:
        group: The Group instance.
        item: The Item instance.
        status: A Status value or member.

    Returns:
        A summary dict with ``created``, ``updated`` and ``skipped`` counts.
    """
    status_value = status.value if hasattr(status, "value") else status
    model = apps.get_model("app", item.media_type)
    member_ids = list(group.members.values_list("id", flat=True))
    existing = {
        entry.user_id: entry
        for entry in model.objects.filter(item=item, user_id__in=member_ids)
    }

    to_create = []
    updated = 0
    skipped = 0

    for user_id in member_ids:
        entry = existing.get(user_id)
        if entry is None:
            to_create.append(model(item=item, user_id=user_id, status=status_value))
        elif entry.status == status_value:
            skipped += 1
        else:
            updated += model.objects.filter(pk=entry.pk).update(status=status_value)

    if to_create:
        model.objects.bulk_create(to_create)

    return {"created": len(to_create), "updated": updated, "skipped": skipped}


def apply_status_to_user(item, user, status) -> dict:
    """
    Apply a status to a single user's tracking entry.

    Unlike the group bulk action, this is an explicit individual action: an
    existing entry has its status replaced rather than skipped.

    Args:
        item: The Item instance.
        user: The user the status applies to.
        status: A Status value or member.

    Returns:
        A summary dict with ``created`` and ``updated`` counts.
    """
    status_value = status.value if hasattr(status, "value") else status
    model = apps.get_model("app", item.media_type)
    updated = model.objects.filter(item=item, user=user).update(status=status_value)
    if updated:
        return {"created": 0, "updated": updated}

    # bulk_create bypasses Model.save(), whose process_status() hook would
    # call the metadata provider for a title we already have locally.
    model.objects.bulk_create([model(item=item, user=user, status=status_value)])
    return {"created": 1, "updated": 0}


def add_item_to_group(group: Group, item, added_by, status=Status.PLANNING) -> tuple:
    """
    Add an item to a group, creating ``status`` only for members without it.

    Adding a title to the group's *to watch* adds it to every member's own
    *to watch* without touching members who already track it: existing
    records keep their status, progress, score and notes intact. Unlike
    :func:`apply_status_to_group_members` (an explicit status change), this
    never updates existing entries.

    Args:
        group: The Group instance.
        item: The Item instance.
        added_by: The user adding the item.
        status: Status for newly created records; defaults to Planning.

    Returns:
        A ``(group_item, summary)`` tuple with ``created``/``updated``/
        ``skipped`` counts matching :func:`apply_status_to_group_members`
        (``updated`` is always 0 here).
    """
    group_item, _ = GroupItem.objects.get_or_create(
        group=group,
        item=item,
        defaults={"added_by": added_by},
    )
    status_value = status.value if hasattr(status, "value") else status
    model = apps.get_model("app", item.media_type)
    member_ids = list(group.members.values_list("id", flat=True))
    existing_ids = set(
        model.objects.filter(item=item, user_id__in=member_ids).values_list(
            "user_id", flat=True
        )
    )
    to_create = [
        model(item=item, user_id=user_id, status=status_value)
        for user_id in member_ids
        if user_id not in existing_ids
    ]
    if to_create:
        model.objects.bulk_create(to_create)
    summary = {
        "created": len(to_create),
        "updated": 0,
        "skipped": len(existing_ids),
    }
    return group_item, summary


def resolve_group_context(user, item) -> dict:
    """
    Resolve the group context that applies to an item in a user's profile.

    **Provisional policy (E10.3).** An item is considered "shared" when more
    than one active member of a group it belongs to also holds that item.
    Shared items resolve to ``"assume"``: callers may assume the group context
    without asking. Anything else resolves to ``"ask"``: the context is
    ambiguous and the user should be prompted before any group-wide action.

    This is a read-only helper. It never writes to the scrobbling user's
    profile or to any other member's profile (no-destruction rule, E2).

    Args:
        user: The user whose profile received the item.
        item: The Item instance.

    Returns:
        dict: ``{"groups": [group_id, ...], "policy": "assume"|"ask"}``. The
        group list holds the ids of the groups that link ``user`` and
        ``item``, either because the user is a member and the group tracks the
        item (``GroupItem``) or because the item entered the user's profile
        from the group and is still attached (``GroupOrigin`` with
        ``detached=False``).
    """
    member_groups = Group.objects.filter(members=user, group_items__item=item)
    origin_groups = Group.objects.filter(
        group_origins__user=user,
        group_origins__item=item,
        group_origins__detached=False,
    )
    groups = (member_groups | origin_groups).distinct()

    policy = "ask"
    for group in groups:
        holders = (
            get_user_model()
            .objects.filter(is_active=True)
            .filter(
                models.Q(
                    added_group_items__group=group,
                    added_group_items__item=item,
                )
                | models.Q(
                    group_origins__group=group,
                    group_origins__item=item,
                    group_origins__detached=False,
                )
            )
            .distinct()
            .count()
        )
        if holders > 1:
            policy = "assume"
            break

    return {"groups": list(groups.values_list("id", flat=True)), "policy": policy}


def _group_scores(
    group_items: list, members: list
) -> dict[int, dict[int, float | None]]:
    """Return ``{item_id: {user_id: score|None}}`` for the given members."""
    member_ids = [m.id for m in members]

    items_by_type = defaultdict(list)
    for gi in group_items:
        items_by_type[gi.item.media_type].append(gi.item.id)

    scores_by_item = {gi.item.id: {m.id: None for m in members} for gi in group_items}

    for media_type, item_ids in items_by_type.items():
        model = apps.get_model("app", media_type)
        rows = model.objects.filter(
            item_id__in=item_ids,
            user_id__in=member_ids,
        ).values("item_id", "user_id", "score")

        for row in rows:
            score = row["score"]
            scores_by_item[row["item_id"]][row["user_id"]] = (
                None if score is None else float(score)
            )

    return scores_by_item


def get_group_comparison(group: Group) -> list[dict]:
    """
    Build a side-by-side rating comparison for the items in a group.

    Ratings (``score``) are shared within the group, so they are compared
    here. The free-text ``notes`` field is private to each user and is
    deliberately neither read nor returned by this function.

    Args:
        group: The Group instance.

    Returns:
        A list of dicts, one per group item, sorted by rating discrepancy
        (largest first, unscored items last):
            - 'item_id' (int): the item ID.
            - 'media_type' (str): the media type.
            - 'scores' (dict): user_id -> submitted score (float) or None.
            - 'average' (float|None): mean of the submitted scores.
            - 'difference' (float|None): gap between the highest and lowest
              submitted score, None when fewer than two members scored.
    """
    group_items = list(group.group_items.select_related("item"))
    members = list(group.members.all())
    scores_by_item = _group_scores(group_items, members)

    comparison = []
    for gi in group_items:
        scores = scores_by_item[gi.item.id]
        submitted = [score for score in scores.values() if score is not None]

        comparison.append(
            {
                "item_id": gi.item.id,
                "media_type": gi.item.media_type,
                "scores": scores,
                "average": (sum(submitted) / len(submitted) if submitted else None),
                "difference": (
                    max(submitted) - min(submitted)
                    if len(submitted) >= _MIN_SCORES_FOR_DIFFERENCE
                    else None
                ),
            }
        )

    comparison.sort(
        key=lambda row: (
            row["difference"] is None,
            -(row["difference"] or 0),
        )
    )
    return comparison


def _default_genre_getter(item) -> list[str]:
    """Best-effort read of an item's genres without importing the Genre model."""
    genres = getattr(item, "genres", ())
    # Works with the E7.1 M2M manager (``.all()``) and plain iterables.
    if callable(getattr(genres, "all", None)):
        genres = genres.all()
    return [str(genre).strip() for genre in genres if genre]


def get_group_genre_stats(group: Group, genre_getter=None) -> dict:
    """
    Aggregate ratings by genre and member for a group.

    Genres are read through a pluggable ``genre_getter`` callable (defaults to
    the item's ``genres`` relation) so this service stays decoupled from the
    E7.1 ``Genre`` model.

    Args:
        group: The Group instance.
        genre_getter: optional callable taking an item and returning an
            iterable of genre names.

    Returns:
        A dict with:
            - 'members' (list): the group members.
            - 'genres' (list): one dict per genre, sorted by rated volume
              (largest first), each with 'genre', 'members'
              (user_id -> {'average': float|None, 'count': int}),
              'average' (float|None), 'difference' (float|None, gap between
              the member averages, None when fewer than two members scored)
              and 'count' (int).
            - 'agreements' (list): genres with the smallest member difference.
            - 'disagreements' (list): genres with the largest member difference.
            - 'volume' (dict): user_id -> number of rated items.
    """
    getter = genre_getter or _default_genre_getter
    group_items = list(group.group_items.select_related("item"))
    members = list(group.members.all())
    scores_by_item = _group_scores(group_items, members)

    genres_by_item = {gi.item.id: list(getter(gi.item)) for gi in group_items}

    genre_member_scores = defaultdict(lambda: defaultdict(list))
    volume = {m.id: 0 for m in members}

    for gi in group_items:
        for user_id, score in scores_by_item[gi.item.id].items():
            if score is None:
                continue
            volume[user_id] += 1
            for genre in genres_by_item[gi.item.id]:
                genre_member_scores[genre][user_id].append(score)

    genres = []
    for genre, per_member in genre_member_scores.items():
        member_stats = {}
        for member in members:
            member_scores = per_member.get(member.id, [])
            member_stats[member.id] = {
                "average": (
                    sum(member_scores) / len(member_scores) if member_scores else None
                ),
                "count": len(member_scores),
            }

        all_scores = [s for scores in per_member.values() for s in scores]
        averages = [
            stats["average"]
            for stats in member_stats.values()
            if stats["average"] is not None
        ]

        genres.append(
            {
                "genre": genre,
                "members": member_stats,
                "average": (sum(all_scores) / len(all_scores) if all_scores else None),
                "difference": (
                    max(averages) - min(averages)
                    if len(averages) >= _MIN_SCORES_FOR_DIFFERENCE
                    else None
                ),
                "count": len(all_scores),
            }
        )

    genres.sort(key=lambda row: (-row["count"], row["genre"]))

    rated = [row for row in genres if row["difference"] is not None]
    agreements = sorted(rated, key=lambda row: row["difference"])
    disagreements = sorted(rated, key=lambda row: row["difference"], reverse=True)

    return {
        "members": members,
        "genres": genres,
        "agreements": agreements,
        "disagreements": disagreements,
        "volume": volume,
    }


# Media types whose propagation is episode-based and handled in S4.
_TV_MEDIA_TYPES = {
    MediaTypes.TV.value,
    MediaTypes.SEASON.value,
    MediaTypes.EPISODE.value,
}

# Personal status only ever advances along this order (design §2.2); Paused and
# Dropped are handled separately (Paused keeps its status, Dropped is terminal).
_STATUS_ORDER = {
    Status.PLANNING.value: 0,
    Status.IN_PROGRESS.value: 1,
    Status.COMPLETED.value: 2,
}

# Only positive group advances propagate; a Paused/Dropped group changes nothing.
_PROPAGATING_GROUP_STATUSES = {Status.IN_PROGRESS.value, Status.COMPLETED.value}


def _resolve_participant_ids(group: Group, participants) -> list:
    """Return participant user ids; ``None`` means every member of the group."""
    if participants is None:
        return list(group.members.values_list("id", flat=True))
    return [p.id if hasattr(p, "id") else p for p in participants]


def propagate_group_progress(group_item: GroupItem, participants=None) -> dict:
    """
    Merge the group's status/progress into the personal records of participants.

    Monotone merge (design §2.2): progress and status only advance, never
    decrease, and completed or dropped records are never reopened. Existing
    ``score``/``notes`` are left untouched. TV media is skipped here (S4 owns
    episode-level propagation).

    ``participants=None`` propagates to every member; an empty list propagates
    to nobody (a group-only update). The operation is idempotent.

    Args:
        group_item: The GroupItem holding the group's status/progress.
        participants: Iterable of user ids (or users) to update; None = all.

    Returns:
        A summary dict with ``created``, ``updated`` and ``skipped`` counts.
    """
    item = group_item.item
    participant_ids = _resolve_participant_ids(group_item.group, participants)
    group_status = group_item.status
    group_progress = group_item.progress

    if (
        item.media_type in _TV_MEDIA_TYPES
        or group_status not in _PROPAGATING_GROUP_STATUSES
    ):
        return {"created": 0, "updated": 0, "skipped": len(participant_ids)}

    model = apps.get_model("app", item.media_type)
    existing = {
        entry.user_id: entry
        for entry in model.objects.filter(item=item, user_id__in=participant_ids)
    }

    now = timezone.now()
    created = 0
    updated = 0
    skipped = 0

    for user_id in participant_ids:
        entry = existing.get(user_id)
        if entry is None:
            entry = model(
                item=item,
                user_id=user_id,
                status=group_status,
                progress=group_progress,
            )
            if group_status == Status.COMPLETED.value:
                entry.end_date = group_item.completed_at or now
            entry.save()
            created += 1
            continue

        # Completed and Dropped are terminal for propagation (design §2.2).
        if entry.status in (Status.COMPLETED.value, Status.DROPPED.value):
            skipped += 1
            continue

        changed = False
        if entry.progress < group_progress:
            entry.progress = group_progress
            changed = True

        if entry.status != Status.PAUSED.value and _STATUS_ORDER.get(
            group_status,
            0,
        ) > _STATUS_ORDER.get(entry.status, 0):
            entry.status = group_status
            changed = True
            if group_status == Status.COMPLETED.value and entry.end_date is None:
                entry.end_date = group_item.completed_at or now

        if changed:
            entry.save()
            updated += 1
        else:
            skipped += 1

    return {"created": created, "updated": updated, "skipped": skipped}


def mark_group_item_status(
    group_item: GroupItem,
    status,
    participants=None,
) -> GroupItem:
    """
    Write the group's own status on a GroupItem and propagate it to members.

    The GroupItem keeps its own status/dates; then non-TV participants receive a
    monotone merge of the group's status and progress
    (:func:`propagate_group_progress`). ``participants=None`` targets every
    member, an empty list targets nobody. TV propagation arrives in S4.

    Args:
        group_item: The GroupItem to update.
        status: A Status value or member.
        participants: Iterable of user ids (or users); None = all members.

    Returns:
        The saved GroupItem.
    """
    status_value = status.value if hasattr(status, "value") else status
    now = timezone.now()
    with transaction.atomic():
        group_item.status = status_value
        group_item.progressed_at = now
        if status_value == Status.IN_PROGRESS.value:
            if group_item.started_at is None:
                group_item.started_at = now
            group_item.completed_at = None
        elif status_value == Status.COMPLETED.value:
            if group_item.started_at is None:
                group_item.started_at = now
            group_item.completed_at = now
        elif status_value == Status.PLANNING.value:
            group_item.started_at = None
            group_item.completed_at = None
        group_item.save(
            update_fields=["status", "started_at", "completed_at", "progressed_at"],
        )
        propagate_group_progress(group_item, participants)
    return group_item


_TAB_STATUS_BUCKETS = {
    Status.PLANNING.value: "pending",
    Status.IN_PROGRESS.value: "watching",
    Status.COMPLETED.value: "watched",
}


def get_group_tab_items(group: Group) -> dict:
    """
    Classify a group's items into tabs from the group's own status.

    Planning goes to pending, In progress to watching, Completed to
    watched; Paused and Dropped fall into others (never mixed into the
    three main tabs).

    Args:
        group: The Group instance.

    Returns:
        A dict with ``pending``/``watching``/``watched``/``others`` lists
        of GroupItem.
    """
    tabs = {"pending": [], "watching": [], "watched": [], "others": []}
    for group_item in group.group_items.select_related("item").all():
        tabs[_TAB_STATUS_BUCKETS.get(group_item.status, "others")].append(
            group_item,
        )
    return tabs


# --- S4: TV propagation (episode-based) -------------------------------------

# Statuses for which a member's TV/season record is terminal for propagation:
# never reopened, never given new episodes as "advancing" (design §2.3).
_TV_TERMINAL_STATUSES = {Status.COMPLETED.value, Status.DROPPED.value}


def episodes_up_to(season_number: int, episode_number: int) -> list[tuple[int, int]]:
    """
    Build the "up to episode N" shortcut: episodes 1..N of a season.

    Args:
        season_number: The season number (0 allowed, though season 0 never
            counts towards progress).
        episode_number: The last episode number to include (inclusive).

    Returns:
        A list of ``(season_number, episode_number)`` tuples, in order.
    """
    return [(season_number, number) for number in range(1, episode_number + 1)]


def _get_or_create_season_item(tv_item: Item, season_number: int) -> Item:
    """Get or create the Item for a season of ``tv_item``."""
    item, _ = Item.objects.get_or_create(
        media_id=tv_item.media_id,
        source=tv_item.source,
        media_type=MediaTypes.SEASON.value,
        season_number=season_number,
        defaults={"title": tv_item.title, "image": settings.IMG_NONE},
    )
    return item


def _get_or_create_episode_item(
    tv_item: Item,
    season_number: int,
    episode_number: int,
) -> Item:
    """Get or create the Item for an episode of ``tv_item``, no metadata call."""
    item, _ = Item.objects.get_or_create(
        media_id=tv_item.media_id,
        source=tv_item.source,
        media_type=MediaTypes.EPISODE.value,
        season_number=season_number,
        episode_number=episode_number,
        defaults={"title": tv_item.title, "image": settings.IMG_NONE},
    )
    return item


def _get_tv_max_progress(tv_item: Item) -> int | None:
    """Best-effort total episode count for ``tv_item``, or None if unknown."""
    try:
        metadata = providers.services.get_media_metadata(
            MediaTypes.TV.value,
            tv_item.media_id,
            tv_item.source,
        )
    except Exception:  # noqa: BLE001 - metadata may be unavailable; best-effort
        return None
    return metadata.get("max_progress")


def mark_group_episodes_watched(
    group_item: GroupItem,
    episodes,
    participants=None,
) -> GroupItem:
    """
    Record episodes watched by the group and propagate them to members.

    ``episodes`` is an iterable of ``(season_number, episode_number)`` tuples.
    Each is written once to the group's episode ledger (idempotent: a repeated
    episode is a no-op). The group's denormalized progress excludes season 0
    (design §1.2/§2.3); the group's status advances Planning -> In progress,
    and reaches Completed only when the total watched matches the show's known
    episode count (best-effort: skipped when metadata is unavailable).

    Then each participant receives a monotone merge of the group's watched
    episodes (:func:`_propagate_group_episodes`): TV/Season/Episode records
    are created as needed, never reduced, Completed/Dropped members are
    skipped entirely, and Paused members get the episodes without leaving
    Paused.

    Args:
        group_item: The GroupItem for the TV show.
        episodes: Iterable of ``(season_number, episode_number)`` tuples.
        participants: Iterable of user ids (or users); None = all members.

    Returns:
        The saved GroupItem.
    """
    tv_item = group_item.item
    now = timezone.now()

    with transaction.atomic():
        for season_number, episode_number in episodes:
            episode_item = _get_or_create_episode_item(
                tv_item,
                season_number,
                episode_number,
            )
            GroupEpisodeWatch.objects.get_or_create(
                group_item=group_item,
                item=episode_item,
            )

        progress = group_item.watched_episodes.filter(
            item__season_number__gt=0,
        ).count()
        group_item.progress = progress
        group_item.progressed_at = now

        if progress > 0 and group_item.status == Status.PLANNING.value:
            group_item.status = Status.IN_PROGRESS.value
            if group_item.started_at is None:
                group_item.started_at = now

        if group_item.status in (Status.PLANNING.value, Status.IN_PROGRESS.value):
            max_progress = _get_tv_max_progress(tv_item)
            if max_progress and progress >= max_progress:
                group_item.status = Status.COMPLETED.value
                if group_item.completed_at is None:
                    group_item.completed_at = now

        group_item.save(
            update_fields=[
                "status",
                "progress",
                "started_at",
                "completed_at",
                "progressed_at",
            ],
        )

    _propagate_group_episodes(group_item, participants)
    return group_item


def _propagate_episodes_for_season(
    tv_item: Item,
    tv,
    season_number: int,
    items: list,
    watched_at_by_item: dict,
    user_id: int,
) -> bool:
    """Merge one season's worth of watched episodes into a member's records."""
    season_model = apps.get_model("app", MediaTypes.SEASON.value)
    episode_model = apps.get_model("app", MediaTypes.EPISODE.value)

    season_item = _get_or_create_season_item(tv_item, season_number)
    season = season_model.objects.filter(related_tv=tv, item=season_item).first()
    touched = False

    if season is None:
        season = season_model(
            item=season_item,
            user_id=user_id,
            related_tv=tv,
            status=Status.PLANNING.value,
            notes="",
        )
        season_model.save_base(season)
        touched = True
    elif season.status in _TV_TERMINAL_STATUSES:
        return False

    existing_item_ids = set(
        episode_model.objects.filter(related_season=season).values_list(
            "item_id",
            flat=True,
        ),
    )
    to_create = [
        episode_model(
            related_season=season,
            item=item,
            end_date=watched_at_by_item.get(item.id),
        )
        for item in items
        if item.id not in existing_item_ids
    ]
    if to_create:
        episode_model.objects.bulk_create(to_create)
        touched = True

    if season.status == Status.PLANNING.value and to_create:
        season_model.objects.filter(pk=season.pk).update(
            status=Status.IN_PROGRESS.value,
        )
        touched = True

    return touched


def _propagate_episode_to_member(
    tv_item: Item,
    watched_items: list,
    watched_at_by_item: dict,
    user_id: int,
    group_status: str,
) -> bool:
    """
    Merge the group's watched episodes into one member's personal records.

    Uses ``save_base``/``bulk_create``/``queryset.update`` throughout instead
    of the model's normal ``save()`` so this never triggers the personal
    model's own metadata-fetching status cascades; the monotone rules of
    design §2.3 are applied explicitly here instead.

    Returns:
        True if the member's records were touched, False if skipped
        (terminal status) or nothing was new.
    """
    tv_model = apps.get_model("app", MediaTypes.TV.value)

    tv = tv_model.objects.filter(item=tv_item, user_id=user_id).first()
    if tv is not None and tv.status in _TV_TERMINAL_STATUSES:
        return False

    if tv is None:
        tv = tv_model(item=tv_item, user_id=user_id, status=Status.PLANNING.value)
        tv_model.save_base(tv)

    by_season = defaultdict(list)
    for item in watched_items:
        by_season[item.season_number].append(item)

    touched = False
    for season_number, items in by_season.items():
        if _propagate_episodes_for_season(
            tv_item,
            tv,
            season_number,
            items,
            watched_at_by_item,
            user_id,
        ):
            touched = True

    if tv.status not in _TV_TERMINAL_STATUSES:
        new_status = tv.status
        if group_status == Status.COMPLETED.value:
            new_status = Status.COMPLETED.value
        elif tv.status == Status.PLANNING.value and touched:
            new_status = Status.IN_PROGRESS.value

        if new_status != tv.status:
            tv_model.objects.filter(pk=tv.pk).update(status=new_status)
            touched = True

    return touched


def _propagate_group_episodes(group_item: GroupItem, participants=None) -> dict:
    """
    Propagate all of the group's watched episodes to selected participants.

    Recomputes from the full ledger every call (not just newly added
    episodes), so a member who was behind on older episodes also catches up;
    idempotent by construction.

    Args:
        group_item: The GroupItem holding the episode ledger.
        participants: Iterable of user ids (or users); None = all members.

    Returns:
        A summary dict with ``updated`` and ``skipped`` counts.
    """
    tv_item = group_item.item
    participant_ids = _resolve_participant_ids(group_item.group, participants)
    watches = list(group_item.watched_episodes.select_related("item").all())
    watched_items = [watch.item for watch in watches]
    watched_at_by_item = {watch.item_id: watch.watched_at for watch in watches}

    summary = {"updated": 0, "skipped": 0}
    for user_id in participant_ids:
        touched = _propagate_episode_to_member(
            tv_item,
            watched_items,
            watched_at_by_item,
            user_id,
            group_item.status,
        )
        summary["updated" if touched else "skipped"] += 1

    return summary
