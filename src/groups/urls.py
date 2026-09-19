from django.urls import path

from . import views

urlpatterns = [
    path("", views.group_list, name="group_list"),
    path("create/", views.group_create, name="group_create"),
    path(
        "invitations/<int:invitation_id>/accept/",
        views.group_invitation_accept,
        name="group_invitation_accept",
    ),
    path(
        "invitations/<int:invitation_id>/reject/",
        views.group_invitation_reject,
        name="group_invitation_reject",
    ),
    path("<int:group_id>/", views.group_detail, name="group_detail"),
    path("<int:group_id>/invite/", views.group_invite, name="group_invite"),
    path(
        "<int:group_id>/set-status/",
        views.group_set_item_status,
        name="group_set_item_status",
    ),
    path(
        "<int:group_id>/members/<int:user_id>/remove/",
        views.group_remove_member,
        name="group_remove_member",
    ),
    path("<int:group_id>/leave/", views.group_leave, name="group_leave"),
    path(
        "<int:group_id>/transfer/",
        views.group_transfer_owner,
        name="group_transfer_owner",
    ),
    path(
        "<int:group_id>/comparison/",
        views.group_comparison,
        name="group_comparison",
    ),
    path(
        "<int:group_id>/genres/",
        views.group_genre_stats,
        name="group_genre_stats",
    ),
]
