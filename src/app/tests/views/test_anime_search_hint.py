from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from app.models import MediaTypes
from app.providers import tmdb

ANIMATION = 16
DRAMA = 18


def tmdb_result(media_id, name, genre_ids, language, countries=()):
    """Build a TMDB TV search result with the fields the provider reads."""
    return {
        "id": media_id,
        "name": name,
        "poster_path": None,
        "genre_ids": genre_ids,
        "original_language": language,
        "origin_country": list(countries),
    }


def tmdb_response(*results):
    """Wrap results in a TMDB search response envelope."""
    return {"page": 1, "results": list(results), "total_results": len(results)}


NARUTO = tmdb_result(46260, "Naruto", [ANIMATION, 10759], "ja", ["JP"])
SIMPSONS = tmdb_result(456, "The Simpsons", [ANIMATION, 35], "en", ["US"])
DORAMA = tmdb_result(61374, "Hanzawa Naoki", [DRAMA], "ja", ["JP"])


@patch("app.providers.services.api_request")
class TmdbAnimeFlagTests(TestCase):
    """TMDB search flags results that look like anime."""

    def setUp(self):
        """Avoid serving search pages cached by other tests."""
        cache.clear()

    def flags(self, mock_request, *results):
        """Search TMDB with ``results`` and map each title to its anime flag."""
        mock_request.return_value = tmdb_response(*results)
        data = tmdb.search(MediaTypes.TV.value, "query", 1)
        return {result["title"]: result["is_anime"] for result in data["results"]}

    def test_japanese_animation_is_anime(self, mock_request):
        """Animation from Japan is flagged."""
        self.assertEqual(self.flags(mock_request, NARUTO), {"Naruto": True})

    def test_japanese_language_alone_is_enough(self, mock_request):
        """Animation in Japanese is flagged even without an origin country."""
        movie = tmdb_result(1, "Spirited Away", [ANIMATION], "ja")
        self.assertEqual(self.flags(mock_request, movie), {"Spirited Away": True})

    def test_japanese_country_alone_is_enough(self, mock_request):
        """Animation produced in Japan is flagged whatever its language."""
        show = tmdb_result(2, "Animatrix", [ANIMATION], "en", ["JP", "US"])
        self.assertEqual(self.flags(mock_request, show), {"Animatrix": True})

    def test_western_animation_is_not_anime(self, mock_request):
        """Animation from elsewhere is not flagged."""
        self.assertEqual(self.flags(mock_request, SIMPSONS), {"The Simpsons": False})

    def test_japanese_live_action_is_not_anime(self, mock_request):
        """Japanese live action is not flagged."""
        self.assertEqual(self.flags(mock_request, DORAMA), {"Hanzawa Naoki": False})


@patch("app.providers.services.api_request")
class AnimeSearchHintViewTests(TestCase):
    """The search page points TV and movie searches with anime to Anime search."""

    HINT = "Some results look like anime."

    def setUp(self):
        """Create a user, log in and clear cached search pages."""
        cache.clear()
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)
        self.client.login(**credentials)

    def search(self, mock_request, *results, query="naruto"):
        """Search TV shows for ``query`` with TMDB returning ``results``."""
        mock_request.return_value = tmdb_response(*results)
        return self.client.get(
            reverse("search"), {"media_type": MediaTypes.TV.value, "q": query}
        )

    def test_hint_links_to_anime_search(self, mock_request):
        """Anime among the results shows the hint with a link to Anime search."""
        response = self.search(mock_request, NARUTO, SIMPSONS, query="naruto & co")

        self.assertContains(response, self.HINT)
        self.assertContains(
            response,
            'href="/search?q=naruto%20%26%20co&media_type=anime&layout=grid"',
        )
        self.assertContains(response, 'Search "naruto &amp; co" in Anime')

    def test_no_hint_without_anime(self, mock_request):
        """Results without anime show no hint."""
        response = self.search(mock_request, SIMPSONS, DORAMA)

        self.assertNotContains(response, self.HINT)

    def test_no_hint_when_anime_disabled(self, mock_request):
        """Users who disabled Anime are not pointed to it."""
        self.user.anime_enabled = False
        self.user.save()

        response = self.search(mock_request, NARUTO)

        self.assertNotContains(response, self.HINT)

    def test_badge_marks_only_anime_cards(self, mock_request):
        """Only anime results carry the Anime badge."""
        response = self.search(mock_request, NARUTO, SIMPSONS)

        self.assertContains(response, "Looks like anime", count=1)
