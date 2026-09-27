"""Tests for the group "Not interested" discard scope (E9, ADR 0002 point 9).

Covers the service layer and the independence between the group scope and
the personal scope (``app.discards``) on the same item.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.discards import discard_item, is_discarded
from app.models import Item, MediaTypes, Sources
from groups.discards import (
    discard_group_item,
    group_discarded_item_ids,
    group_discarded_items,
    is_group_discarded,
    restore_group_item,
)
from groups.models import Group, GroupDiscard


class GroupDiscardServiceTests(TestCase):
    """One member's discard hides an item for the whole group."""

    def setUp(self):
        """Create a group of two members and an item."""
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="owner",
            password="testpassword123",  # noqa: S106
        )
        self.member = user_model.objects.create_user(
            username="member",
            password="testpassword123",  # noqa: S106
        )
        self.group = Group.objects.create(name="Watch club", owner=self.owner)
        self.group.members.add(self.owner, self.member)
        self.item = Item.objects.create(
            title="Movie",
            media_id="1",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
        )

    def test_any_member_can_discard_and_it_hides_for_all(self):
        """A discard by one member is visible to the whole group."""
        discard_group_item(self.group, self.item, self.member)
        self.assertTrue(is_group_discarded(self.group, self.item))
        self.assertIn(self.item.id, group_discarded_item_ids(self.group))

    def test_discard_is_idempotent(self):
        """Discarding twice keeps a single row."""
        discard_group_item(self.group, self.item, self.owner)
        discard_group_item(self.group, self.item, self.member)
        self.assertEqual(
            GroupDiscard.objects.filter(group=self.group, item=self.item).count(), 1
        )

    def test_any_member_can_restore(self):
        """Any member, not just the one who discarded, can restore."""
        discard_group_item(self.group, self.item, self.owner)
        self.assertTrue(restore_group_item(self.group, self.item))
        self.assertFalse(is_group_discarded(self.group, self.item))

    def test_group_discards_are_independent_across_groups(self):
        """A discard in one group does not affect another group."""
        other_group = Group.objects.create(name="Other club", owner=self.owner)
        other_group.members.add(self.owner)
        discard_group_item(self.group, self.item, self.owner)
        self.assertFalse(is_group_discarded(other_group, self.item))

    def test_group_discarded_items_lists_discards(self):
        """The listing helper returns the group's discarded items."""
        discard_group_item(self.group, self.item, self.owner)
        self.assertEqual(
            [d.item_id for d in group_discarded_items(self.group)], [self.item.id]
        )

    def test_personal_and_group_scopes_are_independent(self):
        """Discarding personally does not discard for the group, or vice versa."""
        discard_item(self.member, self.item)
        self.assertFalse(is_group_discarded(self.group, self.item))

        discard_group_item(self.group, self.item, self.owner)
        self.assertFalse(is_discarded(self.owner, self.item))
        # The member's personal discard from above is untouched either way.
        self.assertTrue(is_discarded(self.member, self.item))


class GroupDiscardViewTests(TestCase):
    """The discard/restore/discarded-page endpoints, membership-gated."""

    def setUp(self):
        """Create a group of two members, an outsider, and an item."""
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="owner",
            password="testpassword123",  # noqa: S106
        )
        self.member = user_model.objects.create_user(
            username="member",
            password="testpassword123",  # noqa: S106
        )
        self.outsider = user_model.objects.create_user(
            username="outsider",
            password="testpassword123",  # noqa: S106
        )
        self.group = Group.objects.create(name="Watch club", owner=self.owner)
        self.group.members.add(self.owner, self.member)
        self.item = Item.objects.create(
            title="Movie",
            media_id="1",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
        )

    def test_discard_requires_membership(self):
        """An outsider cannot discard an item for a group they're not in."""
        self.client.login(username="outsider", password="testpassword123")  # noqa: S106
        response = self.client.post(
            reverse("group_discard_item", args=[self.group.id]),
            {"item_id": self.item.id},
        )
        self.assertEqual(response.status_code, 404)

    def test_member_can_discard_and_another_member_can_restore(self):
        """Membership, not authorship, gates the discard/restore actions."""
        self.client.login(username="owner", password="testpassword123")  # noqa: S106
        response = self.client.post(
            reverse("group_discard_item", args=[self.group.id]),
            {"item_id": self.item.id},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(is_group_discarded(self.group, self.item))

        self.client.logout()
        self.client.login(username="member", password="testpassword123")  # noqa: S106
        response = self.client.post(
            reverse("group_restore_item", args=[self.group.id]),
            {"item_id": self.item.id},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(is_group_discarded(self.group, self.item))

    def test_discarded_page_lists_discards_and_requires_membership(self):
        """The standalone discarded page lists rows and is membership-gated."""
        discard_group_item(self.group, self.item, self.owner)

        self.client.login(username="member", password="testpassword123")  # noqa: S106
        response = self.client.get(reverse("group_discarded", args=[self.group.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Movie", response.content.decode())

        self.client.logout()
        self.client.login(username="outsider", password="testpassword123")  # noqa: S106
        response = self.client.get(reverse("group_discarded", args=[self.group.id]))
        self.assertEqual(response.status_code, 404)
