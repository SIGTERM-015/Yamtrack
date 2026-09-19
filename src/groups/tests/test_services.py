from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import TV, Episode, Item, Movie, Season, Status
from groups.models import Group, GroupItem, GroupMembership, GroupOrigin
from groups.services import (
    add_item_to_group,
    get_group_comparison,
    get_group_genre_stats,
    get_group_progress,
    resolve_group_context,
)

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


class AddItemToGroupPreservesPersonalTest(TestCase):
    """Alta en grupo: crear pendientes, preservar registros existentes."""

    def setUp(self):
        """Create a group with two tracked members and one fresh member."""
        self.user1 = User.objects.create(username="gadd_user1")
        self.user2 = User.objects.create(username="gadd_user2")
        self.user3 = User.objects.create(username="gadd_user3")
        self.group = Group.objects.create(name="G", owner=self.user1)
        for u in (self.user1, self.user2, self.user3):
            GroupMembership.objects.create(group=self.group, user=u)
        self.item = Item.objects.create(
            media_id="gadd_m1", title="M1", media_type="movie", source="tmdb"
        )

    def test_completed_and_in_progress_kept_new_member_pending(self):
        """Existing entries keep data; only the fresh member gets Planning."""
        Movie.objects.bulk_create(
            [
                Movie(
                    user=self.user1,
                    item=self.item,
                    status=Status.COMPLETED,
                    progress=1,
                    score=8,
                    notes="mine",
                ),
                Movie(
                    user=self.user2,
                    item=self.item,
                    status=Status.IN_PROGRESS,
                    progress=30,
                    score=7,
                    notes="wip",
                ),
            ]
        )

        add_item_to_group(self.group, self.item, added_by=self.user1)

        done = Movie.objects.get(user=self.user1, item=self.item)
        self.assertEqual(done.status, Status.COMPLETED.value)
        self.assertEqual(done.progress, 1)
        self.assertEqual(done.score, 8)
        self.assertEqual(done.notes, "mine")

        wip = Movie.objects.get(user=self.user2, item=self.item)
        self.assertEqual(wip.status, Status.IN_PROGRESS.value)
        self.assertEqual(wip.progress, 30)
        self.assertEqual(wip.score, 7)
        self.assertEqual(wip.notes, "wip")

        fresh = Movie.objects.get(user=self.user3, item=self.item)
        self.assertEqual(fresh.status, Status.PLANNING.value)


