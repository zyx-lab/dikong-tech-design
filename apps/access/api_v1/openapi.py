from drf_spectacular.extensions import OpenApiAuthenticationExtension


class BearerAuthSessionScheme(OpenApiAuthenticationExtension):
    target_class = "apps.access.api_v1.authentication.BearerAuthSessionAuthentication"
    name = "BearerAuth"

    def get_security_definition(self, auto_schema):
        del auto_schema
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "opaque token",
            "description": "正式 IAM Bearer Token，需通过 /api/v1/iam/session/login 获取。",
        }
