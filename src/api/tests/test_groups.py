"""Group endpoints of the REST API (E11.2-E11.4)."""

from unittest.mock import patch

from django.contrib.auth import get_user_model

from app.models import TV, Episode, Item, MediaTypes, Movie, Sources, Status
from groups.discards import discard_group_item, is_group_discarded
from groups.models import Group, GroupItem, GroupMembership
from users.models import ApiToken, ApiTokenScope

from .base import YamtrackApiTestCase


class GroupApiTestCase(YamtrackApiTestCase):
    """A two-member group (user1 owner, user2) plus an outsider."""

    def setUp(self):
        """Create the group, an outsider and a movie and show in the group."""
        super().setUp()
        self.outsider = get_user_model().objects.create_user(username="outsider")
        self.outsider_headers = {"HTTP_X_API_KEY": self.outsider.token}

        self.group = Group.objects.create(name="Couple", owner=self.user1)
        GroupMembership.objects.create(group=self.group, user=self.user1)
        GroupMembership.objects.create(group=self.group, user=self.user2)

        self.movie = Item.objects.create(
            media_id="9001",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Group Movie",
            image="https://example.com/m.jpg",
        )
        self.show = Item.objects.create(
            media_id="9002",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Group Show",
            image="https://example.com/s.jpg",
        )
        self.movie_gi = GroupItem.objects.create(
            group=self.group, item=self.movie, added_by=self.user1
        )
        self.show_gi = GroupItem.objects.create(
            group=self.group, item=self.show, added_by=self.user1
        )

    def movie_args(self):
        """URL args for the group's movie."""
        return (self.group.id, "movie", "tmdb", "9001")

    def show_args(self):
        """URL args for the group's show."""
        return (self.group.id, "tv", "tmdb", "9002")


class GroupReadTests(GroupApiTestCase):
    """E11.2: listing groups, detail and items."""

    def test_lists_only_my_groups(self):
        """A user sees the groups they belong to, not others."""
        Group.objects.create(name="Not mine", owner=self.outsider)
        response = self.call_api("get", "api_groups", headers=self.auth_headers)
        self.assertEqual(response.status_code, 200)
        names = [row["name"] for row in response.json()["results"]]
        self.assertEqual(names, ["Couple"])
        self.assertEqual(response.json()["results"][0]["member_count"], 2)

    def test_detail_includes_members(self):
        """Group detail lists members and flags the owner."""
        response = self.call_api(
            "get", "api_group_detail", args=(self.group.id,), headers=self.auth_headers
        )
        members = {m["username"]: m["is_owner"] for m in response.json()["members"]}
        self.assertEqual(members, {"api-test-user1": True, "api-test-user2": False})

    def test_non_member_gets_404_everywhere(self):
        """Outsiders can't tell the group exists, read or write."""
        cases = [
            ("get", "api_group_detail", (self.group.id,), None),
            ("get", "api_group_items", (self.group.id,), None),
            ("get", "api_group_item_detail", self.movie_args(), None),
            (
                "patch",
                "api_group_item_detail",
                self.movie_args(),
                {"status": "Completed"},
            ),
            ("delete", "api_group_item_detail", self.movie_args(), None),
            ("get", "api_group_discards", (self.group.id,), None),
        ]
        for method, name, args, payload in cases:
            with self.subTest(method=method, name=name):
                response = self.call_api(
                    method,
                    name,
                    args=args,
                    payload=payload,
                    headers=self.outsider_headers,
                )
                self.assertEqual(response.status_code, 404)
        self.assertTrue(GroupItem.objects.filter(pk=self.movie_gi.pk).exists())

    def test_items_report_group_state_and_member_state(self):
        """Items show the group's own status plus each member's record."""
        Movie.objects.create(
            item=self.movie, user=self.user2, status=Status.COMPLETED.value
        )
        response = self.call_api(
            "get", "api_group_items", args=(self.group.id,), headers=self.auth_headers
        )
        rows = {row["item"]["title"]: row for row in response.json()["results"]}
        movie_row = rows["Group Movie"]
        self.assertEqual(movie_row["status"], Status.PLANNING.value)
        self.assertEqual(movie_row["completed_count"], 1)
        self.assertEqual(movie_row["total_members"], 2)
        by_user = {m["username"]: m["status"] for m in movie_row["members"]}
        self.assertEqual(by_user["api-test-user2"], Status.COMPLETED.value)
        self.assertIsNone(by_user["api-test-user1"])

    def test_items_tab_filter(self):
        """``tab`` buckets by the group's own status; bad tabs are rejected."""
        self.movie_gi.status = Status.IN_PROGRESS.value
        self.movie_gi.save()
        watching = self.call_api(
            "get",
            "api_group_items",
            args=(self.group.id,),
            params={"tab": "watching"},
            headers=self.auth_headers,
        )
        self.assertEqual(
            [r["item"]["title"] for r in watching.json()["results"]], ["Group Movie"]
        )
        bad = self.call_api(
            "get",
            "api_group_items",
            args=(self.group.id,),
            params={"tab": "nope"},
            headers=self.auth_headers,
        )
        self.assertEqual(bad.status_code, 400)


