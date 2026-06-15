from drf_spectacular.extensions import OpenApiAuthenticationExtension


class BearerAuthSessionAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.access.authentication.BearerAuthSessionAuthentication"
    name = "BearerAuth"

    def get_security_definition(self, auto_schema):
        del auto_schema
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "Opaque access token",
            "description": "Paste accessToken returned by POST /api/v2/iam/session/login.",
        }
