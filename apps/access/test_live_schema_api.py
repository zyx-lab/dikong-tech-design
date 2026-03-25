"""IAM schema exposure test events.

- business schema 暴露当前 formal IAM 路由
- 旧 internal auth 路由已卸载
"""

from apps.access.test_live_base import LiveIamApiTestCase


class LiveIamSchemaTests(LiveIamApiTestCase):
    def test_business_schema_should_expose_current_iam_paths(self):
        response = self.client.get("/api/v1/docs/schema/")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        expected_paths = {
            "/api/v1/iam/session/login",
            "/api/v1/iam/session/refresh",
            "/api/v1/iam/session/logout",
            "/api/v1/iam/session/register",
            "/api/v1/iam/session/register-by-phone",
            "/api/v1/iam/me/profile",
            "/api/v1/iam/me/tenants",
            "/api/v1/iam/tenant/me",
            "/api/v1/iam/tenant/members",
            "/api/v1/iam/tenant/members/{memberId}",
            "/api/v1/iam/tenant/roles",
            "/api/v1/iam/tenant/members/{memberId}/roles",
            "/api/v1/iam/tenant/members/{memberId}/enable",
            "/api/v1/iam/tenant/members/{memberId}/disable",
            "/api/v1/iam/tenant/audit-logs",
            "/api/v1/iam/platform/permissions",
            "/api/v1/iam/platform/roles",
            "/api/v1/iam/platform/roles/{roleId}",
            "/api/v1/iam/platform/audit-logs",
            "/api/v1/iam/platform/tenants",
            "/api/v1/iam/platform/tenants/{tenantId}",
            "/api/v1/iam/platform/tenants/{tenantId}/enable",
            "/api/v1/iam/platform/tenants/{tenantId}/disable",
            "/api/v1/iam/platform/tenants/{tenantId}/initialize-admin",
        }
        self.assertTrue(expected_paths.issubset(set(schema["paths"].keys())))
        self.assertNotIn("/internal/auth/login", schema["paths"])
        self.assertNotIn("/api/v1/iam/me/permissions", schema["paths"])
        self.assertIn("BearerAuth", schema["components"]["securitySchemes"])

    def test_old_internal_auth_routes_should_be_unmounted(self):
        response = self.client.get("/internal/auth/login")
        self.assertEqual(response.status_code, 404)
