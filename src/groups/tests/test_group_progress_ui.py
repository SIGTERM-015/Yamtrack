"""Per-episode checklist modal, quick actions and on-demand selection."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import Item
from groups.models import Group, GroupItem, GroupMembership
from groups.services import mark_group_episodes_watched

User = get_user_model()


class GroupEpisodesModalTest(TestCase):
    """The per-episode checklist modal for TV group items."""

    def setUp(self):
        """Create a group with a TV GroupItem and some group-watched episodes."""
        patcher = patch("app.models.providers.services.get_media_metadata")
        self.mock_metadata = patcher.start()
        self.mock_metadata.return_value = {
            "max_progress": 1000,
            "related": {"seasons": [{"season_number": 1}]},
            "season/1": {
                "episodes": [
                    {"episode_number": 1, "name": "Pilot"},
                    {"episode_number": 2, "name": "Second"},
                    {"episode_number": 3, "name": "Third"},
                ],
            },
        }
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
        self.client.login(username="alice", password="pw")  # noqa: S106

    def test_modal_lists_seasons_and_marks_watched_episodes(self):
        """Already-watched episodes render checked; others don't."""
        mark_group_episodes_watched(self.group_item, [(1, 1)])

        response = self.client.get(
            reverse("group_episodes_modal", args=[self.group.id, self.tv_item.id]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Season 1")
        self.assertContains(response, "E1")
        self.assertContains(response, "E2")
        self.assertContains(response, "checked disabled")

    def test_batch_episode_checkboxes_mark_multiple_episodes(self):
        """Submitting episodes[] marks several explicit episodes at once."""
        response = self.client.post(
            reverse("group_mark_episodes", args=[self.group.id]),
            {
                "item_id": self.tv_item.id,
                "episodes": ["1-1", "1-3"],
            },
        )

        self.assertRedirects(
            response,
            reverse("group_detail", args=[self.group.id]) + "#in-progress",
            fetch_redirect_response=False,
        )
        self.group_item.refresh_from_db()
        self.assertEqual(self.group_item.progress, 2)
        watched_numbers = set(
            self.group_item.watched_episodes.values_list(
                "item__episode_number", flat=True
            )
        )
        self.assertEqual(watched_numbers, {1, 3})

    def test_non_member_cannot_open_modal(self):
        """A non-member gets 404 from the episodes modal endpoint."""
        User.objects.create_user(username="out", password="pw")  # noqa: S106
        self.client.login(username="out", password="pw")  # noqa: S106
        response = self.client.get(
            reverse("group_episodes_modal", args=[self.group.id, self.tv_item.id]),
        )
        self.assertEqual(response.status_code, 404)


class GroupCardPolishTest(TestCase):
    """Cards show a quick action select; selection and bulk are on demand."""

    def setUp(self):
        """Create a group with one movie GroupItem."""
        self.alice = User.objects.create_user(username="alice", password="pw")  # noqa: S106
        self.group = Group.objects.create(name="G", owner=self.alice)
        GroupMembership.objects.create(group=self.group, user=self.alice)
        self.item = Item.objects.create(
            media_id="m1", title="Movie 1", media_type="movie", source="tmdb"
        )
        GroupItem.objects.create(group=self.group, item=self.item, added_by=self.alice)
        self.client.login(username="alice", password="pw")  # noqa: S106

    def test_no_in_group_search_box(self):
        """Adding happens from the global search / media page, not the group."""
        response = self.client.get(reverse("group_detail", args=[self.group.id]))
        self.assertNotContains(response, "Add media to the group")
        self.assertNotContains(response, "group-search-results")

    def test_card_actions_live_on_the_poster_not_in_a_select(self):
        """Like home cards: hover buttons and a touch button, no select."""
        response = self.client.get(reverse("group_detail", args=[self.group.id]))
        self.assertNotContains(response, "Quick action")
        self.assertContains(
            response, 'aria-label="Update the group\'s status for Movie 1"'
        )
        self.assertContains(response, 'aria-label="More group actions for Movie 1"')
        self.assertContains(response, 'aria-label="Group actions for Movie 1"')
        self.assertContains(response, "Update group status…")

    def test_selection_mode_and_bulk_bar(self):
        """Select enters selection mode; the bar shows with one selected."""
        response = self.client.get(reverse("group_detail", args=[self.group.id]))
        self.assertContains(response, "group-selection-toggle")
        self.assertContains(response, 'x-show="selected.length > 0"')
        self.assertContains(response, 'aria-label="Select Movie 1"')
        self.assertContains(response, 'name="participants"', count=2)

    def test_manage_modal_keeps_full_controls(self):
        """The card's actions keep participants, discard and removal."""
        response = self.client.get(reverse("group_detail", args=[self.group.id]))
        self.assertContains(response, "Participants")
        self.assertContains(response, "Apply to group")
        self.assertContains(response, "Just me: match group status")
        self.assertContains(response, "Remove from group")
        self.assertContains(
            response, reverse("group_discard_item", args=[self.group.id])
        )

    def test_tv_card_quick_action_marks_episodes(self):
        """A TV item's quick action opens the episode checklist."""
        tv_item = Item.objects.create(
            media_id="t1", title="Show 1", media_type="tv", source="tmdb"
        )
        GroupItem.objects.create(group=self.group, item=tv_item, added_by=self.alice)

        response = self.client.get(reverse("group_detail", args=[self.group.id]))

        self.assertContains(response, "Mark episodes…")
        self.assertContains(
            response, 'aria-label="Mark episodes of Show 1 for the group"'
        )
        self.assertContains(response, 'hx-trigger="load-episodes once"')

    def test_status_change_returns_to_its_section(self):
        """Actions posted from a section come back to that section."""
        with patch("app.models.providers.services.get_media_metadata") as meta:
            meta.return_value = {"max_progress": 1}
            response = self.client.post(
                reverse("group_set_item_status", args=[self.group.id]),
                {
                    "item_id": self.item.id,
                    "status": "In progress",
                    "section": "Planning",
                },
            )
        self.assertRedirects(
            response,
            reverse("group_detail", args=[self.group.id]) + "#planning",
            fetch_redirect_response=False,
        )

    def test_add_item_redirects_back_to_group(self):
        """The generic Add to group modal posts and returns to the group."""
        item2 = Item.objects.create(
            media_id="m2", title="Movie 2", media_type="movie", source="tmdb"
        )
        with patch("app.models.providers.services.get_media_metadata") as meta:
            meta.return_value = {"max_progress": 1}
            response = self.client.post(
                reverse("group_item_add", args=[self.group.id]),
                {"item_id": item2.id},
            )
        self.assertRedirects(response, reverse("group_detail", args=[self.group.id]))
        self.assertTrue(GroupItem.objects.filter(group=self.group, item=item2).exists())


class GroupMemberLinksTest(TestCase):
    """Member names and avatars open their public profiles."""

    def test_members_link_to_profiles(self):
        """Header avatars and the settings member list link to /<username>."""
        alice = User.objects.create_user(username="alice", password="pw")  # noqa: S106
        bob = User.objects.create_user(username="bob", password="pw")  # noqa: S106
        group = Group.objects.create(name="G", owner=alice)
        GroupMembership.objects.create(group=group, user=alice)
        GroupMembership.objects.create(group=group, user=bob)
        self.client.login(username="alice", password="pw")  # noqa: S106

        overview = self.client.get(reverse("group_detail", args=[group.id]))
        settings_tab = self.client.get(reverse("group_settings", args=[group.id]))

        self.assertContains(overview, f'href="{reverse("profile", args=["bob"])}"')
        self.assertContains(settings_tab, f'href="{reverse("profile", args=["bob"])}"')
