from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import SAFE_METHODS

from users.models import ApiToken, User


def authenticate_token(request, raw_token):
    """
    Resolve a raw credential to ``(user, auth)`` or raise.

    Named personal tokens (``ApiToken``, ``ytk_`` prefix) are checked first and
    enforce their scope: a read-only token cannot make unsafe requests. The
    legacy per-user ``User.token`` (shared with the media-server webhooks) is
    still accepted with full access so existing clients keep working.
    """
    api_token = ApiToken.from_raw(raw_token)
    if api_token is not None:
        if request.method not in SAFE_METHODS and not api_token.can_write:
            msg = "This API token is read-only."
            raise PermissionDenied(msg)
        api_token.touch()
        return (api_token.user, api_token)

    try:
        user = User.objects.get(token=raw_token)
    except User.DoesNotExist:
        msg = "Invalid token"
        raise AuthenticationFailed(msg) from None
    return (user, None)


class BearerAuthentication(BaseAuthentication):
    """Bearer Authentication."""

    keyword = "Bearer"

    def authenticate(self, request):
        """Authenticate the user with Bearer token."""
        auth = request.headers.get("Authorization")
        if not auth:
            return None
        parts = auth.split()
        if len(parts) != 2 or parts[0] != self.keyword:  # noqa: PLR2004
            return None
        return authenticate_token(request, parts[1])


class APIKeyAuthentication(BaseAuthentication):
    """API Key Authentication."""

    def authenticate(self, request):
        """Authenticate the user with API Key."""
        auth = request.headers.get("X-API-Key")
        if not auth:
            return None
        return authenticate_token(request, auth)
