"""S2: group-owned status on GroupItem, without propagation."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import Item, Movie, Status
from groups.models import Group, GroupItem, GroupMembership
from groups.services import (
    get_group_sections,
    get_group_tab_items,
    mark_group_item_status,
)

User = get_user_model()


def _media_counts():
    """Count personal tracking rows for the Item used in these tests."""
    return Movie.objects.count()


class MarkGroupItemStatusTest(TestCase):
    """mark_group_item_status writes only the GroupItem."""

    def setUp(self):
        """Create a group with two members and one movie item."""
        self.user1 = User.objects.create(username="user1")
        self.user2 = User.objects.create(username="user2")
        self.group = Group.objects.create(name="Group", owner=self.user1)
        GroupMembership.objects.create(group=self.group, user=self.user1)
        GroupMembership.objects.create(group=self.group, user=self.user2)
        self.item = Item.objects.create(
            media_id="m1",
            title="Movie 1",
            media_type="movie",
            source="tmdb",
        )
        self.group_item = GroupItem.objects.create(
            group=self.group,
            item=self.item,
            added_by=self.user1,
        )

    def test_in_progress_sets_started_and_touches_no_personal_rows(self):
        """In progress fills started_at without creating personal records."""
        before = _media_counts()
        mark_group_item_status(self.group_item, Status.IN_PROGRESS, participants=[])
        self.group_item.refresh_from_db()
        self.assertEqual(self.group_item.status, Status.IN_PROGRESS.value)
        self.assertIsNotNone(self.group_item.started_at)
        self.assertEqual(_media_counts(), before)

    def test_status_change_does_not_modify_existing_personal_rows(self):
        """Existing personal records keep status/progress/score/notes."""
        entry = Movie.objects.create(
            user=self.user1,
            item=self.item,
            status=Status.PLANNING,
            progress=3,
            score=7,
            notes="keep",
        )
        mark_group_item_status(self.group_item, Status.IN_PROGRESS, participants=[])
        mark_group_item_status(self.group_item, Status.COMPLETED, participants=[])
        entry.refresh_from_db()
        self.assertEqual(entry.status, Status.PLANNING.value)
        self.assertEqual(entry.progress, 3)
        self.assertEqual(entry.score, 7)
        self.assertEqual(entry.notes, "keep")

    def test_completed_sets_completed_at(self):
        """Completed fills completed_at."""
        mark_group_item_status(self.group_item, Status.COMPLETED, participants=[])
        self.group_item.refresh_from_db()
        self.assertEqual(self.group_item.status, Status.COMPLETED.value)
        self.assertIsNotNone(self.group_item.completed_at)


class GetGroupTabItemsTest(TestCase):
    """get_group_tab_items classifies by the group's own status."""

    def setUp(self):
        """Create a group with one item per status."""
        self.user = User.objects.create(username="user1")
        self.group = Group.objects.create(name="Group", owner=self.user)
        GroupMembership.objects.create(group=self.group, user=self.user)
        self.items = {}
        for index, status in enumerate(
            [
                Status.PLANNING,
                Status.IN_PROGRESS,
                Status.COMPLETED,
                Status.PAUSED,
                Status.DROPPED,
            ],
        ):
            item = Item.objects.create(
                media_id=f"m{index}",
                title=f"Movie {index}",
                media_type="movie",
                source="tmdb",
            )
            self.items[status] = GroupItem.objects.create(
                group=self.group,
                item=item,
                added_by=self.user,
                status=status.value,
            )

    def test_classifies_all_statuses(self):
        """Planning/In progress/Completed/Paused/Dropped land in tabs."""
        tabs = get_group_tab_items(self.group)
        self.assertEqual(tabs["planning"], [self.items[Status.PLANNING]])
        self.assertEqual(tabs["in_progress"], [self.items[Status.IN_PROGRESS]])
        self.assertEqual(tabs["completed"], [self.items[Status.COMPLETED]])
        self.assertCountEqual(
            tabs["other"],
            [self.items[Status.PAUSED], self.items[Status.DROPPED]],
        )


class GetGroupSectionsTest(TestCase):
    """get_group_sections groups by the group's status, then by media type."""

    def setUp(self):
        """Create a group with no items yet."""
        self.user = User.objects.create(username="user1")
        self.group = Group.objects.create(name="Group", owner=self.user)
        GroupMembership.objects.create(group=self.group, user=self.user)

    def _add(self, title, media_type, status):
        item = Item.objects.create(
            media_id=title,
            title=title,
            media_type=media_type,
            source="tmdb",
            season_number=1 if media_type == "season" else None,
        )
        GroupItem.objects.create(
            group=self.group, item=item, added_by=self.user, status=status.value
        )

    def _layout(self):
        return [
            (
                section["status"],
                section["id"],
                section["count"],
                [
                    (
                        media_type["media_type"],
                        [group_item.item.title for group_item in media_type["items"]],
                    )
                    for media_type in section["media_types"]
                ],
            )
            for section in get_group_sections(self.group)
        ]

    def test_status_sections_then_media_types_in_app_order(self):
        """Statuses in home order; media types in the app's order inside each."""
        self._add("Zelda", "game", Status.PLANNING)
        self._add("Gatsby", "book", Status.PLANNING)
        self._add("Inception", "movie", Status.PLANNING)
        self._add("Breaking Bad", "tv", Status.PLANNING)
        self._add("Stranger Things S1", "season", Status.PLANNING)
        self._add("Witcher", "game", Status.IN_PROGRESS)
        self._add("Matrix", "movie", Status.COMPLETED)
        self._add("Lord of the Rings", "book", Status.DROPPED)
        self._add("Death Note", "anime", Status.PAUSED)

        self.assertEqual(
            self._layout(),
            [
                ("In progress", "in-progress", 1, [("game", ["Witcher"])]),
                (
                    "Planning",
                    "planning",
                    5,
                    [
                        ("tv", ["Breaking Bad"]),
                        ("season", ["Stranger Things S1"]),
                        ("movie", ["Inception"]),
                        ("game", ["Zelda"]),
                        ("book", ["Gatsby"]),
                    ],
                ),
                ("Completed", "completed", 1, [("movie", ["Matrix"])]),
                ("Paused", "paused", 1, [("anime", ["Death Note"])]),
                ("Dropped", "dropped", 1, [("book", ["Lord of the Rings"])]),
            ],
        )

    def test_counts_add_up_across_media_types(self):
        """A section's count is every item in it, whatever its type."""
        self._add("Inception", "movie", Status.COMPLETED)
        self._add("Matrix", "movie", Status.COMPLETED)
        self._add("Witcher", "game", Status.COMPLETED)

        completed = self._layout()[2]

        self.assertEqual(
            completed,
            (
                "Completed",
                "completed",
                3,
                [("movie", ["Matrix", "Inception"]), ("game", ["Witcher"])],
            ),
        )

    def test_empty_group_lists_every_status_empty(self):
        """With nothing in the group, every status is there with no types."""
        self.assertEqual(
            self._layout(),
            [
                ("In progress", "in-progress", 0, []),
                ("Planning", "planning", 0, []),
                ("Completed", "completed", 0, []),
                ("Paused", "paused", 0, []),
                ("Dropped", "dropped", 0, []),
            ],
        )
