from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import Item, Movie, Status
from groups.models import Group, GroupItem, GroupMembership
from groups.services import add_item_to_group, apply_status_to_group_members

User = get_user_model()


class GroupBulkStatusTest(TestCase):
    """Group -> individual bulk status actions must never overwrite data."""

    def setUp(self):
        """Create a group with three members and one movie."""
        self.owner = User.objects.create(username="owner")
        self.alice = User.objects.create(username="alice")
        self.bob = User.objects.create(username="bob")
        self.group = Group.objects.create(name="Watch Club", owner=self.owner)
        for user in (self.owner, self.alice, self.bob):
            GroupMembership.objects.create(group=self.group, user=user)

        self.movie = Item.objects.create(
            media_id="m1", title="Movie 1", media_type="movie", source="tmdb"
        )
        GroupItem.objects.create(group=self.group, item=self.movie, added_by=self.owner)

    def test_status_applied_only_to_members_without_it(self):
        """Members missing the status get it; a different status is updated."""
        Movie.objects.bulk_create(
            [
                Movie(
                    user=self.alice,
                    item=self.movie,
                    status=Status.PLANNING,
                    notes="keep me",
                ),
            ]
        )

        summary = apply_status_to_group_members(
            self.group, self.movie, Status.COMPLETED
        )

        self.assertEqual(summary, {"created": 2, "updated": 1, "skipped": 0})

        alice = Movie.objects.get(user=self.alice, item=self.movie)
        self.assertEqual(alice.status, Status.COMPLETED.value)
        self.assertEqual(alice.notes, "keep me")

        for user in (self.owner, self.bob):
            entry = Movie.objects.get(user=user, item=self.movie)
            self.assertEqual(entry.status, Status.COMPLETED.value)

    def test_existing_completed_entry_is_left_intact(self):
        """A member who watched it months ago keeps date and note untouched."""
        watched_at = timezone.now() - timedelta(days=90)
        Movie.objects.bulk_create(
            [
                Movie(
                    user=self.owner,
                    item=self.movie,
                    status=Status.COMPLETED,
                    start_date=watched_at,
                    end_date=watched_at,
                    notes="saw it in the cinema",
                    score=9,
                ),
            ]
        )

        summary = apply_status_to_group_members(
            self.group, self.movie, Status.COMPLETED
        )

        self.assertEqual(summary, {"created": 2, "updated": 0, "skipped": 1})

        owner_entry = Movie.objects.get(user=self.owner, item=self.movie)
        self.assertEqual(owner_entry.end_date, watched_at)
        self.assertEqual(owner_entry.start_date, watched_at)
        self.assertEqual(owner_entry.notes, "saw it in the cinema")
        self.assertEqual(owner_entry.score, 9)
        self.assertEqual(
            Movie.objects.filter(user=self.owner, item=self.movie).count(), 1
        )

    def test_bulk_action_is_idempotent(self):
        """Running the action twice does not duplicate or change entries."""
        first = apply_status_to_group_members(self.group, self.movie, Status.PLANNING)
        self.assertEqual(first, {"created": 3, "updated": 0, "skipped": 0})

        second = apply_status_to_group_members(self.group, self.movie, Status.PLANNING)
        self.assertEqual(second, {"created": 0, "updated": 0, "skipped": 3})

        self.assertEqual(
            Movie.objects.filter(item=self.movie).count(),
            self.group.members.count(),
        )

    def test_add_item_to_group_sets_to_watch_for_all_members(self):
        """Adding a title to the group also adds it to every member's to watch."""
        item = Item.objects.create(
            media_id="m2", title="Movie 2", media_type="movie", source="tmdb"
        )

        group_item, summary = add_item_to_group(self.group, item, self.owner)

        self.assertEqual(group_item.item, item)
        self.assertEqual(summary, {"created": 3, "updated": 0, "skipped": 0})
        for user in (self.owner, self.alice, self.bob):
            entry = Movie.objects.get(user=user, item=item)
            self.assertEqual(entry.status, Status.PLANNING.value)

        add_item_to_group(self.group, item, self.owner)
        self.assertEqual(
            GroupItem.objects.filter(group=self.group, item=item).count(), 1
        )
        self.assertEqual(
            Movie.objects.filter(item=item).count(), self.group.members.count()
        )

    def test_view_applies_status_to_group_members(self):
        """Posting the bulk form updates every member's status."""
        self.client.force_login(self.owner)
        url = reverse("group_set_item_status", args=[self.group.id])

        response = self.client.post(
            url, {"item_id": self.movie.id, "status": Status.COMPLETED.value}
        )

        self.assertRedirects(response, reverse("group_detail", args=[self.group.id]))
        for user in (self.owner, self.alice, self.bob):
            entry = Movie.objects.get(user=user, item=self.movie)
            self.assertEqual(entry.status, Status.COMPLETED.value)

    def test_scope_mine_only_updates_requesting_user(self):
        """scope=mine touches the poster's entry and no one else's."""
        self.client.force_login(self.alice)
        url = reverse("group_set_item_status", args=[self.group.id])

        response = self.client.post(
            url,
            {
                "item_id": self.movie.id,
                "status": Status.COMPLETED.value,
                "scope": "mine",
            },
        )

        self.assertRedirects(response, reverse("group_detail", args=[self.group.id]))
        self.assertEqual(
            Movie.objects.get(user=self.alice, item=self.movie).status,
            Status.COMPLETED.value,
        )
        self.assertFalse(
            Movie.objects.filter(item=self.movie).exclude(user=self.alice).exists()
        )

    def test_scope_mine_replaces_existing_status(self):
        """An explicit individual action overwrites the user's own status."""
        Movie.objects.bulk_create(
            [
                Movie(
                    user=self.alice,
                    item=self.movie,
                    status=Status.PLANNING,
                    notes="keep me",
                ),
            ]
        )
        self.client.force_login(self.alice)
        url = reverse("group_set_item_status", args=[self.group.id])

        self.client.post(
            url,
            {
                "item_id": self.movie.id,
                "status": Status.COMPLETED.value,
                "scope": "mine",
            },
        )

        entry = Movie.objects.get(user=self.alice, item=self.movie)
        self.assertEqual(entry.status, Status.COMPLETED.value)
        self.assertEqual(entry.notes, "keep me")
        self.assertEqual(Movie.objects.filter(item=self.movie).count(), 1)

    def test_scope_group_updates_every_member(self):
        """scope=group applies the status to all members."""
        self.client.force_login(self.owner)
        url = reverse("group_set_item_status", args=[self.group.id])

        response = self.client.post(
            url,
            {
                "item_id": self.movie.id,
                "status": Status.COMPLETED.value,
                "scope": "group",
            },
        )

        self.assertRedirects(response, reverse("group_detail", args=[self.group.id]))
        for user in (self.owner, self.alice, self.bob):
            entry = Movie.objects.get(user=user, item=self.movie)
            self.assertEqual(entry.status, Status.COMPLETED.value)

    def test_invalid_scope_is_rejected(self):
        """An unknown scope aborts the action without touching any entry."""
        self.client.force_login(self.owner)
        url = reverse("group_set_item_status", args=[self.group.id])

        response = self.client.post(
            url,
            {
                "item_id": self.movie.id,
                "status": Status.COMPLETED.value,
                "scope": "everyone",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Movie.objects.filter(item=self.movie).exists())

    def test_view_rejects_non_member(self):
        """A user outside the group cannot trigger the bulk action."""
        outsider = User.objects.create(username="outsider")
        self.client.force_login(outsider)
        url = reverse("group_set_item_status", args=[self.group.id])

        response = self.client.post(
            url, {"item_id": self.movie.id, "status": Status.COMPLETED.value}
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Movie.objects.filter(item=self.movie).exists())
