from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from app.models import Item
from groups.models import Group
from groups.services import get_group_progress


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
    }
    return render(request, "groups/group_detail.html", context)
