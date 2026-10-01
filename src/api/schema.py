from drf_spectacular.extensions import OpenApiAuthenticationExtension


class BearerAuthenticationScheme(OpenApiAuthenticationExtension):
    """Describe the custom bearer token auth scheme for OpenAPI generation."""

    target_class = "api.authentication.BearerAuthentication"
    name = "bearerAuth"

    def get_security_definition(self, _auto_schema):
        """Return the OpenAPI security scheme for bearer authentication."""
        return {
            "type": "http",
            "scheme": "bearer",
            "description": (
                "Personal API token from Settings > Integrations > API tokens "
                "(read-only tokens get 403 on POST/PUT/PATCH/DELETE). The legacy "
                "integration token is also accepted with full access."
            ),
        }


class ApiKeyAuthenticationScheme(OpenApiAuthenticationExtension):
    """Describe the custom API key auth scheme for OpenAPI generation."""

    target_class = "api.authentication.APIKeyAuthentication"
    name = "ApiKeyAuth"

    def get_security_definition(self, _auto_schema):
        """Return the OpenAPI security scheme for header-based API keys."""
        return {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "Same token as bearerAuth, sent in a header.",
        }
