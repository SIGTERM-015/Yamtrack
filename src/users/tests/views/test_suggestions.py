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
        """A public profile renders the suggestion form."""
        response = self.client.get(
            reverse("suggest_media", args=[self.target.username]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/suggest.html")

    def test_suggest_media_post_creates_suggestion(self):
        """A valid POST stores a pending suggestion for the target user."""
        response = self.client.post(
            reverse("suggest_media", args=[self.target.username]),
            SUGGESTION_PAYLOAD,
        )
        self.assertRedirects(
            response,
            reverse("suggest_media", args=[self.target.username]),
        )
        suggestion = Suggestion.objects.get(target_user=self.target)
        self.assertEqual(suggestion.suggested_by, self.user)
        self.assertEqual(suggestion.title, SUGGESTION_PAYLOAD["title"])
        self.assertEqual(suggestion.status, SuggestionStatus.PENDING.value)

    def test_suggest_media_rate_limit(self):
        """The sixth suggestion from one IP is rejected."""
        url = reverse("suggest_media", args=[self.target.username])
        for _ in range(5):
            self.client.post(url, SUGGESTION_PAYLOAD)

        response = self.client.post(url, SUGGESTION_PAYLOAD, follow=True)

        self.assertEqual(
            Suggestion.objects.filter(target_user=self.target).count(),
            5,
        )
        self.assertContains(response, "too many suggestions")

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

        self.assertEqual(response.status_code, 400)
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
