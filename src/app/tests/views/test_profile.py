"""The unified public profile at /<username>."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import Item, MediaTypes, Movie, Sources, Status
from app.statistics import get_recent_activity


class ProfileViewTests(TestCase):
    """Header, latest activity, privacy and library links."""

    def setUp(self):
        """Create a public owner with two finished movies, one with a comment."""
        self.owner = get_user_model().objects.create_user(
            username="ana",
            password="pw",  # noqa: S106
            bio="Cine y series.",
            letterboxd="https://letterboxd.com/ana",
        )
        now = timezone.now()
        self.movies = []
        for index, (title, notes, public) in enumerate(
            [
                ("Old Movie", "", False),
                ("New Movie", "Me encantó", True),
                ("Secret Movie", "Nota privada", False),
            ],
        ):
            item = Item.objects.create(
                media_id=str(500 + index),
                source=Sources.MANUAL.value,
                media_type=MediaTypes.MOVIE.value,
                title=title,
            )
            # bulk_create skips the custom save, so no provider lookups fire.
            Movie.objects.bulk_create(
                [
                    Movie(
                        item=item,
                        user=self.owner,
                        status=Status.COMPLETED.value,
                        score=8,
                        notes=notes,
                        notes_public=public,
                    ),
                ],
            )
            Movie.objects.filter(item=item).update(
                end_date=now - timedelta(days=10 - index),
            )
            self.movies.append(item)

    def test_profile_url_is_the_username(self):
        """/<username> resolves to the profile and renders the header."""
        self.assertEqual(reverse("profile", args=["ana"]), "/ana")
        response = self.client.get("/ana")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cine y series.")
        self.assertContains(response, 'href="https://letterboxd.com/ana"')

    def test_named_routes_still_win_over_usernames(self):
        """Single-segment app routes are not shadowed by the profile route."""
        get_user_model().objects.create_user(username="search")
        self.client.login(username="ana", password="pw")  # noqa: S106
        response = self.client.get("/search", {"q": "x", "media_type": "movie"})
        self.assertNotEqual(response.resolver_match.url_name, "profile")

    def test_latest_is_newest_first_with_public_comments_only(self):
        """Visitors see public comments; private notes stay hidden."""
        response = self.client.get("/ana")
        titles = [entry["item"].title for entry in response.context["recent_activity"]]
        self.assertEqual(titles, ["Secret Movie", "New Movie", "Old Movie"])
        self.assertContains(response, "Me encantó")
        self.assertNotContains(response, "Nota privada")

    def test_owner_sees_their_own_private_notes(self):
        """The owner sees every note on their own profile."""
        self.client.login(username="ana", password="pw")  # noqa: S106
        response = self.client.get("/ana")
        self.assertContains(response, "Nota privada")
        self.assertContains(response, "Edit profile")

    def test_hidden_reviews_hide_comments_from_visitors(self):
        """Turning off the reviews section hides comments in Latest too."""
        self.owner.profile_show_reviews = False
        self.owner.save()
        response = self.client.get("/ana")
        self.assertContains(response, "New Movie")
        self.assertNotContains(response, "Me encantó")

    def test_private_profile_is_404_for_others(self):
        """A private profile doesn't exist for anyone but the owner."""
        self.owner.profile_private = True
        self.owner.save()
        self.assertEqual(self.client.get("/ana").status_code, 404)
        self.client.login(username="ana", password="pw")  # noqa: S106
        self.assertEqual(self.client.get("/ana").status_code, 200)

    def test_slashless_app_routes_still_redirect(self):
        """/health still appends its slash instead of looking up a user."""
        response = self.client.get("/health")
        self.assertRedirects(response, "/health/", fetch_redirect_response=False)

    def test_trailing_slash_redirects_to_profile(self):
        """/<username>/ redirects to /<username>, also for anonymous visitors."""
        response = self.client.get("/ana/")
        self.assertRedirects(response, "/ana", status_code=301)

    def test_unknown_user_is_404(self):
        """Unknown usernames 404."""
        self.assertEqual(self.client.get("/nobody-here").status_code, 404)

    def test_library_links_to_each_media_type(self):
        """Library lists enabled types with counts, linking to the full lists."""
        response = self.client.get("/ana")
        counts = dict(response.context["library_counts"])
        self.assertEqual(counts[MediaTypes.MOVIE.value], 3)
        self.assertContains(
            response, reverse("medialist", args=["ana", MediaTypes.MOVIE.value])
        )

    def test_recent_activity_respects_the_limit(self):
        """get_recent_activity returns at most ``limit`` entries."""
        self.assertEqual(len(get_recent_activity(self.owner, limit=2)), 2)


