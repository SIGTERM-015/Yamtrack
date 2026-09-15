from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from app.models import Item, MediaTypes, Sources
from groups.models import Group, GroupItem, GroupMembership, GroupOrigin


class GroupModelTest(TestCase):
    """Test case for the Group models."""

    def setUp(self):
        """Set up test data for Group models."""
        user_model = get_user_model()
        self.user1 = user_model.objects.create_user(
            username="user1", password="testpassword123"  # noqa: S106
        )
        self.user2 = user_model.objects.create_user(
            username="user2", password="testpassword123"  # noqa: S106
        )

        self.item1 = Item.objects.create(
            title="Item 1",
            media_id="101",
            media_type=MediaTypes.TV.value,
            source=Sources.TMDB.value,
        )
        self.item2 = Item.objects.create(
            title="Item 2",
            media_id="102",
            media_type=MediaTypes.TV.value,
            source=Sources.TMDB.value,
        )

        self.group = Group.objects.create(
            name="Test Group",
            description="Test Description",
            owner=self.user1,
        )

    def test_group_creation(self):
        """Test that a Group can be created."""
        self.assertEqual(self.group.name, "Test Group")
        self.assertEqual(self.group.owner, self.user1)
        self.assertEqual(self.group.members.count(), 0)

    def test_add_remove_members(self):
        """Test adding and removing members."""
        membership = GroupMembership.objects.create(group=self.group, user=self.user2)
        self.assertIn(self.user2, self.group.members.all())
        self.assertEqual(self.group.memberships.count(), 1)

        membership.delete()
        self.assertNotIn(self.user2, self.group.members.all())
        self.assertEqual(self.group.memberships.count(), 0)

    def test_unique_group_membership(self):
        """Test that a user cannot be added twice to the same group."""
        GroupMembership.objects.create(group=self.group, user=self.user2)
        with self.assertRaises(IntegrityError):
            GroupMembership.objects.create(group=self.group, user=self.user2)

    def test_add_group_item(self):
        """Test adding an item to a group."""
        group_item = GroupItem.objects.create(
            group=self.group,
            item=self.item1,
            added_by=self.user1,
        )
        self.assertEqual(group_item.item, self.item1)
        self.assertEqual(group_item.group, self.group)
        self.assertEqual(group_item.added_by, self.user1)

    def test_unique_group_item(self):
        """Test that the same item cannot be added twice to the same group."""
        GroupItem.objects.create(
            group=self.group,
            item=self.item1,
            added_by=self.user1,
        )
        with self.assertRaises(IntegrityError):
            GroupItem.objects.create(
                group=self.group,
                item=self.item1,
                added_by=self.user2,
            )

    def test_group_origin_lifecycle(self):
        """Test the lifecycle of GroupOrigin (created, detached)."""
        origin = GroupOrigin.objects.create(
            user=self.user2,
            item=self.item1,
            group=self.group,
        )
        self.assertFalse(origin.detached)

        # Mark as detached
        origin.detached = True
        origin.save()

        fetched = GroupOrigin.objects.get(id=origin.id)
        self.assertTrue(fetched.detached)

    def test_unique_group_origin(self):
        """Test that an origin record is unique per user, item, and group."""
        GroupOrigin.objects.create(
            user=self.user2,
            item=self.item1,
            group=self.group,
        )
        with self.assertRaises(IntegrityError):
            GroupOrigin.objects.create(
                user=self.user2,
                item=self.item1,
                group=self.group,
            )
