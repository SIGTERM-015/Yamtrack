import importlib
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.models import Item, MediaTypes, Sources
from lists.models import CustomList, CustomListItem

migration_module = importlib.import_module(
    "lists.migrations.0004_customlistitem_list_item_id_and_more",
)

UTC = ZoneInfo("UTC")


class PopulateListItemIdTests(TestCase):
    """Data migration 0004 backfills sequential list_item_id per list."""

    def _run_migration(self):
        migration_module.populate_list_item_id(apps, None)

    def setUp(self):
        """Create a user, lists, and items for the migration test."""
        self.user = get_user_model().objects.create_user(
            username="migration_user",
            password="12345",  # noqa: S106
        )
        self.list_a = CustomList.objects.create(
            name="List A",
            owner=self.user,
        )
        self.list_b = CustomList.objects.create(
            name="List B",
            owner=self.user,
        )
        self.items = [
            Item.objects.create(
                title=f"Item {index}",
                media_id=f"migration-0004-{index}",
                media_type=MediaTypes.TV.value,
                source=Sources.TMDB.value,
            )
            for index in range(5)
        ]

    def _add_unmigrated(self, custom_list, item, date_added):
        """Add an item as if created before the migration existed."""
        list_item = CustomListItem.objects.create(
            custom_list=custom_list,
            item=item,
        )
        CustomListItem.objects.filter(pk=list_item.pk).update(
            list_item_id=None,
            date_added=date_added,
        )
        return list_item

    def test_items_numbered_by_date_added_per_list(self):
        """Items added out of order still get 0..N-1 following date_added."""
        base = timezone.datetime(2025, 1, 1, tzinfo=UTC)
        # Create in scrambled insertion order; date_added drives the result.
        first = self._add_unmigrated(
            self.list_a,
            self.items[0],
            base + timedelta(days=2),
        )
        second = self._add_unmigrated(
            self.list_a,
            self.items[1],
            base,
        )
        third = self._add_unmigrated(
            self.list_a,
            self.items[2],
            base + timedelta(days=1),
        )
        other_first = self._add_unmigrated(
            self.list_b,
            self.items[3],
            base + timedelta(days=5),
        )
        other_second = self._add_unmigrated(
            self.list_b,
            self.items[4],
            base + timedelta(days=3),
        )

        self._run_migration()

        ids = (
            CustomListItem.objects.filter(custom_list=self.list_a)
            .order_by(
                "list_item_id",
            )
            .values_list("id", flat=True)
        )
        self.assertEqual(list(ids), [second.id, third.id, first.id])
        other_ids = (
            CustomListItem.objects.filter(
                custom_list=self.list_b,
            )
            .order_by("list_item_id")
            .values_list("id", flat=True)
        )
        self.assertEqual(list(other_ids), [other_second.id, other_first.id])

    def test_date_added_tie_broken_by_id(self):
        """Equal date_added values fall back to row id order."""
        moment = timezone.datetime(2025, 6, 1, tzinfo=UTC)
        early = self._add_unmigrated(self.list_a, self.items[0], moment)
        late = self._add_unmigrated(self.list_a, self.items[1], moment)

        self._run_migration()

        early.refresh_from_db()
        late.refresh_from_db()
        self.assertEqual((early.list_item_id, late.list_item_id), (0, 1))

    def test_reverse_resets_to_null(self):
        """The reverse migration clears every backfilled value."""
        moment = timezone.datetime(2025, 6, 1, tzinfo=UTC)
        self._add_unmigrated(self.list_a, self.items[0], moment)
        self._run_migration()
        self.assertTrue(
            CustomListItem.objects.filter(list_item_id__isnull=False).exists(),
        )

        migration_module.reverse_populate_list_item_id(apps, None)

        self.assertFalse(
            CustomListItem.objects.filter(list_item_id__isnull=False).exists(),
        )
