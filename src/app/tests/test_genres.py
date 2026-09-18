from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from app.models import Genre, Item, MediaTypes, Sources


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

