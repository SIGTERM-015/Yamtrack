from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import TV, Episode, Item, Movie, Season, Status
from groups.models import Group, GroupItem, GroupMembership, GroupOrigin
from groups.services import get_group_progress, resolve_group_context

User = get_user_model()


class GroupProgressServiceTest(TestCase):
    """Test the group progress service."""

    def setUp(self):
        """Set up test data."""
        self.user1 = User.objects.create(username="user1")
        self.user2 = User.objects.create(username="user2")
        self.group = Group.objects.create(name="My Group", owner=self.user1)
        GroupMembership.objects.create(group=self.group, user=self.user1)
        GroupMembership.objects.create(group=self.group, user=self.user2)

        self.movie_item = Item.objects.create(
            media_id="m1", title="Movie 1", media_type="movie", source="tmdb"
        )
        self.tv_item = Item.objects.create(
            media_id="t1", title="TV 1", media_type="tv", source="tmdb"
        )

        GroupItem.objects.create(
            group=self.group, item=self.movie_item, added_by=self.user1
        )
        GroupItem.objects.create(
            group=self.group, item=self.tv_item, added_by=self.user1
        )

    def test_progress_movie(self):
        """Test group progress aggregation for movies."""
        Movie.objects.bulk_create(
            [
                Movie(
                    user=self.user1,
                    item=self.movie_item,
                    status=Status.COMPLETED,
                    progress=1,
                ),
                Movie(
                    user=self.user2,
                    item=self.movie_item,
                    status=Status.IN_PROGRESS,
                    progress=0,
                ),
            ]
        )

        res = get_group_progress(self.group)
        self.assertEqual(res[self.movie_item.id]["completed_count"], 1)
        self.assertEqual(
            res[self.movie_item.id]["members"][self.user1.id]["status"],
            Status.COMPLETED.value,
        )
        self.assertEqual(
            res[self.movie_item.id]["members"][self.user1.id]["progress"], 1
        )
        self.assertEqual(
            res[self.movie_item.id]["members"][self.user2.id]["status"],
            Status.IN_PROGRESS.value,
        )

    def test_progress_tv(self):
        """Test group progress aggregation for tv shows."""
        TV.objects.bulk_create(
            [
                TV(user=self.user1, item=self.tv_item, status=Status.COMPLETED),
                TV(user=self.user2, item=self.tv_item, status=Status.IN_PROGRESS),
            ]
        )
        tv1 = TV.objects.get(user=self.user1, item=self.tv_item)
        tv2 = TV.objects.get(user=self.user2, item=self.tv_item)

        season_item = Item.objects.create(
            media_id="s1",
            title="Season 1",
            media_type="season",
            season_number=1,
            source="tmdb",
        )
        Season.objects.bulk_create(
            [
                Season(user=self.user1, item=season_item, related_tv=tv1),
                Season(user=self.user2, item=season_item, related_tv=tv2),
            ]
        )
        s1 = Season.objects.get(user=self.user1, item=season_item, related_tv=tv1)
        s2 = Season.objects.get(user=self.user2, item=season_item, related_tv=tv2)

        ep1_item = Item.objects.create(
            media_id="e1",
            title="Ep 1",
            media_type="episode",
            source="tmdb",
            season_number=1,
            episode_number=1,
        )
        ep2_item = Item.objects.create(
            media_id="e2",
            title="Ep 2",
            media_type="episode",
            source="tmdb",
            season_number=1,
            episode_number=2,
        )

        Episode.objects.bulk_create(
            [
                Episode(item=ep1_item, related_season=s1),
                Episode(item=ep2_item, related_season=s1),
                Episode(item=ep1_item, related_season=s2),
            ]
        )

        res = get_group_progress(self.group)
        self.assertEqual(res[self.tv_item.id]["completed_count"], 1)
        self.assertEqual(res[self.tv_item.id]["members"][self.user1.id]["progress"], 2)
        self.assertEqual(res[self.tv_item.id]["members"][self.user2.id]["progress"], 1)

    def test_progress_tv_season_0_ignored(self):
        """Test tv show progress ignores season 0."""
        TV.objects.bulk_create(
            [TV(user=self.user1, item=self.tv_item, status=Status.COMPLETED)]
        )
        tv1 = TV.objects.get(user=self.user1, item=self.tv_item)

        season_0_item = Item.objects.create(
            media_id="s0",
            title="Season 0",
            media_type="season",
            season_number=0,
            source="tmdb",
        )
        Season.objects.bulk_create(
            [Season(user=self.user1, item=season_0_item, related_tv=tv1)]
        )
        s0 = Season.objects.get(user=self.user1, item=season_0_item, related_tv=tv1)

        ep1_item = Item.objects.create(
            media_id="e1",
            title="Ep 1",
            media_type="episode",
            source="tmdb",
            season_number=0,
            episode_number=1,
        )
        Episode.objects.bulk_create([Episode(item=ep1_item, related_season=s0)])

        res = get_group_progress(self.group)
        self.assertEqual(res[self.tv_item.id]["members"][self.user1.id]["progress"], 0)

    def test_query_efficiency(self):
        """Test query efficiency with assertNumQueries."""
        # We expect exactly 4 queries:
        # 1. Fetch group_items
        # 2. Fetch members
        # 3. Fetch movie tracking data
        # 4. Fetch tv tracking data
        with self.assertNumQueries(4):
            get_group_progress(self.group)

    def test_missing_user_progress(self):
        """Test missing progress reflects properly."""
        # If user2 hasn't started the movie, it should reflect correctly
        Movie.objects.bulk_create(
            [
                Movie(
                    user=self.user1,
                    item=self.movie_item,
                    status=Status.COMPLETED,
                    progress=1,
                )
            ]
        )

        res = get_group_progress(self.group)
        self.assertEqual(
            res[self.movie_item.id]["members"][self.user1.id]["status"],
            Status.COMPLETED.value,
        )

        # User 2 hasn't tracked anything for movie_item
        self.assertIsNone(res[self.movie_item.id]["members"][self.user2.id]["status"])
        self.assertEqual(
            res[self.movie_item.id]["members"][self.user2.id]["progress"], 0
        )


