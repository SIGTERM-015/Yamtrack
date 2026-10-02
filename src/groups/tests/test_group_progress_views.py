"""S5: views for tabs, participant selection, episode marking and removal."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import TV, Item, Movie, Status
from groups.models import Group, GroupItem, GroupMembership

User = get_user_model()


class GroupSetStatusParticipantsTest(TestCase):
    """POST participants[] controls who receives the monotone merge."""

    def setUp(self):
        """Create a group with two members and one movie GroupItem."""
        patcher = patch("app.models.providers.services.get_media_metadata")
        self.mock_metadata = patcher.start()
        self.mock_metadata.return_value = {"max_progress": 1000}
        self.addCleanup(patcher.stop)

        self.alice = User.objects.create_user(username="alice", password="pw")  # noqa: S106
        self.bob = User.objects.create_user(username="bob", password="pw")  # noqa: S106
        self.outsider = User.objects.create_user(username="out", password="pw")  # noqa: S106
        self.group = Group.objects.create(name="G", owner=self.alice)
        GroupMembership.objects.create(group=self.group, user=self.alice)
        GroupMembership.objects.create(group=self.group, user=self.bob)

        self.item = Item.objects.create(
            media_id="m1", title="Movie 1", media_type="movie", source="tmdb"
        )
        self.group_item = GroupItem.objects.create(
            group=self.group, item=self.item, added_by=self.alice
        )
        self.url = reverse("group_set_item_status", args=[self.group.id])

    def test_missing_participants_updates_every_member(self):
        """No participants[] field propagates to every member."""
        self.client.login(username="alice", password="pw")  # noqa: S106
        self.client.post(self.url, {"item_id": self.item.id, "status": "Completed"})

        for user in (self.alice, self.bob):
            self.assertTrue(Movie.objects.filter(user=user, item=self.item).exists())

    def test_participants_alice_only_updates_alice(self):
        """participants=[alice] leaves bob untouched."""
        self.client.login(username="alice", password="pw")  # noqa: S106
        self.client.post(
            self.url,
            {
                "item_id": self.item.id,
                "status": "Completed",
                "participants": [self.alice.id],
            },
        )

        self.assertTrue(Movie.objects.filter(user=self.alice, item=self.item).exists())
        self.assertFalse(Movie.objects.filter(user=self.bob, item=self.item).exists())

    def test_non_member_participant_returns_404_and_writes_nothing(self):
        """An id that isn't a member aborts with 404, no writes at all."""
        self.client.login(username="alice", password="pw")  # noqa: S106
        response = self.client.post(
            self.url,
            {
                "item_id": self.item.id,
                "status": "Completed",
                "participants": [self.outsider.id],
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Movie.objects.filter(item=self.item).exists())
        self.group_item.refresh_from_db()
        self.assertEqual(self.group_item.status, Status.PLANNING.value)

    def test_progress_field_sets_group_progress_before_propagating(self):
        """An explicit progress value is written to the GroupItem and merged."""
        self.client.login(username="alice", password="pw")  # noqa: S106
        self.client.post(
            self.url,
            {"item_id": self.item.id, "status": "In progress", "progress": "5"},
        )

        self.group_item.refresh_from_db()
        self.assertEqual(self.group_item.progress, 5)
        self.assertEqual(Movie.objects.get(user=self.alice, item=self.item).progress, 5)


class GroupDetailTabsTest(TestCase):
    """group_detail renders tabs classified by the group's own status."""

    def setUp(self):
        """Create a group with items in different group-owned statuses."""
        self.alice = User.objects.create_user(username="alice", password="pw")  # noqa: S106
        self.group = Group.objects.create(name="G", owner=self.alice)
        GroupMembership.objects.create(group=self.group, user=self.alice)

        self.pending_item = Item.objects.create(
            media_id="m1", title="Pending Movie", media_type="movie", source="tmdb"
        )
        GroupItem.objects.create(
            group=self.group, item=self.pending_item, added_by=self.alice
        )

        self.watching_item = Item.objects.create(
            media_id="m2", title="Watching Movie", media_type="movie", source="tmdb"
        )
        GroupItem.objects.create(
            group=self.group,
            item=self.watching_item,
            added_by=self.alice,
            status=Status.IN_PROGRESS.value,
        )

        self.other_item = Item.objects.create(
            media_id="m3", title="Paused Movie", media_type="movie", source="tmdb"
        )
        GroupItem.objects.create(
            group=self.group,
            item=self.other_item,
            added_by=self.alice,
            status=Status.PAUSED.value,
        )

        self.client.login(username="alice", password="pw")  # noqa: S106

    def test_default_tab_is_pending(self):
        """Without ?tab=, the Planning tab is shown."""
        response = self.client.get(reverse("group_detail", args=[self.group.id]))
        self.assertContains(response, "Pending Movie")
        self.assertNotContains(response, "Watching Movie")

    def test_watching_tab_shows_in_progress_items(self):
        """?tab=watching shows only In progress group items."""
        response = self.client.get(
            reverse("group_detail", args=[self.group.id]), {"tab": "watching"}
        )
        self.assertContains(response, "Watching Movie")
        self.assertNotContains(response, "Pending Movie")

    def test_others_tab_shows_paused_and_dropped(self):
        """?tab=others shows the group's Paused/Dropped items."""
        response = self.client.get(
            reverse("group_detail", args=[self.group.id]), {"tab": "others"}
        )
        self.assertContains(response, "Paused Movie")

    def test_stats_page_renders_without_error(self):
        """The group stats page renders the comparison/genre sections."""
        response = self.client.get(reverse("group_stats", args=[self.group.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ratings comparison")

    def test_settings_page_shows_members(self):
        """The group settings page shows the members/invite/leave block."""
        response = self.client.get(reverse("group_settings", args=[self.group.id]))
        self.assertContains(response, "alice")

    def test_legacy_tabs_redirect_to_their_pages(self):
        """Old ?tab=settings / ?tab=stats links land on the new pages."""
        url = reverse("group_detail", args=[self.group.id])
        self.assertRedirects(
            self.client.get(url, {"tab": "settings"}),
            reverse("group_settings", args=[self.group.id]),
        )
        self.assertRedirects(
            self.client.get(url, {"tab": "stats"}),
            reverse("group_stats", args=[self.group.id]),
        )

    def test_tabs_are_only_statuses(self):
        """The tab row holds the four status tabs plus a Group settings button."""
        response = self.client.get(reverse("group_detail", args=[self.group.id]))
        self.assertNotContains(response, 'href="?tab=stats"')
        self.assertNotContains(response, 'href="?tab=settings"')
        self.assertContains(response, "Group settings")


class GroupItemRemoveTest(TestCase):
    """Removing an item drops the GroupItem but keeps personal records."""

    def setUp(self):
        """Create a group with one item tracked personally by a member."""
        # Movie.save() -> process_status() fetches metadata when status is
        # Completed; mock it so this never hits the real TMDB API.
        patcher = patch("app.models.providers.services.get_media_metadata")
        self.mock_metadata = patcher.start()
        self.mock_metadata.return_value = {"max_progress": 1000}
        self.addCleanup(patcher.stop)

        self.alice = User.objects.create_user(username="alice", password="pw")  # noqa: S106
        self.bob = User.objects.create_user(username="bob", password="pw")  # noqa: S106
        self.outsider = User.objects.create_user(username="out", password="pw")  # noqa: S106
        self.group = Group.objects.create(name="G", owner=self.alice)
        GroupMembership.objects.create(group=self.group, user=self.alice)
        GroupMembership.objects.create(group=self.group, user=self.bob)

        self.item = Item.objects.create(
            media_id="m1", title="Movie 1", media_type="movie", source="tmdb"
        )
        self.group_item = GroupItem.objects.create(
            group=self.group, item=self.item, added_by=self.alice
        )
        Movie.objects.create(user=self.bob, item=self.item, status=Status.COMPLETED)

    def test_any_member_can_remove_and_personal_records_survive(self):
        """Bob (not the adder) can remove it; his personal record stays."""
        self.client.login(username="bob", password="pw")  # noqa: S106
        url = reverse("group_item_remove", args=[self.group.id, self.item.id])

        response = self.client.post(url)

        self.assertRedirects(response, reverse("group_detail", args=[self.group.id]))
        self.assertFalse(GroupItem.objects.filter(pk=self.group_item.pk).exists())
        self.assertTrue(Movie.objects.filter(user=self.bob, item=self.item).exists())

    def test_non_member_cannot_remove(self):
        """A non-member gets 404 and the GroupItem survives."""
        self.client.login(username="out", password="pw")  # noqa: S106
        url = reverse("group_item_remove", args=[self.group.id, self.item.id])

        response = self.client.post(url)

        self.assertEqual(response.status_code, 404)
        self.assertTrue(GroupItem.objects.filter(pk=self.group_item.pk).exists())


class GroupMarkEpisodesViewTest(TestCase):
    """The episode-marking endpoint and its 'up to episode N' shortcut."""

    def setUp(self):
        """Create a group with one TV GroupItem."""
        patcher = patch("app.models.providers.services.get_media_metadata")
        self.mock_metadata = patcher.start()
        self.mock_metadata.return_value = {"max_progress": 1000}
        self.addCleanup(patcher.stop)

        self.alice = User.objects.create_user(username="alice", password="pw")  # noqa: S106
        self.bob = User.objects.create_user(username="bob", password="pw")  # noqa: S106
        self.group = Group.objects.create(name="G", owner=self.alice)
        GroupMembership.objects.create(group=self.group, user=self.alice)
        GroupMembership.objects.create(group=self.group, user=self.bob)

        self.tv_item = Item.objects.create(
            media_id="t1", title="Show 1", media_type="tv", source="tmdb"
        )
        self.group_item = GroupItem.objects.create(
            group=self.group, item=self.tv_item, added_by=self.alice
        )
        self.url = reverse("group_mark_episodes", args=[self.group.id])

    def test_up_to_episode_marks_a_contiguous_range(self):
        """mode=up_to marks episodes 1..N for every member."""
        self.client.login(username="alice", password="pw")  # noqa: S106
        self.client.post(
            self.url,
            {
                "item_id": self.tv_item.id,
                "season_number": 1,
                "episode_number": 3,
                "mode": "up_to",
            },
        )

        self.group_item.refresh_from_db()
        self.assertEqual(self.group_item.progress, 3)
        for user in (self.alice, self.bob):
            self.assertTrue(TV.objects.filter(user=user, item=self.tv_item).exists())

    def test_non_member_participant_returns_404_and_writes_nothing(self):
        """An id that isn't a member aborts with 404 before writing anything."""
        outsider = User.objects.create_user(username="out", password="pw")  # noqa: S106
        self.client.login(username="alice", password="pw")  # noqa: S106

        response = self.client.post(
            self.url,
            {
                "item_id": self.tv_item.id,
                "season_number": 1,
                "episode_number": 1,
                "participants": [outsider.id],
            },
        )

        self.assertEqual(response.status_code, 404)
        self.group_item.refresh_from_db()
        self.assertEqual(self.group_item.progress, 0)


class GroupBulkSetStatusTest(TestCase):
    """Bulk status action never overwrites/reduces personal progress (E4.2)."""

    def setUp(self):
        """Create a group with two movies, one where a member is ahead."""
        patcher = patch("app.models.providers.services.get_media_metadata")
        self.mock_metadata = patcher.start()
        self.mock_metadata.return_value = {"max_progress": 1000}
        self.addCleanup(patcher.stop)

        self.alice = User.objects.create_user(username="alice", password="pw")  # noqa: S106
        self.bob = User.objects.create_user(username="bob", password="pw")  # noqa: S106
        self.group = Group.objects.create(name="G", owner=self.alice)
        GroupMembership.objects.create(group=self.group, user=self.alice)
        GroupMembership.objects.create(group=self.group, user=self.bob)

        self.item1 = Item.objects.create(
            media_id="m1", title="Movie 1", media_type="movie", source="tmdb"
        )
        self.item2 = Item.objects.create(
            media_id="m2", title="Movie 2", media_type="movie", source="tmdb"
        )
        self.group_item1 = GroupItem.objects.create(
            group=self.group, item=self.item1, added_by=self.alice, progress=1
        )
        self.group_item2 = GroupItem.objects.create(
            group=self.group, item=self.item2, added_by=self.alice, progress=1
        )
        Movie.objects.create(
            user=self.bob, item=self.item1, status=Status.COMPLETED, progress=1
        )
        self.url = reverse("group_bulk_set_status", args=[self.group.id])

    def test_bulk_completes_both_without_reopening_bob(self):
        """Bulk-completing both items doesn't reopen Bob's completed movie."""
        self.client.login(username="alice", password="pw")  # noqa: S106
        self.client.post(
            self.url,
            {
                "item_ids": [self.item1.id, self.item2.id],
                "status": "Completed",
            },
        )

        bob_entry = Movie.objects.get(user=self.bob, item=self.item1)
        self.assertEqual(bob_entry.status, Status.COMPLETED.value)

        for user in (self.alice, self.bob):
            self.assertEqual(
                Movie.objects.get(user=user, item=self.item2).status,
                Status.COMPLETED.value,
            )
