from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.dji_bff.models import SyncStatus, TenantMissionIndex, TenantRouteIndex
from apps.dji_mock.state import mock_dji_state
from apps.dji_mock.test_support import MockDjiUpstreamTestMixin
from apps.drone.models import Drone
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route

User = get_user_model()


class MissionApiTests(MockDjiUpstreamTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.user = User.objects.create_user(username="mission_dispatcher", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="任务调度员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="mission_test_tenant",
            role_code="mission_test_role",
            role_name="任务测试角色",
        )
        grant_role_permissions(
            self.role,
            {
                "mission.view_mission": ScopeType.ALL,
                "mission.manage_mission": ScopeType.ALL,
            },
        )
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

        self.pilot_user = User.objects.create_user(username="mission_pilot", password="pass1234", status=1)
        ensure_staff_profile(self.pilot_user, name="飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, self.pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")

        self.route = Route.objects.create(tenant=self.tenant, name="任务航线")
        self.route_index = TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=self.route,
            dji_wayline_id="wayline-001",
            is_published=True,
        )
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="MISSION-DRONE-001",
            name="任务无人机",
            model="M30",
            device_sn="MISSION-SN-001",
        )

    def test_create_should_sync_job_and_persist_index(self):
        response = self.client.post(
            "/api/v1/missions",
            {
                "name": "园区巡检任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
                "dock_sn": "dock-001",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        mission = Mission.objects.get(name="园区巡检任务")
        self.assertTrue(mission.dji_job_id.startswith("mock-job-"))
        mission_index = TenantMissionIndex.objects.get(mission=mission)
        self.assertEqual(mission_index.dji_job_id, mission.dji_job_id)
        self.assertIn(mission.dji_job_id, mock_dji_state.jobs)
        self.assertEqual(mock_dji_state.jobs[mission.dji_job_id]["dock_sn"], "dock-001")
        self.assertEqual(mock_dji_state.jobs[mission.dji_job_id]["file_id"], "wayline-001")
        self.assertEqual(mock_dji_state.jobs[mission.dji_job_id]["wayline_type"], 0)
        self.assertEqual(mock_dji_state.jobs[mission.dji_job_id]["task_type"], 0)
        self.assertEqual(mock_dji_state.jobs[mission.dji_job_id]["rth_altitude"], 30)
        self.assertEqual(mock_dji_state.jobs[mission.dji_job_id]["out_of_control_action"], 0)

    def test_create_should_require_dock_sn(self):
        response = self.client.post(
            "/api/v1/missions",
            {
                "name": "缺少机库任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["data"], {"dock_sn": ["该字段是必填项。"]})
        self.assertEqual(mock_dji_state.jobs, {})

    def test_create_should_require_route(self):
        response = self.client.post(
            "/api/v1/missions",
            {
                "name": "缺少航线任务",
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
                "dock_sn": "dock-missing-route",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["data"], {"route": ["该字段是必填项。"]})
        self.assertEqual(mock_dji_state.jobs, {})

    def test_create_should_reject_unpublished_route(self):
        self.route_index.is_published = False
        self.route_index.save(update_fields=["is_published", "updated_at"])

        response = self.client.post(
            "/api/v1/missions",
            {
                "name": "未发布航线任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
                "dock_sn": "dock-unpublished-route",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("route", response.data["data"])
        self.assertEqual(mock_dji_state.jobs, {})

    def test_cancel_should_call_gateway_and_mark_mission_canceled(self):
        job = mock_dji_state.create_job({"name": "待取消任务", "dock_sn": "dock-cancel"})
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="待取消任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.PENDING,
            dji_job_id=job["job_id"],
        )
        mission_index = TenantMissionIndex.objects.create(
            tenant=self.tenant,
            mission=mission,
            dji_job_id=job["job_id"],
            sync_status=SyncStatus.SYNCED,
        )

        response = self.client.post(f"/api/v1/missions/{mission.id}/cancel")

        self.assertEqual(response.status_code, 200)
        mission.refresh_from_db()
        mission_index.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.CANCELED)
        self.assertEqual(mission_index.execution_status, str(MissionStatus.CANCELED))
        self.assertEqual(mock_dji_state.jobs[job["job_id"]]["status"], "CANCELED")

    def test_cancel_should_reject_request_body(self):
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="取消请求体验证任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.PENDING,
            dji_job_id="job-cancel-002",
        )

        response = self.client.post(
            f"/api/v1/missions/{mission.id}/cancel",
            {"unexpected": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
