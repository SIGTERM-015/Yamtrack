from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import TV, Item, MediaTypes, Sources
from groups.models import Group


class GroupViewsTest(TestCase):
    """Test case for the Group views."""

    def setUp(self):
        """Set up test data."""
        user_model = get_user_model()
        self.user1 = user_model.objects.create_user(
            username="user1", password="testpassword123",  # noqa: S106
        )
        self.user2 = user_model.objects.create_user(
            username="user2", password="testpassword123",  # noqa: S106
        )
        self.user3 = user_model.objects.create_user(
            username="user3", password="testpassword123",  # noqa: S106
        )

        self.group = Group.objects.create(
            name="Test Group",
            description="Test Description",
            owner=self.user1,
        )
        # user1 and user2 are members, user3 is not
        self.group.members.add(self.user1)
        self.group.members.add(self.user2)

        self.item1 = Item.objects.create(
            title="Item 1",
            media_id="101",
            media_type=MediaTypes.TV.value,
            source=Sources.TMDB.value,
        )

        TV.objects.create(
            user=self.user1,
            item=self.item1,
            status="Watching",
        )

        self.group.group_items.create(
            item=self.item1,
            added_by=self.user1,
        )

    def test_group_list_unauthenticated(self):
        """Test that unauthenticated users are redirected."""
        url = reverse("group_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/accounts/login/"))

    def test_group_list_authenticated(self):
        """Test that list shows only user's groups."""
        self.client.login(username="user1", password="testpassword123")  # noqa: S106
        url = reverse("group_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Group")

        # User3 has no groups
        self.client.login(username="user3", password="testpassword123")  # noqa: S106
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Test Group")
        self.assertContains(response, "You don't belong to any groups yet.")

    def test_group_detail_unauthenticated(self):
        """Test that unauthenticated users are redirected."""
        url = reverse("group_detail", args=[self.group.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/accounts/login/"))

    def test_group_detail_non_member(self):
        """Test that non-members get 404."""
        self.client.login(username="user3", password="testpassword123")  # noqa: S106
        url = reverse("group_detail", args=[self.group.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_group_detail_member(self):
        """Test that members can access and context is correct."""
        self.client.login(username="user1", password="testpassword123")  # noqa: S106
        url = reverse("group_detail", args=[self.group.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Group")
        self.assertContains(response, "Item 1")

        # Check context
        items_data = response.context["items_data"]
        self.assertEqual(len(items_data), 1)
        self.assertEqual(items_data[0]["item"].id, self.item1.id)

        # Should contain progress for both members
        self.assertEqual(len(items_data[0]["member_progress"]), 2)
