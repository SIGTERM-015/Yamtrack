from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import TV, Item, MediaTypes, Sources
from groups.models import Group, GroupInvitation


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


class GroupCreateInviteViewsTest(TestCase):
    """Test case for creating groups and managing invitations."""

    def setUp(self):
        """Set up test data."""
        user_model = get_user_model()
        self.user1 = user_model.objects.create_user(
            username="user1", password="testpassword123",  # noqa: S106
        )
        self.user2 = user_model.objects.create_user(
            username="user2", password="testpassword123",  # noqa: S106
        )
        self.group = Group.objects.create(
            name="Existing Group",
            owner=self.user1,
        )
        self.group.members.add(self.user1)

    def test_group_create_requires_login(self):
        """Unauthenticated users are redirected."""
        response = self.client.get(reverse("group_create"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/accounts/login/"))

    def test_group_create_valid(self):
        """Creating a group makes the creator owner and first member."""
        self.client.login(username="user1", password="testpassword123")  # noqa: S106
        response = self.client.post(
            reverse("group_create"),
            {"name": "New Group", "description": "A new group"},
        )
        group = Group.objects.get(name="New Group")
        self.assertRedirects(response, reverse("group_detail", args=[group.id]))
        self.assertEqual(group.owner, self.user1)
        self.assertTrue(group.members.filter(id=self.user1.id).exists())
        self.assertIn(group, self.user1.joined_groups.all())

    def test_group_create_empty_name(self):
        """An empty name is rejected and no group is created."""
        self.client.login(username="user1", password="testpassword123")  # noqa: S106
        response = self.client.post(reverse("group_create"), {"name": "   "})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Group name is required.")
        self.assertEqual(Group.objects.filter(owner=self.user1).count(), 1)

    def test_group_invite_creates_pending_invitation(self):
        """A member can invite another user."""
        self.client.login(username="user1", password="testpassword123")  # noqa: S106
        response = self.client.post(
            reverse("group_invite", args=[self.group.id]), {"username": "user2"}
        )
        self.assertRedirects(
            response, reverse("group_detail", args=[self.group.id])
        )
        self.assertTrue(
            GroupInvitation.objects.filter(
                group=self.group, invited_user=self.user2, invited_by=self.user1
            ).exists()
        )
        # The invitee is not a member until accepting
        self.assertFalse(self.group.members.filter(id=self.user2.id).exists())

    def test_group_invite_unknown_user(self):
        """Inviting a nonexistent username creates nothing."""
        self.client.login(username="user1", password="testpassword123")  # noqa: S106
        self.client.post(
            reverse("group_invite", args=[self.group.id]), {"username": "ghost"}
        )
        self.assertEqual(GroupInvitation.objects.count(), 0)

    def test_group_invite_non_member_forbidden(self):
        """Non-members cannot invite to a group."""
        self.client.login(username="user2", password="testpassword123")  # noqa: S106
        response = self.client.post(
            reverse("group_invite", args=[self.group.id]), {"username": "user1"}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(GroupInvitation.objects.count(), 0)

    def test_group_invite_already_member(self):
        """Inviting an existing member is a no-op."""
        self.client.login(username="user1", password="testpassword123")  # noqa: S106
        self.client.post(
            reverse("group_invite", args=[self.group.id]), {"username": "user1"}
        )
        self.assertEqual(GroupInvitation.objects.count(), 0)

    def test_group_list_shows_invitation(self):
        """The invitee sees the pending invitation in their group list."""
        GroupInvitation.objects.create(
            group=self.group, invited_user=self.user2, invited_by=self.user1
        )
        self.client.login(username="user2", password="testpassword123")  # noqa: S106
        response = self.client.get(reverse("group_list"))
        self.assertContains(response, "Pending invitations")
        self.assertContains(response, "Existing Group")

    def test_group_detail_visible_to_invitee(self):
        """An invited non-member can open the group detail."""
        GroupInvitation.objects.create(
            group=self.group, invited_user=self.user2, invited_by=self.user1
        )
        self.client.login(username="user2", password="testpassword123")  # noqa: S106
        response = self.client.get(reverse("group_detail", args=[self.group.id]))
        self.assertEqual(response.status_code, 200)

    def test_invitation_accept_creates_membership(self):
        """Accepting joins the group and removes the invitation."""
        invitation = GroupInvitation.objects.create(
            group=self.group, invited_user=self.user2, invited_by=self.user1
        )
        self.client.login(username="user2", password="testpassword123")  # noqa: S106
        response = self.client.post(
            reverse("group_invitation_accept", args=[invitation.id])
        )
        self.assertRedirects(
            response, reverse("group_detail", args=[self.group.id])
        )
        self.assertTrue(self.group.members.filter(id=self.user2.id).exists())
        membership = self.group.memberships.get(user=self.user2)
        self.assertIsNotNone(membership.joined_at)
        self.assertFalse(GroupInvitation.objects.filter(id=invitation.id).exists())
        # Group now appears in the joiner's own list
        self.assertIn(self.group, self.user2.joined_groups.all())

    def test_invitation_accept_by_other_user(self):
        """Only the invited user can accept the invitation."""
        invitation = GroupInvitation.objects.create(
            group=self.group, invited_user=self.user2, invited_by=self.user1
        )
        self.client.login(username="user1", password="testpassword123")  # noqa: S106
        response = self.client.post(
            reverse("group_invitation_accept", args=[invitation.id])
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(GroupInvitation.objects.filter(id=invitation.id).exists())

    def test_invitation_reject(self):
        """Rejecting removes the invitation without joining."""
        invitation = GroupInvitation.objects.create(
            group=self.group, invited_user=self.user2, invited_by=self.user1
        )
        self.client.login(username="user2", password="testpassword123")  # noqa: S106
        response = self.client.post(
            reverse("group_invitation_reject", args=[invitation.id])
        )
        self.assertRedirects(response, reverse("group_list"))
        self.assertFalse(GroupInvitation.objects.filter(id=invitation.id).exists())
        self.assertFalse(self.group.members.filter(id=self.user2.id).exists())
