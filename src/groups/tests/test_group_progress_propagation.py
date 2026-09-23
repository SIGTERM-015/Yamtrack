"""S3: monotone propagation of group status/progress to personal records (non-TV)."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import TV, Book, Item, Movie, Status
from groups.models import Group, GroupItem, GroupMembership
from groups.services import mark_group_item_status, propagate_group_progress

User = get_user_model()

# Large enough that process_progress never clamps the values used in tests.
_MAX_PROGRESS = 1000


class GroupProgressPropagationTest(TestCase):
    """Group status/progress merges into members' personal records."""

    def setUp(self):
        """Create a group with two members and one movie item."""
        patcher = patch("app.models.providers.services.get_media_metadata")
        self.mock_metadata = patcher.start()
        self.mock_metadata.return_value = {"max_progress": _MAX_PROGRESS}
        self.addCleanup(patcher.stop)

        self.alice = User.objects.create(username="alice")
        self.bob = User.objects.create(username="bob")
        self.group = Group.objects.create(name="Group", owner=self.alice)
        GroupMembership.objects.create(group=self.group, user=self.alice)
        GroupMembership.objects.create(group=self.group, user=self.bob)

        self.item = Item.objects.create(
            media_id="m1",
            title="Movie 1",
            media_type="movie",
            source="tmdb",
        )
        self.group_item = GroupItem.objects.create(
            group=self.group,
            item=self.item,
            added_by=self.alice,
        )

    def _advance(self, progress, status=Status.IN_PROGRESS, participants=None):
        """Set the group's progress and mark its status, propagating to members."""
        self.group_item.progress = progress
        self.group_item.save(update_fields=["progress"])
        return mark_group_item_status(self.group_item, status, participants)

    def test_missing_record_is_created_from_group(self):
        """A member without a record gets one with the group's status/progress."""
        self._advance(4)

        for user in (self.alice, self.bob):
            entry = Movie.objects.get(user=user, item=self.item)
            self.assertEqual(entry.status, Status.IN_PROGRESS.value)
            self.assertEqual(entry.progress, 4)

    def test_progress_advances_keeping_score_and_notes(self):
        """A member behind the group advances, keeping score and notes."""
        Movie.objects.create(
            user=self.alice,
            item=self.item,
            status=Status.IN_PROGRESS,
            progress=3,
            score=7,
            notes="keep",
        )

        self._advance(4)

        entry = Movie.objects.get(user=self.alice, item=self.item)
        self.assertEqual(entry.progress, 4)
        self.assertEqual(entry.status, Status.IN_PROGRESS.value)
        self.assertEqual(entry.score, 7)
        self.assertEqual(entry.notes, "keep")

    def test_member_ahead_is_not_reduced(self):
        """Personal 8 vs group 4 keeps 8 (canonical 8/3/4 example)."""
        Movie.objects.create(
            user=self.alice,
            item=self.item,
            status=Status.IN_PROGRESS,
            progress=8,
        )

        self._advance(4)

        entry = Movie.objects.get(user=self.alice, item=self.item)
        self.assertEqual(entry.progress, 8)

    def test_completed_member_is_not_reopened(self):
        """A completed member stays completed when the group is In progress."""
        Movie.objects.create(
            user=self.alice,
            item=self.item,
            status=Status.COMPLETED,
            progress=8,
        )
        before = Movie.objects.get(user=self.alice, item=self.item).progress

        self._advance(4)

        entry = Movie.objects.get(user=self.alice, item=self.item)
        self.assertEqual(entry.status, Status.COMPLETED.value)
        self.assertEqual(entry.progress, before)

    def test_dropped_member_is_untouched(self):
        """A dropped member keeps status, progress and notes."""
        Movie.objects.create(
            user=self.alice,
            item=self.item,
            status=Status.DROPPED,
            progress=3,
            notes="drop",
        )

        self._advance(4)

        entry = Movie.objects.get(user=self.alice, item=self.item)
        self.assertEqual(entry.status, Status.DROPPED.value)
        self.assertEqual(entry.progress, 3)
        self.assertEqual(entry.notes, "drop")

    def test_paused_member_advances_progress_but_keeps_status(self):
        """A paused member advances progress without leaving Paused."""
        Movie.objects.create(
            user=self.alice,
            item=self.item,
            status=Status.PAUSED,
            progress=3,
        )

        self._advance(4)

        entry = Movie.objects.get(user=self.alice, item=self.item)
        self.assertEqual(entry.status, Status.PAUSED.value)
        self.assertEqual(entry.progress, 4)

    def test_paused_or_dropped_group_touches_no_personal_record(self):
        """A Paused/Dropped group never creates or updates personal records."""
        for index, group_status in enumerate((Status.PAUSED, Status.DROPPED)):
            item = Item.objects.create(
                media_id=f"skip{index}",
                title=f"Skip {index}",
                media_type="movie",
                source="tmdb",
            )
            group_item = GroupItem.objects.create(
                group=self.group,
                item=item,
                added_by=self.alice,
                progress=4,
            )

            mark_group_item_status(group_item, group_status)

            self.assertFalse(Movie.objects.filter(item=item).exists())

    def test_participants_limit_propagation(self):
        """Only listed participants are touched; other members stay intact."""
        self._advance(4, participants=[self.alice.id])

        self.assertTrue(Movie.objects.filter(user=self.alice, item=self.item).exists())
        self.assertFalse(Movie.objects.filter(user=self.bob, item=self.item).exists())

    def test_second_identical_call_is_idempotent(self):
        """Re-running propagation changes no personal record."""
        self._advance(4)
        snapshot = list(
            Movie.objects.filter(item=self.item).order_by("user_id").values(),
        )

        summary = propagate_group_progress(self.group_item)

        self.assertEqual(summary, {"created": 0, "updated": 0, "skipped": 2})
        self.assertEqual(
            list(Movie.objects.filter(item=self.item).order_by("user_id").values()),
            snapshot,
        )

    def test_book_media_propagates_progress(self):
        """Non-TV media with a numeric progress (book) propagates too."""
        book_item = Item.objects.create(
            media_id="b1",
            title="Book 1",
            media_type="book",
            source="openlibrary",
        )
        group_item = GroupItem.objects.create(
            group=self.group,
            item=book_item,
            added_by=self.alice,
            progress=40,
        )

        mark_group_item_status(group_item, Status.IN_PROGRESS)

        entry = Book.objects.get(user=self.alice, item=book_item)
        self.assertEqual(entry.status, Status.IN_PROGRESS.value)
        self.assertEqual(entry.progress, 40)

    def test_tv_media_is_not_propagated_yet(self):
        """TV is deferred to S4: no personal record is created here."""
        tv_item = Item.objects.create(
            media_id="t1",
            title="Show 1",
            media_type="tv",
            source="tmdb",
        )
        group_item = GroupItem.objects.create(
            group=self.group,
            item=tv_item,
            added_by=self.alice,
            progress=4,
        )

        summary = propagate_group_progress(group_item)

        self.assertEqual(summary, {"created": 0, "updated": 0, "skipped": 2})
        self.assertFalse(TV.objects.filter(item=tv_item).exists())
