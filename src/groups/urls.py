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
        "<int:group_id>/mark-episodes/",
        views.group_mark_episodes,
        name="group_mark_episodes",
    ),
    path(
        "<int:group_id>/bulk-set-status/",
        views.group_bulk_set_status,
        name="group_bulk_set_status",
    ),
    path(
        "<int:group_id>/items/<int:item_id>/remove/",
        views.group_item_remove,
        name="group_item_remove",
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
    path("<int:group_id>/settings/", views.group_settings, name="group_settings"),
    path("<int:group_id>/banner/", views.group_banner, name="group_banner"),
    path("<int:group_id>/stats/", views.group_stats, name="group_stats"),
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
    path(
        "<int:group_id>/add_item/",
        views.group_item_add,
        name="group_item_add",
    ),
    path(
        "<int:group_id>/items/<int:item_id>/episodes/",
        views.group_episodes_modal,
        name="group_episodes_modal",
    ),
    path(
        "groups_modal/<source:source>/<media_type:media_type>/<str:media_id>",
        views.groups_modal,
        name="groups_modal",
    ),
    path(
        "groups_modal/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>",
        views.groups_modal,
        name="groups_modal",
    ),
    path(
        "groups_modal/<source:source>/<media_type:media_type>/<str:media_id>/<int:season_number>/<int:episode_number>",
        views.groups_modal,
        name="groups_modal",
    ),
    path(
        "<int:group_id>/discard/",
        views.group_discard_item,
        name="group_discard_item",
    ),
    path(
        "<int:group_id>/restore/",
        views.group_restore_item,
        name="group_restore_item",
    ),
    path(
        "<int:group_id>/discarded/",
        views.group_discarded,
        name="group_discarded",
    ),
    path(
        "<int:group_id>/recommend_add/",
        views.group_recommend_add,
        name="group_recommend_add",
    ),
]
