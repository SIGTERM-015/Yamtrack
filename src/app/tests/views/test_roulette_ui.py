"""View tests for the roulette screen (E7.3): personal and group scope."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.discards import discard_item
from app.models import Game, Item, MediaTypes, Movie, Sources, Status
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


class RouletteFilterTests(TestCase):
    """Genre, duration and game-mode filters on the personal roulette."""

    def setUp(self):
        """Create a user with genre-tagged movies and a game."""
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

        self.action_movie = Item.objects.create(
            title="Action Movie",
            media_id="1",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
        )
        self.action_movie.set_genres(["Action"])
        self.drama_movie = Item.objects.create(
            title="Drama Movie",
            media_id="2",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
        )
        self.drama_movie.set_genres(["Drama"])
        self.unknown_genre_movie = Item.objects.create(
            title="No Genre Movie",
            media_id="3",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
        )
        for item in (self.action_movie, self.drama_movie, self.unknown_genre_movie):
            Movie.objects.create(
                user=self.user, item=item, status=Status.PLANNING.value
            )

    def test_genre_rows_report_counts_and_unknown(self):
        """Each genre chip reports how many candidates share it."""
        response = self.client.get(
            reverse("roulette"), {"media_type": MediaTypes.MOVIE.value}
        )
        rows = {row["name"]: row["count"] for row in response.context["genre_rows"]}
        self.assertEqual(rows, {"Action": 1, "Drama": 1})
        self.assertEqual(response.context["unknown_genre_count"], 1)

    def test_strict_filter_hides_titles_with_unknown_genres(self):
        """Strict mode drops titles without any genre from the pool."""
        response = self.client.get(
            reverse("roulette"),
            {"media_type": MediaTypes.MOVIE.value, "strict": "1"},
        )
        self.assertEqual(response.context["pool_count"], 2)

    def test_genre_filter_keeps_only_matching_titles(self):
        """Selecting a genre keeps only titles carrying it."""
        response = self.client.get(
            reverse("roulette"),
            {"media_type": MediaTypes.MOVIE.value, "genres": "Action"},
        )
        self.assertEqual(response.context["pool_count"], 1)

    def test_duration_buckets_and_unknown_runtime(self):
        """Duration buckets classify movies by runtime and count unknowns."""
        with patch("app.roulette_views.services.get_media_metadata") as mock_meta:

            def _metadata(_media_type, media_id, _source):
                runtimes = {"1": "1h 15m", "2": "2h 5m"}
                return {"details": {"runtime": runtimes.get(media_id)}}

            mock_meta.side_effect = _metadata
            response = self.client.get(
                reverse("roulette"), {"media_type": MediaTypes.MOVIE.value}
            )

        buckets = {
            row["key"]: row["count"] for row in response.context["duration_rows"]
        }
        self.assertEqual(buckets["short"], 1)
        self.assertEqual(buckets["long"], 1)
        self.assertEqual(response.context["unknown_duration_count"], 1)

    def test_selecting_a_duration_bucket_filters_the_pool(self):
        """Picking a bucket keeps only movies whose runtime falls in it."""
        with patch("app.roulette_views.services.get_media_metadata") as mock_meta:

            def _metadata(_media_type, media_id, _source):
                runtimes = {"1": "1h 15m", "2": "2h 5m"}
                return {"details": {"runtime": runtimes.get(media_id)}}

            mock_meta.side_effect = _metadata
            response = self.client.get(
                reverse("roulette"),
                {"media_type": MediaTypes.MOVIE.value, "duration": "short"},
            )

        self.assertEqual(response.context["pool_count"], 1)

    def test_game_mode_buckets_from_igdb_metadata(self):
        """Game mode chips classify games into single/co-op/competitive."""
        solo_game = Item.objects.create(
            title="Solo Game",
            media_id="10",
            media_type=MediaTypes.GAME.value,
            source=Sources.IGDB.value,
        )
        coop_game = Item.objects.create(
            title="Co-op Game",
            media_id="11",
            media_type=MediaTypes.GAME.value,
            source=Sources.IGDB.value,
        )
        Game.objects.create(
            user=self.user, item=solo_game, status=Status.PLANNING.value
        )
        Game.objects.create(
            user=self.user, item=coop_game, status=Status.PLANNING.value
        )

        with patch("app.roulette_views.services.get_media_metadata") as mock_meta:

            def _metadata(_media_type, media_id, _source):
                modes = {"10": ["Single player"], "11": ["Co-operative"]}
                return {"details": {"game_modes": modes.get(media_id)}}

            mock_meta.side_effect = _metadata
            response = self.client.get(
                reverse("roulette"), {"media_type": MediaTypes.GAME.value}
            )

        buckets = {row["key"]: row["count"] for row in response.context["mode_rows"]}
        self.assertEqual(buckets["single"], 1)
        self.assertEqual(buckets["co-op"], 1)
        self.assertEqual(buckets["competitive"], 0)


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

    def test_result_shows_how_many_members_have_seen_it(self):
        """A member who already completed the item personally shows as "seen"."""
        Movie.objects.create(
            user=self.member, item=self.item, status=Status.COMPLETED.value
        )
        self.client.login(username="owner", password="testpassword123")  # noqa: S106
        response = self.client.get(
            reverse("roulette"), {"group": self.group.id, "draw": "1"}
        )
        self.assertEqual(response.context["group_seen"], {"seen": 1, "total": 2})

    def test_result_hides_seen_indicator_when_nobody_has_seen_it(self):
        """Nobody has a personal record beyond Planning: no indicator shown."""
        self.client.login(username="owner", password="testpassword123")  # noqa: S106
        response = self.client.get(
            reverse("roulette"), {"group": self.group.id, "draw": "1"}
        )
        self.assertIsNone(response.context["group_seen"])
