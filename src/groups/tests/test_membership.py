from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import Item
from groups.models import Group, GroupOrigin

User = get_user_model()


class GroupMembershipViewsTest(TestCase):
    """Test case for managing group membership."""

    def setUp(self):
        """Set up test data."""
        self.owner = User.objects.create_user(
            username="owner",
            password="testpassword123",  # noqa: S106
        )
        self.member = User.objects.create_user(
            username="member",
            password="testpassword123",  # noqa: S106
        )
        self.outsider = User.objects.create_user(
            username="outsider",
            password="testpassword123",  # noqa: S106
        )

        self.group = Group.objects.create(name="Test Group", owner=self.owner)
        self.group.members.add(self.owner)
        self.group.members.add(self.member)

        self.item = Item.objects.create(
            title="Item 1",
            media_id="101",
            media_type="movie",
            source="tmdb",
        )
        self.origin = GroupOrigin.objects.create(
            user=self.member,
            item=self.item,
            group=self.group,
        )

        self.other_group = Group.objects.create(name="Other Group", owner=self.owner)
        self.other_group.members.add(self.member)
        self.other_origin = GroupOrigin.objects.create(
            user=self.member,
            item=self.item,
            group=self.other_group,
        )

    def login(self, username):
        """Log in the given user."""
        self.client.login(username=username, password="testpassword123")  # noqa: S106

    # --- remove member ---

    def test_remove_member_requires_post(self):
        """Removing a member is a POST-only action."""
        self.login("owner")
        url = reverse("group_remove_member", args=[self.group.id, self.member.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)

    def test_owner_removes_member(self):
        """The owner can remove a member and their origins detach."""
        self.login("owner")
        url = reverse("group_remove_member", args=[self.group.id, self.member.id])
        response = self.client.post(url)

        self.assertRedirects(response, reverse("group_detail", args=[self.group.id]))
        self.assertFalse(self.group.members.filter(id=self.member.id).exists())

        self.origin.refresh_from_db()
        self.assertTrue(self.origin.detached)
        # Origins in other groups are untouched.
        self.other_origin.refresh_from_db()
        self.assertFalse(self.other_origin.detached)

    def test_non_owner_cannot_remove(self):
        """A member who is not the owner gets 403."""
        self.login("member")
        url = reverse("group_remove_member", args=[self.group.id, self.owner.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 403)

    def test_non_member_cannot_remove(self):
        """A non-member gets 404."""
        self.login("outsider")
        url = reverse("group_remove_member", args=[self.group.id, self.member.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)

    def test_remove_unknown_member(self):
        """Removing a non-member target gets 404."""
        self.login("owner")
        url = reverse("group_remove_member", args=[self.group.id, self.outsider.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)

    def test_owner_cannot_remove_self(self):
        """The owner cannot be removed via this endpoint."""
        self.login("owner")
        url = reverse("group_remove_member", args=[self.group.id, self.owner.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.group.members.filter(id=self.owner.id).exists())

    # --- leave ---

    def test_member_leaves(self):
        """A plain member can leave and their origins detach."""
        self.login("member")
        url = reverse("group_leave", args=[self.group.id])
        response = self.client.post(url)

        self.assertRedirects(response, reverse("group_list"))
        self.assertFalse(self.group.members.filter(id=self.member.id).exists())
        self.origin.refresh_from_db()
        self.assertTrue(self.origin.detached)

    def test_owner_cannot_leave_without_transfer(self):
        """The owner gets 400 and stays in the group."""
        self.login("owner")
        url = reverse("group_leave", args=[self.group.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.group.members.filter(id=self.owner.id).exists())

    def test_non_member_cannot_leave(self):
        """A non-member gets 404."""
        self.login("outsider")
        url = reverse("group_leave", args=[self.group.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)

    # --- transfer ownership ---

    def test_owner_transfers_ownership(self):
        """The owner can hand ownership to another member."""
        self.login("owner")
        url = reverse("group_transfer_owner", args=[self.group.id])
        response = self.client.post(url, {"user_id": self.member.id})

        self.assertRedirects(response, reverse("group_detail", args=[self.group.id]))
        self.group.refresh_from_db()
        self.assertEqual(self.group.owner, self.member)
        self.assertTrue(self.group.members.filter(id=self.owner.id).exists())

    def test_non_owner_cannot_transfer(self):
        """A non-owner gets 403."""
        self.login("member")
        url = reverse("group_transfer_owner", args=[self.group.id])
        response = self.client.post(url, {"user_id": self.owner.id})
        self.assertEqual(response.status_code, 403)

    def test_transfer_to_non_member_rejected(self):
        """Ownership cannot go to someone outside the group."""
        self.login("owner")
        url = reverse("group_transfer_owner", args=[self.group.id])
        response = self.client.post(url, {"user_id": self.outsider.id})
        self.assertEqual(response.status_code, 400)
        self.group.refresh_from_db()
        self.assertEqual(self.group.owner, self.owner)

    def test_transfer_to_self_rejected(self):
        """Ownership cannot be transferred to the current owner."""
        self.login("owner")
        url = reverse("group_transfer_owner", args=[self.group.id])
        response = self.client.post(url, {"user_id": self.owner.id})
        self.assertEqual(response.status_code, 400)

    # --- detail page ---

    def test_group_detail_lists_members_and_marks_owner(self):
        """The detail page lists members and flags the owner."""
        self.login("member")
        url = reverse("group_detail", args=[self.group.id])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "owner")
        self.assertContains(response, "Owner")
        self.assertContains(response, "Leave group")
        self.assertNotContains(response, "Make owner")
