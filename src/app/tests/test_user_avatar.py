import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template import Context, Template
from django.test import TestCase, override_settings

from groups.models import Group
from users.tests.test_profile_custom import PNG_BYTES


def render(template, **context):
    """Render a template snippet that loads app_tags."""
    return Template("{% load app_tags %}" + template).render(Context(context))


class UserAvatarTag(TestCase):
    """The single avatar component used wherever a person is shown."""

    def setUp(self):
        """Create a person with no photo."""
        self.media_root = tempfile.TemporaryDirectory()
        self.enterContext(override_settings(MEDIA_ROOT=self.media_root.name))
        self.addCleanup(self.media_root.cleanup)
        self.person = get_user_model().objects.create_user(
            username="alice",
            password="pw",  # noqa: S106
        )

    def test_without_photo_shows_the_initial(self):
        """No avatar: the grey initial bubble, no image."""
        html = render("{% user_avatar person %}", person=self.person)
        self.assertIn(">A</span>", html)
        self.assertIn("bg-[#4b5563]", html)
        self.assertNotIn("<img", html)

    def test_with_photo_covers_the_initial_and_falls_back_on_error(self):
        """Avatar set: the photo covers the initial and removes itself on error."""
        self.person.avatar = SimpleUploadedFile(
            "face.png", PNG_BYTES, content_type="image/png"
        )
        self.person.save()

        html = render("{% user_avatar person %}", person=self.person)

        self.assertIn('src="/media/avatars/face.png"', html)
        self.assertIn("object-cover", html)
        self.assertIn('onerror="this.remove()"', html)
        self.assertIn(">A</span>", html)

    def test_owner_is_painted_indigo(self):
        """The owner of the group or list stands out in indigo."""
        html = render(
            "{% user_avatar person owner=person %}",
            person=self.person,
        )
        self.assertIn("bg-[#4f46e5]", html)

    def test_link_opens_the_profile(self):
        """With link=True the avatar is an anchor to the profile."""
        html = render("{% user_avatar person link=True %}", person=self.person)
        self.assertIn('<a href="/alice"', html)
        self.assertIn("hover:ring-indigo-400", html)

    def test_group_pages_show_member_photos(self):
        """Group list and detail swap a member's initial for their photo."""
        self.person.avatar = SimpleUploadedFile(
            "face.png", PNG_BYTES, content_type="image/png"
        )
        self.person.save()
        group = Group.objects.create(name="Couch", owner=self.person)
        group.members.add(self.person)
        self.client.force_login(self.person)

        for url in ("/groups/", f"/groups/{group.id}/"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, 'src="/media/avatars/face.png"')
