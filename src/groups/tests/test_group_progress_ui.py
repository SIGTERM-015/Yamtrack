"""Inline in-group search, per-episode checklist modal, and card polish."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import Item, MediaTypes, Sources
from groups.models import Group, GroupItem, GroupMembership
from groups.services import mark_group_episodes_watched

User = get_user_model()


class GroupInlineSearchTest(TestCase):
    """The Planning tab's search box adds results to the group inline (HTMX)."""

    def setUp(self):
        """Create a group and log in as a member."""
        patcher = patch("app.models.providers.services.get_media_metadata")
        self.mock_metadata = patcher.start()
        self.mock_metadata.return_value = {
            "title": "Test Movie",
            "image": "http://example.com/image.jpg",
            "max_progress": 1000,
        }
        self.addCleanup(patcher.stop)

        self.alice = User.objects.create_user(username="alice", password="pw")  # noqa: S106
        self.bob = User.objects.create_user(username="bob", password="pw")  # noqa: S106
        self.group = Group.objects.create(name="G", owner=self.alice)
        GroupMembership.objects.create(group=self.group, user=self.alice)
        GroupMembership.objects.create(group=self.group, user=self.bob)
        self.client.login(username="alice", password="pw")  # noqa: S106

    @patch("app.providers.services.search")
    def test_search_results_show_add_button(self, mock_search):
        """A query returns a poster result with an Add button."""
        mock_search.return_value = {
            "results": [
                {
                    "media_id": "238",
                    "title": "Test Movie",
                    "media_type": MediaTypes.MOVIE.value,
                    "source": Sources.TMDB.value,
                    "image": "http://example.com/image.jpg",
                },
            ],
        }

        response = self.client.get(
            reverse("group_search_results", args=[self.group.id]),
            {"q": "test", "media_type": "movie"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Movie")
        self.assertContains(response, "hx-post")
        # The Item is pre-created so the Add button can post item_id directly.
        self.assertTrue(Item.objects.filter(media_id="238", source="tmdb").exists())

    @patch("app.providers.services.search")
    def test_add_button_posts_item_id_of_precreated_item(self, mock_search):
        """Clicking Add (HTMX POST) adds the item and refreshes the grid inline."""
        mock_search.return_value = {
            "results": [
                {
                    "media_id": "238",
                    "title": "Test Movie",
                    "media_type": MediaTypes.MOVIE.value,
                    "source": Sources.TMDB.value,
                    "image": "http://example.com/image.jpg",
                },
            ],
        }
        self.client.get(
            reverse("group_search_results", args=[self.group.id]),
            {"q": "test", "media_type": "movie"},
        )
        item = Item.objects.get(media_id="238", source="tmdb")

        response = self.client.post(
            reverse("group_item_add", args=[self.group.id]),
            {"item_id": item.id},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Movie")
        self.assertTrue(
            GroupItem.objects.filter(group=self.group, item=item).exists(),
        )
        # Only the grid is swapped; the search box and its results stay, and
        # the clicked button becomes an "Added" badge plus a fresh tab count.
        self.assertContains(response, 'id="group-item-grid"')
        self.assertNotContains(response, 'id="group-search-results"')
        self.assertContains(response, f'id="group-add-{item.id}"')
        self.assertContains(response, 'id="tab-count-pending" hx-swap-oob="true"')
        # Non-HTMX callers (e.g. the generic "Add to group" modal) still redirect.
        item2 = Item.objects.create(
            media_id="239", title="Other Movie", media_type="movie", source="tmdb"
        )
        redirect_response = self.client.post(
            reverse("group_item_add", args=[self.group.id]),
            {"item_id": item2.id},
        )
        self.assertRedirects(
            redirect_response, reverse("group_detail", args=[self.group.id])
        )

    @patch("app.providers.services.search")
    def test_already_added_result_shows_added_badge(self, mock_search):
        """A result already in the group shows 'Added' instead of a button."""
        mock_search.return_value = {
            "results": [
                {
                    "media_id": "238",
                    "title": "Test Movie",
                    "media_type": MediaTypes.MOVIE.value,
                    "source": Sources.TMDB.value,
                    "image": "http://example.com/image.jpg",
                },
            ],
        }
        item = Item.objects.create(
            media_id="238", title="Test Movie", media_type="movie", source="tmdb"
        )
        GroupItem.objects.create(group=self.group, item=item, added_by=self.alice)

        response = self.client.get(
            reverse("group_search_results", args=[self.group.id]),
            {"q": "test", "media_type": "movie"},
        )

        self.assertContains(response, "Added")

    def test_non_member_cannot_search(self):
        """A non-member gets 404 from the inline search endpoint."""
        User.objects.create_user(username="out", password="pw")  # noqa: S106
        self.client.login(username="out", password="pw")  # noqa: S106
        response = self.client.get(
            reverse("group_search_results", args=[self.group.id]),
            {"q": "test", "media_type": "movie"},
        )
        self.assertEqual(response.status_code, 404)


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
            reverse("group_detail", args=[self.group.id]) + "?tab=watching",
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
    """The item card shows a single Manage button; actions live in a modal."""

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

    def test_card_shows_manage_button_and_no_raw_action_links(self):
        """The card exposes one Manage button; old inline text-links are gone."""
        response = self.client.get(reverse("group_detail", args=[self.group.id]))

        self.assertContains(response, "Manage")
        self.assertContains(response, "Remove from group")
        self.assertContains(response, "Just me: match group status")
        # The old cramped underlined text-link markup is gone.
        self.assertNotContains(response, "text-red-400 hover:text-red-300 underline")

    def test_manage_modal_has_participants_and_status_form(self):
        """The Manage modal contains the status form with participants."""
        response = self.client.get(reverse("group_detail", args=[self.group.id]))
        self.assertContains(response, "Participants")
        self.assertContains(response, "Apply to group")

    def test_tv_card_has_episodes_button(self):
        """A TV item's Manage modal exposes an Episodes button."""
        tv_item = Item.objects.create(
            media_id="t1", title="Show 1", media_type="tv", source="tmdb"
        )
        GroupItem.objects.create(group=self.group, item=tv_item, added_by=self.alice)

        response = self.client.get(reverse("group_detail", args=[self.group.id]))

        self.assertContains(response, "Episodes")
