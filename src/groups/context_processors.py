from django.utils.functional import SimpleLazyObject


def user_groups(request):
    """
    Expose the user's groups to templates, queried at most once per request.

    Media cards use it to decide whether to show the "Add to group" button;
    the lazy object keeps pages without cards free of the query.
    """

    def load():
        if not request.user.is_authenticated:
            return []
        if not hasattr(request, "_user_groups"):
            request._user_groups = list(request.user.joined_groups.order_by("name"))
        return request._user_groups

    return {"user_groups": SimpleLazyObject(load)}
