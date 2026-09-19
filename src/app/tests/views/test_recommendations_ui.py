"""View tests for the recommendations UI (E8.4).

The provider metadata lookup is mocked, mirroring ``test_home.py``: a stored
movie stands in for the user's history and its metadata supplies the candidate
list through ``related.recommendations``.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import Item, MediaTypes, Movie, Sources, Status


class RecommendationViewTests(TestCase):
    """Test the recommendations page and its one-click actions."""

    def setUp(self):
        """Create a user with one watched movie and mock the provider."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        # Patch before saving media: ``Movie.save`` calls ``process_status``,
        # which hits the provider for ``max_progress``.
        self.metadata_patcher = patch("app.providers.services.get_media_metadata")
        self.mock_get_media_metadata = self.metadata_patcher.start()
        self.mock_get_media_metadata.side_effect = self._metadata
        self.addCleanup(self.metadata_patcher.stop)

        item = Item.objects.create(
            media_id="source1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Source Movie",
            image="http://example.com/source.jpg",
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            score=8,
        )

    @staticmethod
    def _metadata(
        _media_type,
        media_id,
        _source,
        _season_numbers=None,
        _episode_number=None,
    ):
        """Return metadata with two candidate recommendations for the source."""
        if media_id == "source1":
            return {
                "title": "Source Movie",
                "image": "http://example.com/source.jpg",
                "max_progress": 0,
                "related": {
                    "recommendations": [
                        {
                            "source": Sources.TMDB.value,
                            "media_type": MediaTypes.MOVIE.value,
                            "media_id": "rec1",
                            "title": "Recommended One",
                            "image": "http://example.com/1.jpg",
                        },
                        {
                            "source": Sources.TMDB.value,
                            "media_type": MediaTypes.MOVIE.value,
                            "media_id": "rec2",
                            "title": "Recommended Two",
                            "image": "http://example.com/2.jpg",
                        },
                    ],
                },
            }
        return {
            "title": media_id,
            "image": "",
            "max_progress": 0,
            "related": {"recommendations": []},
        }

    def test_requires_login(self):
        """Anonymous visitors are sent to the login page."""
        self.client.logout()
        response = self.client.get(reverse("recommendations"))
        self.assertEqual(response.status_code, 302)

    def test_lists_ranked_candidates(self):
        """The page renders the provider candidates with a planning action."""
        response = self.client.get(reverse("recommendations"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Recommended One", content)
        self.assertIn("Recommended Two", content)
        self.assertIn(reverse("media_save"), content)
        self.assertIn('value="Planning"', content)

    def test_group_mode_is_stub(self):
        """The group tab renders its pending note instead of candidates."""
        response = self.client.get(reverse("recommendations"), {"mode": "group"})
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("E8.5", content)
        self.assertNotIn("Recommended One", content)

    def test_exclude_hides_candidate(self):
        """A dismissed identity is removed from the ranking."""
        response = self.client.get(
            reverse("recommendations"),
            {"exclude": f"{Sources.TMDB.value}|rec1|{MediaTypes.MOVIE.value}"},
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("Recommended One", content)
        self.assertIn("Recommended Two", content)

    def test_one_click_adds_to_planning(self):
        """The rendered form payload plans the candidate and returns to the page."""
        response = self.client.post(
            f"{reverse('media_save')}?next={reverse('recommendations')}",
            {
                "media_id": "rec1",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "status": Status.PLANNING.value,
                "progress": 0,
            },
        )
        self.assertRedirects(response, reverse("recommendations"))
        self.assertTrue(
            Movie.objects.filter(
                user=self.user,
                item__media_id="rec1",
                status=Status.PLANNING.value,
            ).exists(),
        )
