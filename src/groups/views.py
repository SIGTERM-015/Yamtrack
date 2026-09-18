from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from app.models import Item, Status
from groups.models import Group
from groups.services import apply_status_to_group_members, get_group_progress


@login_required
def group_list(request):
    """View to list user groups."""
    groups = request.user.joined_groups.all()
    return render(request, "groups/group_list.html", {"groups": groups})


@login_required
def group_detail(request, group_id):
    """View to display group detail."""
    group = get_object_or_404(Group, id=group_id)

    if not group.members.filter(id=request.user.id).exists():
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
            member_progress.append({
                "user": members_dict.get(m_id),
                "status": m_data["status"],
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
        "status_choices": Status.choices,
    }
    return render(request, "groups/group_detail.html", context)


@login_required
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