class GroupComparisonServiceTest(TestCase):
    """Test the group ratings comparison service."""

    def setUp(self):
        """Set up test data."""
        patcher = patch("app.models.providers.services.get_media_metadata")
        self.mock_get_media_metadata = patcher.start()
        self.mock_get_media_metadata.return_value = {"max_progress": 1}
        self.addCleanup(patcher.stop)

        self.user1 = User.objects.create(username="user1")
        self.user2 = User.objects.create(username="user2")
        self.group = Group.objects.create(name="My Group", owner=self.user1)
        GroupMembership.objects.create(group=self.group, user=self.user1)
        GroupMembership.objects.create(group=self.group, user=self.user2)

        self.movie_agree = Item.objects.create(
            media_id="m1", title="Agreed Movie", media_type="movie", source="tmdb"
        )
        self.movie_disagree = Item.objects.create(
            media_id="m2", title="Divided Movie", media_type="movie", source="tmdb"
        )
        GroupItem.objects.create(
            group=self.group, item=self.movie_agree, added_by=self.user1
        )
        GroupItem.objects.create(
            group=self.group, item=self.movie_disagree, added_by=self.user1
        )

    def test_comparison_scores_average_and_difference(self):
        """Scores, combined average and difference are computed per item."""
        Movie.objects.create(
            user=self.user1,
            item=self.movie_agree,
            status=Status.COMPLETED,
            score=7,
        )
        Movie.objects.create(
            user=self.user2,
            item=self.movie_agree,
            status=Status.COMPLETED,
            score=7,
        )
        Movie.objects.create(
            user=self.user1,
            item=self.movie_disagree,
            status=Status.COMPLETED,
            score=9,
        )
        Movie.objects.create(
            user=self.user2,
            item=self.movie_disagree,
            status=Status.COMPLETED,
            score=3,
        )

        comparison = {row["item_id"]: row for row in get_group_comparison(self.group)}

        agree = comparison[self.movie_agree.id]
        self.assertEqual(agree["scores"][self.user1.id], 7.0)
        self.assertEqual(agree["scores"][self.user2.id], 7.0)
        self.assertEqual(agree["average"], 7.0)
        self.assertEqual(agree["difference"], 0.0)

        disagree = comparison[self.movie_disagree.id]
        self.assertEqual(disagree["average"], 6.0)
        self.assertEqual(disagree["difference"], 6.0)

    def test_comparison_sorted_by_discrepancy(self):
        """Items with the biggest rating gap come first."""
        Movie.objects.create(
            user=self.user1,
            item=self.movie_agree,
            status=Status.COMPLETED,
            score=7,
        )
        Movie.objects.create(
            user=self.user2,
            item=self.movie_agree,
            status=Status.COMPLETED,
            score=7,
        )
        Movie.objects.create(
            user=self.user1,
            item=self.movie_disagree,
            status=Status.COMPLETED,
            score=9,
        )
        Movie.objects.create(
            user=self.user2,
            item=self.movie_disagree,
            status=Status.COMPLETED,
            score=3,
        )

        ordered = [row["item_id"] for row in get_group_comparison(self.group)]
        self.assertEqual(ordered, [self.movie_disagree.id, self.movie_agree.id])

    def test_comparison_single_score_has_no_difference(self):
        """An item scored by only one member has no difference."""
        Movie.objects.create(
            user=self.user1,
            item=self.movie_agree,
            status=Status.COMPLETED,
            score=8,
        )

        comparison = {row["item_id"]: row for row in get_group_comparison(self.group)}
        row = comparison[self.movie_agree.id]

        self.assertEqual(row["scores"][self.user1.id], 8.0)
        self.assertIsNone(row["scores"][self.user2.id])
        self.assertEqual(row["average"], 8.0)
        self.assertIsNone(row["difference"])

    def test_comparison_never_exposes_private_notes(self):
        """Private notes must not leak through the comparison."""
        Movie.objects.create(
            user=self.user1,
            item=self.movie_agree,
            status=Status.COMPLETED,
            score=8,
            notes="secret private note",
        )

        comparison = get_group_comparison(self.group)

        for row in comparison:
            self.assertNotIn("notes", row)
        self.assertNotIn("secret private note", str(comparison))


