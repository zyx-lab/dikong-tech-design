from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.test.utils import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.drone.models import Drone
from apps.mission.models import Mission
from apps.route.models import Route


def _persist_mission_fixture(**kwargs) -> Mission:
    mission = Mission(**kwargs)
    Mission.objects.bulk_create([mission])
    return Mission.objects.get(pk=mission.pk)

User = get_user_model()


@override_settings(ROOT_URLCONF="apps.mission.urls")
class MissionApiTests(TestCase):
    @staticmethod
    def _payload(data: dict) -> dict:
        return data.get("data", data)

    def setUp(self):
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
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="MISSION-DRONE-001",
            name="任务无人机",
            model="M30",
            device_sn="MISSION-SN-001",
        )

    def test_create_should_require_route(self):
        response = self.client.post(
            "/missions",
            {
                "name": "缺少航线任务",
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["data"], {"route": ["该字段是必填项。"]})

    def test_create_should_require_drone(self):
        response = self.client.post(
            "/missions",
            {
                "name": "缺少无人机任务",
                "route": self.route.id,
                "pilot": self.pilot_member.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["data"], {"drone": ["该字段是必填项。"]})

    def test_create_should_initialize_execution_window_fields(self):
        response = self.client.post(
            "/missions",
            {
                "name": "待执行任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        payload = self._payload(response.data)
        self.assertIn("started_at", payload)
        self.assertIn("finished_at", payload)
        self.assertEqual(payload["status"], 0)
        self.assertIsNone(payload["started_at"])
        self.assertIsNone(payload["finished_at"])

    def test_create_should_allow_unpublished_route_because_mission_is_now_local_only(self):
        response = self.client.post(
            "/missions",
            {
                "name": "本地任务不再要求已发布航线",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(Mission.objects.filter(name="本地任务不再要求已发布航线").exists())

    def test_put_should_allow_route_and_drone_change_only_when_pending(self):
        other_route = Route.objects.create(tenant=self.tenant, name="改绑航线")
        other_drone = Drone.objects.create(
            tenant=self.tenant,
            code="MISSION-DRONE-002",
            name="改绑无人机",
            model="M30",
            device_sn="MISSION-SN-002",
        )
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="待执行任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=0,
        )

        response = self.client.put(
            f"/missions/{mission.id}",
            {"route": other_route.id, "drone": other_drone.id},
            format="json",
        )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        mission.refresh_from_db()
        self.assertEqual(mission.route_id, other_route.id)
        self.assertEqual(mission.route_name, other_route.name)
        self.assertEqual(mission.drone_id, other_drone.id)
        self.assertEqual(mission.device_sn, other_drone.device_sn)
        self.assertEqual(mission.drone_name, other_drone.name)

    def test_put_should_reject_any_update_when_status_is_running(self):
        mission = _persist_mission_fixture(
            tenant=self.tenant,
            name="执行中任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=1,
        )

        response = self.client.put(
            f"/missions/{mission.id}",
            {"remark": "不允许修改"},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "C0201")

    def test_advance_should_move_pending_to_running_and_write_started_at(self):
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="状态推进任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=0,
        )

        response = self.client.post(f"/missions/{mission.id}/advance")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        mission.refresh_from_db()
        self.assertEqual(mission.status, 1)
        self.assertIsNotNone(mission.started_at)
        self.assertIsNone(mission.finished_at)

    def test_advance_should_move_running_to_completed_and_write_finished_at(self):
        started_at = timezone.now() - timedelta(minutes=10)
        mission = _persist_mission_fixture(
            tenant=self.tenant,
            name="状态完成任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=1,
        )

        response = self.client.post(f"/missions/{mission.id}/advance")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        mission.refresh_from_db()
        self.assertEqual(mission.status, 2)
        self.assertIsNotNone(mission.started_at)
        self.assertIsNotNone(mission.finished_at)

    def test_advance_should_reject_completed_mission(self):
        mission = _persist_mission_fixture(
            tenant=self.tenant,
            name="已完成任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=2,
        )

        response = self.client.post(f"/missions/{mission.id}/advance")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "C0201")

    def test_advance_should_reject_second_running_mission_for_same_drone(self):
        _persist_mission_fixture(
            tenant=self.tenant,
            name="占用无人机任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=1,
        )
        waiting = _persist_mission_fixture(
            tenant=self.tenant,
            name="等待执行任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=0,
        )

        response = self.client.post(f"/missions/{waiting.id}/advance")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "C0201")

    def test_model_should_require_started_at_when_status_is_running(self):
        mission = _persist_mission_fixture(
            tenant=self.tenant,
            name="缺少开始时间任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=0,
        )

        Mission.objects.filter(pk=mission.pk).update(status=1)
        mission.refresh_from_db()

        with self.assertRaises(ValidationError):
            mission.save()

    def test_model_should_require_finished_at_when_status_is_completed(self):
        mission = _persist_mission_fixture(
            tenant=self.tenant,
            name="缺少结束时间任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=0,
        )

        Mission.objects.filter(pk=mission.pk).update(status=2)
        mission.refresh_from_db()

        with self.assertRaises(ValidationError):
            mission.save()

    def test_patch_should_return_method_not_allowed(self):
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="禁止PATCH任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=0,
        )

        response = self.client.patch(
            f"/missions/{mission.id}",
            {"remark": "不应成功"},
            format="json",
        )

        self.assertEqual(response.status_code, 405, response.data)

    def test_mission_soft_delete_should_be_irreversible(self):
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="软删不可恢复任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            is_deleted=True,
            deleted_at=timezone.now(),
        )

        mission.is_deleted = False
        mission.deleted_at = None
        with self.assertRaises(ValidationError):
            mission.save()

    def test_delete_should_soft_delete_mission_and_hide_it_from_api(self):
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="软删任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
        )

        delete_response = self.client.delete(f"/missions/{mission.id}")

        self.assertEqual(delete_response.status_code, 200)
        mission.refresh_from_db()
        self.assertTrue(mission.is_deleted)
        self.assertIsNotNone(mission.deleted_at)

        list_response = self.client.get("/missions")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["data"]["total"], 0)

        detail_response = self.client.get(f"/missions/{mission.id}")
        self.assertEqual(detail_response.status_code, 404)
        self.assertEqual(detail_response.data["code"], "C0404")

    def test_delete_should_reject_request_body(self):
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="删除请求体验证任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
        )

        response = self.client.delete(
            f"/missions/{mission.id}",
            {"unexpected": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")

    def test_create_should_rollback_when_log_action_fails(self):
        with patch("apps.mission.views.log_action", side_effect=RuntimeError("log failed")):
            response = self.client.post(
                "/missions",
                {
                    "name": "创建回滚任务",
                    "route": self.route.id,
                    "drone": self.drone.id,
                    "pilot": self.pilot_member.id,
                },
                format="json",
            )

        self.assertEqual(response.status_code, 500)
        self.assertFalse(Mission.objects.filter(name="创建回滚任务").exists())
