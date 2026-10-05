from collections import defaultdict
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Exists, OuterRef
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from app import config
from app.models import Item, MediaTypes, Status
from app.providers import services as provider_services
from groups import discards as discard_service
from groups.models import Group, GroupInvitation, GroupItem, GroupOrigin
from groups.services import (
    GROUP_TABS,
    add_item_to_group,
    apply_status_to_user,
    episodes_up_to,
    get_group_comparison,
    get_group_genre_stats,
    get_group_progress,
    get_group_tab_items,
    mark_group_episodes_watched,
    mark_group_item_status,
)
from lists.views import get_or_create_item

# Tab labels reuse the global status labels so groups read like every other list.
_TAB_LABELS = {
    "planning": Status.PLANNING.label,
    "in_progress": Status.IN_PROGRESS.label,
    "completed": Status.COMPLETED.label,
    "other": "Other",
}
_VALID_TABS = {*GROUP_TABS, "stats", "discarded", "settings"}


def _redirect_to_group(request, group):
    """Redirect back to the group, keeping the tab the action came from."""
    url = reverse("group_detail", args=[group.id])
    tab = request.POST.get("tab")
    if tab in _VALID_TABS:
        url = f"{url}?tab={tab}"
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


def _build_item_view(group_item, progress_data, members):
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
    """View to display group detail: five tabs driven by the group's own status."""
    group = get_object_or_404(Group, id=group_id)

    is_member = group.members.filter(id=request.user.id).exists()
    invitation = group.invitations.filter(invited_user=request.user).first()

    if not is_member and invitation is None:
        msg = "Group not found"
        raise Http404(msg)

    tab = request.GET.get("tab", "planning")
    if is_member and tab == "settings":
        return redirect("group_settings", group_id=group.id)
    if is_member and tab == "stats":
        return redirect("group_stats", group_id=group.id)
    if tab not in _VALID_TABS:
        tab = "planning"

    members = list(group.members.all())
    progress_data = get_group_progress(group)
    tab_items = get_group_tab_items(group)

    nav_tabs = [
        {"key": key, "label": _TAB_LABELS[key], "count": len(tab_items[key])}
        for key in GROUP_TABS
    ]

    context = {
        "group": group,
        "is_member": is_member,
        "invitation": invitation,
        "status_choices": Status.choices,
        "tab": tab,
        "nav_tabs": nav_tabs,
        "members": members,
        "discarded_count": discard_service.group_discarded_items(group).count(),
        "MediaTypes": MediaTypes,
    }

    if tab in GROUP_TABS:
        items_data = [
            _build_item_view(group_item, progress_data, members)
            for group_item in tab_items[tab]
        ]
        context["items_data"] = items_data
    elif tab == "discarded":
        context["discards"] = discard_service.group_discarded_items(group)

    if tab == "planning":
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

    group = Group.objects.create(
        name=name,
        description=description,
        owner=request.user,
    )
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
    return redirect(f"{reverse('group_detail', args=[group.id])}?tab=in_progress")


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
@require_POST
def group_item_add(request, group_id):
    """Add an item to a group, creating Planning for members without it."""
    group = get_object_or_404(Group, id=group_id)

    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)

    item = get_object_or_404(Item, id=request.POST.get("item_id"))
    add_item_to_group(group, item, request.user)

    messages.success(request, f"{item.title} was added to '{group.name}'.")
    return redirect("group_detail", group_id=group.id)


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
    """Return the modal showing the user's groups and allowing to add the item."""
    item = get_or_create_item(
        media_type,
        media_id,
        source,
        season_number,
        episode_number,
    )

    groups = request.user.joined_groups.annotate(
        has_item=Exists(GroupItem.objects.filter(group=OuterRef("pk"), item=item)),
    ).order_by("name")

    return render(
        request,
        "groups/components/fill_groups.html",
        {"item": item, "groups": groups},
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


def _safe_next(request, default):
    """Return ``?next=`` if it is a same-site relative path, else ``default``."""
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and next_url.startswith("/"):
        return next_url
    return default


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


@login_required
@require_POST
def group_recommend_add(request, group_id):
    """Add a recommendation candidate to the group, creating its Item first."""
    group = get_object_or_404(Group, id=group_id)
    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)

    item = _resolve_group_item(request)
    add_item_to_group(group, item, request.user)
    messages.success(request, f"{item.title} was added to '{group.name}'.")
    return redirect(
        _safe_next(request, f"{reverse('recommendations')}?mode=group&group={group.id}")
    )
