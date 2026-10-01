"""Named, scoped, revocable personal API tokens (ADR 0001 §7)."""

from datetime import timedelta

from django.utils import timezone

from lists.models import CustomList
from users.models import ApiToken, ApiTokenScope

from .base import YamtrackApiTestCase


class ApiTokenAuthenticationTests(YamtrackApiTestCase):
    """ApiToken credentials authenticate and enforce their scope."""

    def setUp(self):
        """Create a read-only and a read-write token for user1."""
        super().setUp()
        self.read_token, self.read_raw = ApiToken.create_for(
            self.user1, "reader", ApiTokenScope.READ.value
        )
        self.write_token, self.write_raw = ApiToken.create_for(
            self.user1, "writer", ApiTokenScope.WRITE.value
        )

    def test_only_hash_is_stored(self):
        """The plain value is never persisted, only its hash and a prefix."""
        self.assertNotEqual(self.read_token.token_hash, self.read_raw)
        self.assertEqual(
            self.read_token.token_hash, ApiToken.hash_secret(self.read_raw)
        )
        self.assertTrue(self.read_raw.startswith(self.read_token.prefix))
        self.assertFalse(
            ApiToken.objects.filter(token_hash=self.read_raw).exists(),
        )

    def test_read_token_can_read_via_api_key_and_bearer(self):
        """A read token authenticates as its owner with either header."""
        for headers in (
            {"HTTP_X_API_KEY": self.read_raw},
            {"HTTP_AUTHORIZATION": f"Bearer {self.read_raw}"},
        ):
            with self.subTest(headers=next(iter(headers))):
                response = self.call_api("get", "api_lists", headers=headers)
                self.assertEqual(response.status_code, 200)

    def test_read_token_cannot_write(self):
        """Unsafe methods with a read-only token are rejected, nothing written."""
        response = self.call_api(
            "post",
            "api_lists",
            payload={"name": "Nope"},
            headers={"HTTP_X_API_KEY": self.read_raw},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("read-only", response.json()["detail"])
        self.assertFalse(CustomList.objects.filter(name="Nope").exists())

    def test_write_token_can_write(self):
        """A read-write token can create resources for its owner."""
        response = self.call_api(
            "post",
            "api_lists",
            payload={"name": "Via token"},
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.write_raw}"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            CustomList.objects.filter(name="Via token", owner=self.user1).exists()
        )

    def test_revoked_token_is_rejected_and_others_keep_working(self):
        """Revoking one token leaves the user's other tokens untouched."""
        self.read_token.revoke()

        revoked = self.call_api(
            "get", "api_lists", headers={"HTTP_X_API_KEY": self.read_raw}
        )
        still_ok = self.call_api(
            "get", "api_lists", headers={"HTTP_X_API_KEY": self.write_raw}
        )
        legacy = self.call_api("get", "api_lists", headers=self.auth_headers)

        self.assertEqual(revoked.status_code, 403)
        self.assertEqual(still_ok.status_code, 200)
        self.assertEqual(legacy.status_code, 200)

    def test_tokens_are_scoped_to_their_owner(self):
        """A token only ever sees its owner's data."""
        CustomList.objects.create(name="User2 private", owner=self.user2)
        response = self.call_api(
            "get", "api_lists", headers={"HTTP_X_API_KEY": self.read_raw}
        )
        names = [row["name"] for row in response.json()["results"]]
        self.assertNotIn("User2 private", names)

    def test_last_used_is_recorded_and_rate_limited(self):
        """Use stamps last_used_at, without a write on every request."""
        self.assertIsNone(self.read_token.last_used_at)
        self.call_api("get", "api_lists", headers={"HTTP_X_API_KEY": self.read_raw})
        self.read_token.refresh_from_db()
        first_use = self.read_token.last_used_at
        self.assertIsNotNone(first_use)

        self.call_api("get", "api_lists", headers={"HTTP_X_API_KEY": self.read_raw})
        self.read_token.refresh_from_db()
        self.assertEqual(self.read_token.last_used_at, first_use)

        stale = timezone.now() - timedelta(
            seconds=ApiToken.LAST_USED_RESOLUTION_SECONDS + 1
        )
        ApiToken.objects.filter(pk=self.read_token.pk).update(last_used_at=stale)
        self.call_api("get", "api_lists", headers={"HTTP_X_API_KEY": self.read_raw})
        self.read_token.refresh_from_db()
        self.assertGreater(self.read_token.last_used_at, stale)

    def test_unknown_prefixed_token_is_rejected(self):
        """A well-formed but unknown ytk_ token fails authentication."""
        response = self.call_api(
            "get", "api_lists", headers={"HTTP_X_API_KEY": "ytk_does-not-exist"}
        )
        self.assertEqual(response.status_code, 403)
