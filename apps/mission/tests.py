from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AuditLog, EmploymentStatus, GroupPermissionScope, ScopeStatus, ScopeType, StaffProfile, StaffType, StaffTypeGroup
from apps.drone.models import Drone, DroneStatus
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route, RouteStatus

User = get_user_model()


class MissionApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.dispatcher_staff_type = StaffType.objects.create(code="mission_dispatch_test", name="任务调度员", status=1)
        self.pilot_staff_type = StaffType.objects.create(code="pilot_operator", name="飞手", status=1)

        self.dispatcher_user = User.objects.create_user(username="mission_dispatcher", password="pass1234", status=1)
        self.dispatcher_staff = StaffProfile.objects.create(
            user=self.dispatcher_user,
            staff_no="M-001",
            name="任务调度员A",
            employment_status=EmploymentStatus.ACTIVE,
            staff_type=self.dispatcher_staff_type,
        )

        self.pilot_user = User.objects.create_user(username="mission_pilot", password="pass1234", status=1)
        self.pilot_staff = StaffProfile.objects.create(
            user=self.pilot_user,
            staff_no="P-001",
            name="飞手A",
            employment_status=EmploymentStatus.ACTIVE,
            staff_type=self.pilot_staff_type,
        )

        self.route = Route.objects.create(name="城区巡检航线", status=RouteStatus.ACTIVE)
        self.drone = Drone.objects.create(
            code="DRN-001",
            name="巡检机-001",
            model="M300",
            serial_no="SN-MISSION-001",
            status=DroneStatus.ENABLED,
        )

    def _grant_permission(self, permission_code: str):
        app_label, codename = permission_code.split(".", 1)
        perm = Permission.objects.get(content_type__app_label=app_label, codename=codename)
        group = Group.objects.create(name=f"{permission_code}-group")
        group.permissions.add(perm)
        StaffTypeGroup.objects.create(staff_type=self.dispatcher_staff_type, group=group, status=ScopeStatus.ACTIVE)
        GroupPermissionScope.objects.create(
            group=group,
            permission=perm,
            scope_type=ScopeType.ALL,
            status=ScopeStatus.ACTIVE,
        )

    def _create_mission(self, *, name: str, status: int = MissionStatus.PENDING) -> Mission:
        return Mission.objects.create(
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
