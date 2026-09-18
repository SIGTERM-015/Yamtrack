from django.urls import path

from . import views

urlpatterns = [
    path("", views.group_list, name="group_list"),
    path("<int:group_id>/", views.group_detail, name="group_detail"),
    path(
        "<int:group_id>/set-status/",
        views.group_set_item_status,
        name="group_set_item_status",
    ),
]
