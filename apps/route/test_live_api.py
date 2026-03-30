"""Route live HTTP contract tests."""

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_live_base import LiveDjiGatewayApiTestCase, User
from apps.access.test_support import ensure_staff_profile, ensure_tenant_role_binding, grant_role_permissions


class LiveRouteApiTests(LiveDjiGatewayApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="route_live_admin", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="实时航线管理员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="route_live_tenant",
            role_code="route_live_role",
            role_name="实时航线角色",
        )
        grant_role_permissions(
            self.role,
            {
                "route.view_route": ScopeType.ALL,
                "route.manage_route": ScopeType.ALL,
            },
        )
        self.login(username="route_live_admin", password="pass1234", tenant_code=self.tenant.code)

    def test_route_detail_publish_download_should_follow_http_contract(self):
        create_response = self.client.post(
            "/api/v1/routes",
            {
                "name": "实时航线",
                "waypoints": [
                    {"sequence": 1, "latitude": 31.5, "longitude": 121.5, "altitude": 80},
                    {"sequence": 2, "latitude": 31.6, "longitude": 121.6, "altitude": 90},
                ],
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201)
        create_data = create_response.json()["data"]
        self.assertFalse(create_data["is_published"])
        route_id = create_data["id"]

        detail_response = self.client.get(f"/api/v1/routes/{route_id}")
        self.assertEqual(detail_response.status_code, 200)
        detail_data = detail_response.json()["data"]
        self.assertEqual(detail_data["waypoints"][0]["sequence"], 1)

        publish_response = self.client.post(f"/api/v1/routes/{route_id}/publish")
        self.assertEqual(publish_response.status_code, 200)
        publish_data = publish_response.json()["data"]
        self.assertTrue(publish_data["is_published"])

        download_response = self.client.get(f"/api/v1/routes/{route_id}/download")
        self.assertEqual(download_response.status_code, 200)
        self.assertIn("mock wayline binary", download_response.text)
