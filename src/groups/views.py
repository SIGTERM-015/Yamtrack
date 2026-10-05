from collections import defaultdict
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.messages.storage.base import Message
from django.db.models import Exists, F, OuterRef
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_POST

from app import config
from app.models import Item, MediaTypes, Status
from app.providers import services as provider_services
from groups import discards as discard_service
from groups.forms import GroupBannerForm
from groups.models import Group, GroupInvitation, GroupItem, GroupOrigin
from groups.services import (
    GROUP_SECTION_STATUSES,
    add_item_to_group,
    apply_status_to_user,
    episodes_up_to,
    get_group_comparison,
    get_group_genre_stats,
    get_group_progress,
    get_group_sections,
    mark_group_episodes_watched,
    mark_group_item_status,
)
from lists.views import get_or_create_item

# Poster rows shown per media type before "Load all", as on the home page.
_SECTION_ITEMS_LIMIT = 14


def _redirect_to_group(request, group):
    """Redirect back to the group, at the status section the action came from."""
    url = reverse("group_detail", args=[group.id])
    section = request.POST.get("section")
    if section in GROUP_SECTION_STATUSES:
        url = f"{url}#{slugify(section)}"
    return redirect(url)


def _resolve_participants_or_404(request, group):
    """
    Resolve the ``participants[]`` POST field into a list of user ids.

    Missing field means "every member" (``None``). Any id that is not a
    member of the group aborts with 404 *before* any write happens.
    """
    raw_ids = request.POST.getlist("participants")
    if not raw_ids:
        return None

    try:
        ids = [int(raw_id) for raw_id in raw_ids]
    except (TypeError, ValueError):
        msg = "Invalid participant"
        raise Http404(msg) from None

    member_ids = set(group.members.values_list("id", flat=True))
    if not set(ids) <= member_ids:
        msg = "Invalid participant"
        raise Http404(msg)

    return ids


def _build_item_view(group_item, progress_data, members, discarded_ids):
    """Build one poster-grid entry: group state + per-member rows."""
    item = group_item.item
    p_data = progress_data.get(item.id, {"completed_count": 0, "members": {}})

    member_rows = []
    for member in members:
        m_data = p_data["members"].get(member.id, {"status": None, "progress": 0})
        status = m_data["status"]
        status_config = config.get_status_config(status) if status else None
        member_rows.append(
            {
                "user": member,
                "status": status,
                "status_color": (
                    status_config["text_color"] if status_config else "text-gray-500"
                ),
                "progress": m_data["progress"],
                # Preselected, except a member whose personal record is
                # Dropped (design decision: respected, unchecked by default).
                "default_checked": status != Status.DROPPED.value,
            }
        )

    group_status_config = config.get_status_config(group_item.status)
    return {
        "group_item": group_item,
        "item": item,
        "is_tv": item.media_type == MediaTypes.TV.value,
        "completed_count": p_data["completed_count"],
        "total_members": len(members),
        "member_progress": member_rows,
        "is_discarded": item.id in discarded_ids,
        "group_status_color": (
            group_status_config["text_color"]
            if group_status_config
            else "text-gray-300"
        ),
    }


def _group_stats_context(group, members):
    """Ratings comparison and genre stats for the stats page and summary."""
    stats = {}
    comparison_data = get_group_comparison(group)
    comparison_items = {
        item.id: item
        for item in Item.objects.filter(
            id__in=[row["item_id"] for row in comparison_data],
        )
    }
    stats["rows"] = [
        {
            "item": comparison_items[row["item_id"]],
            "scores": [
                {"user": member, "score": row["scores"].get(member.id)}
                for member in members
            ],
            "average": row["average"],
            "difference": row["difference"],
        }
        for row in comparison_data
        if row["item_id"] in comparison_items
    ]

    genre_stats = get_group_genre_stats(group)
    stats["genres"] = [
        {
            "genre": genre["genre"],
            "average": genre["average"],
            "difference": genre["difference"],
            "count": genre["count"],
            "members": [
                {
                    "user": member,
                    "average": genre["members"][member.id]["average"],
                    "count": genre["members"][member.id]["count"],
                }
                for member in members
            ],
        }
        for genre in genre_stats["genres"]
    ]
    stats["agreements"] = genre_stats["agreements"]
    stats["disagreements"] = genre_stats["disagreements"]
    stats["member_volumes"] = [
        {"user": member, "count": genre_stats["volume"].get(member.id, 0)}
        for member in members
    ]

    return stats


