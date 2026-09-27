"""S4: episode-based propagation of group-watched TV episodes to members."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import TV, Episode, Item, Season, Status
from groups.models import Group, GroupEpisodeWatch, GroupItem, GroupMembership
from groups.services import episodes_up_to, mark_group_episodes_watched

User = get_user_model()


class GroupEpisodePropagationTest(TestCase):
    """Group-watched episodes merge into members' personal TV records."""

    def setUp(self):
        """Create a group with two members and one TV item."""
        patcher = patch("app.models.providers.services.get_media_metadata")
        self.mock_metadata = patcher.start()
        # 20 total regular-season episodes; group only completes when it
        # reaches this count (best-effort completion, design §2.3).
        self.mock_metadata.return_value = {"max_progress": 20}
        self.addCleanup(patcher.stop)

        self.alice = User.objects.create(username="alice")
        self.bob = User.objects.create(username="bob")
        self.group = Group.objects.create(name="Group", owner=self.alice)
        GroupMembership.objects.create(group=self.group, user=self.alice)
        GroupMembership.objects.create(group=self.group, user=self.bob)

        self.tv_item = Item.objects.create(
            media_id="t1",
            title="Show 1",
            media_type="tv",
            source="tmdb",
        )
        self.group_item = GroupItem.objects.create(
            group=self.group,
            item=self.tv_item,
            added_by=self.alice,
        )

    def _season(self, user, season_number, status=Status.IN_PROGRESS):
        """Create a personal Season (and its TV) for a user, bypassing hooks."""
        season_item, _ = Item.objects.get_or_create(
            media_id=self.tv_item.media_id,
            source=self.tv_item.source,
            media_type="season",
            season_number=season_number,
            defaults={"title": self.tv_item.title, "image": "x"},
        )
        tv, _ = TV.objects.get_or_create(
            item=self.tv_item,
            user=user,
            defaults={"status": Status.IN_PROGRESS.value},
        )
        season = Season(
            item=season_item,
            user=user,
            related_tv=tv,
            status=status.value if hasattr(status, "value") else status,
            notes="",
        )
        Season.save_base(season)
        return tv, season

    def _episode_item(self, season_number, episode_number):
        item, _ = Item.objects.get_or_create(
            media_id=self.tv_item.media_id,
            source=self.tv_item.source,
            media_type="episode",
            season_number=season_number,
            episode_number=episode_number,
            defaults={"title": self.tv_item.title, "image": "x"},
        )
        return item

    def test_marking_episode_creates_it_for_every_participant(self):
        """Marking S1E1 by the group creates an Episode for each member."""
        mark_group_episodes_watched(self.group_item, [(1, 1)])

        for user in (self.alice, self.bob):
            tv = TV.objects.get(item=self.tv_item, user=user)
            season = Season.objects.get(related_tv=tv, item__season_number=1)
            self.assertTrue(
                Episode.objects.filter(
                    related_season=season,
                    item__season_number=1,
                    item__episode_number=1,
                ).exists(),
            )

    def test_member_who_already_watched_episode_is_not_duplicated(self):
        """A member who already has S1E1 gets no duplicate row."""
        _, season = self._season(self.alice, 1)
        episode_item = self._episode_item(1, 1)
        # bulk_create bypasses Episode.save()'s finale-detection metadata call.
        Episode.objects.bulk_create(
            [Episode(related_season=season, item=episode_item, end_date=None)],
        )

        mark_group_episodes_watched(self.group_item, [(1, 1)])

        self.assertEqual(
            Episode.objects.filter(related_season=season, item=episode_item).count(),
            1,
        )

    def test_member_ahead_keeps_extra_episode_and_gains_new_one(self):
        """Member with S1E3 keeps it; group marking S1E2 adds S1E2 too."""
        _, season = self._season(self.alice, 1)
        ep3 = self._episode_item(1, 3)
        Episode.objects.bulk_create(
            [Episode(related_season=season, item=ep3, end_date=None)],
        )

        mark_group_episodes_watched(self.group_item, [(1, 2)])

        episode_numbers = set(
            Episode.objects.filter(related_season=season).values_list(
                "item__episode_number",
                flat=True,
            ),
        )
        self.assertEqual(episode_numbers, {2, 3})

    def test_completed_member_is_not_reopened(self):
        """A Completed member's TV/season are untouched by group episodes."""
        tv, season = self._season(self.alice, 1, status=Status.COMPLETED)
        TV.objects.filter(pk=tv.pk).update(status=Status.COMPLETED.value)

        mark_group_episodes_watched(self.group_item, [(1, 5)])

        self.assertFalse(
            Episode.objects.filter(
                related_season=season,
                item__episode_number=5,
            ).exists(),
        )
        tv.refresh_from_db()
        self.assertEqual(tv.status, Status.COMPLETED.value)

    def test_dropped_member_is_skipped(self):
        """A Dropped member's TV is left untouched entirely."""
        tv, season = self._season(self.bob, 1, status=Status.DROPPED)
        TV.objects.filter(pk=tv.pk).update(status=Status.DROPPED.value)

        mark_group_episodes_watched(self.group_item, [(1, 1)])

        self.assertFalse(
            Episode.objects.filter(related_season=season).exists(),
        )

    def test_paused_member_gets_episodes_but_keeps_paused_status(self):
        """A Paused member receives the episode without leaving Paused."""
        tv, season = self._season(self.bob, 1, status=Status.PAUSED)
        TV.objects.filter(pk=tv.pk).update(status=Status.PAUSED.value)
        Season.objects.filter(pk=season.pk).update(status=Status.PAUSED.value)

        mark_group_episodes_watched(self.group_item, [(1, 1)])

        self.assertTrue(
            Episode.objects.filter(
                related_season=season,
                item__episode_number=1,
            ).exists(),
        )
        tv.refresh_from_db()
        season.refresh_from_db()
        self.assertEqual(tv.status, Status.PAUSED.value)
        self.assertEqual(season.status, Status.PAUSED.value)

    def test_season_zero_excluded_from_group_progress(self):
        """Season 0 (specials) episodes don't count towards group progress."""
        mark_group_episodes_watched(self.group_item, [(0, 1), (1, 1)])

        self.group_item.refresh_from_db()
        self.assertEqual(self.group_item.progress, 1)

    def test_group_status_advances_to_in_progress(self):
        """Marking the first episode moves the group from Planning."""
        mark_group_episodes_watched(self.group_item, [(1, 1)])

        self.group_item.refresh_from_db()
        self.assertEqual(self.group_item.status, Status.IN_PROGRESS.value)

    def test_group_completes_when_progress_reaches_max(self):
        """The group reaches Completed once its progress hits max_progress."""
        episodes = episodes_up_to(1, 20)

        mark_group_episodes_watched(self.group_item, episodes)

        self.group_item.refresh_from_db()
        self.assertEqual(self.group_item.status, Status.COMPLETED.value)
        self.assertEqual(self.group_item.progress, 20)

    def test_group_completion_propagates_to_non_terminal_members(self):
        """A group that completes marks in-progress members' TV Completed."""
        self._season(self.alice, 1)

        mark_group_episodes_watched(self.group_item, episodes_up_to(1, 20))

        tv = TV.objects.get(item=self.tv_item, user=self.alice)
        self.assertEqual(tv.status, Status.COMPLETED.value)

    def test_second_identical_call_is_idempotent(self):
        """Re-running the same episodes changes no personal record."""
        mark_group_episodes_watched(self.group_item, [(1, 1), (1, 2)])
        before = list(
            Episode.objects.order_by(
                "related_season_id", "item__episode_number"
            ).values("related_season_id", "item__episode_number"),
        )
        watch_count_before = GroupEpisodeWatch.objects.count()

        mark_group_episodes_watched(self.group_item, [(1, 1), (1, 2)])

        after = list(
            Episode.objects.order_by(
                "related_season_id", "item__episode_number"
            ).values("related_season_id", "item__episode_number"),
        )
        self.assertEqual(before, after)
        self.assertEqual(GroupEpisodeWatch.objects.count(), watch_count_before)

    def test_participants_limit_propagation(self):
        """Only listed participants receive the episode."""
        mark_group_episodes_watched(
            self.group_item,
            [(1, 1)],
            participants=[self.alice.id],
        )

        self.assertTrue(TV.objects.filter(item=self.tv_item, user=self.alice).exists())
        self.assertFalse(TV.objects.filter(item=self.tv_item, user=self.bob).exists())

    def test_episodes_up_to_helper(self):
        """The 'up to episode N' shortcut expands to a contiguous list."""
        self.assertEqual(
            episodes_up_to(2, 3),
            [(2, 1), (2, 2), (2, 3)],
        )
