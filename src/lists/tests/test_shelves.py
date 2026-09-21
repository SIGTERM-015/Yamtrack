from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import Item, MediaTypes, Sources
from lists.models import CustomList


class FeaturedShelvesTest(TestCase):
    """Test featured shelves on the public profile."""

    def setUp(self):
        """Create an owner with a public profile and one item."""
        self.user = get_user_model().objects.create_user(
            username="shelfowner",
            password="12345",  # noqa: S106
            profile_private=False,
        )
        self.item = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Shelf Movie",
        )

    def _make_shelf(self, name, sort_order, *, featured=True):
        """Create a shelf owned by the user holding the shared item."""
        shelf = CustomList.objects.create(
            name=name,
            owner=self.user,
            is_featured=featured,
            sort_order=sort_order,
        )
        shelf.items.add(self.item)
        return shelf

    def test_featured_shelf_visible_on_public_profile(self):
        """A featured shelf and its titles show on the public profile."""
        shelf = self._make_shelf("Mi top 10 de 2026", 0)

        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mi top 10 de 2026")
        self.assertContains(response, "Shelf Movie")
        self.assertEqual(list(response.context["featured_shelves"]), [shelf])

    def test_non_featured_list_not_shown_as_shelf(self):
        """A regular list is not rendered as a shelf."""
        self._make_shelf("Private Notes", 0, featured=False)

        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Private Notes")
        self.assertEqual(list(response.context["featured_shelves"]), [])

    def test_shelves_ordered_manually(self):
        """Shelves are ordered by their manual sort_order."""
        second = self._make_shelf("Second", 2)
        first = self._make_shelf("First", 1)

        shelves = list(CustomList.objects.get_featured_shelves(self.user))

        self.assertEqual(shelves, [first, second])
