"""View tests for the roulette screen (E7.3): personal and group scope."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.discards import discard_item
from app.models import Item, MediaTypes, Movie, Sources, Status
from groups.discards import discard_group_item
from groups.models import Group, GroupItem


class RoulettePersonalTests(TestCase):
    """Personal-scope roulette: draws from the user's own Planning items."""

    def setUp(self):
        """Create a user with two pending movies."""
        patcher = patch("app.models.providers.services.get_media_metadata")
        self.mock_get_media_metadata = patcher.start()
        self.mock_get_media_metadata.return_value = {"max_progress": 0}
        self.addCleanup(patcher.stop)

        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="user1",
            password="testpassword123",  # noqa: S106
        )
        self.client.login(username="user1", password="testpassword123")  # noqa: S106

        self.item1 = Item.objects.create(
            title="Movie One",
            media_id="1",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
        )
        self.item2 = Item.objects.create(
            title="Movie Two",
            media_id="2",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
        )
        Movie.objects.create(
            user=self.user, item=self.item1, status=Status.PLANNING.value
        )
        Movie.objects.create(
            user=self.user, item=self.item2, status=Status.PLANNING.value
        )

    def test_requires_login(self):
        """Anonymous visitors are sent to the login page."""
        self.client.logout()
        response = self.client.get(reverse("roulette"))
        self.assertEqual(response.status_code, 302)

    def test_draw_proposes_without_mutating(self):
        """Drawing returns a title but never changes its status."""
        response = self.client.get(reverse("roulette"), {"draw": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["result"])
        self.assertEqual(
            Movie.objects.get(item=response.context["result"]).status,
            Status.PLANNING.value,
        )

    def test_draw_excludes_personally_discarded_items(self):
        """A personally discarded item never comes up."""
        discard_item(self.user, self.item1)
        for _ in range(15):
            response = self.client.get(reverse("roulette"), {"draw": "1"})
            result = response.context["result"]
            if result is not None:
                self.assertEqual(result.id, self.item2.id)

    def test_empty_state_when_pool_is_exhausted_by_filters(self):
        """A genre with no match shows an explanation, not a raw empty page."""
        response = self.client.get(
            reverse("roulette"), {"draw": "1", "genres": "Nonexistent"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["result"])
        self.assertTrue(response.context["empty_reason"])

    def test_start_moves_item_to_in_progress(self):
        """Confirming Start advances the user's own record."""
        response = self.client.post(
            reverse("roulette_start"), {"item_id": self.item1.id}
        )
        self.assertEqual(response.status_code, 302)
        movie = Movie.objects.get(user=self.user, item=self.item1)
        self.assertEqual(movie.status, Status.IN_PROGRESS.value)

    def test_not_interested_discards_and_excludes_from_future_draws(self):
        """Discarding from the roulette excludes the item from future draws."""
        response = self.client.post(reverse("discard_item"), {"item_id": self.item1.id})
        self.assertEqual(response.status_code, 302)
        for _ in range(15):
            response = self.client.get(reverse("roulette"), {"draw": "1"})
            result = response.context["result"]
            if result is not None:
                self.assertEqual(result.id, self.item2.id)


class RouletteGroupTests(TestCase):
    """Group-scope roulette: draws from the group's own pending items."""

    def setUp(self):
        """Create a group of two members, an outsider, and a pending item."""
        patcher = patch("app.models.providers.services.get_media_metadata")
        self.mock_get_media_metadata = patcher.start()
        self.mock_get_media_metadata.return_value = {"max_progress": 0}
        self.addCleanup(patcher.stop)

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
            title="Group Movie",
            media_id="1",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
        )
        self.group_item = GroupItem.objects.create(
            group=self.group, item=self.item, added_by=self.owner
        )

    def test_only_members_can_access_group_roulette(self):
        """A non-member gets a 404, not the group's pool."""
        self.client.login(username="outsider", password="testpassword123")  # noqa: S106
        response = self.client.get(
            reverse("roulette"), {"group": self.group.id, "draw": "1"}
        )
        self.assertEqual(response.status_code, 404)

    def test_member_draws_from_the_group_pool(self):
        """A member draws the group's own pending item."""
        self.client.login(username="member", password="testpassword123")  # noqa: S106
        response = self.client.get(
            reverse("roulette"), {"group": self.group.id, "draw": "1"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["result"].id, self.item.id)

    def test_group_discard_excludes_item_from_group_draws(self):
        """A group discard removes the item from the group's own draws."""
        discard_group_item(self.group, self.item, self.owner)
        self.client.login(username="member", password="testpassword123")  # noqa: S106
        response = self.client.get(
            reverse("roulette"), {"group": self.group.id, "draw": "1"}
        )
        self.assertIsNone(response.context["result"])

    def test_start_uses_the_group_service_and_advances_all_members(self):
        """Start marks the GroupItem In progress and propagates to members."""
        self.client.login(username="member", password="testpassword123")  # noqa: S106
        response = self.client.post(
            reverse("roulette_start"),
            {"item_id": self.item.id, "group_id": self.group.id},
        )
        self.assertEqual(response.status_code, 302)
        self.group_item.refresh_from_db()
        self.assertEqual(self.group_item.status, Status.IN_PROGRESS.value)
        self.assertTrue(
            Movie.objects.filter(
                user=self.owner, item=self.item, status=Status.IN_PROGRESS.value
            ).exists()
        )
        self.assertTrue(
            Movie.objects.filter(
                user=self.member, item=self.item, status=Status.IN_PROGRESS.value
            ).exists()
        )

    def test_start_requires_membership(self):
        """A non-member cannot start a group item via the roulette."""
        self.client.login(username="outsider", password="testpassword123")  # noqa: S106
        response = self.client.post(
            reverse("roulette_start"),
            {"item_id": self.item.id, "group_id": self.group.id},
        )
        self.assertEqual(response.status_code, 404)
