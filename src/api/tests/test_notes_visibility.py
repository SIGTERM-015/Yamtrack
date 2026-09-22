"""Tests that media notes are exposed according to the ``notes_public`` flag."""

from rest_framework.test import APIRequestFactory

from api.serializers import (
    HistorySerializer,
    MediaSerializer,
    TimelineItemSerializer,
)
from app.models import MediaTypes

from .base import YamtrackApiTestCase

PRIVATE_NOTE = "not for other eyes"

SERIALIZERS = (MediaSerializer, HistorySerializer, TimelineItemSerializer)


class NotesVisibilityTestCase(YamtrackApiTestCase):
    """The API must mirror the web's public-review rule for notes."""

    def setUp(self):
        """Give user1 a tracked movie carrying a note."""
        super().setUp()
        self.media = self.movie_medias[0]
        self.media.notes = PRIVATE_NOTE
        self.media.notes_public = False
        self.media.save()

    def serialize_for(self, serializer_class, viewer):
        """Serialize the tracked movie as ``viewer`` would see it."""
        request = APIRequestFactory().get("/")
        request.user = viewer
        return serializer_class(self.media, context={"request": request}).data

    def test_owner_sees_own_notes_when_private(self):
        """The owner always sees their own notes."""
        for serializer_class in SERIALIZERS:
            with self.subTest(serializer=serializer_class.__name__):
                data = self.serialize_for(serializer_class, self.user1)
                self.assertEqual(data["notes"], PRIVATE_NOTE)

    def test_owner_sees_own_notes_when_public(self):
        """Making a note public does not change what the owner sees."""
        self.media.notes_public = True
        self.media.save()

        for serializer_class in SERIALIZERS:
            with self.subTest(serializer=serializer_class.__name__):
                data = self.serialize_for(serializer_class, self.user1)
                self.assertEqual(data["notes"], PRIVATE_NOTE)

    def test_other_user_does_not_see_private_notes(self):
        """Another user must not see notes flagged as private."""
        for serializer_class in SERIALIZERS:
            with self.subTest(serializer=serializer_class.__name__):
                data = self.serialize_for(serializer_class, self.user2)
                self.assertIsNone(data["notes"])

    def test_other_user_sees_public_notes(self):
        """Another user sees the notes the owner marked public."""
        self.media.notes_public = True
        self.media.save()

        for serializer_class in SERIALIZERS:
            with self.subTest(serializer=serializer_class.__name__):
                data = self.serialize_for(serializer_class, self.user2)
                self.assertEqual(data["notes"], PRIVATE_NOTE)

    def test_media_endpoint_still_returns_owner_notes(self):
        """The media endpoint keeps returning the owner's own notes."""
        response = self.call_api(
            "get",
            "api_media_type_list",
            args=(MediaTypes.MOVIE.value,),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        notes = [item["notes"] for item in response.json()["results"]]
        self.assertIn(PRIVATE_NOTE, notes)
