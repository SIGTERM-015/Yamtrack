from .base import YamtrackApiTestCase
from .helpers import check_health_structure, check_info_structure


class PublicEndpointsTests(YamtrackApiTestCase):
    """Verify public endpoints stay accessible without auth."""

    def test_health_endpoint(self):
        """Health endpoint test."""
        response = self.call_api("get", "api_health")

        self.assertIn(response.status_code, [200, 500])
        payload = response.json()
        check_health_structure(self, payload)

    def test_info_endpoint(self):
        """Info endpoint test."""
        response = self.call_api("get", "api_info")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_info_structure(self, payload)

    def test_info_endpoint_anonymous_hides_sensitive_fields(self):
        """Anonymous callers do not see version, debug or admin_enabled."""
        response = self.call_api("get", "api_info")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("version", payload)
        self.assertNotIn("debug", payload)
        self.assertNotIn("admin_enabled", payload)

    def test_info_endpoint_authenticated_exposes_sensitive_fields(self):
        """Authenticated callers see version, debug and admin_enabled."""
        response = self.call_api("get", "api_info", headers=self.auth_headers)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("version", payload)
        self.assertIn("debug", payload)
        self.assertIn("admin_enabled", payload)
