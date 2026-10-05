"""One-click "Add to group" from media cards, with Spotify-style memory."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import TV, Item, MediaTypes, Sources, Status
from groups.models import Group, GroupItem

User = get_user_model()


class QuickAddTest(TestCase):
    """The card button adds to the only or last used group, else asks."""

    def setUp(self):
        """Create alice in one group with bob, and a TV item."""
        patcher = patch("app.models.providers.services.get_media_metadata")
        patcher.start().return_value = {"max_progress": 1, "related": {"seasons": []}}
        self.addCleanup(patcher.stop)

        self.alice = User.objects.create_user(username="alice", password="pw")  # noqa: S106
        self.bob = User.objects.create_user(username="bob", password="pw")  # noqa: S106
        self.couple = Group.objects.create(name="Couple", owner=self.alice)
        self.couple.members.add(self.alice, self.bob)
        self.item = Item.objects.create(
            media_id="101",
            title="Show",
            media_type=MediaTypes.TV.value,
            source=Sources.TMDB.value,
        )
        self.url = reverse(
            "group_quick_add", args=[Sources.TMDB.value, MediaTypes.TV.value, "101"]
        )
        self.client.login(username="alice", password="pw")  # noqa: S106

    def post(self, url=None, data=None):
        """POST like htmx does from a media card."""
        return self.client.post(
            url or self.url, data or {}, headers={"HX-Request": "true"}
        )

    def add_friends_group(self):
        """Put alice in a second group."""
        friends = Group.objects.create(name="Friends", owner=self.alice)
        friends.members.add(self.alice)
        return friends

    def in_group(self, group):
        """Whether the item is in ``group``."""
        return GroupItem.objects.filter(group=group, item=self.item).exists()

    def search_page(self):
        """Render the search results (cards) for the item."""
        results = {
            "page": 1,
            "total_pages": 1,
            "results": [
                {
                    "media_id": "101",
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "title": "Show",
                    "image": "http://example.com/x.jpg",
                },
            ],
        }
        with patch("app.views.services.search", return_value=results):
            return self.client.get(
                reverse("search"), {"q": "Show", "media_type": MediaTypes.TV.value}
            )

    def test_button_hidden_without_groups(self):
        """A user in no group sees no Add to group button on cards."""
        User.objects.create_user(username="carol", password="pw")  # noqa: S106
        self.client.login(username="carol", password="pw")  # noqa: S106

        response = self.search_page()

        self.assertContains(response, 'title="Add to custom lists"')
        self.assertNotContains(response, 'title="Add to group"')

    def test_button_shown_with_one_or_more_groups(self):
        """With one group, and with several, the card shows the button."""
        self.assertContains(self.search_page(), 'title="Add to group"', count=1)
        self.add_friends_group()
        self.assertContains(self.search_page(), 'title="Add to group"', count=1)

    def test_single_group_adds_directly(self):
        """With one group, the click adds right away and says where."""
        response = self.post()

        self.assertTrue(self.in_group(self.couple))
        self.assertEqual(
            sorted(TV.objects.filter(item=self.item).values_list("status", flat=True)),
            [Status.PLANNING.value, Status.PLANNING.value],
        )
        self.assertContains(response, "Added to Couple")
        self.assertNotContains(response, "Change")

    def test_already_in_group_says_so(self):
        """Adding twice reports the item was already there."""
        self.post()

        response = self.post()

        self.assertContains(response, "Already in Couple")
        self.assertEqual(GroupItem.objects.filter(item=self.item).count(), 1)

    def test_several_groups_ask_first_then_reuse_last(self):
        """First use with N groups asks; later clicks reuse the chosen group."""
        friends = self.add_friends_group()

        picker = self.post()
        self.assertTemplateUsed(picker, "groups/components/fill_groups.html")
        self.assertContains(picker, "Couple")
        self.assertContains(picker, "Friends")
        self.assertFalse(GroupItem.objects.filter(item=self.item).exists())

        chosen = self.post(self.url, {"group_id": friends.id})
        self.assertContains(chosen, "Added to Friends")
        self.assertContains(chosen, "Change")

        other = Item.objects.create(
            media_id="202", title="Other", media_type="tv", source="tmdb"
        )
        url = reverse("group_quick_add", args=["tmdb", "tv", "202"])
        reused = self.post(url)
        self.assertContains(reused, "Added to Friends")
        self.assertTrue(GroupItem.objects.filter(group=friends, item=other).exists())
        self.assertFalse(
            GroupItem.objects.filter(group=self.couple, item=other).exists()
        )

    def test_change_moves_item_and_keeps_personal_records(self):
        """Change takes the item out of the group it just entered, records stay."""
        friends = self.add_friends_group()
        self.post(self.url, {"group_id": friends.id})
        TV.objects.filter(user=self.alice, item=self.item).update(score=9)

        response = self.post(self.url, {"group_id": self.couple.id, "change": "1"})

        self.assertContains(response, "Moved to Couple")
        self.assertFalse(self.in_group(friends))
        self.assertTrue(self.in_group(self.couple))
        self.assertEqual(TV.objects.get(user=self.alice, item=self.item).score, 9)
        self.assertTrue(TV.objects.filter(user=self.bob, item=self.item).exists())

        again = self.post()
        self.assertContains(again, "Already in Couple")
        self.assertNotContains(again, "Change")

    def test_change_never_removes_an_item_that_was_already_there(self):
        """If quick add found the item already in the group, Change leaves it."""
        friends = self.add_friends_group()
        GroupItem.objects.create(group=friends, item=self.item, added_by=self.bob)

        self.post(self.url, {"group_id": friends.id})
        self.post(self.url, {"group_id": self.couple.id, "change": "1"})

        self.assertTrue(self.in_group(friends))
        self.assertTrue(self.in_group(self.couple))

    def test_no_change_offered_when_every_group_has_the_item(self):
        """Change would lead nowhere if the item is already in all the groups."""
        friends = self.add_friends_group()
        GroupItem.objects.create(group=self.couple, item=self.item, added_by=self.bob)

        response = self.post(self.url, {"group_id": friends.id})

        self.assertContains(response, "Added to Friends")
        self.assertNotContains(response, "Change")

    def test_each_toast_changes_its_own_add(self):
        """Adding A then B, A's Change still moves A, not just the latest add."""
        friends = self.add_friends_group()
        other = Item.objects.create(
            media_id="202",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Other",
        )
        other_url = reverse("group_quick_add", args=["tmdb", "tv", "202"])
        self.post(self.url, {"group_id": friends.id})
        self.post(other_url, {"group_id": friends.id})

        response = self.post(self.url, {"group_id": self.couple.id, "change": "1"})

        self.assertContains(response, "Moved to Couple")
        self.assertFalse(self.in_group(friends))
        self.assertTrue(self.in_group(self.couple))
        self.assertTrue(GroupItem.objects.filter(group=friends, item=other).exists())

    def test_change_picker_offers_move(self):
        """The toast's Change link opens the picker in move mode."""
        self.add_friends_group()
        response = self.client.get(
            reverse("groups_modal", args=["tmdb", "tv", "101"]), {"change": "1"}
        )
        self.assertContains(response, "Move to group")
        self.assertContains(response, '"change": "1"')

    def test_foreign_group_is_404(self):
        """A group the user is not in cannot be targeted."""
        stranger = User.objects.create_user(username="eve", password="pw")  # noqa: S106
        theirs = Group.objects.create(name="Theirs", owner=stranger)
        theirs.members.add(stranger)

        response = self.post(self.url, {"group_id": theirs.id})

        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.in_group(theirs))

    def test_htmx_add_answers_in_place_with_toast(self):
        """The card answers with a toast fragment, not a redirect."""
        response = self.post()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("HX-Redirect", response.headers)
        self.assertNotIn("HX-Location", response.headers)
        self.assertContains(response, 'id="messages-list" hx-swap-oob="beforeend"')
        self.assertContains(
            response, f'href="{reverse("group_detail", args=[self.couple.id])}"'
        )
        self.assertContains(response, "View group")

    def test_without_js_returns_to_next_not_the_group(self):
        """A plain form POST goes back to ``next`` with a flash message."""
        response = self.client.post(self.url, {"next": "/search?q=Show"})

        self.assertRedirects(response, "/search?q=Show", fetch_redirect_response=False)
        self.assertTrue(self.in_group(self.couple))

    def test_without_js_falls_back_to_referer_then_home(self):
        """Without ``next``, go back to the referring page or home, never the group."""
        group_url = reverse("group_detail", args=[self.couple.id])

        from_referer = self.client.post(
            self.url, headers={"Referer": "http://testserver/lists/"}
        )
        self.assertEqual(from_referer.url, "http://testserver/lists/")

        no_referer = self.client.post(self.url)
        self.assertEqual(no_referer.url, reverse("home"))
        self.assertNotEqual(no_referer.url, group_url)

    def test_without_js_ignores_offsite_next(self):
        """An external ``next`` or Referer is not followed."""
        response = self.client.post(
            self.url,
            {"next": "https://evil.example/"},
            headers={"Referer": "https://evil.example/"},
        )
        self.assertEqual(response.url, reverse("home"))

    def test_recommendation_add_stays_on_recommendations(self):
        """The group recommendations "Add to group" posts to quick add in place."""
        self.add_friends_group()
        response = self.post(self.url, {"group_id": self.couple.id, "fixed": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Added to Couple")
        self.assertNotContains(response, "Change")