def _group_stats_summary(stats):
    """Pick the two or three facts worth a one-line summary, or None."""
    rows = [row for row in stats["rows"] if row["average"] is not None]
    if not rows:
        return None
    biggest_gap = max(
        (row for row in rows if row["difference"] is not None),
        key=lambda row: row["difference"],
        default=None,
    )
    return {
        "rated_together": len(rows),
        "top_agreement": stats["agreements"][0] if stats["agreements"] else None,
        "biggest_gap": biggest_gap
        if biggest_gap and biggest_gap["difference"]
        else None,
    }


def _get_member_group_or_404(request, group_id):
    group = get_object_or_404(Group, id=group_id)
    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)
    return group


@login_required
@require_GET
def group_settings(request, group_id):
    """Members, invitations, ownership and leaving: managing the group."""
    group = _get_member_group_or_404(request, group_id)
    return render(
        request,
        "groups/group_settings.html",
        {"group": group, "members": list(group.members.all()), "is_member": True},
    )


@login_required
@require_GET
def group_stats(request, group_id):
    """Full ratings comparison and genre stats for the group."""
    group = _get_member_group_or_404(request, group_id)
    members = list(group.members.all())
    return render(
        request,
        "groups/group_stats.html",
        {"group": group, "members": members, **_group_stats_context(group, members)},
    )


@login_required
def group_list(request):
    """View to list user groups and pending invitations."""
    groups = request.user.joined_groups.all()
    invitations = request.user.group_invitations.select_related("group", "invited_by")
    return render(
        request,
        "groups/group_list.html",
        {"groups": groups, "invitations": invitations},
    )


@login_required
def group_detail(request, group_id):
    """Group page: the group's items by its own status, then by media type."""
    group = get_object_or_404(Group, id=group_id)

    is_member = group.members.filter(id=request.user.id).exists()
    invitation = group.invitations.filter(invited_user=request.user).first()

    if not is_member and invitation is None:
        msg = "Group not found"
        raise Http404(msg)

    # Old tab links: settings and stats have their own pages; the status tabs
    # are now sections of this page, so their keys just land here.
    tab = request.GET.get("tab")
    if is_member and tab == "settings":
        return redirect("group_settings", group_id=group.id)
    if is_member and tab == "stats":
        return redirect("group_stats", group_id=group.id)

    members = list(group.members.all())
    discards = discard_service.group_discarded_items(group)
    context = {
        "group": group,
        "is_member": is_member,
        "invitation": invitation,
        "members": members,
        "discarded_count": discards.count(),
        "MediaTypes": MediaTypes,
    }

    if tab == "discarded":
        context["show_discarded"] = True
        context["discards"] = discards
        return render(request, "groups/group_detail.html", context)

    progress_data = get_group_progress(group)
    discarded_ids = discard_service.group_discarded_item_ids(group)
    sections = get_group_sections(group)
    for section in sections:
        for media_type in section["media_types"]:
            media_type["items"] = [
                _build_item_view(group_item, progress_data, members, discarded_ids)
                for group_item in media_type["items"]
            ]
    # In Progress and Planning always show, with an empty state, like on the
    # home page; the other statuses only when they hold something.
    context["sections"] = [
        section
        for section in sections
        if section["count"]
        or section["status"] in {Status.IN_PROGRESS.value, Status.PLANNING.value}
    ]
    context["has_items"] = any(section["count"] for section in sections)
    # The bulk bar skips TV shows (they move by episodes) and says so.
    context["tv_item_ids"] = [
        data["item"].id
        for section in sections
        for media_type in section["media_types"]
        if media_type["media_type"] == MediaTypes.TV.value
        for data in media_type["items"]
    ]
    context["items_limit"] = _SECTION_ITEMS_LIMIT
    context["status_choices"] = Status.choices
    if is_member:
        context["stats_summary"] = _group_stats_summary(
            _group_stats_context(group, members)
        )
    return render(request, "groups/group_detail.html", context)


