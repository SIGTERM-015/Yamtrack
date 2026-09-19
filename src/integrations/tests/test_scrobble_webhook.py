"""Tests for the agnostic scrobble webhook endpoint."""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from app.models import Item, Movie, Status
from groups.models import Group, GroupItem, GroupMembership, GroupOrigin

METADATA_PATCH = "app.models.providers.services.get_media_metadata"
FETCH_RELEASES_PATCH = "app.models.Item.fetch_releases"


class ScrobbleWebhookTests(TestCase):
    """Tests for the scrobble webhook endpoint."""

    def setUp(self):
        """Set up test user, client and network-free patches."""
        self.client = Client()
        self.credentials = {"username": "testuser", "token": "test-token"}
        self.user = get_user_model().objects.create_superuser(**self.credentials)
        self.url = reverse("scrobble_webhook", kwargs={"token": "test-token"})

        metadata = patch(METADATA_PATCH, return_value={"max_progress": 1})
        metadata.start()
        self.addCleanup(metadata.stop)

        fetch_releases = patch(FETCH_RELEASES_PATCH)
        fetch_releases.start()
        self.addCleanup(fetch_releases.stop)

    def _post(self, payload, url=None):
        """POST a JSON payload to the scrobble endpoint."""
        return self.client.post(
            url or self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    @staticmethod
    def _payload(**overrides):
        """Return a valid payload with optional overrides."""
        payload = {
            "media_id": "550",
            "source": "tmdb",
            "media_type": "movie",
            "progress": 95,
            "title": "Fight Club",
        }
        payload.update(overrides)
        return payload

    def test_invalid_token_returns_401(self):
        """An unknown token must be rejected."""
        url = reverse("scrobble_webhook", kwargs={"token": "invalid-token"})
        response = self._post(self._payload(), url=url)

        self.assertEqual(response.status_code, 401)

    def test_invalid_token_is_not_logged(self):
        """The rejected token must never be written to the logs."""
        url = reverse("scrobble_webhook", kwargs={"token": "invalid-token"})
        with self.assertLogs("integrations.views", level="WARNING") as captured:
            response = self._post(self._payload(), url=url)

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("invalid-token", "\n".join(captured.output))

    def test_missing_payload_returns_400(self):
        """An empty body must be rejected."""
        response = self.client.post(self.url, content_type="application/json")

        self.assertEqual(response.status_code, 400)

    def test_invalid_json_returns_400(self):
        """A non-JSON body must be rejected."""
        response = self.client.post(
            self.url,
            data="not-json",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_missing_required_fields_returns_400(self):
        """Payloads without media_id, source or progress must be rejected."""
        response = self._post({"media_id": "550", "source": "tmdb"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Movie.objects.count(), 0)

    def test_unknown_source_returns_400(self):
        """A source outside the known providers must be rejected."""
        response = self._post(self._payload(source="stremio"))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Movie.objects.count(), 0)

    def test_progress_out_of_range_returns_400(self):
        """Progress outside 0-100 must be rejected."""
        response = self._post(self._payload(progress=150))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Movie.objects.count(), 0)

    def test_below_threshold_does_not_track(self):
        """Playback below the watched threshold must not create tracking."""
        response = self._post(self._payload(progress=50))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ignored")
        self.assertEqual(Movie.objects.count(), 0)
        self.assertEqual(Item.objects.count(), 0)

    def test_event_creates_tracking_entry(self):
        """A simulated event creates the tracking entry."""
        response = self._post(self._payload(progress=92))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "created")

        movie = Movie.objects.get(user=self.user)
        self.assertEqual(movie.item.media_id, "550")
        self.assertEqual(movie.item.source, "tmdb")
        self.assertEqual(movie.item.media_type, "movie")
        self.assertEqual(movie.item.title, "Fight Club")
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.progress, 1)
        self.assertIsNotNone(movie.end_date)

    def test_duplicate_event_does_not_create_duplicate(self):
        """Repeated events for the same playback must not duplicate tracking."""
        self._post(self._payload(progress=92))
        response = self._post(self._payload(progress=99))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "duplicate")
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Item.objects.count(), 1)

    def test_group_context_logged_as_assume_without_writing_others(self):
        """A shared item logs policy=assume and never writes to other profiles."""
        partner = get_user_model().objects.create_user(username="partner")
        group = Group.objects.create(name="Couple", owner=self.user)
        GroupMembership.objects.create(group=group, user=self.user)
        GroupMembership.objects.create(group=group, user=partner)

        item = Item.objects.create(
            media_id="550", source="tmdb", media_type="movie", title="Fight Club"
        )
        GroupItem.objects.create(group=group, item=item, added_by=self.user)
        GroupOrigin.objects.create(user=self.user, item=item, group=group)
        GroupOrigin.objects.create(user=partner, item=item, group=group)

        with self.assertLogs("integrations.webhooks.scrobble", level="INFO") as logs:
            response = self._post(self._payload(progress=92))

        self.assertEqual(response.status_code, 200)
        output = "\n".join(logs.output)
        self.assertIn("policy=assume", output)
        self.assertIn(f"groups=[{group.id}]", output)

        # No-destruction (E2): only the scrobbling user got a tracking entry.
        self.assertEqual(Movie.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Movie.objects.filter(user=partner).count(), 0)

    def test_group_context_logged_as_ask_without_group(self):
        """An item with no group context logs policy=ask and empty groups."""
        with self.assertLogs("integrations.webhooks.scrobble", level="INFO") as logs:
            response = self._post(self._payload(progress=92))

        self.assertEqual(response.status_code, 200)
        output = "\n".join(logs.output)
        self.assertIn("policy=ask", output)
        self.assertIn("groups=[]", output)