class ResolveGroupContextTest(TestCase):
    """Test the provisional group-context resolution policy (E10.3)."""

    def setUp(self):
        """Set up two members of a group that tracks an item."""
        self.user1 = User.objects.create(username="user1")
        self.user2 = User.objects.create(username="user2")
        self.group = Group.objects.create(name="My Group", owner=self.user1)
        GroupMembership.objects.create(group=self.group, user=self.user1)
        GroupMembership.objects.create(group=self.group, user=self.user2)

        self.item = Item.objects.create(
            media_id="m1", title="Movie 1", media_type="movie", source="tmdb"
        )
        GroupItem.objects.create(group=self.group, item=self.item, added_by=self.user1)
        # user1 owns the item in their profile via the group (still attached).
        GroupOrigin.objects.create(user=self.user1, item=self.item, group=self.group)

    def test_no_group_returns_ask_with_no_groups(self):
        """A user with no group link resolves to ask with no groups."""
        lonely = User.objects.create(username="lonely")

        result = resolve_group_context(lonely, self.item)

        self.assertEqual(result, {"groups": [], "policy": "ask"})

    def test_single_holder_returns_ask(self):
        """Only one active member holds the item, so the context is ambiguous."""
        result = resolve_group_context(self.user1, self.item)

        self.assertEqual(result["groups"], [self.group.id])
        self.assertEqual(result["policy"], "ask")

    def test_multiple_holders_returns_assume(self):
        """A second active member holding the item makes the context assumable."""
        GroupOrigin.objects.create(user=self.user2, item=self.item, group=self.group)

        result = resolve_group_context(self.user1, self.item)

        self.assertEqual(result["groups"], [self.group.id])
        self.assertEqual(result["policy"], "assume")

    def test_inactive_holder_does_not_trigger_assume(self):
        """Inactive members are not counted as active holders."""
        self.user2.is_active = False
        self.user2.save()
        GroupOrigin.objects.create(user=self.user2, item=self.item, group=self.group)

        result = resolve_group_context(self.user1, self.item)

        self.assertEqual(result["policy"], "ask")

    def test_detached_origin_is_ignored(self):
        """A detached origin is independent user data, not a group holder."""
        GroupOrigin.objects.create(
            user=self.user2, item=self.item, group=self.group, detached=True
        )

        result = resolve_group_context(self.user1, self.item)

        self.assertEqual(result["policy"], "ask")