class GroupWriteTests(GroupApiTestCase):
    """E11.3: add, remove, status/progress and episodes, with UI rules."""

    @patch("lists.views.services.get_media_metadata")
    def test_add_item_creates_planning_only_for_members_without_it(self, mock_meta):
        """Adding keeps existing personal records and creates Planning otherwise."""
        mock_meta.return_value = {
            "title": "New Movie",
            "image": "https://x/y.jpg",
            "max_progress": 1,
        }
        new_item = Item.objects.create(
            media_id="9010", source="tmdb", media_type="movie", title="New Movie"
        )
        Movie.objects.create(
            item=new_item, user=self.user2, status=Status.COMPLETED.value, score=9
        )

        response = self.call_api(
            "post",
            "api_group_items",
            args=(self.group.id,),
            payload={"media_type": "movie", "source": "tmdb", "media_id": "9010"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            Movie.objects.get(item=new_item, user=self.user1).status,
            Status.PLANNING.value,
        )
        kept = Movie.objects.get(item=new_item, user=self.user2)
        self.assertEqual((kept.status, kept.score), (Status.COMPLETED.value, 9))

        again = self.call_api(
            "post",
            "api_group_items",
            args=(self.group.id,),
            payload={"media_type": "movie", "source": "tmdb", "media_id": "9010"},
            headers=self.auth_headers,
        )
        self.assertEqual(again.status_code, 200)

    def test_add_validates_identifier(self):
        """Missing fields, seasons/episodes and bad sources are rejected."""
        for payload in (
            {"media_type": "movie"},
            {"media_type": "season", "source": "tmdb", "media_id": "1"},
            {"media_type": "movie", "source": "mal", "media_id": "1"},
        ):
            with self.subTest(payload=payload):
                response = self.call_api(
                    "post",
                    "api_group_items",
                    args=(self.group.id,),
                    payload=payload,
                    headers=self.auth_headers,
                )
                self.assertEqual(response.status_code, 400)

    def test_remove_keeps_personal_records(self):
        """DELETE unlinks from the group only."""
        Movie.objects.create(item=self.movie, user=self.user2, status="Completed")
        response = self.call_api(
            "delete",
            "api_group_item_detail",
            args=self.movie_args(),
            headers=self.auth_headers2,
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(GroupItem.objects.filter(pk=self.movie_gi.pk).exists())
        self.assertTrue(Movie.objects.filter(item=self.movie, user=self.user2).exists())

    def test_status_propagates_monotonically(self):
        """Completing never reopens or reduces; Dropped members are skipped."""
        Movie.objects.create(item=self.movie, user=self.user2, status="Dropped")
        response = self.call_api(
            "patch",
            "api_group_item_detail",
            args=self.movie_args(),
            payload={"status": "Completed"},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "Completed")
        self.assertEqual(
            Movie.objects.get(item=self.movie, user=self.user1).status, "Completed"
        )
        self.assertEqual(
            Movie.objects.get(item=self.movie, user=self.user2).status, "Dropped"
        )

        back = self.call_api(
            "patch",
            "api_group_item_detail",
            args=self.movie_args(),
            payload={"status": "In progress"},
            headers=self.auth_headers,
        )
        self.assertEqual(back.json()["status"], "In progress")
        self.assertEqual(
            Movie.objects.get(item=self.movie, user=self.user1).status, "Completed"
        )

    def test_participants_limit_propagation(self):
        """Only the chosen participants' personal records change."""
        self.call_api(
            "patch",
            "api_group_item_detail",
            args=self.movie_args(),
            payload={"status": "In progress", "participants": [self.user1.id]},
            headers=self.auth_headers,
        )
        self.assertTrue(Movie.objects.filter(item=self.movie, user=self.user1).exists())
        self.assertFalse(
            Movie.objects.filter(item=self.movie, user=self.user2).exists()
        )

    def test_participants_must_be_members(self):
        """A non-member participant aborts before any write."""
        response = self.call_api(
            "patch",
            "api_group_item_detail",
            args=self.movie_args(),
            payload={"status": "Completed", "participants": [self.outsider.id]},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 400)
        self.movie_gi.refresh_from_db()
        self.assertEqual(self.movie_gi.status, Status.PLANNING.value)

    def test_status_and_progress_validation(self):
        """Bad status, negative progress and TV progress are rejected."""
        cases = [
            (self.movie_args(), {"status": "Watching"}),
            (self.movie_args(), {"progress": -1}),
            (self.movie_args(), {"progress": "3"}),
            (self.show_args(), {"progress": 3}),
        ]
        for args, payload in cases:
            with self.subTest(payload=payload):
                response = self.call_api(
                    "patch",
                    "api_group_item_detail",
                    args=args,
                    payload=payload,
                    headers=self.auth_headers,
                )
                self.assertEqual(response.status_code, 400)

    @patch("groups.services._get_tv_max_progress", return_value=None)
    def test_mark_episodes_list_and_up_to(self, _mock_max):
        """Episodes propagate to members; up_to expands; repeats are no-ops."""
        response = self.call_api(
            "post",
            "api_group_item_episodes",
            args=self.show_args(),
            payload={"episodes": [[1, 1], [1, 3]]},
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["progress"], 2)
        self.assertEqual(response.json()["status"], "In progress")
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user2).count(), 2
        )

        up_to = self.call_api(
            "post",
            "api_group_item_episodes",
            args=self.show_args(),
            payload={"season_number": 1, "up_to_episode": 4},
            headers=self.auth_headers,
        )
        self.assertEqual(up_to.json()["progress"], 4)

        ledger = self.call_api(
            "get",
            "api_group_item_episodes",
            args=self.show_args(),
            headers=self.auth_headers,
        )
        self.assertEqual(
            [
                (e["season_number"], e["episode_number"])
                for e in ledger.json()["results"]
            ],
            [(1, 1), (1, 2), (1, 3), (1, 4)],
        )
        self.assertEqual(TV.objects.filter(item=self.show).count(), 2)

    def test_episodes_validation(self):
        """Bad bodies and non-TV items are rejected."""
        for args, payload in (
            (self.show_args(), {}),
            (self.show_args(), {"episodes": [[1]]}),
            (self.show_args(), {"episodes": [[1, 0]]}),
            (self.movie_args(), {"episodes": [[1, 1]]}),
        ):
            with self.subTest(payload=payload, args=args):
                response = self.call_api(
                    "post",
                    "api_group_item_episodes",
                    args=args,
                    payload=payload,
                    headers=self.auth_headers,
                )
                self.assertEqual(response.status_code, 400)

    def test_read_only_token_cannot_write_groups(self):
        """Group writes need a read-write token; reads work with read-only."""
        _, raw = ApiToken.create_for(self.user1, "ro", ApiTokenScope.READ.value)
        headers = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}
        read = self.call_api(
            "get", "api_group_items", args=(self.group.id,), headers=headers
        )
        write = self.call_api(
            "patch",
            "api_group_item_detail",
            args=self.movie_args(),
            payload={"status": "Completed"},
            headers=headers,
        )
        self.assertEqual(read.status_code, 200)
        self.assertEqual(write.status_code, 403)
        self.movie_gi.refresh_from_db()
        self.assertEqual(self.movie_gi.status, Status.PLANNING.value)