@login_required
def group_create(request):
    """View to create a group; the creator becomes owner and first member."""
    if request.method != "POST":
        return render(request, "groups/group_create.html")

    name = request.POST.get("name", "").strip()
    description = request.POST.get("description", "").strip()

    if not name:
        return render(
            request,
            "groups/group_create.html",
            {
                "error": "Group name is required.",
                "name": name,
                "description": description,
            },
        )

    group = Group(name=name, description=description, owner=request.user)
    banner_form = GroupBannerForm(request.POST, request.FILES, instance=group)
    if not banner_form.is_valid():
        return render(
            request,
            "groups/group_create.html",
            {
                "error": banner_form.errors["banner"][0],
                "name": name,
                "description": description,
            },
        )
    banner_form.save()
    group.members.add(request.user)
    messages.success(request, f"Group '{group.name}' created.")
    return redirect("group_detail", group_id=group.id)


@login_required
@require_POST
def group_invite(request, group_id):
    """Invite an existing user to a group the requester belongs to."""
    group = get_object_or_404(Group, id=group_id)

    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)

    username = request.POST.get("username", "").strip()

    if not username:
        messages.error(request, "Enter a username to invite.")
        return redirect("group_settings", group_id=group.id)

    user_model = get_user_model()
    invited_user = user_model.objects.filter(username__iexact=username).first()

    if invited_user is None:
        messages.error(request, f"No user named '{username}'.")
    elif invited_user == request.user:
        messages.error(request, "You are already in this group.")
    elif group.members.filter(id=invited_user.id).exists():
        messages.error(request, f"{invited_user.username} is already a member.")
    elif group.invitations.filter(invited_user=invited_user).exists():
        messages.error(
            request,
            f"{invited_user.username} already has a pending invitation.",
        )
    else:
        GroupInvitation.objects.create(
            group=group,
            invited_user=invited_user,
            invited_by=request.user,
        )
        messages.success(request, f"Invitation sent to {invited_user.username}.")

    return redirect("group_settings", group_id=group.id)


@login_required
@require_POST
def group_invitation_accept(request, invitation_id):
    """Accept a pending invitation and join the group."""
    invitation = get_object_or_404(
        GroupInvitation, id=invitation_id, invited_user=request.user
    )
    group = invitation.group
    group.members.add(request.user)
    invitation.delete()
    messages.success(request, f"You joined '{group.name}'.")
    return redirect("group_detail", group_id=group.id)


@login_required
@require_POST
def group_invitation_reject(request, invitation_id):
    """Reject a pending invitation."""
    invitation = get_object_or_404(
        GroupInvitation, id=invitation_id, invited_user=request.user
    )
    group = invitation.group
    invitation.delete()
    messages.success(request, f"You declined the invitation to '{group.name}'.")
    return redirect("group_list")


def _detach_group_origins(user, group):
    """Mark a departing member's group-born items as belonging to them."""
    GroupOrigin.objects.filter(user=user, group=group).update(detached=True)


@login_required
@require_POST
def group_set_item_status(request, group_id):
    """
    Set the group's own status/progress on an item and propagate it.

    ``scope=mine`` (default the requester unaffected by ``participants[]``)
    replaces the requester's own record explicitly. ``scope=group`` (the
    default) writes the group's status/progress on the GroupItem and merges
    it monotonically into the selected ``participants[]`` (all members when
    omitted); an id that is not a member aborts with 404 before any write.
    """
    group = get_object_or_404(Group, id=group_id)

    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)

    group_item = get_object_or_404(
        GroupItem, group=group, item_id=request.POST.get("item_id")
    )

    status = request.POST.get("status")
    if status not in {choice.value for choice in Status}:
        msg = "Invalid status"
        raise Http404(msg)

    scope = request.POST.get("scope", "group")
    if scope == "mine":
        apply_status_to_user(group_item.item, request.user, status)
    elif scope == "group":
        participants = _resolve_participants_or_404(request, group)

        progress_raw = request.POST.get("progress")
        if progress_raw not in (None, ""):
            try:
                progress_value = max(int(progress_raw), 0)
            except ValueError:
                messages.error(request, "Invalid progress value.")
                return _redirect_to_group(request, group)
            group_item.progress = progress_value
            group_item.save(update_fields=["progress"])

        mark_group_item_status(group_item, status, participants)
        status_label = group_item.get_status_display().lower()
        messages.success(
            request,
            f"'{group_item.item.title}' is now {status_label} for the group.",
        )
    else:
        msg = "Invalid scope"
        raise Http404(msg)

    return _redirect_to_group(request, group)


