import logging

from django.core.management.base import BaseCommand

from app.models import Item
from app.providers import services

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Backfill genres for existing items from their metadata provider."""

    help = "Fetch and persist genres for existing items."

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of items to process.",
        )

    def handle(self, *args, **options):
        """Iterate items and persist their genres."""
        limit = options["limit"]
        items = Item.objects.order_by("id")
        if limit is not None:
            items = items[:limit]

        processed = 0
        for item in items:
            try:
                metadata = services.get_media_metadata(
                    item.media_type,
                    item.media_id,
                    item.source,
                )
            except Exception:  # noqa: BLE001 - providers vary and may be unavailable
                logger.warning("Failed to fetch metadata for item %s", item.id)
                continue

            item.set_genres(metadata.get("genres"))
            processed += 1

        self.stdout.write(self.style.SUCCESS(f"Processed {processed} items."))
