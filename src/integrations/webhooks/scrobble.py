"""Processor for agnostic playback ("scrobble") webhook events."""

import logging

from django.utils import timezone

from app.models import Item, MediaTypes, Movie, Sources, Status

logger = logging.getLogger(__name__)

# Playback is considered watched from this percentage onwards.
WATCHED_THRESHOLD_PERCENT = 80

# Playback progress is expressed as a percentage.
MAX_PROGRESS_PERCENT = 100

# Minimal slice: only movies are tracked for now (E10.3/E10.4 will extend this).
SUPPORTED_MEDIA_TYPES = (MediaTypes.MOVIE.value,)

REQUIRED_FIELDS = ("media_id", "source", "progress")


class ScrobblePayloadError(ValueError):
    """Raised when an incoming scrobble payload is invalid."""


class ScrobbleWebhookProcessor:
    """Create tracking entries from agnostic playback events."""

    def __init__(self, watched_threshold=WATCHED_THRESHOLD_PERCENT):
        """Store the percentage from which playback counts as watched."""
        self.watched_threshold = watched_threshold

    def validate(self, payload):
        """Validate and normalise an incoming payload.

        Returns:
            dict: the normalised payload.

        Raises:
            ScrobblePayloadError: if the payload is missing or invalid data.

        """
        if not isinstance(payload, dict):
            msg = "payload must be a JSON object"
            raise ScrobblePayloadError(msg)

        missing = [field for field in REQUIRED_FIELDS if not payload.get(field)]
        if missing:
            msg = f"missing fields: {', '.join(missing)}"
            raise ScrobblePayloadError(msg)

        source = str(payload["source"])
        if source not in Sources.values:
            msg = f"unknown source: {source}"
            raise ScrobblePayloadError(msg)

        media_type = str(payload.get("media_type") or MediaTypes.MOVIE.value)
        if media_type not in SUPPORTED_MEDIA_TYPES:
            msg = f"unsupported media_type: {media_type}"
            raise ScrobblePayloadError(msg)

        try:
            progress = float(payload["progress"])
        except (TypeError, ValueError) as exc:
            msg = "progress must be a number"
            raise ScrobblePayloadError(msg) from exc

        if not 0 <= progress <= MAX_PROGRESS_PERCENT:
            msg = "progress must be between 0 and 100"
            raise ScrobblePayloadError(msg)

        return {
            "media_id": str(payload["media_id"]),
            "source": source,
            "media_type": media_type,
            "progress": progress,
            "title": str(payload.get("title") or payload["media_id"]),
            "image": str(payload.get("image") or ""),
        }

    def process_payload(self, payload, user):
        """Create or update the tracking entry for a playback event.

        Returns:
            str: one of ``ignored``, ``created``, ``updated`` or ``duplicate``.

        """
        data = self.validate(payload)

        if data["progress"] < self.watched_threshold:
            logger.info(
                "Ignoring scrobble for %s (%s): %.1f%% below %.1f%% threshold",
                data["media_id"],
                data["source"],
                data["progress"],
                self.watched_threshold,
            )
            return "ignored"

        item, _ = Item.objects.get_or_create(
            media_id=data["media_id"],
            source=data["source"],
            media_type=data["media_type"],
            defaults={"title": data["title"], "image": data["image"]},
        )

        current_movie = (
            Movie.objects.filter(item=item, user=user).order_by("-created_at").first()
        )

        if current_movie and current_movie.status == Status.COMPLETED.value:
            logger.info("Duplicate scrobble for %s: %s", user, item)
            return "duplicate"

        now = timezone.now().replace(second=0, microsecond=0)

        if current_movie is None:
            Movie.objects.create(
                item=item,
                user=user,
                progress=1,
                status=Status.COMPLETED.value,
                start_date=now,
                end_date=now,
            )
            logger.info("Created tracking entry from scrobble for %s: %s", user, item)
            return "created"

        current_movie.progress = 1
        current_movie.status = Status.COMPLETED.value
        current_movie.end_date = now
        current_movie.save()
        logger.info("Updated tracking entry from scrobble for %s: %s", user, item)
        return "updated"