@login_required
@require_POST
def group_mark_episodes(request, group_id):
    """
    Mark TV episodes as watched by the group and propagate them to members.

    Accepts ``season_number``/``episode_number`` and an optional
    ``mode=up_to`` to expand to every episode from 1 up to that number
    (the "up to episode N" shortcut). Same ``participants[]`` semantics as
    :func:`group_set_item_status`.
    """
    group = get_object_or_404(Group, id=group_id)

    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)

    group_item = get_object_or_404(
        GroupItem,
        group=group,
        item_id=request.POST.get("item_id"),
        item__media_type=MediaTypes.TV.value,
    )

    participants = _resolve_participants_or_404(request, group)

    episodes = []
    raw_pairs = request.POST.getlist("episodes")
    if raw_pairs:
        try:
            for raw_pair in raw_pairs:
                season_str, episode_str = raw_pair.split("-", 1)
                episodes.append((int(season_str), int(episode_str)))
        except (TypeError, ValueError):
            messages.error(request, "Invalid episode selection.")
            return redirect("group_detail", group_id=group.id)
    else:
        try:
            season_number = int(request.POST.get("season_number"))
            episode_number = int(request.POST.get("episode_number"))
        except (TypeError, ValueError):
            messages.error(request, "Invalid season or episode number.")
            return redirect("group_detail", group_id=group.id)

        mode = request.POST.get("mode", "single")
        episodes = (
            episodes_up_to(season_number, episode_number)
            if mode == "up_to"
            else [(season_number, episode_number)]
        )

    if not episodes:
        messages.error(request, "Select at least one episode.")
        return redirect("group_detail", group_id=group.id)

    mark_group_episodes_watched(group_item, episodes, participants)
    messages.success(request, f"Marked episodes watched for '{group_item.item.title}'.")
    group_url = reverse("group_detail", args=[group.id])
    return redirect(f"{group_url}#{slugify(Status.IN_PROGRESS.value)}")


@login_required
@require_POST
def group_bulk_set_status(request, group_id):
    """
    Apply a status to several items at once, using the monotone merge.

    Never overwrites or reduces a participant's personal progress (E4.2):
    each selected item is routed through :func:`mark_group_item_status`. TV
    items are skipped here; episode-based progress uses
    :func:`group_mark_episodes`.
    """
    group = get_object_or_404(Group, id=group_id)

    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)

    status = request.POST.get("status")
    if status not in {choice.value for choice in Status}:
        msg = "Invalid status"
        raise Http404(msg)

    participants = _resolve_participants_or_404(request, group)

    item_ids = request.POST.getlist("item_ids")
    group_items = GroupItem.objects.filter(group=group, item_id__in=item_ids).exclude(
        item__media_type=MediaTypes.TV.value
    )

    for group_item in group_items:
        mark_group_item_status(group_item, status, participants)

    messages.success(request, f"Updated {len(group_items)} item(s).")
    return _redirect_to_group(request, group)


@login_required
@require_POST
def group_item_remove(request, group_id, item_id):
    """Remove an item from the group. Any member can do it; personal records stay."""
    group = get_object_or_404(Group, id=group_id)

    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)

    group_item = get_object_or_404(GroupItem, group=group, item_id=item_id)
    title = group_item.item.title
    group_item.delete()
    messages.success(request, f"'{title}' was removed from the group.")
    return _redirect_to_group(request, group)


