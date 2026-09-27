"""Personal "Not interested" discards (individual scope).

Reversible marks, independent of score and of the Dropped status. A discard
hides an item from that user's future roulette draws and recommendations
only; see :mod:`groups.discards` for the group-scoped equivalent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.models import Discard

if TYPE_CHECKING:
    from app.models import Item
    from users.models import User

__all__ = [
    "discard_item",
    "discarded_item_ids",
    "discarded_items",
    "is_discarded",
    "restore_item",
]


def discard_item(user: User, item: Item) -> Discard:
    """Mark ``item`` as not interesting for ``user``. Idempotent."""
    discard, _ = Discard.objects.get_or_create(user=user, item=item)
    return discard


def restore_item(user: User, item: Item) -> bool:
    """Undo a personal discard. Returns whether a row was removed."""
    deleted, _ = Discard.objects.filter(user=user, item=item).delete()
    return deleted > 0


def is_discarded(user: User, item: Item) -> bool:
    """Return whether ``user`` has discarded ``item``."""
    return Discard.objects.filter(user=user, item=item).exists()


def discarded_item_ids(user: User) -> set[int]:
    """Return the ids of every item ``user`` has discarded."""
    return set(Discard.objects.filter(user=user).values_list("item_id", flat=True))


def discarded_items(user: User):
    """Return the user's discarded items, most recent first, with the item joined."""
    return (
        Discard.objects.filter(user=user).select_related("item").order_by("-created_at")
    )
