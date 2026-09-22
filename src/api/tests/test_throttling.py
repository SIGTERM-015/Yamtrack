"""Tests for the rate limiting applied to the API."""

from unittest.mock import patch

from rest_framework.throttling import SimpleRateThrottle

from .base import YamtrackApiTestCase

# DRF binds its `THROTTLE_RATES` class attribute at import time, so the rates
# are patched directly instead of through the settings. Every rate is defined
# so the throttles enabled in `REST_FRAMEWORK` can be instantiated, and the
# counter is keyed on the user, which is created fresh for every test.
_THROTTLE_RATES = {
    "anon": "1000/minute",
    "user": "1000/minute",
    "search": "2/minute",
}


class ThrottleTests(YamtrackApiTestCase):
    """Verify that exceeding a throttle rate returns HTTP 429."""

    @patch.object(SimpleRateThrottle, "THROTTLE_RATES", _THROTTLE_RATES)
    @patch("api.views.services.search")
    def test_search_is_throttled_after_the_limit(self, mock_search):
        """The third search request within the window is rejected."""
        mock_search.return_value = {
            "results": [{"id": 1, "title": "Example"}],
            "total_results": 1,
            "total_pages": 1,
        }

        responses = [
            self.call_api(
                "get",
                "api_search_provider",
                args=("movie",),
                headers=self.auth_headers,
            )
            for _ in range(3)
        ]

        self.assertEqual(
            [response.status_code for response in responses],
            [200, 200, 429],
        )
