"""The unified public profile at /<username>."""

from datetime import timedelta

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
