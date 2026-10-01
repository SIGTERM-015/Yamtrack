"""Settings UI for creating, listing and revoking API tokens."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from users.models import ApiToken, ApiTokenScope


class ApiTokenSettingsViewTests(TestCase):
    """The Integrations page manages personal API tokens."""

    def setUp(self):
        """Log in a user."""
        self.credentials = {"username": "tokenuser", "password": "testpass123"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_create_shows_value_once(self):
        """The plain token is shown on the next render only, then never again."""
        response = self.client.post(
            reverse("create_api_token"),
            {"name": "Stremio", "scope": ApiTokenScope.WRITE.value},
            follow=True,
        )

        token = ApiToken.objects.get(user=self.user, name="Stremio")
        self.assertEqual(token.scope, ApiTokenScope.WRITE.value)
        self.assertContains(response, "won't be shown again")
        raw_value = response.context["new_api_token"]["value"]
        self.assertEqual(ApiToken.from_raw(raw_value), token)

        second = self.client.get(reverse("integrations"))
        self.assertNotContains(second, raw_value)
        self.assertContains(second, token.prefix)
        self.assertContains(second, "Stremio")

    def test_create_requires_name_and_valid_scope(self):
        """Missing names and unknown scopes create nothing."""
        self.client.post(reverse("create_api_token"), {"name": "  ", "scope": "read"})
        self.client.post(reverse("create_api_token"), {"name": "x", "scope": "admin"})
        self.assertFalse(ApiToken.objects.exists())

    def test_revoke_own_token(self):
        """Revoking hides the token from the list and stops it authenticating."""
        token, raw = ApiToken.create_for(self.user, "Old", ApiTokenScope.READ.value)

        response = self.client.post(
            reverse("revoke_api_token", args=[token.id]), follow=True
        )

        token.refresh_from_db()
        self.assertFalse(token.is_active)
        self.assertIsNone(ApiToken.from_raw(raw))
        self.assertNotIn(token, response.context["api_tokens"])

    def test_cannot_revoke_someone_elses_token(self):
        """Another user's token id returns 404 and stays active."""
        other = get_user_model().objects.create_user(username="other", password="x")  # noqa: S106
        token, _ = ApiToken.create_for(other, "Theirs", ApiTokenScope.READ.value)

        response = self.client.post(reverse("revoke_api_token", args=[token.id]))

        self.assertEqual(response.status_code, 404)
        token.refresh_from_db()
        self.assertTrue(token.is_active)