@login_required
@require_GET
def group_episodes_modal(request, group_id, item_id):
    """
    Render the episode checklist modal for a TV group item (HTMX partial).

    Lists the show's regular seasons and episodes (best-effort, from the
    same provider metadata the personal season page uses); episodes already
    in the group's ledger show pre-checked and disabled. Submits to
    :func:`group_mark_episodes` with the checked episodes plus the existing
    "up to episode N" shortcut and the same participants[] selection used
    elsewhere.
    """
    group = get_object_or_404(Group, id=group_id)

    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)

    group_item = get_object_or_404(
        GroupItem,
        group=group,
        item_id=item_id,
        item__media_type=MediaTypes.TV.value,
    )
    tv_item = group_item.item
    members = list(group.members.all())

    watched_by_season = defaultdict(set)
    for season_number, episode_number in group_item.watched_episodes.values_list(
        "item__season_number",
        "item__episode_number",
    ):
        watched_by_season[season_number].add(episode_number)

    seasons = []
    try:
        tv_metadata = provider_services.get_media_metadata(
            MediaTypes.TV.value,
            tv_item.media_id,
            tv_item.source,
        )
        season_numbers = sorted(
            season["season_number"]
            for season in tv_metadata.get("related", {}).get("seasons", [])
            if season["season_number"] and season["season_number"] > 0
        )
        if season_numbers:
            tv_with_seasons = provider_services.get_media_metadata(
                "tv_with_seasons",
                tv_item.media_id,
                tv_item.source,
                season_numbers,
            )
            for season_number in season_numbers:
                season_metadata = tv_with_seasons.get(f"season/{season_number}", {})
                watched = watched_by_season.get(season_number, set())
                episodes = [
                    {
                        "number": episode["episode_number"],
                        "title": episode.get("name") or episode.get("title") or "",
                        "watched": episode["episode_number"] in watched,
                    }
                    for episode in season_metadata.get("episodes", [])
                ]
                if episodes:
                    seasons.append({"number": season_number, "episodes": episodes})
    except Exception:  # noqa: BLE001 - metadata may be unavailable; best-effort
        seasons = []

    return render(
        request,
        "groups/components/episodes_modal.html",
        {
            "group": group,
            "group_item": group_item,
            "item": tv_item,
            "seasons": seasons,
            "members": members,
        },
    )


def _is_safe_url(request, url):
    """Whether ``url`` points back to this site."""
    return url_has_allowed_host_and_scheme(
        url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    )


def _safe_next(request, default):
    """Return ``next`` (POST or GET) if it points to this site, else ``default``."""
    next_url = request.POST.get("next") or request.GET.get("next")
    return next_url if _is_safe_url(request, next_url) else default


def _safe_referer(request):
    """Return the Referer if it points to this site, else ``None``."""
    referer = request.headers.get("Referer")
    return referer if _is_safe_url(request, referer) else None


# Session key holding the GroupItem the last quick add created, so "Change"
# can move it elsewhere without touching items the user added on purpose.
_QUICK_ADD_UNDO = "group_quick_add_undo"


def _render_group_picker(request, item, *, change=False):
    """Render the modal to choose which group receives the item."""
    groups = request.user.joined_groups.annotate(
        has_item=Exists(GroupItem.objects.filter(group=OuterRef("pk"), item=item)),
    ).order_by("name")
    return render(
        request,
        "groups/components/fill_groups.html",
        {"item": item, "groups": groups, "change": change},
    )


@login_required
@require_GET
def groups_modal(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
):
    """Return the group picker; ``?change=1`` moves the last quick add."""
    item = get_or_create_item(
        media_type,
        media_id,
        source,
        season_number,
        episode_number,
    )
    return _render_group_picker(request, item, change="change" in request.GET)


def _undo_quick_add(request, item, target_group):
    """
    Remove the item from the group the last quick add put it in.

    Only the GroupItem that quick add itself created is removed, and only while
    nobody has used it yet. Personal records stay (agreement 4).
    """
    group_item = (
        GroupItem.objects.filter(
            id=request.session.get(_QUICK_ADD_UNDO),
            item=item,
            group__members=request.user,
            status=Status.PLANNING.value,
            progress=0,
        )
        .exclude(group=target_group)
        .select_related("group")
        .first()
    )
    if group_item is None:
        return None
    group_item.delete()
    del request.session[_QUICK_ADD_UNDO]
    return group_item.group


def _quick_add_target(memberships, group_id):
    """
    Pick the membership quick add should use, or ``None`` to ask.

    ``memberships`` comes most recently quick-added first.
    """
    if group_id:
        membership = next((m for m in memberships if str(m.group_id) == group_id), None)
    elif len(memberships) == 1 or (memberships and memberships[0].quick_added_at):
        return memberships[0]
    elif memberships:
        return None
    else:
        membership = None
    if membership is None:
        msg = "Group not found"
        raise Http404(msg)
    return membership


