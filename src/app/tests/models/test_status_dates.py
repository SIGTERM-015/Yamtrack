import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import TV, Book, Item, MediaTypes, Movie, Sources, Status

NOW = datetime.datetime(2026, 10, 5, 18, 30, 42, 123, tzinfo=datetime.UTC)
NOW_MINUTE = datetime.datetime(2026, 10, 5, 18, 30, tzinfo=datetime.UTC)
EARLIER = datetime.datetime(2025, 1, 2, 9, 0, tzinfo=datetime.UTC)


@patch("app.models.timezone.now", return_value=NOW)
@patch(
    "app.models.providers.services.get_media_metadata",
    return_value={"max_progress": None},
)
class StatusDatesTests(TestCase):
    """Status changes fill in empty start/end dates server-side."""

    def setUp(self):
        """Create a user and a movie and book item."""
        self.user = get_user_model().objects.create_user(username="test")
        self.movie_item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="The Godfather",
        )
        self.book_item = Item.objects.create(
            media_id="OL1",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Dune",
        )

    def test_create_completed_sets_end_date(self, *_):
        """A record created as Completed gets end_date = now, start untouched."""
        movie = Movie.objects.create(
            item=self.movie_item,
            user=self.user,
            status=Status.COMPLETED.value,
        )

        movie.refresh_from_db()
        self.assertEqual(movie.end_date, NOW_MINUTE)
        self.assertIsNone(movie.start_date)

    def test_change_to_completed_sets_end_date(self, *_):
        """Switching an existing record to Completed stamps end_date."""
        book = Book.objects.create(
            item=self.book_item,
            user=self.user,
            status=Status.PLANNING.value,
        )
        self.assertIsNone(book.end_date)

        book.status = Status.COMPLETED.value
        book.save()

        book.refresh_from_db()
        self.assertEqual(book.end_date, NOW_MINUTE)

    def test_change_to_in_progress_sets_start_date(self, *_):
        """Starting a record stamps start_date and leaves end_date empty."""
        book = Book.objects.create(
            item=self.book_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        book.status = Status.IN_PROGRESS.value
        book.save()

        book.refresh_from_db()
        self.assertEqual(book.start_date, NOW_MINUTE)
        self.assertIsNone(book.end_date)

    def test_existing_dates_are_kept(self, *_):
        """Dates set by the person (or an import) are never overwritten."""
        book = Book.objects.create(
            item=self.book_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            start_date=EARLIER,
        )
        self.assertEqual(book.start_date, EARLIER)

        book.status = Status.COMPLETED.value
        book.end_date = EARLIER
        book.save()

        book.refresh_from_db()
        self.assertEqual(book.start_date, EARLIER)
        self.assertEqual(book.end_date, EARLIER)

    def test_other_statuses_set_no_dates(self, *_):
        """Planning, Paused and Dropped do not invent dates."""
        for status in (Status.PLANNING, Status.PAUSED, Status.DROPPED):
            book = Book.objects.create(
                item=self.book_item,
                user=self.user,
                status=status.value,
            )
            self.assertIsNone(book.start_date, status)
            self.assertIsNone(book.end_date, status)
            book.delete()

    def test_unchanged_status_does_not_refill_cleared_date(self, *_):
        """Clearing the date of a completed record without a status change sticks."""
        movie = Movie.objects.create(
            item=self.movie_item,
            user=self.user,
            status=Status.COMPLETED.value,
        )

        movie.end_date = None
        movie.notes = "edited"
        movie.save()

        movie.refresh_from_db()
        self.assertIsNone(movie.end_date)

    def test_status_date_defaults(self, *_):
        """Dated models get the matching field; TV has no concrete dates."""
        self.assertEqual(
            Movie.status_date_defaults(Status.COMPLETED.value),
            {"end_date": NOW_MINUTE},
        )
        self.assertEqual(
            Book.status_date_defaults(Status.IN_PROGRESS.value),
            {"start_date": NOW_MINUTE},
        )
        self.assertEqual(Book.status_date_defaults(Status.PAUSED.value), {})
        self.assertEqual(TV.status_date_defaults(Status.COMPLETED.value), {})