class ProfileChromeTests(TestCase):
    """Visitors get a page, not the app; the owner can preview it."""

    def setUp(self):
        """Create a public owner with a private note on a finished movie."""
        self.owner = get_user_model().objects.create_user(
            username="leo",
            password="pw",  # noqa: S106
        )
        item = Item.objects.create(
            media_id="900",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Preview Movie",
        )
        Movie.objects.bulk_create(
            [
                Movie(
                    item=item,
                    user=self.owner,
                    status=Status.COMPLETED.value,
                    notes="Solo para mí",
                    notes_public=False,
                ),
            ],
        )
        Movie.objects.filter(item=item).update(end_date=timezone.now())

    def test_anonymous_visitor_gets_minimal_chrome(self):
        """No sidebar or global search for visitors without an account."""
        response = self.client.get("/leo")
        self.assertTrue(response.context["public_view"])
        self.assertNotContains(response, 'id="global-search"')
        self.assertNotContains(response, "<aside")
        self.assertContains(response, "Sign in")

    def test_logged_in_viewer_keeps_app_chrome(self):
        """Members browsing the app keep their navigation on profiles."""
        self.client.login(username="leo", password="pw")  # noqa: S106
        response = self.client.get("/leo")
        self.assertFalse(response.context["public_view"])
        self.assertContains(response, 'id="global-search"')
        self.assertContains(response, "View as visitor")
        self.assertContains(response, "Copy link")

    def test_owner_preview_matches_what_visitors_see(self):
        """?as=visitor hides private notes and owner-only controls."""
        self.client.login(username="leo", password="pw")  # noqa: S106
        response = self.client.get("/leo", {"as": "visitor"})
        self.assertTrue(response.context["public_view"])
        self.assertNotContains(response, "Solo para mí")
        self.assertNotContains(response, "Edit profile")
        self.assertContains(response, "Exit visitor view")

    def test_preview_only_for_the_owner(self):
        """Another member asking for ?as=visitor just gets the normal view."""
        get_user_model().objects.create_user(username="ana", password="pw")  # noqa: S106
        self.client.login(username="ana", password="pw")  # noqa: S106
        response = self.client.get("/leo", {"as": "visitor"})
        self.assertFalse(response.context["preview"])

    def test_owner_can_preview_a_private_profile(self):
        """A private profile still previews for its owner, with a warning."""
        self.owner.profile_private = True
        self.owner.save()
        self.client.login(username="leo", password="pw")  # noqa: S106
        response = self.client.get("/leo", {"as": "visitor"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your profile is private")

    def test_anonymous_per_type_list_is_also_minimal(self):
        """The per-type lists use the same visitor chrome."""
        response = self.client.get(reverse("medialist", args=["leo", "movie"]))
        self.assertNotContains(response, 'id="global-search"')


class ProfileLayoutTests(TestCase):
    """Section order, compact heatmap and the public title page."""

    def setUp(self):
        """Create ana with one rated, commented movie and a shelf."""
        self.owner = get_user_model().objects.create_user(
            username="ana",
            password="pw",  # noqa: S106
        )
        self.item = Item.objects.create(
            media_id="27205",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Inception",
        )
        Movie.objects.bulk_create(
            [
                Movie(
                    item=self.item,
                    user=self.owner,
                    status=Status.COMPLETED.value,
                    score=9,
                    notes="Los sueños dentro de sueños",
                    notes_public=True,
                ),
            ],
        )
        Movie.objects.filter(item=self.item).update(end_date=timezone.now())
        from lists.models import CustomList, CustomListItem  # noqa: PLC0415

        shelf = CustomList.objects.create(
            name="Top", owner=self.owner, is_featured=True
        )
        CustomListItem.objects.create(custom_list=shelf, item=self.item)
        self.metadata = {
            "title": "Inception",
            "media_type": "movie",
            "source": "tmdb",
            "media_id": "27205",
            "image": "https://example.com/i.jpg",
            "synopsis": "A thief who steals secrets through dreams.",
            "genres": ["Science Fiction"],
            "score": 8.4,
            "score_count": 40000,
            "cast": [{"name": "Leonardo DiCaprio", "character": "Cobb", "image": ""}],
            "details": {},
            "related": {"recommendations": [{"title": "Should not show"}]},
            "max_progress": 1,
        }

    def test_shelves_come_before_latest_and_heatmap_is_compact(self):
        """Shelves lead; the heatmap has no title or year navigation."""
        content = self.client.get("/ana").content.decode()
        self.assertLess(
            content.index("shelves-heading"), content.index("latest-heading")
        )
        self.assertNotIn("Consumption Heatmap", content)
        self.assertNotIn("Previous year", content)
        self.assertIn("in the last months", content)

    def test_cards_open_the_public_title_page(self):
        """Latest and shelf cards link to /<user>/<type>/<source>/<id>."""
        response = self.client.get("/ana")
        self.assertContains(response, 'href="/ana/movie/tmdb/27205"')

    @patch("app.views.services.get_media_metadata")
    def test_title_page_puts_the_verdict_first(self, mock_metadata):
        """Score, status, date and public comment, plus title info; no recs."""
        mock_metadata.return_value = self.metadata
        response = self.client.get("/ana/movie/tmdb/27205")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Los sueños dentro de sueños")
        self.assertContains(response, "A thief who steals secrets through dreams.")
        self.assertContains(response, "Leonardo DiCaprio")
        self.assertContains(response, "8.4/10")
        self.assertNotContains(response, "Should not show")
        self.assertNotContains(response, "Add to tracker")
        self.assertNotContains(response, 'id="global-search"')

    @patch("app.views.services.get_media_metadata")
    def test_title_page_hides_private_notes_from_visitors(self, mock_metadata):
        """A private note only shows to its owner."""
        mock_metadata.return_value = self.metadata
        Movie.objects.filter(item=self.item).update(notes_public=False)
        self.assertNotContains(
            self.client.get("/ana/movie/tmdb/27205"), "Los sueños dentro de sueños"
        )
        self.client.login(username="ana", password="pw")  # noqa: S106
        self.assertContains(
            self.client.get("/ana/movie/tmdb/27205"), "Only you can see this note."
        )

    def test_title_page_404s_outside_the_library(self):
        """Titles the person hasn't tracked or shelved aren't on their profile."""
        response = self.client.get("/ana/movie/tmdb/999999")
        self.assertEqual(response.status_code, 404)
