from collections import defaultdict
from django.apps import apps
from django.db import models
from app.models import Status
from groups.models import Group

def get_group_progress(group: Group) -> dict:
    """
    Get aggregated progress for all items tracked by a group.

    Args:
        group: The Group instance.

    Returns:
        A dictionary mapping item_id (int) to a dictionary containing:
            - 'item_id': the item ID.
            - 'media_type': the media type.
            - 'completed_count' (int): Number of members who have completed the item.
            - 'total_members' (int): Total number of members in the group.
            - 'members' (dict): A dictionary mapping user_id (int) to:
                - 'status' (str|None): The tracking status, e.g., 'Completed', or None if not tracked.
                - 'progress' (int): The current progress (calculated for TV, raw for others).
    """
    group_items = list(group.group_items.select_related("item"))
    members = list(group.members.all())
    
    result = {}
    for gi in group_items:
        result[gi.item.id] = {
            "item_id": gi.item.id,
            "media_type": gi.item.media_type,
            "completed_count": 0,
            "total_members": len(members),
            "members": {m.id: {"status": None, "progress": 0} for m in members}
        }
    
    items_by_type = defaultdict(list)
    for gi in group_items:
        items_by_type[gi.item.media_type].append(gi.item.id)
    
    member_ids = [m.id for m in members]
    
    for media_type, item_ids in items_by_type.items():
        # Dynamically load the media model
        model = apps.get_model("app", media_type)
        
        qs = model.objects.filter(item_id__in=item_ids, user_id__in=member_ids)
        
        # If TV show, we must calculate progress from the related episodes,
        # explicitly excluding season 0.
        if media_type.lower() == "tv":
            qs = qs.annotate(
                calculated_progress=models.Count(
                    "seasons__episodes",
                    filter=models.Q(seasons__item__season_number__gt=0),
                )
            ).values("item_id", "user_id", "status", "calculated_progress")
        else:
            qs = qs.values("item_id", "user_id", "status", "progress")
            
        for row in qs:
            item_id = row["item_id"]
            user_id = row["user_id"]
            status = row["status"]
            
            # Map the right progress field
            if media_type.lower() == "tv":
                progress = row.get("calculated_progress", 0)
            else:
                progress = row.get("progress", 0)
                
            result[item_id]["members"][user_id] = {
                "status": status,
                "progress": progress
            }
            if status == Status.COMPLETED.value:
                result[item_id]["completed_count"] += 1
                
    return result
