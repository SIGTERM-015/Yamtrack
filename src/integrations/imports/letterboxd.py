import logging
from collections import defaultdict
from csv import DictReader

from django.apps import apps
from django.utils import timezone
from django.utils.dateparse import parse_datetime

import app
import app.providers
from app.models import MediaTypes, Sources, Status
from app.providers.services import ProviderAPIError
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)

# Letterboxd uses a 0.5-5 star rating; Yamtrack scores on a 0-10 scale.
LETTERBOXD_MIN_RATING = 0.5
LETTERBOXD_MAX_RATING = 5
LETTERBOXD_SCORE_SCALE = 2


def importer(file, user, mode):
    """Import media from a Letterboxd CSV file."""
    letterboxd_importer = LetterboxdImporter(file, user, mode)
    return letterboxd_importer.import_data()


class LetterboxdImporter:
    """Class to handle importing user data from a Letterboxd CSV export."""

    def __init__(self, file, user, mode):
        """Initialize the importer with file, user, and mode.

        Args:
            file: Uploaded CSV file
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
        """
        self.file = file
        self.user = user
        self.mode = mode
        self.warnings = []

        # Track existing media for "new" mode
        self.existing_media = helpers.get_existing_media(user)

        # Track media IDs to delete in overwrite mode
        self.to_delete = defaultdict(lambda: defaultdict(set))

        # Track bulk creation lists for each media type
        self.bulk_media = defaultdict(list)

        # Filled in once the CSV header is known
        self.has_rating_column = False
        self.has_watched_date_column = False

        logger.info(
            "Initialized Letterboxd importer for user %s with mode %s",
            user.username,
            mode,
        )

    def import_data(self):
        """Import all user data from CSV."""
        try:
            decoded_file = self.file.read().decode("utf-8").splitlines()
        except UnicodeDecodeError as e:
            msg = "Invalid file format. Please upload a CSV file."
            raise MediaImportError(msg) from e

        reader = DictReader(decoded_file)
        self.has_rating_column = "Rating" in (reader.fieldnames or [])
        self.has_watched_date_column = "Watched Date" in (reader.fieldnames or [])
        rows = list(reader)

        # Track media IDs and their titles from the import file
        media_id_counts = defaultdict(int)
        media_id_titles = defaultdict(list)

        # First pass: identify duplicates and validate entries
        for row in rows:
            try:
                self._process_first_pass(row, media_id_counts, media_id_titles)
            except Exception as error:
                error_msg = f"Error processing entry: {row}"
                raise MediaImportUnexpectedError(error_msg) from error

        # Second pass: add non-duplicates to bulk_media
        for row in rows:
            try:
                self._process_second_pass(row, media_id_counts)
            except Exception as error:
                error_msg = f"Error processing entry: {row}"
                raise MediaImportUnexpectedError(error_msg) from error

        # Add consolidated warnings for duplicates
        self._add_duplicate_warnings(media_id_counts, media_id_titles)

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages if self.warnings else None

    def _process_first_pass(self, row, media_id_counts, media_id_titles):
        """First pass to identify duplicate entries and validate data."""
        tmdb_id = self._extract_tmdb_id(row)

        title = row.get("Name", "").strip()

        if not tmdb_id:
            self.warnings.append(f"{title}: Invalid or missing TMDB ID")
            return

        tmdb_data = self._lookup_in_tmdb(tmdb_id)

        if not tmdb_data:
            self.warnings.append(
                f"{title}: Couldn't find a match in {Sources.TMDB.label}",
            )
            return

        media_id = tmdb_data["media_id"]
        media_id_counts[media_id] += 1
        media_id_titles[media_id].append(title)

    def _process_second_pass(self, row, media_id_counts):
        """Second pass to process non-duplicate entries."""
        tmdb_id = self._extract_tmdb_id(row)
        if not tmdb_id:
            return  # Already added warning in first pass

        tmdb_data = self._lookup_in_tmdb(tmdb_id)
        if not tmdb_data:
            return  # Already added warning in first pass

        media_id = tmdb_data["media_id"]

        # Skip if this media_id appears more than once
        if media_id_counts[media_id] > 1:
            return

        media_type = MediaTypes.MOVIE.value

        # Check if we should process this entry based on mode
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            media_type,
            Sources.TMDB.value,
            str(media_id),
            self.mode,
        ):
            return

        item, _ = self._create_or_update_item(tmdb_data, media_type)
        instance = self._create_media_instance(item, row, media_type)
        self.bulk_media[media_type].append(instance)

    def _add_duplicate_warnings(self, media_id_counts, media_id_titles):
        """Add warnings for duplicate entries."""
        for media_id, count in media_id_counts.items():
            if count > 1:
                titles = list(dict.fromkeys(media_id_titles[media_id]))
                title_list = helpers.join_with_commas_and(titles)
                self.warnings.append(
                    f"{title_list}: They were matched to the same TMDB ID {media_id} "
                    "- none imported",
                )

    def _extract_tmdb_id(self, row):
        """Extract and clean the TMDB ID from a row."""
        tmdb_id = row.get("tmdbID", "").strip()

        if not tmdb_id or not tmdb_id.isdigit():
            return None

        return tmdb_id

    def _lookup_in_tmdb(self, tmdb_id):
        """Look up a movie in TMDB using its TMDB ID."""
        try:
            return app.providers.tmdb.movie(tmdb_id)
        except ProviderAPIError as e:
            logger.warning("Error looking up TMDB ID %s: %s", tmdb_id, e)
            return None

    def _create_or_update_item(self, tmdb_data, media_type):
        """Create or update the item in database."""
        return app.models.Item.objects.update_or_create(
            media_id=tmdb_data["media_id"],
            source=Sources.TMDB.value,
            media_type=media_type,
            defaults={
                "title": tmdb_data["title"],
                "image": tmdb_data["image"],
            },
        )

    def _create_media_instance(self, item, row, media_type):
        """Create media instance with all parameters."""
        model = apps.get_model(app_label="app", model_name=media_type)

        # Parse user rating (0-10 scale)
        rating = self._parse_rating(row.get("Rating", ""))

        # Watchlist files have no rating column; everything else was watched.
        is_watched = self.has_rating_column
        status = Status.COMPLETED.value if is_watched else Status.PLANNING.value

        params = {
            "item": item,
            "user": self.user,
            "score": rating,
            "status": status,
        }

        # Parse dates
        date_created = self._parse_date(row.get("Date", ""))
        watched_str = (
            row.get("Watched Date", "")
            if self.has_watched_date_column
            else row.get("Date", "")
        )
        date_watched = self._parse_date(watched_str)

        # filter out None dates
        dates = [date_created, date_watched]
        most_recent_date = max(date for date in dates if date)

        # Movies can have progress and end_date set directly.
        if is_watched and status == Status.COMPLETED.value:
            params["progress"] = 1
            params["end_date"] = date_watched

        instance = model(**params)
        instance._history_date = most_recent_date or timezone.now()

        return instance

    def _parse_rating(self, rating_str):
        """Parse a Letterboxd 0.5-5 rating into Yamtrack's 0-10 score scale."""
        if not rating_str or rating_str.strip() == "":
            return None

        try:
            rating = float(rating_str.strip())
            if LETTERBOXD_MIN_RATING <= rating <= LETTERBOXD_MAX_RATING:
                return round(rating * LETTERBOXD_SCORE_SCALE, 1)
        except (ValueError, TypeError):
            pass

        return None

    def _parse_date(self, date_str):
        """
        Parse date string into datetime object.

        Letterboxd exports dates in YYYY-MM-DD format.
        """
        date_str = date_str.strip()

        if not date_str:
            return None

        date = parse_datetime(date_str)

        if not date:
            logger.warning("Could not parse date: %s", date_str)
            return None

        return date.replace(
            hour=0,
            minute=0,
            second=0,
            tzinfo=timezone.get_current_timezone(),
        )
