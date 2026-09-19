from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST


from app import config
from app.models import Item, Status
from groups.models import Group, GroupInvitation
from groups.services import apply_status_to_group_members, get_group_progress


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
    """View to display group detail."""
    group = get_object_or_404(Group, id=group_id)

    is_member = group.members.filter(id=request.user.id).exists()
    invitation = group.invitations.filter(invited_user=request.user).first()

    if not is_member and invitation is None:
        msg = "Group not found"
        raise Http404(msg)

    progress_data = get_group_progress(group)

    items_data = []

    group_items = Item.objects.filter(id__in=progress_data.keys())
    items_dict = {item.id: item for item in group_items}

    members = list(group.members.all())
    members_dict = {m.id: m for m in members}

    for item_id, p_data in progress_data.items():
        item = items_dict.get(item_id)
        if not item:
            continue

        member_progress = []
        for m_id, m_data in p_data["members"].items():
            status = m_data["status"]
            status_config = config.get_status_config(status) if status else None
            member_progress.append({
                "user": members_dict.get(m_id),
                "status": status,
                "status_color": (
                    status_config["text_color"] if status_config else "text-gray-500"
                ),
                "progress": m_data["progress"],
            })

        items_data.append({
            "item": item,
            "completed_count": p_data["completed_count"],
            "total_members": p_data["total_members"],
            "member_progress": member_progress,
        })

    context = {
        "group": group,
        "items_data": items_data,
        "is_member": is_member,
        "invitation": invitation,
        "status_choices": Status.choices,
    }
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
        return redirect("group_detail", group_id=group.id)

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

    return redirect("group_detail", group_id=group.id)


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


@require_POST
def group_set_item_status(request, group_id):
    """Apply a status to every member of a group, without overwriting data."""
    group = get_object_or_404(Group, id=group_id)

    if not group.members.filter(id=request.user.id).exists():
        msg = "Group not found"
        raise Http404(msg)

    item = get_object_or_404(
        Item, id=request.POST.get("item_id"), group_items__group=group
    )

    status = request.POST.get("status")
    if status not in {choice.value for choice in Status}:
        msg = "Invalid status"
        raise Http404(msg)

    apply_status_to_group_members(group, item, status)
    return redirect("group_detail", group_id=group.id)
