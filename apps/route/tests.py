from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.drone.models import Drone
from apps.dji_bff.models import SyncStatus, TenantRouteIndex
from apps.dji_mock.state import mock_dji_state
from apps.dji_mock.test_support import MockDjiUpstreamTestMixin
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route

User = get_user_model()


class RouteApiTests(MockDjiUpstreamTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.user = User.objects.create_user(username="route_admin", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="航线管理员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="route_test_tenant",
            role_code="route_test_role",
            role_name="航线测试角色",
        )
        grant_role_permissions(
            self.role,
            {
                "route.view_route": ScopeType.ALL,
                "route.manage_route": ScopeType.ALL,
                "mission.manage_mission": ScopeType.ALL,
            },
        )
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

        self.pilot_user = User.objects.create_user(username="route_pilot", password="pass1234", status=1)
        ensure_staff_profile(self.pilot_user, name="飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, self.pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="ROUTE-DRONE-001",
            name="任务无人机",
            model="M30",
            device_sn="ROUTE-SN-001",
        )

    def test_create_should_upload_kmz_and_create_route_index(self):
        response = self.client.post(
            "/api/v1/routes",
            {
                "name": "城市巡检航线",
                "file": SimpleUploadedFile("route.kmz", b"fake-kmz", content_type="application/octet-stream"),
            },
        )

        self.assertEqual(response.status_code, 201)
        route = Route.objects.get(name="城市巡检航线")
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertTrue(route_index.dji_wayline_id.startswith("mock-wayline-"))
        self.assertEqual(route_index.sync_status, SyncStatus.SYNCED)
        self.assertIn(route_index.dji_wayline_id, mock_dji_state.waylines)

    def test_create_should_reject_duplicate_wayline_name_from_upstream(self):
        existing_wayline = mock_dji_state.create_wayline(name="城市巡检航线")

        response = self.client.post(
            "/api/v1/routes",
            {
                "name": "城市巡检航线",
                "file": SimpleUploadedFile("route.kmz", b"fake-kmz", content_type="application/octet-stream"),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["data"], {"name": ["DJI 航线名称已存在"]})
        self.assertFalse(Route.objects.filter(tenant=self.tenant, name="城市巡检航线").exists())
        self.assertEqual(list(mock_dji_state.waylines.keys()), [existing_wayline["wayline_id"]])

    def test_download_should_redirect_to_dji_url(self):
        wayline = mock_dji_state.create_wayline(name="下载航线")
        route = Route.objects.create(tenant=self.tenant, name="下载航线", creator_name="管理员")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id=wayline["wayline_id"],
            sync_status=SyncStatus.SYNCED,
        )

        response = self.client.get(f"/api/v1/routes/{route.id}/download")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/__mock-dji__/_downloads/waylines/{wayline['wayline_id']}.kmz")

    def test_delete_should_reject_when_active_mission_exists(self):
        wayline = mock_dji_state.create_wayline(name="被占用航线")
        route = Route.objects.create(tenant=self.tenant, name="被占用航线", creator_name="管理员")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id=wayline["wayline_id"],
            sync_status=SyncStatus.SYNCED,
        )
        Mission.objects.create(
            tenant=self.tenant,
            name="运行中任务",
            route=route,
            route_name=route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.RUNNING,
        )

        response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertIn(wayline["wayline_id"], mock_dji_state.waylines)

    def test_delete_should_remove_route_when_only_historical_missions_exist(self):
        wayline = mock_dji_state.create_wayline(name="可删除航线")
        route = Route.objects.create(tenant=self.tenant, name="可删除航线", creator_name="管理员")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id=wayline["wayline_id"],
            sync_status=SyncStatus.SYNCED,
        )
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="已完成任务",
            route=route,
            route_name=route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
        )

        response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Route.objects.filter(id=route.id).exists())
        mission.refresh_from_db()
        self.assertIsNone(mission.route)
        self.assertNotIn(wayline["wayline_id"], mock_dji_state.waylines)
