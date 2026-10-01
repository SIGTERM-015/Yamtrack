"""
Group endpoints for the REST API (ADR 0001 §7, E11.2-E11.4).

Every operation runs as the authenticated user and follows the same rules as
the web UI, by calling the same services: only members can see or act on a
group (others get 404), group progress never reduces personal progress nor
reopens completed records, and removing an item keeps personal records.
Creating groups and managing members stay out of the API on purpose.
"""

from http import HTTPStatus as HTTP  # noqa: N814

from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import permissions
from rest_framework import views as drf_views
from rest_framework.response import Response

from app.models import Item, MediaTypes, Status
from groups import discards as discard_service
from groups.models import Group, GroupItem
from groups.services import (
    add_item_to_group,
    episodes_up_to,
    get_group_progress,
    get_group_tab_items,
    mark_group_episodes_watched,
    mark_group_item_status,
)
from lists.views import get_or_create_item

from .helpers import check_source_type, paginate_data, parse_limit_offset
from .serializers import ItemSerializer

GROUP_TABS = ("pending", "watching", "watched", "others")


def _bad_request(detail):
    return Response({"detail": detail}, status=HTTP.BAD_REQUEST)


def _get_member_group(request, group_id):
    """Return the group if the user is a member; 404 otherwise (no leak)."""
    group = get_object_or_404(Group, id=group_id)
    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found."
        raise Http404(msg)
    return group


def _get_group_item(group, media_type, source, media_id):
    """Return the GroupItem for a top-level media identifier, or 404."""
    return get_object_or_404(
        GroupItem.objects.select_related("item", "added_by"),
        group=group,
        item__media_type=media_type,
        item__source=source,
        item__media_id=media_id,
        item__season_number=None,
        item__episode_number=None,
    )


def _parse_media_identifier(body):
    """
    Validate ``media_type``/``source``/``media_id`` from a request body.

    Returns ``(identifier, error_response)``.
    """
    media_type = body.get("media_type")
    source = body.get("source")
    media_id = body.get("media_id")
    if not (media_type and source and media_id):
        return None, _bad_request(
            "Fields 'media_type', 'source' and 'media_id' are required.",
        )
    if media_type in (MediaTypes.SEASON.value, MediaTypes.EPISODE.value):
        return None, _bad_request(
            "Groups track top-level media; add the TV show instead.",
        )
    if media_type not in MediaTypes.values or not check_source_type(media_type, source):
        return None, _bad_request("Invalid 'media_type' or 'source'.")
    return (media_type, source, str(media_id)), None


def _parse_participants(group, body):
    """
    Validate the optional ``participants`` list of member user ids.

    Missing or null means every member (the UI default); the services already
    skip members whose personal record is Dropped or Completed where needed.
    Returns ``(participants, error_response)``.
    """
    raw = body.get("participants")
    if raw is None:
        return None, None
    if not isinstance(raw, list) or not all(
        isinstance(user_id, int) and not isinstance(user_id, bool) for user_id in raw
    ):
        return None, _bad_request("Field 'participants' must be a list of user ids.")
    member_ids = set(group.members.values_list("id", flat=True))
    if not set(raw) <= member_ids:
        return None, _bad_request("Every participant must be a member of the group.")
    return raw, None


def _serialize_member(member, group):
    return {
        "id": member.id,
        "username": member.username,
        "is_owner": member.id == group.owner_id,
    }


def _serialize_group(group, *, include_members=False):
    data = {
        "id": group.id,
        "name": group.name,
        "description": group.description,
        "owner": group.owner.username,
        "member_count": group.members.count(),
        "item_count": group.group_items.count(),
        "created_at": group.created_at,
    }
    if include_members:
        data["members"] = [
            _serialize_member(member, group) for member in group.members.all()
        ]
    return data


def _serialize_group_item(group_item, progress_data, members):
    """Group-owned state plus each member's personal status/progress."""
    item = group_item.item
    p_data = progress_data.get(item.id, {"completed_count": 0, "members": {}})
    return {
        "item": ItemSerializer(item).data,
        "status": group_item.status,
        "progress": group_item.progress,
        "started_at": group_item.started_at,
        "completed_at": group_item.completed_at,
        "progressed_at": group_item.progressed_at,
        "added_by": group_item.added_by.username,
        "added_at": group_item.added_at,
        "completed_count": p_data["completed_count"],
        "total_members": len(members),
        "members": [
            {
                "id": member.id,
                "username": member.username,
                **p_data["members"].get(member.id, {"status": None, "progress": 0}),
            }
            for member in members
        ],
    }


def _group_item_response(group, group_item, status=HTTP.OK):
    group_item.refresh_from_db()
    members = list(group.members.all())
    return Response(
        _serialize_group_item(group_item, get_group_progress(group), members),
        status=status,
    )


