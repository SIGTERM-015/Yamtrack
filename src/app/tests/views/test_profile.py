"""The unified public profile at /<username>."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import Item, MediaTypes, Movie, Sources, Status
from app.statistics import get_recent_activity
from users.models import Suggestion


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


class ProfileDiaryAndTimelineTests(TestCase):
    """Latest as a diary (option A) and the separate day-by-day timeline."""

    def setUp(self):
        """Ana finished three movies over three days; one has a comment."""
        self.owner = get_user_model().objects.create_user(
            username="ana",
            password="pw",  # noqa: S106
        )
        now = timezone.now()
        rows = [
            ("Rated", 8, "", False, 0),
            ("Commented", None, "Me encantó", True, 1),
            ("Plain", None, "", False, 2),
        ]
        for index, (title, score, notes, public, days_ago) in enumerate(rows):
            item = Item.objects.create(
                media_id=str(700 + index),
                source=Sources.MANUAL.value,
                media_type=MediaTypes.MOVIE.value,
                title=title,
            )
            Movie.objects.bulk_create(
                [
                    Movie(
                        item=item,
                        user=self.owner,
                        status=Status.COMPLETED.value,
                        score=score,
                        notes=notes,
                        notes_public=public,
                    ),
                ],
            )
            Movie.objects.filter(item=item).update(
                end_date=now - timedelta(days=days_ago),
            )

    def test_latest_splits_opinions_from_the_rest(self):
        """Scored or commented titles get a row; the rest go to the strip."""
        response = self.client.get("/ana")
        opinions = [e["item"].title for e in response.context["latest_opinions"]]
        others = [e["item"].title for e in response.context["latest_others"]]
        self.assertEqual(opinions, ["Rated", "Commented"])
        self.assertEqual(others, ["Plain"])
        self.assertContains(response, 'href="/ana/timeline"')

    def test_also_strip_shows_the_three_latest(self):
        """The strip under Latest keeps only the three most recent plain titles."""
        now = timezone.now()
        for index in range(5):
            item = Item.objects.create(
                media_id=str(800 + index),
                source=Sources.MANUAL.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Extra {index}",
            )
            Movie.objects.bulk_create(
                [Movie(item=item, user=self.owner, status=Status.COMPLETED.value)],
            )
            Movie.objects.filter(item=item).update(
                end_date=now - timedelta(hours=index + 1),
            )

        response = self.client.get("/ana")

        others = [e["item"].title for e in response.context["latest_others"]]
        self.assertEqual(others, ["Extra 0", "Extra 1", "Extra 2"])

    def test_timeline_groups_by_day_newest_first(self):
        """The timeline shows one section per active day, newest first."""
        response = self.client.get("/ana/timeline")
        self.assertEqual(response.status_code, 200)
        days = [group["day"] for group in response.context["day_groups"]]
        self.assertEqual(days, sorted(days, reverse=True))
        self.assertEqual(len(days), 3)
        self.assertContains(response, "Me encantó")

    def test_timeline_paginates_by_days(self):
        """With a small page size, an Older link continues the timeline."""
        from app.statistics import get_profile_timeline  # noqa: PLC0415

        first, next_before = get_profile_timeline(self.owner, days=2)
        self.assertEqual(len(first), 2)
        self.assertIsNotNone(next_before)
        rest, after = get_profile_timeline(self.owner, before=next_before, days=2)
        self.assertEqual([g["entries"][0]["item"].title for g in rest], ["Plain"])
        self.assertIsNone(after)

    def test_no_template_comment_leaks(self):
        """Multi-line {# #} comments render as text; none must reach the page."""
        for url in ("/ana", "/ana/timeline"):
            with self.subTest(url=url):
                self.assertNotContains(self.client.get(url), "{#")

    def test_timeline_respects_privacy(self):
        """A private profile's timeline 404s for others."""
        self.owner.profile_private = True
        self.owner.save()
        self.assertEqual(self.client.get("/ana/timeline").status_code, 404)


class PublicListPosterTests(TestCase):
    """Per-type lists show visitors big-score posters (option B)."""

    def setUp(self):
        """Ana has one rated movie with a public comment."""
        self.owner = get_user_model().objects.create_user(
            username="ana",
            password="pw",  # noqa: S106
        )
        item = Item.objects.create(
            media_id="801",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Poster Movie",
        )
        Movie.objects.bulk_create(
            [
                Movie(
                    item=item,
                    user=self.owner,
                    status=Status.COMPLETED.value,
                    score=7,
                    notes="Bonita",
                    notes_public=True,
                ),
            ],
        )

    def test_visitors_get_poster_grid_linking_to_title_pages(self):
        """No management controls; posters open the person's title page."""
        response = self.client.get(reverse("medialist", args=["ana", "movie"]))
        self.assertTrue(response.context["public_grid"])
        self.assertContains(response, 'href="/ana/movie/manual/801"')
        self.assertContains(response, "Bonita")
        self.assertNotContains(response, 'title="Table View"')

    def test_owner_keeps_management_cards(self):
        """The owner still gets the regular cards and layout toggle."""
        self.client.login(username="ana", password="pw")  # noqa: S106
        response = self.client.get(reverse("medialist", args=["ana", "movie"]))
        self.assertFalse(response.context["public_grid"])
        self.assertContains(response, 'title="Table View"')


class EpisodeRangeLabelTests(TestCase):
    """Episode lists collapse into ranges in the timeline."""

    def test_format_episode_ranges(self):
        """Consecutive episodes merge; gaps and seasons split."""
        from app.statistics import format_episode_ranges  # noqa: PLC0415

        self.assertEqual(
            format_episode_ranges([(1, 1), (1, 2), (1, 3), (1, 5), (2, 1)]),
            "S1E1-E3, S1E5, S2E1",
        )


class PublicSeasonLinkTests(TestCase):
    """Season posters on a public list open the show's title page."""

    def test_season_url_redirects_to_the_show(self):
        """/<user>/season/<source>/<id> goes to /<user>/tv/<source>/<id>."""
        get_user_model().objects.create_user(username="ana")
        response = self.client.get("/ana/season/tmdb/95396")
        self.assertRedirects(
            response, "/ana/tv/tmdb/95396", fetch_redirect_response=False
        )


class ProfileRecommendTests(TestCase):
    """The "Recommend something" box on someone else's profile."""

    def setUp(self):
        """Create a public owner and a visitor account."""
        self.owner = get_user_model().objects.create_user(
            username="ana",
            password="pw",  # noqa: S106
        )
        get_user_model().objects.create_user(
            username="bob",
            password="pw",  # noqa: S106
        )

    def test_visitors_get_the_recommend_box(self):
        """Signed-in and anonymous visitors can open the box."""
        response = self.client.get(reverse("profile", args=["ana"]))
        self.assertContains(response, "Recommend something")
        self.assertContains(response, "arrive without your name")
        self.assertContains(response, 'hx-get="/suggest/ana"')

        self.client.login(username="bob", password="pw")  # noqa: S106
        response = self.client.get(reverse("profile", args=["ana"]))
        self.assertContains(response, "Recommend something")
        self.assertContains(response, "see it came from you")

    def test_no_recommend_box_when_suggestions_are_off(self):
        """Turning suggestions off hides the button."""
        self.owner.suggestions_enabled = False
        self.owner.save(update_fields=["suggestions_enabled"])

        response = self.client.get(reverse("profile", args=["ana"]))

        self.assertNotContains(response, "Recommend something")

    def test_owner_gets_inbox_link_with_pending_count(self):
        """The owner sees a link to their suggestions, not the box."""
        Suggestion.objects.create(
            target_user=self.owner,
            title="Show",
            media_type=MediaTypes.TV.value,
            media_id="1",
            source=Sources.TMDB.value,
        )
        self.client.login(username="ana", password="pw")  # noqa: S106

        response = self.client.get(reverse("profile", args=["ana"]))

        self.assertNotContains(response, "Recommend something")
        self.assertContains(response, f'href="{reverse("suggestions")}"')
        self.assertEqual(response.context["pending_suggestions"], 1)

    def test_visitor_preview_explains_instead_of_searching(self):
        """The owner's visitor preview shows the button without a live box."""
        self.client.login(username="ana", password="pw")  # noqa: S106

        response = self.client.get(reverse("profile", args=["ana"]), {"as": "visitor"})

        self.assertContains(response, "Recommend something")
        self.assertNotContains(response, 'hx-get="/suggest/ana"')
        self.assertContains(response, "Visitors search for a title here")
