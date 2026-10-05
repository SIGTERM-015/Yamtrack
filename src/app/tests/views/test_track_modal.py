import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import (
    BoardGame,
    Book,
    Game,
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
)


class TrackModalViewTests(TestCase):
    """Test the track modal view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        self.movie = Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )

    def test_track_modal_view_existing_media(self):
        """Test the track modal view for existing media."""
        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            )
            + "?return_url=/home",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_track.html")

        self.assertIn("form", response.context)
        self.assertIn("media", response.context)
        self.assertEqual(response.context["media"], self.movie)
        self.assertEqual(response.context["return_url"], "/home")

    @patch("app.providers.services.get_media_metadata")
    def test_track_modal_view_new_media(self, mock_get_metadata):
        """Test the track modal view for new media."""
        mock_get_metadata.return_value = {
            "media_id": "278",
            "title": "New Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
        }

        response = self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "278",
                },
            )
            + "?return_url=/home&title=New+Movie",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_track.html")

        self.assertIn("form", response.context)
        self.assertEqual(response.context["form"].initial["media_id"], "278")
        self.assertEqual(
            response.context["form"].initial["media_type"],
            MediaTypes.MOVIE.value,
        )


class TrackModalDateFieldsTests(TestCase):
    """The track modal shows dates per media type."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def _modal(self, media_type, media_id, source):
        """Render the track modal for an item."""
        return self.client.get(
            reverse(
                "track_modal",
                kwargs={
                    "source": source,
                    "media_type": media_type,
                    "media_id": media_id,
                },
            )
            + "?return_url=/home",
        )

    def _track(self, model, media_type, source, **fields):
        """Track a new item of ``model`` for the user."""
        item = Item.objects.create(
            media_id="1",
            source=source,
            media_type=media_type,
            title="Title",
            image="http://example.com/image.jpg",
        )
        return model.objects.create(item=item, user=self.user, **fields)

    def test_movie_has_single_watched_on_date(self):
        """A movie shows one "Watched on" date and no visible start date."""
        self._track(
            Movie,
            MediaTypes.MOVIE.value,
            Sources.TMDB.value,
            status=Status.PLANNING.value,
            start_date=datetime.datetime(2024, 3, 1, tzinfo=datetime.UTC),
        )

        response = self._model_html(MediaTypes.MOVIE.value, Sources.TMDB.value)

        self.assertIn("Watched on", response)
        self.assertNotIn("Start date", response)
        self.assertNotIn("More details", response)
        # The stored start date survives edits as a hidden input.
        self.assertIn('type="hidden" name="start_date" value="2024-03-01', response)

    def test_boardgame_has_single_played_on_date(self):
        """A board game shows one "Played on" date."""
        self._track(
            BoardGame,
            MediaTypes.BOARDGAME.value,
            Sources.BGG.value,
            status=Status.PLANNING.value,
        )

        response = self._model_html(MediaTypes.BOARDGAME.value, Sources.BGG.value)

        self.assertIn("Played on", response)
        self.assertNotIn("Start date", response)
        self.assertNotIn("More details", response)

    def test_book_dates_collapsed_when_empty(self):
        """A book without dates keeps them in a closed "More details" block."""
        self._track(
            Book,
            MediaTypes.BOOK.value,
            Sources.OPENLIBRARY.value,
            status=Status.PLANNING.value,
        )

        response = self._model_html(MediaTypes.BOOK.value, Sources.OPENLIBRARY.value)

        self.assertIn("More details", response)
        self.assertIn('<details class="group mb-5" >', response)
        self.assertIn("Start date", response)
        self.assertIn("End date", response)

    def test_game_dates_open_when_set(self):
        """A game with a stored date opens the "More details" block."""
        self._track(
            Game,
            MediaTypes.GAME.value,
            Sources.IGDB.value,
            status=Status.IN_PROGRESS.value,
            start_date=datetime.datetime(2024, 3, 1, tzinfo=datetime.UTC),
        )

        response = self._model_html(MediaTypes.GAME.value, Sources.IGDB.value)

        self.assertIn('<details class="group mb-5" open>', response)
        self.assertIn('name="start_date" value="2024-03-01', response)

    def _model_html(self, media_type, source):
        """Return the rendered modal HTML for the tracked item."""
        response = self._modal(media_type, "1", source)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()
