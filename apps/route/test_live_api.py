"""Route live HTTP smoke tests."""

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_live_base import LiveDjiGatewayApiTestCase, User
from apps.access.test_support import ensure_staff_profile, ensure_tenant_role_binding, grant_role_permissions
from apps.dji_bff.models import SyncStatus, TenantRouteIndex
from apps.dji_mock.state import mock_dji_state
from apps.route.models import Route


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

    def test_download_and_delete_should_follow_http_contract(self):
        wayline = mock_dji_state.create_wayline(name="实时航线")
        route = Route.objects.create(tenant=self.tenant, name="实时航线", creator_name="管理员")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id=wayline["wayline_id"],
            sync_status=SyncStatus.SYNCED,
        )

        download_response = self.client.get(f"/api/v1/routes/{route.id}/download")
        self.assertEqual(download_response.status_code, 200)
        self.assertIn("mock wayline binary", download_response.text)

        delete_response = self.client.delete(f"/api/v1/routes/{route.id}")
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(Route.objects.filter(id=route.id).exists())
        self.assertNotIn(wayline["wayline_id"], mock_dji_state.waylines)

        removed_enable_response = self.client.post(f"/api/v1/routes/{route.id}/enable")
        self.assertEqual(removed_enable_response.status_code, 404)