# /api/v1/groups/
class GroupsView(drf_views.APIView):
    """Groups the authenticated user belongs to."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """List the user's groups."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err
        groups = list(
            request.user.joined_groups.select_related("owner").order_by("name"),
        )
        data = paginate_data(request, groups, limit, offset)
        data["results"] = [_serialize_group(group) for group in data["results"]]
        return Response(data, status=HTTP.OK)


# /api/v1/groups/[group_id]/
class GroupDetailView(drf_views.APIView):
    """A single group with its members."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, group_id):
        """Return the group and its members."""
        group = _get_member_group(request, group_id)
        return Response(_serialize_group(group, include_members=True), status=HTTP.OK)


# /api/v1/groups/[group_id]/items/
class GroupItemsView(drf_views.APIView):
    """The group's media, bucketed by the group's own status."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, group_id):
        """List group items, optionally filtered by ``tab``."""
        group = _get_member_group(request, group_id)
        tab = request.GET.get("tab")
        if tab and tab not in GROUP_TABS:
            return _bad_request(f"Invalid tab; use one of: {', '.join(GROUP_TABS)}.")
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        tabs = get_group_tab_items(group)
        group_items = (
            tabs[tab] if tab else [gi for key in GROUP_TABS for gi in tabs[key]]
        )
        data = paginate_data(request, group_items, limit, offset)
        members = list(group.members.all())
        progress_data = get_group_progress(group)
        data["results"] = [
            _serialize_group_item(group_item, progress_data, members)
            for group_item in data["results"]
        ]
        return Response(data, status=HTTP.OK)

    def post(self, request, group_id):
        """
        Add a media to the group.

        Members without a personal record get it as Planning; existing
        records are left untouched.
        """
        group = _get_member_group(request, group_id)
        identifier, err = _parse_media_identifier(request.data or {})
        if err:
            return err
        media_type, source, media_id = identifier

        existing = GroupItem.objects.filter(
            group=group,
            item__media_type=media_type,
            item__source=source,
            item__media_id=media_id,
            item__season_number=None,
        ).first()
        if existing is not None:
            return _group_item_response(group, existing)

        try:
            item = get_or_create_item(media_type, media_id, source)
        except Exception:  # noqa: BLE001 - provider errors surface as 400
            return _bad_request("Media not found at the provider.")
        group_item, _ = add_item_to_group(group, item, request.user)
        return _group_item_response(group, group_item, status=HTTP.CREATED)


# /api/v1/groups/[group_id]/items/[media_type]/[source]/[media_id]/
class GroupItemDetailView(drf_views.APIView):
    """One media inside a group."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, group_id, media_type, source, media_id):
        """Return the group's state for this media."""
        group = _get_member_group(request, group_id)
        group_item = _get_group_item(group, media_type, source, media_id)
        return _group_item_response(group, group_item)

    def patch(self, request, group_id, media_type, source, media_id):
        """
        Set the group's status (and, for non-TV, progress) and propagate it.

        Propagation is a monotone merge into the participants' personal
        records: never reduces progress, never reopens Completed, skips
        Dropped. TV progress comes from episodes (see the episodes endpoint).
        """
        group = _get_member_group(request, group_id)
        group_item = _get_group_item(group, media_type, source, media_id)
        body = request.data or {}

        status_value = body.get("status", group_item.status)
        if status_value not in Status.values:
            return _bad_request(
                f"Invalid status; use one of: {', '.join(Status.values)}.",
            )

        progress = body.get("progress")
        if progress is not None:
            if group_item.item.media_type == MediaTypes.TV.value:
                return _bad_request(
                    "TV progress is driven by episodes; use the episodes endpoint.",
                )
            if not isinstance(progress, int) or isinstance(progress, bool):
                return _bad_request("Field 'progress' must be an integer.")
            if progress < 0:
                return _bad_request("Field 'progress' must be zero or positive.")

        participants, err = _parse_participants(group, body)
        if err:
            return err

        if progress is not None:
            group_item.progress = progress
            group_item.save(update_fields=["progress"])
        mark_group_item_status(group_item, status_value, participants)
        return _group_item_response(group, group_item)

    def delete(self, request, group_id, media_type, source, media_id):
        """Remove the media from the group; personal records are kept."""
        group = _get_member_group(request, group_id)
        group_item = _get_group_item(group, media_type, source, media_id)
        group_item.delete()
        return Response(status=HTTP.NO_CONTENT)


