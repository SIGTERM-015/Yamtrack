"""View tests for the recommendations UI (E8.4).

The provider metadata lookup is mocked, mirroring ``test_home.py``: a stored
movie stands in for the user's history and its metadata supplies the candidate
list through ``related.recommendations``.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import Discard, Item, MediaTypes, Movie, Sources, Status
from groups.models import Group, GroupItem


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
        titles = {"rec1": "Recommended One", "rec2": "Recommended Two"}
        return {
            "title": titles.get(media_id, media_id),
            "image": f"http://example.com/{media_id}.jpg",
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

    def test_group_mode_without_groups_shows_message(self):
        """A user with no groups sees an explanatory message, not candidates."""
        response = self.client.get(reverse("recommendations"), {"mode": "group"})
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("Recommended One", content)
        self.assertIn("You're not in any group yet", content)

    def test_group_mode_shows_group_candidates(self):
        """Group mode ranks candidates seeded from every member's history."""
        other = get_user_model().objects.create_user(
            username="other",
            password="12345",  # noqa: S106
        )
        group = Group.objects.create(name="Watch club", owner=self.user)
        group.members.add(self.user, other)

        response = self.client.get(
            reverse("recommendations"), {"mode": "group", "group": group.id}
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Recommended One", content)
        self.assertIn(reverse("group_recommend_add", args=[group.id]), content)
        self.assertIn(reverse("group_discard_item", args=[group.id]), content)

    def test_group_mode_excludes_group_library_and_discards(self):
        """The group's own items and its discards never show up as candidates."""
        other = get_user_model().objects.create_user(
            username="other",
            password="12345",  # noqa: S106
        )
        group = Group.objects.create(name="Watch club", owner=self.user)
        group.members.add(self.user, other)

        rec2_item = Item.objects.create(
            media_id="rec2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Recommended Two",
            image="http://example.com/2.jpg",
        )
        GroupItem.objects.create(group=group, item=rec2_item, added_by=self.user)

        response = self.client.post(
            reverse("group_discard_item", args=[group.id]),
            {
                "media_id": "rec1",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
            },
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.get(
            reverse("recommendations"), {"mode": "group", "group": group.id}
        )
        content = response.content.decode()
        self.assertNotIn("Recommended One", content)
        self.assertNotIn("Recommended Two", content)

    def test_discard_hides_candidate_and_persists(self):
        """Discarding a candidate removes it from the ranking, for good."""
        response = self.client.post(
            reverse("discard_item"),
            {
                "media_id": "rec1",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "next": reverse("recommendations"),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Discard.objects.filter(user=self.user, item__media_id="rec1").exists()
        )

        response = self.client.get(reverse("recommendations"))
        content = response.content.decode()
        self.assertNotIn("Recommended One", content)
        self.assertIn("Recommended Two", content)

    def test_discarded_tab_lists_and_restores(self):
        """The Discarded tab lists a discard and its Restore action undoes it."""
        self.client.post(
            reverse("discard_item"),
            {
                "media_id": "rec1",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
            },
        )
        response = self.client.get(reverse("recommendations"), {"mode": "discarded"})
        self.assertIn("Recommended One", response.content.decode())

        item = Item.objects.get(media_id="rec1")
        response = self.client.post(reverse("restore_item", args=[item.id]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Discard.objects.filter(user=self.user, item=item).exists())

        response = self.client.get(reverse("recommendations"))
        self.assertIn("Recommended One", response.content.decode())

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
