from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.models import (
    MediaTypes,
    Movie,
    Status,
)
from integrations.imports import (
    letterboxd,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"

# Titles returned by the mocked TMDB provider, keyed by TMDB ID.
TMDB_TITLES = {
    "155": "The Dark Knight",
    "278": "The Shawshank Redemption",
    "496243": "Parasite",
    "129": "Spirited Away",
    "27205": "Inception",
    "244786": "Whiplash",
    "693134": "Dune: Part Two",
    "545611": "Everything Everywhere All at Once",
}


def fake_tmdb_movie(media_id):
    """Return a minimal TMDB movie payload for the given media id."""
    return {
        "media_id": media_id,
        "title": TMDB_TITLES.get(media_id, f"Movie {media_id}"),
        "image": "https://image.tmdb.org/t/p/w500/poster.jpg",
    }


class ImportLetterboxd(TestCase):
    """Test importing media from Letterboxd CSV exports."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    def _import(self, filename):
        with Path(mock_path / filename).open("rb") as file:
            return letterboxd.importer(file, self.user, "new")

    @patch("app.providers.tmdb.movie")
    def test_import_letterboxd_ratings(self, mock_tmdb_movie):
        """Test importing a Letterboxd ratings CSV."""
        mock_tmdb_movie.side_effect = fake_tmdb_movie

        imported_counts, warnings = self._import("import_letterboxd_ratings.csv")

        self.assertEqual(imported_counts[MediaTypes.MOVIE.value], 4)
        self.assertIsNone(warnings)
        self.assertEqual(Movie.objects.count(), 4)

        dark_knight = Movie.objects.get(item__title="The Dark Knight")
        self.assertEqual(dark_knight.score, 9)
        self.assertEqual(dark_knight.status, Status.COMPLETED.value)
        self.assertEqual(dark_knight.progress, 1)
        self.assertEqual(
            dark_knight.end_date,
            datetime(2025, 2, 2, tzinfo=timezone.get_current_timezone()),
        )

        shawshank = Movie.objects.get(item__title="The Shawshank Redemption")
        self.assertEqual(shawshank.score, 10)

        parasite = Movie.objects.get(item__title="Parasite")
        self.assertEqual(parasite.score, 6)

        spirited_away = Movie.objects.get(item__title="Spirited Away")
        self.assertEqual(spirited_away.score, 8)

    @patch("app.providers.tmdb.movie")
    def test_import_letterboxd_diary(self, mock_tmdb_movie):
        """Test importing a Letterboxd diary CSV with a duplicate entry."""
        mock_tmdb_movie.side_effect = fake_tmdb_movie

        imported_counts, warnings = self._import("import_letterboxd_diary.csv")

        # Inception appears twice and is matched to the same TMDB ID, so it is skipped.
        self.assertEqual(imported_counts[MediaTypes.MOVIE.value], 2)
        self.assertIn(
            "Inception: They were matched to the same TMDB ID 27205 - none imported",
            warnings,
        )
        self.assertFalse(Movie.objects.filter(item__title="Inception").exists())

        parasite = Movie.objects.get(item__title="Parasite")
        self.assertIsNone(parasite.score)
        self.assertEqual(parasite.status, Status.COMPLETED.value)
        self.assertEqual(parasite.progress, 1)
        self.assertEqual(
            parasite.end_date,
            datetime(2025, 3, 1, tzinfo=timezone.get_current_timezone()),
        )

        whiplash = Movie.objects.get(item__title="Whiplash")
        self.assertEqual(whiplash.score, 10)
        self.assertEqual(whiplash.status, Status.COMPLETED.value)

    @patch("app.providers.tmdb.movie")
    def test_import_letterboxd_watchlist(self, mock_tmdb_movie):
        """Test importing a Letterboxd watchlist CSV without ratings."""
        mock_tmdb_movie.side_effect = fake_tmdb_movie

        imported_counts, warnings = self._import("import_letterboxd_watchlist.csv")

        self.assertEqual(imported_counts[MediaTypes.MOVIE.value], 2)
        self.assertIsNone(warnings)
        self.assertEqual(Movie.objects.count(), 2)

        dune = Movie.objects.get(item__title="Dune: Part Two")
        self.assertIsNone(dune.score)
        self.assertEqual(dune.status, Status.PLANNING.value)
        self.assertEqual(dune.progress, 0)
        self.assertIsNone(dune.end_date)
