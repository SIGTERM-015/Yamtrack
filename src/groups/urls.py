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
]
