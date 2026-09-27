"""Group "Not interested" discards (group scope).

One member discarding is enough to hide an item for the whole group; any
member can restore it. Independent from the individual scope in
:mod:`app.discards` (CONTEXT.md, ADR 0002 point 9).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from groups.models import GroupDiscard

if TYPE_CHECKING:
    from app.models import Item
    from groups.models import Group
    from users.models import User

__all__ = [
    "discard_group_item",
    "group_discarded_item_ids",
    "group_discarded_items",
    "is_group_discarded",
    "restore_group_item",
]


def discard_group_item(group: Group, item: Item, discarded_by: User) -> GroupDiscard:
    """Mark ``item`` as not interesting for ``group``. Idempotent.

    Any member can perform this; the first one to discard wins the row (the
    ``discarded_by`` of an existing discard is left untouched).
    """
    discard, _ = GroupDiscard.objects.get_or_create(
        group=group, item=item, defaults={"discarded_by": discarded_by}
    )
    return discard


def restore_group_item(group: Group, item: Item) -> bool:
    """Undo a group discard. Any member can call this. Returns whether removed."""
    deleted, _ = GroupDiscard.objects.filter(group=group, item=item).delete()
    return deleted > 0


def is_group_discarded(group: Group, item: Item) -> bool:
    """Return whether ``group`` has discarded ``item``."""
    return GroupDiscard.objects.filter(group=group, item=item).exists()


def group_discarded_item_ids(group: Group) -> set[int]:
    """Return the ids of every item discarded by ``group``."""
    return set(
        GroupDiscard.objects.filter(group=group).values_list("item_id", flat=True)
    )


def group_discarded_items(group: Group):
    """Return the group's discarded items, most recent first, item joined."""
    return (
        GroupDiscard.objects.filter(group=group)
        .select_related("item", "discarded_by")
        .order_by("-created_at")
    )
