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
from apps.flight_record.models import FlightRecord, FlightRecordStatus
from apps.media_file.models import MediaFile, MediaType
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
        try:
            from apps.mission.flight_state import flight_state_registry
        except ModuleNotFoundError:
            return
        flight_state_registry.clear()

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
            started_at=timezone.now() - timedelta(minutes=5),
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
        self.assertFalse(FlightRecord.objects.filter(mission=mission).exists())

    def test_advance_should_move_running_to_completed_and_create_flight_record_snapshot(self):
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
            started_at=started_at,
        )
        MediaFile.objects.create(
            tenant=self.tenant,
            mission=mission,
            device_sn=self.drone.device_sn,
            media_type=MediaType.VIDEO,
            file_name="mission-video.mp4",
            file_url="https://example.com/mission-video.mp4",
            captured_at=started_at + timedelta(minutes=2),
        )
        MediaFile.objects.create(
            tenant=self.tenant,
            mission=mission,
            device_sn=self.drone.device_sn,
            media_type=MediaType.PHOTO,
            file_name="mission-photo.jpg",
            file_url="https://example.com/mission-photo.jpg",
            captured_at=started_at + timedelta(minutes=3),
            is_deleted=True,
            deleted_at=started_at + timedelta(minutes=4),
        )

        response = self.client.post(f"/missions/{mission.id}/advance")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        mission.refresh_from_db()
        self.assertEqual(mission.status, 2)
        self.assertIsNotNone(mission.started_at)
        self.assertIsNotNone(mission.finished_at)
        record = FlightRecord.objects.get(mission=mission)
        self.assertEqual(record.tenant_id, self.tenant.id)
        self.assertEqual(record.flight_no, f"FR-{mission.id}")
        self.assertEqual(record.device_sn, self.drone.device_sn)
        self.assertEqual(record.mission_name, mission.name)
        self.assertEqual(record.route_name, mission.route_name)
        self.assertEqual(record.airport_name, "")
        self.assertEqual(record.drone_id, mission.drone_id)
        self.assertEqual(record.drone_name, mission.drone_name)
        self.assertEqual(record.pilot_id, mission.pilot_id)
        self.assertEqual(record.pilot_name, mission.pilot_name)
        self.assertEqual(record.start_time, mission.started_at)
        self.assertEqual(record.end_time, mission.finished_at)
        self.assertEqual(record.status, FlightRecordStatus.COMPLETED)
        self.assertEqual(record.photo_count, 0)
        self.assertEqual(record.video_count, 1)
        self.assertEqual(
            record.flight_duration,
            int((mission.finished_at - mission.started_at).total_seconds()),
        )

    def test_retrieve_should_return_flying_status_when_running_mission_drone_is_airborne(self):
        from apps.mission.flight_state import flight_state_registry

        mission = _persist_mission_fixture(
            tenant=self.tenant,
            name="飞行中任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=1,
            started_at=timezone.now() - timedelta(minutes=3),
        )
        flight_state_registry.update_from_mode_code(device_sn=self.drone.device_sn, mode_code=5)

        response = self.client.get(f"/missions/{mission.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._payload(response.data)["status"], 3)

    def test_retrieve_should_fall_back_to_running_when_airborne_state_is_stale(self):
        from apps.mission.flight_state import flight_state_registry

        mission = _persist_mission_fixture(
            tenant=self.tenant,
            name="超时回退任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=1,
            started_at=timezone.now() - timedelta(minutes=3),
        )
        flight_state_registry.update_from_mode_code(
            device_sn=self.drone.device_sn,
            mode_code=5,
            observed_at=timezone.now() - timedelta(seconds=30),
        )

        response = self.client.get(f"/missions/{mission.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._payload(response.data)["status"], 1)

    def test_retrieve_should_not_override_completed_status_with_airborne_registry(self):
        from apps.mission.flight_state import flight_state_registry

        mission = _persist_mission_fixture(
            tenant=self.tenant,
            name="完成任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=2,
            started_at=timezone.now() - timedelta(minutes=10),
            finished_at=timezone.now() - timedelta(minutes=1),
        )
        flight_state_registry.update_from_mode_code(device_sn=self.drone.device_sn, mode_code=5)

        response = self.client.get(f"/missions/{mission.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._payload(response.data)["status"], 2)

    def test_advance_should_reject_completed_mission(self):
        started_at = timezone.now() - timedelta(minutes=10)
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
            started_at=started_at,
            finished_at=started_at + timedelta(minutes=5),
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
            started_at=timezone.now() - timedelta(minutes=5),
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