@login_required
@require_POST
def group_quick_add(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
):
    """
    Add an item to one of the user's groups in a single click.

    The target is ``group_id`` when given, else the only group, else the group
    last used with quick add. A user with several groups who never chose one
    gets the picker instead. The answer is a toast; with several groups it
    offers "Change", which reopens the picker with ``change`` set so the item
    moves out of the group this action just added it to.
    """
    memberships = list(
        request.user.group_memberships.select_related("group").order_by(
            F("quick_added_at").desc(nulls_last=True), "group__name"
        )
    )
    membership = _quick_add_target(memberships, request.POST.get("group_id"))

    item = get_or_create_item(
        media_type,
        media_id,
        source,
        season_number,
        episode_number,
    )
    back = _safe_next(request, _safe_referer(request) or reverse("home"))
    if membership is None:
        if request.headers.get("HX-Request"):
            return _render_group_picker(request, item)
        messages.info(request, "Choose a group to add it to.")
        return redirect(back)

    group = membership.group
    moved_from = None
    if request.POST.get("change"):
        moved_from = _undo_quick_add(request, item, group)
    group_item = GroupItem.objects.filter(group=group, item=item).first()
    added = group_item is None
    if added:
        group_item, _ = add_item_to_group(group, item, request.user)
        request.session[_QUICK_ADD_UNDO] = group_item.id
    elif request.session.get(_QUICK_ADD_UNDO) != group_item.id:
        # The item was already there: "Change" must never take it out.
        request.session.pop(_QUICK_ADD_UNDO, None)

    membership.quick_added_at = timezone.now()
    membership.save(update_fields=["quick_added_at"])

    if moved_from:
        text = f"Moved to {group.name}"
    elif added:
        text = f"Added to {group.name}"
    else:
        text = f"Already in {group.name}"
    level = messages.SUCCESS if added or moved_from else messages.INFO
    if not request.headers.get("HX-Request"):
        # Without JS, stay where the user was, never on the group page.
        messages.add_message(request, level, text)
        return redirect(back)
    return render(
        request,
        "groups/components/quick_add_done.html",
        {
            "item": item,
            "group": group,
            "toast": Message(level, text),
            # Only an add this action made can be moved; "fixed" callers
            # (group recommendations) already name their group.
            "can_change": (added or bool(moved_from))
            and len(memberships) > 1
            and not request.POST.get("fixed"),
        },
    )


@login_required
@require_POST
def group_remove_member(request, group_id, user_id):
    """Remove another member from a group. Owner-only."""
    group = get_object_or_404(Group, id=group_id)

    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)

    if group.owner_id != request.user.id:
        return HttpResponse("Only the owner can remove members.", status=403)

    member = group.members.filter(id=user_id).first()
    if member is None:
        msg = "Member not found"
        raise Http404(msg)

    if member.id == group.owner_id:
        return HttpResponse(
            "The owner cannot be removed; transfer ownership first.", status=400
        )

    group.members.remove(member)
    _detach_group_origins(member, group)
    messages.success(request, f"{member.username} was removed from the group.")
    return redirect("group_settings", group_id=group.id)


@login_required
@require_POST
def group_leave(request, group_id):
    """Leave a group. The owner must transfer ownership first."""
    group = get_object_or_404(Group, id=group_id)

    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)

    if group.owner_id == request.user.id:
        return HttpResponse("Transfer ownership before leaving the group.", status=400)

    group.members.remove(request.user)
    _detach_group_origins(request.user, group)
    messages.success(request, f"You left '{group.name}'.")
    return redirect("group_list")


@login_required
@require_POST
def group_banner(request, group_id):
    """Set or remove the group's cover image. Owner-only: managing the group."""
    group = _get_member_group_or_404(request, group_id)

    if group.owner_id != request.user.id:
        return HttpResponse("Only the owner can change the banner.", status=403)

    previous = group.banner.name
    if request.POST.get("remove"):
        group.banner = ""
        group.save(update_fields=["banner"])
        messages.success(request, "Banner removed.")
    else:
        form = GroupBannerForm(request.POST, request.FILES, instance=group)
        if "banner" not in request.FILES:
            messages.error(request, "Choose an image to upload.")
            return redirect("group_settings", group_id=group.id)
        if not form.is_valid():
            messages.error(request, form.errors["banner"][0])
            return redirect("group_settings", group_id=group.id)
        form.save()
        messages.success(request, "Banner updated.")

    if previous and previous != group.banner.name:
        group.banner.storage.delete(previous)
    return redirect("group_settings", group_id=group.id)