class GroupDiscardTests(GroupApiTestCase):
    """E11.4: shared group discards."""

    def test_any_member_discards_and_another_restores(self):
        """Discard by one member, list it, restore by the other."""
        added = self.call_api(
            "post",
            "api_group_discards",
            args=(self.group.id,),
            payload={"media_type": "movie", "source": "tmdb", "media_id": "9001"},
            headers=self.auth_headers,
        )
        self.assertEqual(added.status_code, 201)
        self.assertTrue(is_group_discarded(self.group, self.movie))

        listing = self.call_api(
            "get",
            "api_group_discards",
            args=(self.group.id,),
            headers=self.auth_headers2,
        )
        self.assertEqual(listing.json()["results"][0]["discarded_by"], "api-test-user1")

        restored = self.call_api(
            "delete",
            "api_group_discard_detail",
            args=self.movie_args(),
            headers=self.auth_headers2,
        )
        self.assertEqual(restored.status_code, 204)
        self.assertFalse(is_group_discarded(self.group, self.movie))

        missing = self.call_api(
            "delete",
            "api_group_discard_detail",
            args=self.movie_args(),
            headers=self.auth_headers2,
        )
        self.assertEqual(missing.status_code, 404)

    def test_group_discard_does_not_touch_personal_scope(self):
        """Group and personal discards are independent."""
        discard_group_item(self.group, self.movie, self.user1)
        response = self.call_api(
            "get",
            "api_group_discards",
            args=(self.group.id,),
            headers=self.auth_headers,
        )
        self.assertEqual(response.json()["pagination"]["total"], 1)
        self.assertFalse(self.user1.discards.exists())
