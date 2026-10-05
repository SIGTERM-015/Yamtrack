from django.conf import settings
from django.db import models

from app.models import Item, Status
from users.models import validate_image_upload

MAX_BANNER_SIZE = 5 * 1024 * 1024  # 5 MB


def validate_banner(image):
    """Reject banners that are too large or not a supported image format."""
    validate_image_upload(image, max_size=MAX_BANNER_SIZE, label="Banner")


class Group(models.Model):
    """
    Model representing a group of users.

    A group allows users to share a common list of items.
    """

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    banner = models.ImageField(
        upload_to="group_banners/",
        blank=True,
        validators=[validate_banner],
        help_text="Cover image (max 5 MB, JPEG/PNG/WEBP/GIF).",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_groups",
    )
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="GroupMembership",
        related_name="joined_groups",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        """Return string representation."""
        return self.name


class GroupMembership(models.Model):
    """
    Intermediate model for group membership.

    Tracks when a user joined a group.
    """

    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="group_memberships",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Meta class."""

        constraints = [
            models.UniqueConstraint(
                fields=["group", "user"], name="unique_group_membership"
            ),
        ]

    def __str__(self):
        """Return string representation."""
        return f"{self.user} in {self.group}"


class GroupItem(models.Model):
    """
    Model representing an item added to a group.

    Tracks which user added the item to the group and when.
    """

    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="group_items"
    )
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="group_items")
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="added_group_items",
    )
    added_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=255,
        choices=Status,
        default=Status.PLANNING.value,
    )
    progress = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    progressed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Meta class."""

        constraints = [
            models.UniqueConstraint(fields=["group", "item"], name="unique_group_item"),
        ]

    def __str__(self):
        """Return string representation."""
        return f"{self.item} in {self.group}"


class GroupEpisodeWatch(models.Model):
    """
    Model representing an episode watched by a group.

    The group tracks its own episode ledger (one row per episode) instead of a
    counter, so propagation can tell exactly which episodes the group watched.
    """

    group_item = models.ForeignKey(
        GroupItem, on_delete=models.CASCADE, related_name="watched_episodes"
    )
    item = models.ForeignKey(
        Item, on_delete=models.CASCADE, related_name="group_episode_watches"
    )
    watched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Meta class."""

        constraints = [
            models.UniqueConstraint(
                fields=["group_item", "item"], name="unique_group_episode_watch"
            ),
        ]
        indexes = [models.Index(fields=["group_item"])]

    def __str__(self):
        """Return string representation."""
        return f"{self.item} watched by {self.group_item.group}"


class GroupOrigin(models.Model):
    """
    Model tracking the origin of an item in a user's personal profile.

    Product rule: "if you touched it, it's yours; if you didn't touch it,
    it's the group's."
    When an item enters a user's personal profile because a group added it, this model
    records that origin. If the user later interacts with the item (e.g., scores it,
    starts it), the 'detached' flag should be set to True.

    While 'detached' is False, the item is considered "owned by the group" and can
    be automatically removed from the user's profile if it's removed from the group.
    Once 'detached' is True, it is independent user data.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="group_origins"
    )
    item = models.ForeignKey(
        Item, on_delete=models.CASCADE, related_name="group_origins"
    )
    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="group_origins"
    )
    detached = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Meta class."""

        constraints = [
            models.UniqueConstraint(
                fields=["user", "item", "group"], name="unique_group_origin"
            ),
        ]
        indexes = [
            models.Index(fields=["user", "item"]),
        ]

    def __str__(self):
        """Return string representation."""
        return f"{self.item} for {self.user} from {self.group}"


class GroupInvitation(models.Model):
    """
    Pending invitation for a user to join a group.

    A row exists only while the invitation is pending. Accepting it creates a
    GroupMembership; rejecting it deletes the row.
    """

    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="invitations"
    )
    invited_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="group_invitations",
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_group_invitations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Meta class."""

        constraints = [
            models.UniqueConstraint(
                fields=["group", "invited_user"], name="unique_group_invitation"
            ),
        ]

    def __str__(self):
        """Return string representation."""
        return f"Invitation of {self.invited_user} to {self.group}"


class GroupDiscard(models.Model):
    """
    A group's "not interested" mark for an item.

    Reversible and distinct from a score or the Dropped status. One member
    discarding is enough to hide the item for the whole group; any member can
    restore it. Independent from any individual discard on the same item
    (CONTEXT.md, ADR 0002 point 9).
    """

    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="discards")
    item = models.ForeignKey(
        Item, on_delete=models.CASCADE, related_name="group_discards"
    )
    discarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="group_discards_made",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Meta class."""

        constraints = [
            models.UniqueConstraint(
                fields=["group", "item"], name="unique_group_discard"
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        """Return string representation."""
        return f"{self.item} discarded by {self.group}"
