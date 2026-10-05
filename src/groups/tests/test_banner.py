import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from groups.models import Group, validate_banner
from users.tests.test_profile_custom import PNG_BYTES


def png(name="cover.png"):
    """Return a fresh uploadable 1x1 PNG."""
    return SimpleUploadedFile(name, PNG_BYTES, content_type="image/png")


class GroupBanner(TestCase):
    """The group's cover image: shown on its card, managed by the owner."""

    def setUp(self):
        """Create an owner, a member and their group; isolate MEDIA_ROOT."""
        media_root = tempfile.TemporaryDirectory()
        self.addCleanup(media_root.cleanup)
        self.media_root = Path(media_root.name)
        self.enterContext(override_settings(MEDIA_ROOT=media_root.name))

        users = get_user_model().objects
        self.owner = users.create_user(username="owner", password="pw")  # noqa: S106
        self.member = users.create_user(username="member", password="pw")  # noqa: S106
        self.group = Group.objects.create(name="Couch", owner=self.owner)
        self.group.members.add(self.owner, self.member)
        self.url = reverse("group_banner", args=[self.group.id])

    def test_owner_uploads_banner_and_card_shows_it(self):
        """The upload is stored and both the card and the detail render it."""
        self.client.force_login(self.owner)

        response = self.client.post(self.url, {"banner": png()})

        self.assertRedirects(response, reverse("group_settings", args=[self.group.id]))
        self.group.refresh_from_db()
        self.assertEqual(self.group.banner.name, "group_banners/cover.png")
        self.assertTrue((self.media_root / "group_banners/cover.png").exists())
        for url in (
            reverse("group_list"),
            reverse("group_detail", args=[self.group.id]),
        ):
            with self.subTest(url=url):
                self.assertContains(
                    self.client.get(url), 'src="/media/group_banners/cover.png"'
                )

    def test_member_cannot_change_banner(self):
        """Only the owner manages the group (CONTEXT.md, agreement 3)."""
        self.client.force_login(self.member)

        response = self.client.post(self.url, {"banner": png()})

        self.assertEqual(response.status_code, 403)
        self.group.refresh_from_db()
        self.assertEqual(self.group.banner.name, "")
        settings_page = self.client.get(reverse("group_settings", args=[self.group.id]))
        self.assertContains(settings_page, "Only the owner can change the banner.")

    def test_outsider_gets_404(self):
        """Someone outside the group cannot even see it exists."""
        outsider = get_user_model().objects.create_user(
            username="outsider",
            password="pw",  # noqa: S106
        )
        self.client.force_login(outsider)

        response = self.client.post(self.url, {"banner": png()})

        self.assertEqual(response.status_code, 404)

    def test_owner_removes_banner_and_file(self):
        """Removing clears the field and deletes the stored file."""
        self.client.force_login(self.owner)
        self.client.post(self.url, {"banner": png()})

        self.client.post(self.url, {"remove": "1"})

        self.group.refresh_from_db()
        self.assertEqual(self.group.banner.name, "")
        self.assertFalse((self.media_root / "group_banners/cover.png").exists())
        self.assertNotContains(self.client.get(reverse("group_list")), "group_banners/")

    def test_replacing_banner_deletes_the_old_file(self):
        """A new upload replaces the previous file rather than orphaning it."""
        self.client.force_login(self.owner)
        self.client.post(self.url, {"banner": png("first.png")})

        self.client.post(self.url, {"banner": png("second.png")})

        self.group.refresh_from_db()
        self.assertEqual(self.group.banner.name, "group_banners/second.png")
        self.assertFalse((self.media_root / "group_banners/first.png").exists())

    def test_rejects_non_image_and_keeps_current_banner(self):
        """A non-image is refused with a message; the banner is unchanged."""
        self.client.force_login(self.owner)
        self.client.post(self.url, {"banner": png()})

        response = self.client.post(
            self.url,
            {"banner": SimpleUploadedFile("x.png", b"<svg/>", "image/png")},
            follow=True,
        )

        self.assertContains(response, "Upload a valid image")
        self.group.refresh_from_db()
        self.assertEqual(self.group.banner.name, "group_banners/cover.png")

    def test_rejects_oversized_banner(self):
        """Banners over 5 MB are rejected."""
        big = SimpleUploadedFile("big.png", b"x" * (5 * 1024 * 1024 + 1))
        with self.assertRaisesMessage(ValidationError, "smaller than 5 MB"):
            validate_banner(big)

    def test_create_group_with_banner(self):
        """The create form accepts an optional banner."""
        self.client.force_login(self.owner)

        self.client.post(
            reverse("group_create"),
            {"name": "Movie night", "banner": png("night.png")},
        )

        group = Group.objects.get(name="Movie night")
        self.assertEqual(group.banner.name, "group_banners/night.png")

    def test_create_group_with_invalid_banner_creates_nothing(self):
        """An invalid banner keeps the form, with the error, and no group."""
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("group_create"),
            {"name": "Movie night", "banner": SimpleUploadedFile("x.png", b"no")},
        )

        self.assertContains(response, "Upload a valid image")
        self.assertFalse(Group.objects.filter(name="Movie night").exists())

    def test_card_without_banner_keeps_the_plain_look(self):
        """No banner: no image in the card header."""
        self.client.force_login(self.owner)

        response = self.client.get(reverse("group_list"))

        self.assertNotContains(response, "group_banners/")
        self.assertContains(response, "bg-gradient-to-t from-[#1e2329]")
