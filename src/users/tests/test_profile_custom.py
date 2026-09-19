import base64
import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from users.models import validate_avatar

# 1x1 transparent PNG.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg==",
)


class ProfileCustomization(TestCase):
    """Test the E9.3 profile customization fields."""

    def setUp(self):
        """Create and log in a user."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_saves_bio_and_social_links(self):
        """Posting the account form persists bio and social links."""
        self.client.post(
            reverse("account"),
            {
                "username": "test",
                "bio": "Hello there",
                "letterboxd": "https://letterboxd.com/test",
                "mastodon": "https://mastodon.social/@test",
            },
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.bio, "Hello there")
        self.assertEqual(self.user.letterboxd, "https://letterboxd.com/test")
        self.assertEqual(self.user.mastodon, "https://mastodon.social/@test")

    def test_uploads_avatar(self):
        """Posting an avatar file stores it on the user."""
        with (
            tempfile.TemporaryDirectory() as media_root,
            override_settings(MEDIA_ROOT=media_root),
        ):
            self.client.post(
                reverse("account"),
                {
                    "username": "test",
                    "avatar": SimpleUploadedFile(
                        "avatar.png", PNG_BYTES, content_type="image/png"
                    ),
                },
            )
            self.user.refresh_from_db()
            self.assertIn("avatars/", self.user.avatar.name)

    def test_rejects_oversized_avatar(self):
        """Avatars larger than the limit are rejected."""
        big = SimpleUploadedFile(
            "big.png", b"x" * (2 * 1024 * 1024 + 1), content_type="image/png"
        )
        with self.assertRaises(ValidationError):
            validate_avatar(big)

    def test_rejects_non_image_avatar(self):
        """Files that are not images are rejected."""
        not_image = SimpleUploadedFile(
            "fake.png", b"not an image", content_type="image/png"
        )
        with self.assertRaises(ValidationError):
            validate_avatar(not_image)

    def test_renders_account_page_with_custom_fields(self):
        """Account view renders successfully and displays profile fields."""
        response = self.client.get(reverse("account"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Avatar")
        self.assertContains(response, "Bio")
        self.assertContains(response, "Social links")
        self.assertContains(response, 'enctype="multipart/form-data"')