# /api/v1/groups/[group_id]/items/[media_type]/[source]/[media_id]/episodes/
class GroupItemEpisodesView(drf_views.APIView):
    """Episodes watched together, for a TV show in the group."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, group_id, media_type, source, media_id):
        """List the episodes in the group's ledger."""
        group = _get_member_group(request, group_id)
        group_item = _get_group_item(group, media_type, source, media_id)
        episodes = group_item.watched_episodes.select_related("item").order_by(
            "item__season_number", "item__episode_number"
        )
        return Response(
            {
                "progress": group_item.progress,
                "results": [
                    {
                        "season_number": watch.item.season_number,
                        "episode_number": watch.item.episode_number,
                        "watched_at": watch.watched_at,
                    }
                    for watch in episodes
                ],
            },
            status=HTTP.OK,
        )

    def post(self, request, group_id, media_type, source, media_id):
        """
        Mark episodes watched by the group and propagate them.

        Body: ``episodes`` as ``[[season, episode], ...]`` (any order), or the
        "up to" shortcut ``season_number`` + ``up_to_episode``. Optional
        ``participants``. Re-marking an episode is a no-op.
        """
        group = _get_member_group(request, group_id)
        group_item = _get_group_item(group, media_type, source, media_id)
        if group_item.item.media_type != MediaTypes.TV.value:
            return _bad_request("Episodes only apply to TV shows.")
        body = request.data or {}

        episodes, err = _parse_episodes(body)
        if err:
            return err
        participants, err = _parse_participants(group, body)
        if err:
            return err

        mark_group_episodes_watched(group_item, episodes, participants)
        return _group_item_response(group, group_item)


def _is_positive_int(value, *, allow_zero=False):
    if not isinstance(value, int) or isinstance(value, bool):
        return False
    return value >= 0 if allow_zero else value > 0


def _parse_episodes(body):
    """Return ``(episodes, error_response)`` from an episodes request body."""
    raw = body.get("episodes")
    if raw is not None:
        if not isinstance(raw, list) or not raw:
            return None, _bad_request(
                "Field 'episodes' must be a non-empty list of [season, episode].",
            )
        episodes = []
        for pair in raw:
            if (
                not isinstance(pair, list | tuple)
                or len(pair) != 2  # noqa: PLR2004
                or not _is_positive_int(pair[0], allow_zero=True)
                or not _is_positive_int(pair[1])
            ):
                return None, _bad_request(
                    "Each episode must be [season_number, episode_number].",
                )
            episodes.append((pair[0], pair[1]))
        return episodes, None

    season_number = body.get("season_number")
    up_to = body.get("up_to_episode")
    if _is_positive_int(season_number, allow_zero=True) and _is_positive_int(up_to):
        return episodes_up_to(season_number, up_to), None
    return None, _bad_request(
        "Send 'episodes', or 'season_number' with 'up_to_episode'.",
    )


# /api/v1/groups/[group_id]/discards/
class GroupDiscardsView(drf_views.APIView):
    """The group's "not interested" list, shared by every member."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, group_id):
        """List the group's discards, most recent first."""
        group = _get_member_group(request, group_id)
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err
        discards = list(discard_service.group_discarded_items(group))
        data = paginate_data(request, discards, limit, offset)
        data["results"] = [
            {
                "item": ItemSerializer(discard.item).data,
                "discarded_by": discard.discarded_by.username,
                "created_at": discard.created_at,
            }
            for discard in data["results"]
        ]
        return Response(data, status=HTTP.OK)

    def post(self, request, group_id):
        """Discard a media for the whole group. Idempotent."""
        group = _get_member_group(request, group_id)
        identifier, err = _parse_media_identifier(request.data or {})
        if err:
            return err
        media_type, source, media_id = identifier
        try:
            item = get_or_create_item(media_type, media_id, source)
        except Exception:  # noqa: BLE001 - provider errors surface as 400
            return _bad_request("Media not found at the provider.")
        discard = discard_service.discard_group_item(group, item, request.user)
        return Response(
            {
                "item": ItemSerializer(item).data,
                "discarded_by": discard.discarded_by.username,
                "created_at": discard.created_at,
            },
            status=HTTP.CREATED,
        )


# /api/v1/groups/[group_id]/discards/[media_type]/[source]/[media_id]/
class GroupDiscardDetailView(drf_views.APIView):
    """Restore a discarded media for the group."""

    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, group_id, media_type, source, media_id):
        """Undo the group discard. Any member can do it."""
        group = _get_member_group(request, group_id)
        item = get_object_or_404(
            Item,
            media_type=media_type,
            source=source,
            media_id=media_id,
            season_number=None,
            episode_number=None,
        )
        if not discard_service.restore_group_item(group, item):
            return Response(
                {"detail": "Media is not discarded for this group."},
                status=HTTP.NOT_FOUND,
            )
        return Response(status=HTTP.NO_CONTENT)
