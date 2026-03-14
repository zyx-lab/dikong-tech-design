from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AuditLog, EmploymentStatus, ScopeType
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.drone.models import Drone, DroneStatus
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route, RouteStatus

User = get_user_model()


class MissionApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.dispatcher_user = User.objects.create_user(username="mission_dispatcher", password="pass1234", status=1)
        self.dispatcher_staff = ensure_staff_profile(
            self.dispatcher_user,
            staff_no="M-001",
            name="任务调度员A",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.dispatcher_user,
            tenant_code="mission_test_tenant",
            role_code="mission_test_role",
            role_name="任务测试角色",
        )
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

        self.pilot_user = User.objects.create_user(username="mission_pilot", password="pass1234", status=1)
        self.pilot_staff = ensure_staff_profile(
            self.pilot_user,
            staff_no="P-001",
            name="飞手A",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _pilot_tenant, pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(pilot_member, code="pilot_operator", name="飞手")

        self.route = Route.objects.create(tenant=self.tenant, name="城区巡检航线", status=RouteStatus.ACTIVE)
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="DRN-001",
            name="巡检机-001",
            model="M300",
            serial_no="SN-MISSION-001",
            status=DroneStatus.ENABLED,
        )

    def _grant_permission(self, permission_code: str):
        grant_role_permissions(
            self.role,
            {permission_code: ScopeType.ALL},
            group_name=f"{permission_code}-group",
        )

    def _create_mission(self, *, name: str, status: int = MissionStatus.PENDING) -> Mission:
        return Mission.objects.create(
            tenant=self.tenant,
            name=name,
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_staff,
            pilot_name=self.pilot_staff.name,
            status=status,
        )

    def test_create_mission_should_return_success(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post(
            "/api/v1/missions",
            {
                "name": "前山河晨检任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": self.pilot_staff.id,
                "scheduled_at": "2026-03-09T09:00:00+08:00",
                "remark": "晴天执行",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["name"], "前山河晨检任务")
        self.assertEqual(response.data["status"], MissionStatus.PENDING)
        self.assertEqual(response.data["route_name"], self.route.name)
        self.assertEqual(response.data["drone_name"], self.drone.name)
        self.assertEqual(response.data["pilot_name"], self.pilot_staff.name)

    def test_create_mission_invalid_params_should_return_invalid_params(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post(
            "/api/v1/missions",
            {
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": self.pilot_staff.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("name", response.data)

    def test_create_mission_with_disabled_route_should_return_invalid_params(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        disabled_route = Route.objects.create(name="禁用航线", status=RouteStatus.DISABLED)

        response = self.client.post(
            "/api/v1/missions",
            {
                "name": "禁用航线任务",
                "route": disabled_route.id,
                "drone": self.drone.id,
                "pilot": self.pilot_staff.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("route", response.data)

    def test_create_mission_with_disabled_drone_should_return_invalid_params(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        disabled_drone = Drone.objects.create(
            code="DRN-002",
            name="停用任务机",
            model="M300",
            serial_no="SN-MISSION-002",
            status=DroneStatus.DISABLED,
        )

        response = self.client.post(
            "/api/v1/missions",
            {
                "name": "停用无人机任务",
                "route": self.route.id,
                "drone": disabled_drone.id,
                "pilot": self.pilot_staff.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("drone", response.data)

    def test_create_mission_with_inactive_pilot_should_return_invalid_params(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        inactive_pilot_user = User.objects.create_user(username="inactive_mission_pilot", password="pass1234", status=1)
        inactive_pilot = ensure_staff_profile(
            inactive_pilot_user,
            staff_no="P-002",
            name="离职飞手",
            employment_status=EmploymentStatus.INACTIVE,
        )
        _pilot_tenant, inactive_pilot_member, _pilot_role = ensure_tenant_role_binding(
            inactive_pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(inactive_pilot_member, code="pilot_operator", name="飞手")

        response = self.client.post(
            "/api/v1/missions",
            {
                "name": "离职飞手任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": inactive_pilot.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("pilot", response.data)

    def test_create_mission_with_non_pilot_staff_should_return_invalid_params(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        observer_user = User.objects.create_user(username="mission_observer", password="pass1234", status=1)
        observer_staff = ensure_staff_profile(
            observer_user,
            staff_no="O-001",
            name="观察员A",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _observer_tenant, observer_member, _observer_role = ensure_tenant_role_binding(
            observer_user,
            tenant=self.tenant,
            role_code="route_planner",
            role_name="观察员",
        )
        ensure_tenant_member_position(observer_member, code="route_planner", name="观察员")

        response = self.client.post(
            "/api/v1/missions",
            {
                "name": "非飞手任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": observer_staff.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("pilot", response.data)

    def test_create_mission_with_nonexistent_route_should_return_invalid_params(self):
        """测试 route 不存在"""
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post(
            "/api/v1/missions",
            {
                "name": "不存在航线任务",
                "route": 99999,
                "drone": self.drone.id,
                "pilot": self.pilot_staff.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")

    def test_create_mission_with_nonexistent_drone_should_return_invalid_params(self):
        """测试 drone 不存在"""
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post(
            "/api/v1/missions",
            {
                "name": "不存在无人机任务",
                "route": self.route.id,
                "drone": 99999,
                "pilot": self.pilot_staff.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")

    def test_create_mission_with_nonexistent_pilot_should_return_invalid_params(self):
        """测试 pilot 不存在"""
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post(
            "/api/v1/missions",
            {
                "name": "不存在飞手任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": 99999,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")

    def test_create_mission_without_auth_should_return_permission_denied(self):
        response = self.client.post(
            "/api/v1/missions",
            {
                "name": "未认证创建任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": self.pilot_staff.id,
            },
            format="json",
        )
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_create_mission_without_permission_should_return_permission_denied(self):
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post(
            "/api/v1/missions",
            {
                "name": "无权限创建任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": self.pilot_staff.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_patch_mission_should_return_success(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="待更新任务", status=MissionStatus.PENDING)

        response = self.client.patch(
            f"/api/v1/missions/{mission.id}",
            {
                "name": "更新后任务",
                "remark": "调整执行窗口",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["name"], "更新后任务")
        self.assertEqual(response.data["remark"], "调整执行窗口")

        mission.refresh_from_db()
        self.assertEqual(mission.name, "更新后任务")
        self.assertEqual(mission.remark, "调整执行窗口")

    def test_patch_mission_with_status_should_return_invalid_params(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="状态字段更新测试", status=MissionStatus.PENDING)

        response = self.client.patch(
            f"/api/v1/missions/{mission.id}",
            {"status": MissionStatus.RUNNING},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("status", response.data)

    def test_patch_mission_not_found_should_return_resource_not_found(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.patch(
            "/api/v1/missions/999999",
            {"name": "不存在任务"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_patch_mission_without_auth_should_return_permission_denied(self):
        mission = self._create_mission(name="未认证更新任务")

        response = self.client.patch(
            f"/api/v1/missions/{mission.id}",
            {"remark": "未认证请求"},
            format="json",
        )

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_patch_mission_without_permission_should_return_permission_denied(self):
        mission = self._create_mission(name="无权限更新任务")
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.patch(
            f"/api/v1/missions/{mission.id}",
            {"remark": "无权限请求"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_list_missions_should_return_success(self):
        self._grant_permission("mission.view_mission")
        self.client.force_authenticate(self.dispatcher_user)
        self._create_mission(name="任务A")
        self._create_mission(name="任务B", status=MissionStatus.RUNNING)

        response = self.client.get("/api/v1/missions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertIn("results", response.data)
        self.assertGreaterEqual(len(response.data["results"]), 2)

    def test_list_missions_with_status_filter_should_return_filtered_results(self):
        self._grant_permission("mission.view_mission")
        self.client.force_authenticate(self.dispatcher_user)
        self._create_mission(name="待执行任务", status=MissionStatus.PENDING)
        self._create_mission(name="执行中任务", status=MissionStatus.RUNNING)

        response = self.client.get("/api/v1/missions", {"status": MissionStatus.RUNNING})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["name"], "执行中任务")

    def test_list_missions_without_auth_should_return_permission_denied(self):
        response = self.client.get("/api/v1/missions")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_list_missions_without_permission_should_return_permission_denied(self):
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.get("/api/v1/missions")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_retrieve_mission_should_return_success(self):
        self._grant_permission("mission.view_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="详情任务A", status=MissionStatus.RUNNING)

        response = self.client.get(f"/api/v1/missions/{mission.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["id"], mission.id)
        self.assertEqual(response.data["name"], "详情任务A")
        self.assertEqual(response.data["status"], MissionStatus.RUNNING)

    def test_retrieve_mission_not_found_should_return_resource_not_found(self):
        self._grant_permission("mission.view_mission")
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.get("/api/v1/missions/999999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_retrieve_mission_without_auth_should_return_permission_denied(self):
        mission = self._create_mission(name="未认证详情任务")

        response = self.client.get(f"/api/v1/missions/{mission.id}")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_retrieve_mission_without_permission_should_return_permission_denied(self):
        mission = self._create_mission(name="无权限详情任务")
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.get(f"/api/v1/missions/{mission.id}")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_cancel_mission_should_return_success(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="待取消任务", status=MissionStatus.RUNNING)

        response = self.client.post(f"/api/v1/missions/{mission.id}/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], MissionStatus.CANCELED)

        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.CANCELED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="MISSION_CANCEL",
                target_type="mission",
                target_id=str(mission.id),
            ).exists()
        )

    def test_cancel_mission_with_body_should_return_invalid_params(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="取消请求体任务", status=MissionStatus.PENDING)

        response = self.client.post(
            f"/api/v1/missions/{mission.id}/cancel",
            {"unexpected": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.PENDING)

    def test_cancel_mission_state_conflict_should_return_state_conflict(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="已完成任务", status=MissionStatus.COMPLETED)

        response = self.client.post(f"/api/v1/missions/{mission.id}/cancel")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "STATE_CONFLICT")
        self.assertEqual(response.data["business_detail_code"], "STATE_CONFLICT")
        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.COMPLETED)

    def test_cancel_mission_not_found_should_return_resource_not_found(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post("/api/v1/missions/999999/cancel")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_cancel_mission_without_auth_should_return_permission_denied(self):
        mission = self._create_mission(name="未认证取消任务", status=MissionStatus.RUNNING)

        response = self.client.post(f"/api/v1/missions/{mission.id}/cancel")

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_cancel_mission_without_permission_should_return_permission_denied(self):
        mission = self._create_mission(name="无权限取消任务", status=MissionStatus.RUNNING)
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post(f"/api/v1/missions/{mission.id}/cancel")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_start_mission_should_return_success(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="待启动任务", status=MissionStatus.PENDING)

        response = self.client.post(f"/api/v1/missions/{mission.id}/start")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], MissionStatus.RUNNING)

        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.RUNNING)
        self.assertTrue(
            AuditLog.objects.filter(
                action="MISSION_START",
                target_type="mission",
                target_id=str(mission.id),
            ).exists()
        )

    def test_start_running_mission_should_be_idempotent_success(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="执行中任务", status=MissionStatus.RUNNING)

        response = self.client.post(f"/api/v1/missions/{mission.id}/start")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], MissionStatus.RUNNING)

    def test_start_mission_with_body_should_return_invalid_params(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="启动请求体任务", status=MissionStatus.PENDING)

        response = self.client.post(
            f"/api/v1/missions/{mission.id}/start",
            {"unexpected": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.PENDING)

    def test_start_mission_state_conflict_should_return_state_conflict(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="已取消任务", status=MissionStatus.CANCELED)

        response = self.client.post(f"/api/v1/missions/{mission.id}/start")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "STATE_CONFLICT")
        self.assertEqual(response.data["business_detail_code"], "STATE_CONFLICT")
        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.CANCELED)

    def test_start_mission_not_found_should_return_resource_not_found(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post("/api/v1/missions/999999/start")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_start_mission_without_auth_should_return_permission_denied(self):
        mission = self._create_mission(name="未认证启动任务", status=MissionStatus.PENDING)

        response = self.client.post(f"/api/v1/missions/{mission.id}/start")

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_start_mission_without_permission_should_return_permission_denied(self):
        mission = self._create_mission(name="无权限启动任务", status=MissionStatus.PENDING)
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post(f"/api/v1/missions/{mission.id}/start")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_pause_mission_should_return_success(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="待暂停任务", status=MissionStatus.RUNNING)

        response = self.client.post(f"/api/v1/missions/{mission.id}/pause")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], MissionStatus.PAUSED)

        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.PAUSED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="MISSION_PAUSE",
                target_type="mission",
                target_id=str(mission.id),
            ).exists()
        )

    def test_pause_paused_mission_should_be_idempotent_success(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="已暂停任务", status=MissionStatus.PAUSED)

        response = self.client.post(f"/api/v1/missions/{mission.id}/pause")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], MissionStatus.PAUSED)

    def test_pause_mission_with_body_should_return_invalid_params(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="暂停请求体任务", status=MissionStatus.RUNNING)

        response = self.client.post(
            f"/api/v1/missions/{mission.id}/pause",
            {"unexpected": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.RUNNING)

    def test_pause_mission_state_conflict_should_return_state_conflict(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="待执行任务", status=MissionStatus.PENDING)

        response = self.client.post(f"/api/v1/missions/{mission.id}/pause")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "STATE_CONFLICT")
        self.assertEqual(response.data["business_detail_code"], "STATE_CONFLICT")
        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.PENDING)

    def test_pause_mission_not_found_should_return_resource_not_found(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post("/api/v1/missions/999999/pause")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_pause_mission_without_auth_should_return_permission_denied(self):
        mission = self._create_mission(name="未认证暂停任务", status=MissionStatus.RUNNING)

        response = self.client.post(f"/api/v1/missions/{mission.id}/pause")

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_pause_mission_without_permission_should_return_permission_denied(self):
        mission = self._create_mission(name="无权限暂停任务", status=MissionStatus.RUNNING)
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post(f"/api/v1/missions/{mission.id}/pause")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_resume_mission_should_return_success(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="待恢复任务", status=MissionStatus.PAUSED)

        response = self.client.post(f"/api/v1/missions/{mission.id}/resume")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], MissionStatus.RUNNING)

        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.RUNNING)
        self.assertTrue(
            AuditLog.objects.filter(
                action="MISSION_RESUME",
                target_type="mission",
                target_id=str(mission.id),
            ).exists()
        )

    def test_resume_running_mission_should_be_idempotent_success(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="执行中任务", status=MissionStatus.RUNNING)

        response = self.client.post(f"/api/v1/missions/{mission.id}/resume")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], MissionStatus.RUNNING)

    def test_resume_mission_with_body_should_return_invalid_params(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="恢复请求体任务", status=MissionStatus.PAUSED)

        response = self.client.post(
            f"/api/v1/missions/{mission.id}/resume",
            {"unexpected": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.PAUSED)

    def test_resume_mission_state_conflict_should_return_state_conflict(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="待执行任务", status=MissionStatus.PENDING)

        response = self.client.post(f"/api/v1/missions/{mission.id}/resume")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "STATE_CONFLICT")
        self.assertEqual(response.data["business_detail_code"], "STATE_CONFLICT")
        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.PENDING)

    def test_resume_mission_not_found_should_return_resource_not_found(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post("/api/v1/missions/999999/resume")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_resume_mission_without_auth_should_return_permission_denied(self):
        mission = self._create_mission(name="未认证恢复任务", status=MissionStatus.PAUSED)

        response = self.client.post(f"/api/v1/missions/{mission.id}/resume")

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_resume_mission_without_permission_should_return_permission_denied(self):
        mission = self._create_mission(name="无权限恢复任务", status=MissionStatus.PAUSED)
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post(f"/api/v1/missions/{mission.id}/resume")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_complete_mission_should_return_success(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="待完成任务", status=MissionStatus.RUNNING)

        response = self.client.post(f"/api/v1/missions/{mission.id}/complete")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], MissionStatus.COMPLETED)

        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.COMPLETED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="MISSION_COMPLETE",
                target_type="mission",
                target_id=str(mission.id),
            ).exists()
        )

    def test_complete_completed_mission_should_be_idempotent_success(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="已完成任务", status=MissionStatus.COMPLETED)

        response = self.client.post(f"/api/v1/missions/{mission.id}/complete")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], MissionStatus.COMPLETED)

    def test_complete_mission_with_body_should_return_invalid_params(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="完成请求体任务", status=MissionStatus.RUNNING)

        response = self.client.post(
            f"/api/v1/missions/{mission.id}/complete",
            {"unexpected": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.RUNNING)

    def test_complete_mission_state_conflict_should_return_state_conflict(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="已暂停任务", status=MissionStatus.PAUSED)

        response = self.client.post(f"/api/v1/missions/{mission.id}/complete")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "STATE_CONFLICT")
        self.assertEqual(response.data["business_detail_code"], "STATE_CONFLICT")
        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.PAUSED)

    def test_complete_mission_not_found_should_return_resource_not_found(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post("/api/v1/missions/999999/complete")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_complete_mission_without_auth_should_return_permission_denied(self):
        mission = self._create_mission(name="未认证完成任务", status=MissionStatus.RUNNING)

        response = self.client.post(f"/api/v1/missions/{mission.id}/complete")

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_complete_mission_without_permission_should_return_permission_denied(self):
        mission = self._create_mission(name="无权限完成任务", status=MissionStatus.RUNNING)
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post(f"/api/v1/missions/{mission.id}/complete")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_fail_mission_should_return_success(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="待失败任务", status=MissionStatus.RUNNING)

        response = self.client.post(f"/api/v1/missions/{mission.id}/fail")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], MissionStatus.FAILED)

        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.FAILED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="MISSION_FAIL",
                target_type="mission",
                target_id=str(mission.id),
            ).exists()
        )

    def test_fail_failed_mission_should_be_idempotent_success(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="已失败任务", status=MissionStatus.FAILED)

        response = self.client.post(f"/api/v1/missions/{mission.id}/fail")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], MissionStatus.FAILED)

    def test_fail_mission_with_body_should_return_invalid_params(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="失败请求体任务", status=MissionStatus.RUNNING)

        response = self.client.post(
            f"/api/v1/missions/{mission.id}/fail",
            {"unexpected": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.RUNNING)

    def test_fail_mission_state_conflict_should_return_state_conflict(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)
        mission = self._create_mission(name="已暂停任务", status=MissionStatus.PAUSED)

        response = self.client.post(f"/api/v1/missions/{mission.id}/fail")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "STATE_CONFLICT")
        self.assertEqual(response.data["business_detail_code"], "STATE_CONFLICT")
        mission.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.PAUSED)

    def test_fail_mission_not_found_should_return_resource_not_found(self):
        self._grant_permission("mission.manage_mission")
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post("/api/v1/missions/999999/fail")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_fail_mission_without_auth_should_return_permission_denied(self):
        mission = self._create_mission(name="未认证失败任务", status=MissionStatus.RUNNING)

        response = self.client.post(f"/api/v1/missions/{mission.id}/fail")

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_fail_mission_without_permission_should_return_permission_denied(self):
        mission = self._create_mission(name="无权限失败任务", status=MissionStatus.RUNNING)
        self.client.force_authenticate(self.dispatcher_user)

        response = self.client.post(f"/api/v1/missions/{mission.id}/fail")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})
