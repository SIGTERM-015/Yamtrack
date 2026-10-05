"""Tests for the profile title-suggestion mailbox."""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from app.models import TV, Item, MediaTypes, Sources, Status
from app.providers.services import ProviderAPIError
from users.models import Suggestion, SuggestionStatus
from users.views import SUGGESTION_RATE_LIMIT, _suggestion_rate_limited

SUGGESTION_PAYLOAD = {
    "title": "Suggested Show",
    "media_type": MediaTypes.TV.value,
    "media_id": "123",
    "source": Sources.TMDB.value,
    "image": "http://example.com/tv.jpg",
    "message": "You should watch this.",
}


class SuggestMediaTests(TestCase):
    """Tests for the public suggestion form."""

    def setUp(self):
        """Create users and clear the rate-limit cache."""
        cache.clear()
        self.credentials = {"username": "suggester", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.target = get_user_model().objects.create_user(
            username="target",
            password="12345",  # noqa: S106
            profile_private=False,
        )
        self.client.login(**self.credentials)

    def test_suggest_media_get(self):
        """The standalone page renders the suggestion box with a search box."""
        response = self.client.get(
            reverse("suggest_media", args=[self.target.username]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/suggest.html")
        self.assertContains(response, 'id="suggest-search"')
        self.assertContains(response, "Why do you recommend it?")

    @patch("app.providers.services.search")
    def test_htmx_search_returns_results_for_the_chosen_type(self, mock_search):
        """An HTMX search returns only the poster list, searched by type."""
        mock_search.return_value = {
            "results": [
                {
                    "media_id": "603",
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "title": "The Matrix",
                    "image": "http://example.com/matrix.jpg",
                },
            ],
        }

        response = self.client.get(
            reverse("suggest_media", args=[self.target.username]),
            {"q": "matrix", "media_type": MediaTypes.MOVIE.value},
            headers={"HX-Request": "true"},
        )

        mock_search.assert_called_once_with(
            MediaTypes.MOVIE.value,
            "matrix",
            1,
            Sources.TMDB.value,
        )
        self.assertTemplateUsed(response, "users/components/suggest_results.html")
        self.assertTemplateNotUsed(response, "users/suggest.html")
        self.assertContains(response, 'data-title="The Matrix"')
        self.assertContains(response, 'data-media-id="603"')

    @patch("app.providers.services.search")
    def test_search_ignores_types_the_target_does_not_use(self, mock_search):
        """A media type the target disabled falls back to their first type."""
        mock_search.return_value = {"results": []}
        self.target.anime_enabled = False
        self.target.save(update_fields=["anime_enabled"])

        response = self.client.get(
            reverse("suggest_media", args=[self.target.username]),
            {"q": "frieren", "media_type": MediaTypes.ANIME.value},
            headers={"HX-Request": "true"},
        )

        self.assertEqual(mock_search.call_args.args[0], MediaTypes.TV.value)
        self.assertContains(response, "No results for")

    @patch("app.providers.services.search")
    def test_search_provider_error_is_shown_in_place(self, mock_search):
        """A failing provider shows a message instead of the 500 page."""
        mock_search.side_effect = ProviderAPIError(
            Sources.TMDB.value,
            Exception("boom"),
        )

        response = self.client.get(
            reverse("suggest_media", args=[self.target.username]),
            {"q": "matrix", "media_type": MediaTypes.MOVIE.value},
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search isn&#x27;t working right now")

    def test_suggest_media_post_creates_suggestion(self):
        """A valid POST stores a pending suggestion and confirms it."""
        response = self.client.post(
            reverse("suggest_media", args=[self.target.username]),
            SUGGESTION_PAYLOAD,
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sent to target")
        suggestion = Suggestion.objects.get(target_user=self.target)
        self.assertEqual(suggestion.suggested_by, self.user)
        self.assertEqual(suggestion.title, SUGGESTION_PAYLOAD["title"])
        self.assertEqual(suggestion.message, SUGGESTION_PAYLOAD["message"])
        self.assertEqual(suggestion.status, SuggestionStatus.PENDING.value)

    def test_anonymous_suggestion_has_no_sender(self):
        """Anonymous visitors can suggest; the suggestion has no sender."""
        self.client.logout()

        response = self.client.post(
            reverse("suggest_media", args=[self.target.username]),
            SUGGESTION_PAYLOAD,
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        suggestion = Suggestion.objects.get(target_user=self.target)
        self.assertIsNone(suggestion.suggested_by)

    def test_suggest_media_rate_limit(self):
        """The sixth suggestion from one IP is rejected with a message."""
        url = reverse("suggest_media", args=[self.target.username])
        for _ in range(5):
            self.client.post(url, SUGGESTION_PAYLOAD)

        response = self.client.post(url, SUGGESTION_PAYLOAD)

        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            Suggestion.objects.filter(target_user=self.target).count(),
            5,
        )
        self.assertContains(response, "too many suggestions", status_code=429)

    def test_suggest_media_disabled_returns_404(self):
        """A user with suggestions disabled is not reachable."""
        self.target.suggestions_enabled = False
        self.target.save(update_fields=["suggestions_enabled"])

        response = self.client.get(
            reverse("suggest_media", args=[self.target.username]),
        )
        self.assertEqual(response.status_code, 404)

    def test_suggest_media_private_get_returns_404(self):
        """A private target's suggestion form is not reachable."""
        self.target.profile_private = True
        self.target.save(update_fields=["profile_private"])

        response = self.client.get(
            reverse("suggest_media", args=[self.target.username]),
        )

        self.assertEqual(response.status_code, 404)

    def test_suggest_media_private_post_returns_404(self):
        """A private target never receives a suggestion."""
        self.target.profile_private = True
        self.target.save(update_fields=["profile_private"])

        response = self.client.post(
            reverse("suggest_media", args=[self.target.username]),
            SUGGESTION_PAYLOAD,
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            Suggestion.objects.filter(target_user=self.target).exists(),
        )

    def test_suggest_media_private_anonymous_returns_404(self):
        """Anonymous visitors cannot see or post to a private target."""
        self.client.logout()
        self.target.profile_private = True
        self.target.save(update_fields=["profile_private"])
        url = reverse("suggest_media", args=[self.target.username])

        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, SUGGESTION_PAYLOAD).status_code, 404)
        self.assertFalse(
            Suggestion.objects.filter(target_user=self.target).exists(),
        )

    def test_suggest_media_invalid_source_returns_400(self):
        """A provider that does not match the media type is rejected."""
        payload = {**SUGGESTION_PAYLOAD, "source": "not-provider"}

        response = self.client.post(
            reverse("suggest_media", args=[self.target.username]),
            payload,
        )

        self.assertContains(
            response,
            "That title can&#x27;t be suggested",
            status_code=400,
        )
        self.assertFalse(
            Suggestion.objects.filter(target_user=self.target).exists(),
        )

    def test_suggest_media_rate_limit_concurrent(self):
        """Concurrent requests from one IP cannot exceed the limit."""
        request = SimpleNamespace(META={"REMOTE_ADDR": "10.9.9.9"})
        attempts = SUGGESTION_RATE_LIMIT * 2
        barrier = threading.Barrier(attempts)

        def submit(_):
            barrier.wait(timeout=5)
            return _suggestion_rate_limited(request, self.target)

        with ThreadPoolExecutor(max_workers=attempts) as pool:
            limited = list(pool.map(submit, range(attempts)))

        self.assertEqual(limited.count(False), SUGGESTION_RATE_LIMIT)


class SuggestionResolutionTests(TestCase):
    """Tests for accepting and discarding received suggestions."""

    def setUp(self):
        """Create a user with a pending suggestion."""
        cache.clear()
        self.credentials = {"username": "receiver", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.suggestion = Suggestion.objects.create(
            target_user=self.user,
            title="Suggested Show",
            media_type=MediaTypes.TV.value,
            media_id="123",
            source=Sources.TMDB.value,
        )

    @patch("app.providers.services.get_media_metadata")
    def test_accept_suggestion_creates_item_and_tv(self, mock_metadata):
        """Accepting adds the title to the watchlist as planning."""
        mock_metadata.return_value = {
            "title": "Suggested Show",
            "image": "http://example.com/tv.jpg",
        }

        response = self.client.post(
            reverse("accept_suggestion", args=[self.suggestion.id]),
        )

        self.assertRedirects(response, reverse("suggestions"))
        item = Item.objects.get(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
        )
        tv = TV.objects.get(item=item, user=self.user)
        self.assertEqual(tv.status, Status.PLANNING.value)
        self.suggestion.refresh_from_db()
        self.assertEqual(self.suggestion.status, SuggestionStatus.ACCEPTED.value)

    @patch("app.providers.services.get_media_metadata")
    def test_accept_suggestion_invalid_source_returns_400(self, mock_metadata):
        """A suggestion stored with an invalid provider is rejected, not 500."""
        self.suggestion.source = "not-a-provider"
        self.suggestion.save(update_fields=["source"])

        response = self.client.post(
            reverse("accept_suggestion", args=[self.suggestion.id]),
        )

        self.assertEqual(response.status_code, 400)
        mock_metadata.assert_not_called()
        self.suggestion.refresh_from_db()
        self.assertEqual(self.suggestion.status, SuggestionStatus.PENDING.value)
        self.assertFalse(Item.objects.exists())

    def test_discard_suggestion(self):
        """Discarding marks the suggestion as discarded."""
        response = self.client.post(
            reverse("discard_suggestion", args=[self.suggestion.id]),
        )

        self.assertRedirects(response, reverse("suggestions"))
        self.suggestion.refresh_from_db()
        self.assertEqual(self.suggestion.status, SuggestionStatus.DISCARDED.value)


class SuggestionInboxTests(TestCase):
    """The receiver's list of pending suggestions."""

    def setUp(self):
        """Create a receiver with one signed and one anonymous suggestion."""
        self.user = get_user_model().objects.create_user(
            username="receiver",
            password="12345",  # noqa: S106
        )
        self.sender = get_user_model().objects.create_user(
            username="friend",
            password="12345",  # noqa: S106
        )
        self.client.force_login(self.user)
        Suggestion.objects.create(
            target_user=self.user,
            suggested_by=self.sender,
            title="Signed Show",
            media_type=MediaTypes.TV.value,
            media_id="1",
            source=Sources.TMDB.value,
            message="Best finale ever.",
        )
        Suggestion.objects.create(
            target_user=self.user,
            title="Anonymous Movie",
            media_type=MediaTypes.MOVIE.value,
            media_id="2",
            source=Sources.TMDB.value,
        )

    def test_inbox_shows_sender_link_message_and_anonymous(self):
        """Each pending suggestion says who sent it and why."""
        response = self.client.get(reverse("suggestions"))

        self.assertContains(response, "Best finale ever.")
        self.assertContains(response, f'href="{reverse("profile", args=["friend"])}"')
        self.assertContains(response, "an anonymous visitor")
        self.assertContains(response, "Add to planning", count=2)
