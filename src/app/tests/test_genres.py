from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from app.models import Genre, Item, MediaTypes, Movie, Sources


class SetGenresTests(TestCase):
    """Tests for Item.set_genres."""

    def setUp(self):
        """Create an item."""
        self.item = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV",
            image="http://example.com/image.jpg",
        )

    def test_set_genres_dedupes_and_strips(self):
        """Genre names are stripped, deduped and persisted via the M2M."""
        self.item.set_genres([" Action ", "Action", "Drama", "", None])

        names = set(self.item.genres.values_list("name", flat=True))
        self.assertEqual(names, {"Action", "Drama"})
        self.assertEqual(Genre.objects.count(), 2)

    def test_set_genres_ignores_empty(self):
        """Empty input leaves genres untouched."""
        self.item.set_genres(None)
        self.item.set_genres([])

        self.assertEqual(self.item.genres.count(), 0)
        self.assertEqual(Genre.objects.count(), 0)


class BackfillGenresCommandTests(TestCase):
    """Tests for the backfill_genres command (no network)."""

    def setUp(self):
        """Create an item."""
        self.item = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV",
            image="http://example.com/image.jpg",
        )

    @patch("app.management.commands.backfill_genres.services.get_media_metadata")
    def test_backfill_sets_genres(self, mock_metadata):
        """The command persists genres from the mocked metadata."""
        mock_metadata.return_value = {"genres": ["Action", "Drama"]}

        call_command("backfill_genres")

        names = set(self.item.genres.values_list("name", flat=True))
        self.assertEqual(names, {"Action", "Drama"})


class BackfillGenresSeasonEpisodeTests(TestCase):
    """Backfill must pass season/episode numbers instead of skipping them."""

    @patch("app.management.commands.backfill_genres.services.get_media_metadata")
    def test_backfill_season_passes_season_numbers(self, mock_metadata):
        """A season item is fetched with its season_number and gets genres."""
        season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=2,
        )
        mock_metadata.return_value = {"genres": ["Comedy"]}

        call_command("backfill_genres")

        mock_metadata.assert_called_once_with(
            MediaTypes.SEASON.value,
            "1668",
            Sources.TMDB.value,
            [2],
            None,
        )
        names = set(season.genres.values_list("name", flat=True))
        self.assertEqual(names, {"Comedy"})

    @patch("app.management.commands.backfill_genres.services.get_media_metadata")
    def test_backfill_episode_passes_season_and_episode(self, mock_metadata):
        """An episode item is fetched with its season/episode numbers."""
        episode = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=2,
            episode_number=5,
        )
        mock_metadata.return_value = {"genres": ["Comedy"]}

        call_command("backfill_genres")

        mock_metadata.assert_called_once_with(
            MediaTypes.EPISODE.value,
            "1668",
            Sources.TMDB.value,
            [2],
            5,
        )
        names = set(episode.genres.values_list("name", flat=True))
        self.assertEqual(names, {"Comedy"})


class MediaSaveGenresTests(TestCase):
    """media_save persists genres when re-saving an existing instance."""

    def setUp(self):
        """Create a user, a movie item and its instance, then log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        self.movie = Movie.objects.create(item=self.item, user=self.user)

    @patch("app.views.services.get_media_metadata")
    def test_resave_persists_genres(self, mock_metadata):
        """Re-saving an item without genres fetches and persists them."""
        mock_metadata.return_value = {"genres": ["Action", "Drama"]}

        self.client.post(
            reverse("media_save"),
            {
                "instance_id": self.movie.id,
                "media_id": "10494",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "score": 10,
                "progress": 1,
            },
        )

        names = set(self.item.genres.values_list("name", flat=True))
        self.assertEqual(names, {"Action", "Drama"})

    @patch("app.views.services.get_media_metadata")
    def test_resave_with_genres_skips_fetch(self, mock_metadata):
        """Re-saving an item that already has genres makes no provider call."""
        self.item.set_genres(["Action"])

        self.client.post(
            reverse("media_save"),
            {
                "instance_id": self.movie.id,
                "media_id": "10494",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "score": 10,
                "progress": 1,
            },
        )

        mock_metadata.assert_not_called()
        names = set(self.item.genres.values_list("name", flat=True))
        self.assertEqual(names, {"Action"})

