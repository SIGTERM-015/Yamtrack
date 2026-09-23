from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from app.models import Item, Status
from groups.models import Group, GroupEpisodeWatch, GroupItem

User = get_user_model()


class GroupItemProgressDefaultsTests(TestCase):
    """GroupItem initializes as Planning with zero progress."""

    def test_defaults(self):
        """A GroupItem created without progress data is Planning/0."""
        user = User.objects.create_user(username="owner", password="testpassword123")  # noqa: S106
        group = Group.objects.create(name="G", owner=user)
        item = Item.objects.create(media_id="1", source="tmdb", media_type="movie")
        group_item = GroupItem.objects.create(group=group, item=item, added_by=user)

        self.assertEqual(group_item.status, Status.PLANNING.value)
        self.assertEqual(group_item.progress, 0)


class GroupEpisodeWatchTests(TestCase):
    """GroupEpisodeWatch enforces one row per (group_item, item)."""

    def test_duplicate_watch_raises(self):
        """Duplicating (group_item, item) raises IntegrityError."""
        user = User.objects.create_user(username="owner", password="testpassword123")  # noqa: S106
        group = Group.objects.create(name="G", owner=user)
        show = Item.objects.create(media_id="1", source="tmdb", media_type="tv")
        episode = Item.objects.create(
            media_id="1",
            source="tmdb",
            media_type="episode",
            season_number=1,
            episode_number=1,
        )
        group_item = GroupItem.objects.create(group=group, item=show, added_by=user)
        GroupEpisodeWatch.objects.create(group_item=group_item, item=episode)

        with self.assertRaises(IntegrityError):
            GroupEpisodeWatch.objects.create(group_item=group_item, item=episode)