@login_required
@require_POST
def group_transfer_owner(request, group_id):
    """Transfer group ownership to another member. Owner-only."""
    group = get_object_or_404(Group, id=group_id)

    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)

    if group.owner_id != request.user.id:
        return HttpResponse("Only the owner can transfer ownership.", status=403)

    new_owner = group.members.filter(id=request.POST.get("user_id")).first()
    if new_owner is None or new_owner.id == group.owner_id:
        return HttpResponse(
            "Choose another member to transfer ownership to.", status=400
        )

    group.owner = new_owner
    group.save(update_fields=["owner"])
    messages.success(request, f"{new_owner.username} is the new owner of the group.")
    return redirect("group_settings", group_id=group.id)


@login_required
def group_comparison(request, group_id):
    """View to compare group members' ratings side by side."""
    group = get_object_or_404(Group, id=group_id)

    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)

    comparison_data = get_group_comparison(group)
    items_dict = {
        item.id: item
        for item in Item.objects.filter(
            id__in=[data["item_id"] for data in comparison_data]
        )
    }
    members = list(group.members.all())

    rows = []
    for data in comparison_data:
        item = items_dict.get(data["item_id"])
        if item is None:
            continue

        rows.append(
            {
                "item": item,
                "scores": [
                    {"user": member, "score": data["scores"].get(member.id)}
                    for member in members
                ],
                "average": data["average"],
                "difference": data["difference"],
            }
        )

    context = {
        "group": group,
        "members": members,
        "rows": rows,
    }
    return render(request, "groups/group_comparison.html", context)


@login_required
def group_genre_stats(request, group_id):
    """View to compare group members' average ratings per genre."""
    group = get_object_or_404(Group, id=group_id)

    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)

    stats = get_group_genre_stats(group)
    members = stats["members"]

    genre_rows = [
        {
            "genre": genre["genre"],
            "average": genre["average"],
            "difference": genre["difference"],
            "count": genre["count"],
            "members": [
                {
                    "user": member,
                    "average": genre["members"][member.id]["average"],
                    "count": genre["members"][member.id]["count"],
                }
                for member in members
            ],
        }
        for genre in stats["genres"]
    ]

    context = {
        "group": group,
        "members": members,
        "genres": genre_rows,
        "agreements": stats["agreements"],
        "disagreements": stats["disagreements"],
        "member_volumes": [
            {"user": member, "count": stats["volume"].get(member.id, 0)}
            for member in members
        ],
    }
    return render(request, "groups/group_genre_stats.html", context)


# --- Group discards ("Not interested"), E9 ----------------------------------


def _resolve_group_item(request):
    """Return the Item named in the POST body, creating it if needed."""
    if request.POST.get("item_id"):
        return get_object_or_404(Item, id=request.POST["item_id"])
    return get_or_create_item(
        request.POST.get("media_type"),
        request.POST.get("media_id"),
        request.POST.get("source"),
        int(request.POST["season_number"])
        if request.POST.get("season_number")
        else None,
    )


@login_required
@require_POST
def group_discard_item(request, group_id):
    """Mark an item "not interesting" for a group. Any member can do this."""
    group = get_object_or_404(Group, id=group_id)
    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)

    item = _resolve_group_item(request)
    discard_service.discard_group_item(group, item, request.user)
    next_url = _safe_next(request, reverse("group_detail", args=[group.id]))
    separator = "&" if "?" in next_url else "?"
    query = urlencode({"discarded_item": item.id, "discarded_title": item.title})
    return redirect(f"{next_url}{separator}{query}")


@login_required
@require_POST
def group_restore_item(request, group_id):
    """Undo a group discard. Any member can do this."""
    group = get_object_or_404(Group, id=group_id)
    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)

    item = get_object_or_404(Item, id=request.POST.get("item_id"))
    discard_service.restore_group_item(group, item)
    messages.success(request, f'"{item.title}" is back for the group.')
    default_next = f"{reverse('group_detail', args=[group.id])}?tab=discarded"
    return redirect(_safe_next(request, default_next))


@login_required
@require_GET
def group_discarded(request, group_id):
    """Standalone page listing a group's discarded items, with restore."""
    group = get_object_or_404(Group, id=group_id)
    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)

    discards = discard_service.group_discarded_items(group)
    return render(
        request,
        "groups/group_discarded.html",
        {"group": group, "discards": discards},
    )
