from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.forms import MovieForm
from app.models import (
    TV,
    Item,
    MediaTypes,
    Movie,
    Sources,
    Status,
)
from app.templatetags import app_tags
from lists.models import CustomList, CustomListItem
from users.forms import UserUpdateForm


class MediaListViewTests(TestCase):
    """Test the media list view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.external_credentials = {
            "username": "test2",
            "password": "12345",
            "profile_private": True,
        }
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.external_user = get_user_model().objects.create_user(
            **self.external_credentials
        )
        self.client.login(**self.credentials)
        self.metadata_patcher = patch("app.providers.services.get_media_metadata")
        self.mock_get_media_metadata = self.metadata_patcher.start()
        self.mock_get_media_metadata.return_value = {"max_progress": 1}
        self.addCleanup(self.metadata_patcher.stop)

        movies_id = ["278", "238", "129", "424", "680"]
        num_completed = 3
        for i in range(1, 6):
            item = Item.objects.create(
                media_id=movies_id[i - 1],
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Test Movie {i}",
                image="http://example.com/image.jpg",
            )
            status = (
                Status.COMPLETED.value
                if i < num_completed
                else Status.IN_PROGRESS.value
            )
            Movie.objects.create(
                item=item,
                user=self.user,
                status=status,
                progress=1 if i < num_completed else 0,
                score=i,
            )

    def test_media_list_view(self):
        """Test the media list view displays media items."""
        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value])
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_list.html")

        self.assertIn("media_list", response.context)
        self.assertEqual(response.context["media_list"].paginator.count, 5)

        self.assertIn("sort_choices", response.context)
        self.assertIn("status_choices", response.context)
        self.assertEqual(response.context["media_type"], MediaTypes.MOVIE.value)
        self.assertEqual(
            response.context["media_type_plural"],
            app_tags.media_type_readable_plural(MediaTypes.MOVIE.value).lower(),
        )

    def test_media_list_with_filters(self):
        """Test the media list view with filters."""
        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value])
            + "?status=Completed&sort=score&layout=table",
        )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            response.context["current_status"],
            Status.COMPLETED.value,
        )
        self.assertEqual(response.context["current_sort"], "score")
        self.assertEqual(response.context["current_layout"], "table")

        self.assertEqual(response.context["media_list"].paginator.count, 2)

        self.user.refresh_from_db()
        self.assertEqual(self.user.movie_status, Status.COMPLETED.value)
        self.assertEqual(self.user.movie_sort, "score")
        self.assertEqual(self.user.movie_layout, "table")

    def test_media_list_htmx_request(self):
        """Test the media list view with HTMX request."""
        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value])
            + "?layout=grid",
            headers={"hx-request": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/media_grid_items.html")

        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value])
            + "?layout=table",
            headers={"hx-request": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/media_table_items.html")

    def test_media_list_soft_navigation_returns_full_page(self):
        """Soft-navigation body swaps (after an edit modal) get the full page."""
        response = self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value]),
            headers={"hx-request": "true", "x-soft-navigation": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_list.html")

    def test_public_media_list_ignores_invalid_filters(self):
        """Test invalid public filters fall back to the target user's preferences."""
        self.external_user.profile_private = False
        self.external_user.save(update_fields=["profile_private"])

        response = self.client.get(
            reverse(
                "medialist", args=[self.external_user.username, MediaTypes.MOVIE.value]
            )
            + "?status=invalid&sort=bad_field&layout=invalid",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["current_status"], self.external_user.movie_status
        )
        self.assertEqual(
            response.context["current_sort"], self.external_user.movie_sort
        )
        self.assertEqual(
            response.context["current_layout"], self.external_user.movie_layout
        )

    def test_anonymous_user_can_view_public_media_list(self):
        """Test anonymous users can view public media lists."""
        self.external_user.profile_private = False
        self.external_user.save(update_fields=["profile_private"])
        self.client.logout()

        response = self.client.get(
            reverse(
                "medialist", args=[self.external_user.username, MediaTypes.MOVIE.value]
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("media_list", response.context)

    def test_profile_private_defaults_to_false(self):
        """New users have public profiles by default (CONTEXT.md 20/22)."""
        user = get_user_model().objects.create_user(
            username="private-default",
        )

        self.assertFalse(user.profile_private)

    def test_private_media_list(self):
        """Test the private media list view."""
        response = self.client.get(
            reverse(
                "medialist", args=[self.external_user.username, MediaTypes.MOVIE.value]
            )
        )
        self.assertEqual(response.status_code, 404)

        form = UserUpdateForm(
            data={"username": "test2", "profile_private": False},
            instance=self.external_user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        external_user = form.save()
        external_user.refresh_from_db()

        response = self.client.get(
            reverse(
                "medialist", args=[self.external_user.username, MediaTypes.MOVIE.value]
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("media_list", response.context)

    def _create_note(self, user, notes, notes_public, score=None):
        """Create a completed movie entry carrying a note."""
        item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Review Movie",
            image="http://example.com/image.jpg",
        )
        return Movie.objects.create(
            item=item,
            user=user,
            status=Status.COMPLETED.value,
            notes=notes,
            notes_public=notes_public,
            score=score,
        )

    def test_public_review_visible_to_anonymous(self):
        """Notes marked public appear as reviews on a public profile."""
        self._create_note(self.external_user, "Great movie", notes_public=True)
        self.external_user.profile_private = False
        self.external_user.save(update_fields=["profile_private"])
        self.client.logout()

        response = self.client.get(
            reverse(
                "medialist", args=[self.external_user.username, MediaTypes.MOVIE.value]
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Great movie")

    def test_public_review_on_tv_list_without_end_date(self):
        """TV has no end_date field; its public reviews still render."""
        item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Review Show",
            image="http://example.com/image.jpg",
        )
        TV.objects.create(
            item=item,
            user=self.external_user,
            status=Status.IN_PROGRESS.value,
            notes="Great show",
            notes_public=True,
        )
        self.external_user.profile_private = False
        self.external_user.save(update_fields=["profile_private"])
        self.client.logout()

        response = self.client.get(
            reverse(
                "medialist", args=[self.external_user.username, MediaTypes.TV.value]
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Great show")

    def test_private_review_hidden(self):
        """Notes not marked public never appear as reviews."""
        self._create_note(self.external_user, "Secret note", notes_public=False)
        self.external_user.profile_private = False
        self.external_user.save(update_fields=["profile_private"])
        self.client.logout()

        response = self.client.get(
            reverse(
                "medialist", args=[self.external_user.username, MediaTypes.MOVIE.value]
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Secret note")

    def test_public_review_shows_score_and_link_to_title_page(self):
        """Visitors see the score and comment on a poster opening the title page."""
        review = self._create_note(
            self.external_user,
            "Loved it",
            notes_public=True,
            score=9,
        )
        self.external_user.profile_private = False
        self.external_user.save(update_fields=["profile_private"])
        self.client.logout()

        response = self.client.get(
            reverse(
                "medialist", args=[self.external_user.username, MediaTypes.MOVIE.value]
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "9")
        self.assertContains(response, "Loved it")
        self.assertContains(
            response,
            reverse(
                "profile_media",
                args=[
                    self.external_user.username,
                    review.item.media_type,
                    review.item.source,
                    review.item.media_id,
                ],
            ),
        )

    def test_notes_public_defaults_to_true(self):
        """New entries share their notes on a public profile by default."""
        item = Item.objects.create(
            media_id="551",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Default Review Movie",
            image="http://example.com/image.jpg",
        )
        review = Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            notes="Default note",
        )
        review.refresh_from_db()

        self.assertTrue(review.notes_public)

    def test_track_form_prechecks_public_note_only_for_new_entries(self):
        """A new entry's form starts public; an existing private note stays so."""
        self.assertIn("checked", str(MovieForm()["notes_public"]))
        private = Movie(notes_public=False)
        self.assertNotIn("checked", str(MovieForm(instance=private)["notes_public"]))


class ProfileSectionVisibilityViewTests(TestCase):
    """Tests for per-section visibility on the public profile page."""

    def setUp(self):
        """Create a public owner with a heatmap completion, a shelf and a review."""
        self.credentials = {"username": "owner_sections", "password": "12345"}
        self.owner = get_user_model().objects.create_user(
            **self.credentials,
            profile_private=False,
        )
        self.visitor_credentials = {"username": "visitor_sections", "password": "12345"}
        self.visitor = get_user_model().objects.create_user(**self.visitor_credentials)

        item = Item.objects.create(
            media_id="9001",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Section Movie",
            image="http://example.com/image.jpg",
        )
        # bulk_create bypasses the custom save so no provider lookups fire.
        Movie.objects.bulk_create(
            [
                Movie(
                    item=item,
                    user=self.owner,
                    status=Status.COMPLETED.value,
                    notes="A public review",
                    notes_public=True,
                ),
            ],
        )
        self.review = Movie.objects.get(item=item, user=self.owner)
        Movie.objects.filter(pk=self.review.pk).update(
            end_date="2026-01-01T00:00:00Z",
        )

        self.shelf = CustomList.objects.create(
            name="Owner Shelf",
            owner=self.owner,
            is_featured=True,
        )
        CustomListItem.objects.create(custom_list=self.shelf, item=item)

    def _get_profile(self):
        return self.client.get(reverse("profile", args=[self.owner.username]))

    def test_owner_sees_all_sections_even_if_hidden(self):
        """The owner always sees their own sections, regardless of the toggles."""
        self.owner.profile_show_heatmap = False
        self.owner.profile_show_shelves = False
        self.owner.profile_show_reviews = False
        self.owner.save()
        self.client.login(**self.credentials)

        response = self._get_profile()

        self.assertContains(response, "Activity over the last months")
        self.assertContains(response, "Owner Shelf")
        self.assertContains(response, "A public review")

    def test_visitor_sees_enabled_sections_by_default(self):
        """A logged-in visitor sees all sections by default (default to visible)."""
        self.client.login(**self.visitor_credentials)

        response = self._get_profile()

        self.assertContains(response, "Activity over the last months")
        self.assertContains(response, "Owner Shelf")
        self.assertContains(response, "A public review")

    def test_visitor_does_not_see_disabled_heatmap(self):
        """Hiding the heatmap section removes it for a visitor but not the owner."""
        self.owner.profile_show_heatmap = False
        self.owner.save()
        self.client.login(**self.visitor_credentials)

        response = self._get_profile()

        self.assertNotContains(response, "Activity over the last months")

    def test_visitor_does_not_see_disabled_shelves(self):
        """Hiding shelves removes the section for a visitor."""
        self.owner.profile_show_shelves = False
        self.owner.save()
        self.client.login(**self.visitor_credentials)

        response = self._get_profile()

        self.assertNotContains(response, "Owner Shelf")

    def test_visitor_does_not_see_disabled_reviews(self):
        """Hiding reviews removes the section for a visitor."""
        self.owner.profile_show_reviews = False
        self.owner.save()
        self.client.login(**self.visitor_credentials)

        response = self._get_profile()

        self.assertNotContains(response, "A public review")

    def test_anonymous_visitor_follows_the_same_section_rules(self):
        """Anonymous visitors are gated by the same per-section toggles."""
        self.owner.profile_show_reviews = False
        self.owner.save()

        response = self._get_profile()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activity over the last months")
        self.assertNotContains(response, "A public review")


class HeatmapDayDetailCrossUserTests(TestCase):
    """Tests for the heatmap day-detail endpoint across owner/visitor/anonymous."""

    def setUp(self):
        """Create a public owner with a completion, and a separate visitor."""
        self.credentials = {"username": "heatmap_owner", "password": "12345"}
        self.owner = get_user_model().objects.create_user(
            **self.credentials,
            profile_private=False,
        )
        self.visitor_credentials = {"username": "heatmap_visitor", "password": "12345"}
        self.visitor = get_user_model().objects.create_user(**self.visitor_credentials)

        item = Item.objects.create(
            media_id="9101",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Heatmap Detail Movie",
            image="none.jpg",
        )
        self.movie = Movie.objects.create(
            item=item,
            user=self.owner,
            status=Status.COMPLETED.value,
        )
        Movie.objects.filter(pk=self.movie.pk).update(
            end_date="2026-01-05T12:00:00Z",
        )

    def _detail(self, username=None):
        params = {"date": "2026-01-05"}
        if username:
            params["username"] = username
        return self.client.get(reverse("heatmap_day_detail"), params)

    def test_visitor_sees_public_owners_detail(self):
        """A logged-in visitor can see another user's day detail when public."""
        self.client.login(**self.visitor_credentials)

        response = self._detail(username=self.owner.username)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Heatmap Detail Movie")

    def test_anonymous_sees_public_owners_detail(self):
        """An anonymous visitor can see a public profile's day detail."""
        response = self._detail(username=self.owner.username)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Heatmap Detail Movie")

    def test_hidden_heatmap_section_blocks_visitors(self):
        """Turning off the heatmap section 404s the endpoint for other viewers."""
        self.owner.profile_show_heatmap = False
        self.owner.save()

        response = self._detail(username=self.owner.username)

        self.assertEqual(response.status_code, 404)

    def test_private_profile_blocks_visitors(self):
        """A private profile blocks the day-detail endpoint for other viewers."""
        self.owner.profile_private = True
        self.owner.save()

        response = self._detail(username=self.owner.username)

        self.assertEqual(response.status_code, 404)

    def test_owner_sees_their_own_detail_even_if_hidden(self):
        """The owner can always see their own detail, toggle or privacy aside."""
        self.owner.profile_private = True
        self.owner.profile_show_heatmap = False
        self.owner.save()
        self.client.login(**self.credentials)

        response = self._detail(username=self.owner.username)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Heatmap Detail Movie")

    def test_missing_username_uses_the_logged_in_users_own_data(self):
        """Without a username, the endpoint falls back to the caller's own data."""
        self.client.login(**self.credentials)

        response = self._detail()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Heatmap Detail Movie")

    def test_missing_username_and_anonymous_is_a_bad_request(self):
        """An anonymous caller with no username has no data to scope to."""
        response = self._detail()

        self.assertEqual(response.status_code, 400)
