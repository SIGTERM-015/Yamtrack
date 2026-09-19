from collections import defaultdict

from django.apps import apps
from django.db import models

from app.models import Status
from groups.models import Group, GroupItem


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


def add_item_to_group(group: Group, item, added_by, status=Status.PLANNING) -> tuple:
    """
    Add an item to a group and apply ``status`` to every member.

    Adding a title to the group's *to watch* adds it to every member's own
    *to watch* without overwriting members who already track it.

    Args:
        group: The Group instance.
        item: The Item instance.
        added_by: The user adding the item.
        status: Status applied to members; defaults to Planning (to watch).

    Returns:
        A ``(group_item, summary)`` tuple matching
        :func:`apply_status_to_group_members`.
    """
    group_item, _ = GroupItem.objects.get_or_create(
        group=group,
        item=item,
        defaults={"added_by": added_by},
    )
    summary = apply_status_to_group_members(group, item, status)
    return group_item, summary
