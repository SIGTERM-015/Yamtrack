from django.contrib import admin

from .models import Group, GroupItem, GroupMembership, GroupOrigin


class GroupMembershipInline(admin.TabularInline):
    """Inline for GroupMembership."""

    model = GroupMembership
    extra = 1


class GroupItemInline(admin.TabularInline):
    """Inline for GroupItem."""

    model = GroupItem
    extra = 1


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    """Admin configuration for Group."""

    list_display = ("name", "owner", "created_at")
    search_fields = ("name", "description", "owner__username")
    list_filter = ("created_at",)
    inlines = [GroupMembershipInline, GroupItemInline]


@admin.register(GroupMembership)
class GroupMembershipAdmin(admin.ModelAdmin):
    """Admin configuration for GroupMembership."""

    list_display = ("group", "user", "joined_at")
    search_fields = ("group__name", "user__username")
    list_filter = ("joined_at",)


@admin.register(GroupItem)
class GroupItemAdmin(admin.ModelAdmin):
    """Admin configuration for GroupItem."""

    list_display = ("group", "item", "added_by", "added_at")
    search_fields = ("group__name", "item__title", "added_by__username")
    list_filter = ("added_at",)


@admin.register(GroupOrigin)
class GroupOriginAdmin(admin.ModelAdmin):
    """Admin configuration for GroupOrigin."""

    list_display = ("user", "item", "group", "detached", "created_at")
    search_fields = ("user__username", "item__title", "group__name")
    list_filter = ("detached", "created_at")
