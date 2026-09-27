"""Tests for the personal "Not interested" discard scope (E9).

Covers the model/service layer directly: independence from a group discard
on the same item is exercised in ``groups/tests/test_discards.py``.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.discards import (
    discard_item,
    discarded_item_ids,
    discarded_items,
    is_discarded,
    restore_item,
)
from app.models import Discard, Item, MediaTypes, Sources


class PersonalDiscardTests(TestCase):
    """Discarding and restoring an item for one user."""

    def setUp(self):
        """Create two users and an item."""
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="user1",
            password="testpassword123",  # noqa: S106
        )
        self.other = user_model.objects.create_user(
            username="user2",
            password="testpassword123",  # noqa: S106
        )
        self.item = Item.objects.create(
            title="Movie",
            media_id="1",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
        )

    def test_discard_then_is_discarded(self):
        """A discarded item is reported as discarded for that user."""
        self.assertFalse(is_discarded(self.user, self.item))
        discard_item(self.user, self.item)
        self.assertTrue(is_discarded(self.user, self.item))

    def test_discard_is_idempotent(self):
        """Discarding twice does not create a second row."""
        discard_item(self.user, self.item)
        discard_item(self.user, self.item)
        self.assertEqual(
            Discard.objects.filter(user=self.user, item=self.item).count(), 1
        )

    def test_restore_undoes_a_discard(self):
        """Restoring removes the discard and reports success."""
        discard_item(self.user, self.item)
        self.assertTrue(restore_item(self.user, self.item))
        self.assertFalse(is_discarded(self.user, self.item))

    def test_restore_without_a_discard_is_a_no_op(self):
        """Restoring something never discarded reports no removal."""
        self.assertFalse(restore_item(self.user, self.item))

    def test_discard_is_scoped_to_one_user(self):
        """One user's discard does not affect another user's view of the item."""
        discard_item(self.user, self.item)
        self.assertFalse(is_discarded(self.other, self.item))
        self.assertNotIn(self.item.id, discarded_item_ids(self.other))

    def test_discarded_item_ids_and_items(self):
        """The listing helpers return exactly the user's discarded items."""
        discard_item(self.user, self.item)
        self.assertEqual(discarded_item_ids(self.user), {self.item.id})
        self.assertEqual(
            [d.item_id for d in discarded_items(self.user)], [self.item.id]
        )
