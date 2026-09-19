import csv
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.test import TestCase
from django.urls import reverse

from app.models import (
    TV,
    Anime,
    Book,
    Episode,
    Game,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)


class ExportCSVTest(TestCase):
    """Test exporting media to CSV."""

    def setUp(self):
        """Create necessary data for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_superuser(**self.credentials)
        self.client.login(**self.credentials)

        item_movie = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="https://image.url",
        )
        Movie.objects.create(
            item=item_movie,
            user=self.user,
            score=9,
            status=Status.COMPLETED.value,
            notes="Nice",
            start_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="https://image.url",
            season_number=1,
        )

        season = Season.objects.create(
            item=item_season,
            user=self.user,
            score=9,
            status=Status.IN_PROGRESS.value,
            notes="Nice",
        )

        item_episode = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="https://image.url",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_episode,
            related_season=season,
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_anime = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="https://image.url",
        )
        Anime.objects.create(
            item=item_anime,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=2,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_manga = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            title="Berserk",
            image="https://image.url",
        )
        Manga.objects.create(
            item=item_manga,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=2,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_game = Item.objects.create(
            media_id="1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="The Witcher 3: Wild Hunt",
            image="https://image.url",
        )
        Game.objects.create(
            item=item_game,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=120,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_book = Item.objects.create(
            media_id="OL21733390M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Fantastic Mr. Fox",
            image="https://image.url",
        )
        Book.objects.create(
            item=item_book,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=120,
            start_date=datetime(2021, 6, 1, 0, 0, tzinfo=UTC),
        )

    def test_export_csv(self):
        """Basic test exporting media to CSV."""
        # Generate the CSV file by accessing the export view
        response = self.client.get(reverse("export_csv"))

        # Assert that the response is successful (status code 200)
        self.assertEqual(response.status_code, 200)

        # Assert that the response content type is text/csv
        self.assertEqual(response["Content-Type"], "text/csv")

        # Read the streaming content and decode it
        content = b"".join(response.streaming_content).decode("utf-8")

        # Create a CSV reader from the CSV content
        reader = csv.DictReader(StringIO(content))

        db_media_ids = set(
            Item.objects.filter(
                Q(tv__user=self.user)
                | Q(movie__user=self.user)
                | Q(season__user=self.user)
                | Q(episode__related_season__user=self.user)
                | Q(anime__user=self.user)
                | Q(manga__user=self.user)
                | Q(game__user=self.user)
                | Q(book__user=self.user),
            ).values_list("media_id", flat=True),
        )

        # Verify each row in the CSV exists in the database
        for row in reader:
            media_id = row["media_id"]
            self.assertIn(media_id, db_media_ids)


class LetterboxdExportTest(TestCase):
    """Test exporting a user's movies to a Letterboxd-compatible CSV."""

    def setUp(self):
        """Create movies for two users plus a non-movie item."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.other_user = get_user_model().objects.create_user(
            **{**self.credentials, "username": "other"},
        )
        self.client.login(**self.credentials)

        # Saving a completed movie calls the provider; keep the test offline.
        metadata_patcher = patch(
            "app.models.providers.services.get_media_metadata",
        )
        self.mock_metadata = metadata_patcher.start()
        self.mock_metadata.return_value = {"max_progress": None}
        self.addCleanup(metadata_patcher.stop)

        tmdb_item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="https://image.url",
        )
        Movie.objects.create(
            item=tmdb_item,
            user=self.user,
            score=Decimal("9.0"),
            status=Status.COMPLETED.value,
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

        manual_item = Item.objects.create(
            media_id="8f1b9e6a-0000-0000-0000-000000000000",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="A Manual Movie",
            image="https://image.url",
        )
        Movie.objects.create(
            item=manual_item,
            user=self.user,
            score=Decimal("8.5"),
            status=Status.COMPLETED.value,
        )

        other_item = Item.objects.create(
            media_id="603",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="The Matrix",
            image="https://image.url",
        )
        Movie.objects.create(
            item=other_item,
            user=self.other_user,
            score=Decimal("10.0"),
            status=Status.COMPLETED.value,
        )

        tv_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Friends",
            image="https://image.url",
        )
        TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

    def test_export_letterboxd_csv(self):
        """Only the requesting user's movies are exported with Letterboxd columns."""
        response = self.client.get(reverse("export_letterboxd"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")

        content = b"".join(response.streaming_content).decode("utf-8")
        reader = csv.DictReader(StringIO(content))
        rows = list(reader)

        self.assertEqual(
            reader.fieldnames,
            ["tmdbID", "Title", "Year", "Rating10", "WatchedDate"],
        )
        self.assertEqual(len(rows), 2)

        by_title = {row["Title"]: row for row in rows}
        self.assertNotIn("The Matrix", by_title)
        self.assertNotIn("Friends", by_title)

        perfect_blue = by_title["Perfect Blue"]
        self.assertEqual(perfect_blue["tmdbID"], "10494")
        self.assertEqual(perfect_blue["Rating10"], "9")
        self.assertEqual(perfect_blue["WatchedDate"], "2023-06-01")

        manual = by_title["A Manual Movie"]
        self.assertEqual(manual["tmdbID"], "")
        self.assertEqual(manual["Rating10"], "8.5")
        self.assertEqual(manual["WatchedDate"], "")