class GroupGenreStatsServiceTest(TestCase):
    """Test the group genre statistics service."""

    def setUp(self):
        """Set up test data."""
        patcher = patch("app.models.providers.services.get_media_metadata")
        self.mock_get_media_metadata = patcher.start()
        self.mock_get_media_metadata.return_value = {"max_progress": 1}
        self.addCleanup(patcher.stop)

        self.user1 = User.objects.create(username="user1")
        self.user2 = User.objects.create(username="user2")
        self.group = Group.objects.create(name="My Group", owner=self.user1)
        GroupMembership.objects.create(group=self.group, user=self.user1)
        GroupMembership.objects.create(group=self.group, user=self.user2)

        self.action = Item.objects.create(
            media_id="g1", title="Pure Action", media_type="movie", source="tmdb"
        )
        self.thriller = Item.objects.create(
            media_id="g2", title="Pure Thriller", media_type="movie", source="tmdb"
        )
        self.mixed = Item.objects.create(
            media_id="g3", title="Action Thriller", media_type="movie", source="tmdb"
        )
        for item in (self.action, self.thriller, self.mixed):
            GroupItem.objects.create(group=self.group, item=item, added_by=self.user1)

        self.item_genres = {
            self.action.id: ["Action"],
            self.thriller.id: ["Thriller"],
            self.mixed.id: ["Action", "Thriller"],
        }

    def _getter(self, item):
        return self.item_genres.get(item.id, [])

    def test_genre_stats_averages_per_member(self):
        """Average per genre and member, volume and agreement ranking."""
        for item, user1_score, user2_score in (
            (self.action, 8, 6),
            (self.thriller, 4, 8),
            (self.mixed, 6, 6),
        ):
            Movie.objects.create(
                user=self.user1,
                item=item,
                status=Status.COMPLETED,
                score=user1_score,
            )
            Movie.objects.create(
                user=self.user2,
                item=item,
                status=Status.COMPLETED,
                score=user2_score,
            )

        stats = get_group_genre_stats(self.group, genre_getter=self._getter)
        genres = {row["genre"]: row for row in stats["genres"]}

        action = genres["Action"]
        self.assertEqual(action["members"][self.user1.id]["average"], 7.0)
        self.assertEqual(action["members"][self.user1.id]["count"], 2)
        self.assertEqual(action["members"][self.user2.id]["average"], 6.0)
        self.assertEqual(action["average"], 6.5)
        self.assertEqual(action["difference"], 1.0)
        self.assertEqual(action["count"], 4)

        thriller = genres["Thriller"]
        self.assertEqual(thriller["members"][self.user1.id]["average"], 5.0)
        self.assertEqual(thriller["members"][self.user2.id]["average"], 7.0)
        self.assertEqual(thriller["average"], 6.0)
        self.assertEqual(thriller["difference"], 2.0)

        self.assertEqual(stats["volume"][self.user1.id], 3)
        self.assertEqual(stats["volume"][self.user2.id], 3)

        # Same volume, so genres fall back to alphabetical order.
        self.assertEqual(
            [row["genre"] for row in stats["genres"]], ["Action", "Thriller"]
        )
        self.assertEqual(stats["agreements"][0]["genre"], "Action")
        self.assertEqual(stats["disagreements"][0]["genre"], "Thriller")

    def test_genre_stats_missing_member_score(self):
        """A genre rated by only one member has no difference."""
        Movie.objects.create(
            user=self.user1,
            item=self.action,
            status=Status.COMPLETED,
            score=5,
        )
        Movie.objects.create(
            user=self.user2,
            item=self.thriller,
            status=Status.COMPLETED,
            score=9,
        )

        stats = get_group_genre_stats(self.group, genre_getter=self._getter)
        genres = {row["genre"]: row for row in stats["genres"]}

        self.assertEqual(genres["Action"]["average"], 5.0)
        self.assertIsNone(genres["Action"]["members"][self.user2.id]["average"])
        self.assertEqual(genres["Action"]["members"][self.user2.id]["count"], 0)
        self.assertIsNone(genres["Action"]["difference"])

        self.assertEqual(stats["agreements"], [])
        self.assertEqual(stats["disagreements"], [])

    def test_genre_stats_ignores_unrated_and_untracked_volumes(self):
        """Only scored items count toward volume and genre aggregates."""
        Movie.objects.create(
            user=self.user1,
            item=self.action,
            status=Status.COMPLETED,
            score=8,
        )
        Movie.objects.create(
            user=self.user1,
            item=self.mixed,
            status=Status.IN_PROGRESS,
            score=None,
        )

        stats = get_group_genre_stats(self.group, genre_getter=self._getter)
        genres = {row["genre"]: row for row in stats["genres"]}

        self.assertEqual(stats["volume"][self.user1.id], 1)
        self.assertEqual(stats["volume"][self.user2.id], 0)
        self.assertEqual(genres["Action"]["count"], 1)
        self.assertNotIn("Thriller", genres)

    def test_genre_stats_default_getter_without_genre_model(self):
        """Without a getter and no genres relation, no genres are returned."""
        Movie.objects.create(
            user=self.user1,
            item=self.action,
            status=Status.COMPLETED,
            score=8,
        )

        stats = get_group_genre_stats(self.group)

        self.assertEqual(stats["genres"], [])
        self.assertEqual(stats["volume"][self.user1.id], 1)
