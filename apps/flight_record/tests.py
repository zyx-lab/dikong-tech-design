from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import AuditLog, EmploymentStatus, ScopeType
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.drone.models import Drone, DroneStatus
from apps.flight_record.models import FlightRecord, FlightRecordStatus
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route

User = get_user_model()


def _persist_mission_fixture(**kwargs) -> Mission:
    # Flight record tests only need a persisted mission fixture.
    mission = Mission(**kwargs)
    Mission.objects.bulk_create([mission])
    return Mission.objects.get(pk=mission.pk)


class FlightRecordApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self._flight_no_seq = 1

        self.viewer_user = User.objects.create_user(username="flight_record_viewer", password="pass1234", status=1)
        self.viewer_staff = ensure_staff_profile(
            self.viewer_user,
            staff_no="FR-001",
            name="记录查看员A",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.viewer_user,
            tenant_code="flight_record_test_tenant",
            role_code="flight_record_test_role",
            role_name="飞行记录测试角色",
        )
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

        self.pilot_user = User.objects.create_user(username="flight_record_pilot", password="pass1234", status=1)
        self.pilot_staff = ensure_staff_profile(
            self.pilot_user,
            staff_no="FR-P-001",
            name="飞手A",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _pilot_tenant, pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        self.pilot_member = pilot_member
        ensure_tenant_member_position(pilot_member, code="pilot_operator", name="飞手")

        self.route = Route.objects.create(tenant=self.tenant, name="飞行记录测试航线")
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="FR-DRN-001",
            name="飞行记录测试机",
            model="M300",
            device_sn="SN-FR-001",
            status=DroneStatus.ENABLED,
        )
        self.mission = _persist_mission_fixture(
            tenant=self.tenant,
            name="飞行记录测试任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name=self.pilot_staff.name,
            status=MissionStatus.RUNNING,
        )

    def _grant_permission(self, permission_code: str):
        grant_role_permissions(
            self.role,
            {permission_code: ScopeType.ALL},
            group_name=f"{permission_code}-group",
        )

    def _create_flight_record(self, *, status: int = FlightRecordStatus.COMPLETED, mission: Mission | None = None) -> FlightRecord:
        current_mission = mission or self.mission
        flight_no = f"YJ20260308{self._flight_no_seq:04d}"
        self._flight_no_seq += 1
        start_time = timezone.now() - timedelta(minutes=20)
        end_time = timezone.now()
        return FlightRecord.objects.create(
            tenant=self.tenant,
            flight_no=flight_no,
            mission=current_mission,
            mission_name=current_mission.name,
            route_name=self.route.name,
            airport_name="珠海金湾机场",
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name=self.pilot_staff.name,
            start_time=start_time,
            end_time=end_time,
            flight_duration=1200,
            photo_count=12,
            video_count=3,
            status=status,
        )

    def test_create_flight_record_should_return_success(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)

        response = self.client.post(
            "/api/v1/flight-records",
            {
                "flight_no": "YJ202603080999",
                "mission": self.mission.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
                "airport_name": "珠海金湾机场",
                "start_time": "2026-03-08T08:00:00+08:00",
                "end_time": "2026-03-08T08:18:20+08:00",
                "photo_count": 20,
                "video_count": 4,
                "status": FlightRecordStatus.COMPLETED,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["code"], "00000")
        self.assertEqual(response.data["msg"], "success")
        self.assertEqual(response.data["data"]["flight_no"], "YJ202603080999")
        self.assertEqual(response.data["data"]["mission"], self.mission.id)
        self.assertEqual(response.data["data"]["drone"], self.drone.id)
        self.assertEqual(response.data["data"]["pilot"], self.pilot_member.id)
        self.assertEqual(response.data["data"]["mission_name"], self.mission.name)
        self.assertEqual(response.data["data"]["drone_name"], self.drone.name)
        self.assertEqual(response.data["data"]["pilot_name"], self.pilot_staff.name)
        self.assertEqual(response.data["data"]["status"], FlightRecordStatus.COMPLETED)

    def test_model_should_reject_cross_tenant_mission(self):
        other_tenant, _, _ = ensure_tenant_role_binding(
            self.viewer_user,
            tenant_code="flight_record_model_other_tenant",
            role_code="flight_record_model_other_role",
            role_name="飞行记录模型其他租户角色",
        )
        other_route = Route.objects.create(tenant=other_tenant, name="其他租户航线")
        other_drone = Drone.objects.create(
            tenant=other_tenant,
            code="FR-OTHER-MODEL-DRONE",
            name="其他租户无人机",
            model="M300",
            device_sn="FR-OTHER-MODEL-SN",
            status=DroneStatus.ENABLED,
        )
        _other_pilot_tenant, other_pilot_member, _other_pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=other_tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(other_pilot_member, code="pilot_operator", name="飞手")
        other_mission = _persist_mission_fixture(
            tenant=other_tenant,
            name="其他租户任务",
            route=other_route,
            route_name=other_route.name,
            drone=other_drone,
            drone_name=other_drone.name,
            pilot=other_pilot_member,
            pilot_name=self.pilot_staff.name,
            status=MissionStatus.RUNNING,
        )

        with self.assertRaises(ValidationError):
            FlightRecord.objects.create(
                tenant=self.tenant,
                flight_no="YJ202603089998",
                mission=other_mission,
                mission_name=other_mission.name,
                route_name=other_route.name,
                airport_name="跨租户机场",
                drone=self.drone,
                drone_name=self.drone.name,
                pilot=self.pilot_member,
                pilot_name=self.pilot_staff.name,
                start_time=timezone.now() - timedelta(minutes=5),
                end_time=timezone.now(),
                flight_duration=300,
                status=FlightRecordStatus.COMPLETED,
            )

    def test_create_flight_record_with_cross_tenant_mission_should_return_invalid_params(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        other_tenant, _, _ = ensure_tenant_role_binding(
            self.viewer_user,
            tenant_code="flight_record_other_tenant",
            role_code="flight_record_other_role",
            role_name="飞行记录其他租户角色",
        )
        other_route = Route.objects.create(tenant=other_tenant, name="其他租户航线")
        other_drone = Drone.objects.create(
            tenant=other_tenant,
            code="FR-OTHER-DRN-001",
            name="其他租户无人机",
            model="M300",
            device_sn="FR-OTHER-SN-001",
            status=DroneStatus.ENABLED,
        )
        _other_pilot_tenant, other_pilot_member, _other_pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=other_tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(other_pilot_member, code="pilot_operator", name="飞手")
        other_mission = _persist_mission_fixture(
            tenant=other_tenant,
            name="其他租户任务",
            route=other_route,
            route_name=other_route.name,
            drone=other_drone,
            drone_name=other_drone.name,
            pilot=other_pilot_member,
            pilot_name=self.pilot_staff.name,
            status=MissionStatus.RUNNING,
        )

        response = self.client.post(
            "/api/v1/flight-records",
            {
                "flight_no": "YJ202603081998",
                "mission": other_mission.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertIn("mission", response.data["data"])

    def test_create_flight_record_invalid_params_should_return_invalid_params(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)

        response = self.client.post(
            "/api/v1/flight-records",
            {
                "mission": self.mission.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["code"], "B0001")
        self.assertIn("flight_no", response.data["data"])

    def test_model_should_reject_end_time_before_start_time(self):
        start_time = timezone.now()
        end_time = start_time - timedelta(minutes=5)

        with self.assertRaises(ValidationError):
            FlightRecord.objects.create(
                tenant=self.tenant,
                flight_no="YJ202603089997",
                mission=self.mission,
                mission_name=self.mission.name,
                route_name=self.route.name,
                airport_name="模型时间机场",
                drone=self.drone,
                drone_name=self.drone.name,
                pilot=self.pilot_member,
                pilot_name=self.pilot_staff.name,
                start_time=start_time,
                end_time=end_time,
                status=FlightRecordStatus.COMPLETED,
            )

    def test_model_should_reject_inactive_pilot(self):
        inactive_pilot_user = User.objects.create_user(username="flight_record_inactive_pilot", password="pass1234", status=1)
        inactive_pilot = ensure_staff_profile(
            inactive_pilot_user,
            staff_no="FR-P-MODEL-003",
            name="模型离职飞手",
            employment_status=EmploymentStatus.INACTIVE,
        )
        _pilot_tenant, inactive_pilot_member, _pilot_role = ensure_tenant_role_binding(
            inactive_pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(inactive_pilot_member, code="pilot_operator", name="飞手")

        with self.assertRaises(ValidationError):
            FlightRecord.objects.create(
                tenant=self.tenant,
                flight_no="YJ202603089996",
                mission=self.mission,
                mission_name=self.mission.name,
                route_name=self.route.name,
                airport_name="模型离职飞手机场",
                drone=self.drone,
                drone_name=self.drone.name,
                pilot=inactive_pilot_member,
                pilot_name=inactive_pilot.name,
                start_time=timezone.now() - timedelta(minutes=10),
                end_time=timezone.now(),
                status=FlightRecordStatus.COMPLETED,
            )

    def test_model_should_reject_non_pilot_staff(self):
        observer_user = User.objects.create_user(username="flight_record_observer", password="pass1234", status=1)
        observer_staff = ensure_staff_profile(
            observer_user,
            staff_no="FR-O-MODEL-001",
            name="模型观察员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _observer_tenant, observer_member, _observer_role = ensure_tenant_role_binding(
            observer_user,
            tenant=self.tenant,
            role_code="route_planner",
            role_name="观察员",
        )
        ensure_tenant_member_position(observer_member, code="route_planner", name="观察员")

        with self.assertRaises(ValidationError):
            FlightRecord.objects.create(
                tenant=self.tenant,
                flight_no="YJ202603089995",
                mission=self.mission,
                mission_name=self.mission.name,
                route_name=self.route.name,
                airport_name="模型非飞手机场",
                drone=self.drone,
                drone_name=self.drone.name,
                pilot=observer_member,
                pilot_name=observer_staff.name,
                start_time=timezone.now() - timedelta(minutes=10),
                end_time=timezone.now(),
                status=FlightRecordStatus.COMPLETED,
            )

    def test_model_should_reject_mission_drone_mismatch(self):
        other_drone = Drone.objects.create(
            tenant=self.tenant,
            code="FR-MISMATCH-DRONE",
            name="模型不一致无人机",
            model="M350",
            device_sn="FR-MISMATCH-SN",
            status=DroneStatus.ENABLED,
        )

        with self.assertRaises(ValidationError):
            FlightRecord.objects.create(
                tenant=self.tenant,
                flight_no="YJ202603089994",
                mission=self.mission,
                mission_name=self.mission.name,
                route_name=self.route.name,
                airport_name="模型绑定不一致机场",
                drone=other_drone,
                drone_name=other_drone.name,
                pilot=self.pilot_member,
                pilot_name=self.pilot_staff.name,
                start_time=timezone.now() - timedelta(minutes=10),
                end_time=timezone.now(),
                status=FlightRecordStatus.COMPLETED,
            )

    def test_model_should_reject_mission_pilot_mismatch(self):
        other_pilot_user = User.objects.create_user(username="flight_record_other_pilot_mismatch", password="pass1234", status=1)
        other_pilot = ensure_staff_profile(
            other_pilot_user,
            staff_no="FR-P-MISMATCH-001",
            name="模型其他飞手",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _other_pilot_tenant, other_pilot_member, _other_pilot_role = ensure_tenant_role_binding(
            other_pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(other_pilot_member, code="pilot_operator", name="飞手")

        with self.assertRaises(ValidationError):
            FlightRecord.objects.create(
                tenant=self.tenant,
                flight_no="YJ202603089993",
                mission=self.mission,
                mission_name=self.mission.name,
                route_name=self.route.name,
                airport_name="模型飞手不一致机场",
                drone=self.drone,
                drone_name=self.drone.name,
                pilot=other_pilot_member,
                pilot_name=other_pilot.name,
                start_time=timezone.now() - timedelta(minutes=10),
                end_time=timezone.now(),
                status=FlightRecordStatus.COMPLETED,
            )

    def test_model_should_reject_invalid_status_transition(self):
        record = self._create_flight_record(status=FlightRecordStatus.COMPLETED)
        record.status = FlightRecordStatus.ABORTED

        with self.assertRaises(ValidationError):
            record.save()

    def test_create_flight_record_duplicate_should_return_idempotent_duplicate(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        self._create_flight_record()
        existing_no = FlightRecord.objects.order_by("-id").first().flight_no

        response = self.client.post(
            "/api/v1/flight-records",
            {
                "flight_no": existing_no,
                "mission": self.mission.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "C0101")
        self.assertEqual(response.data["code"], "C0101")

    def test_create_flight_record_duplicate_in_other_tenant_should_be_allowed(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        flight_no = "YJ202603089999"

        other_user = User.objects.create_user(username="flight_record_other_viewer", password="pass1234", status=1)
        other_pilot_user = User.objects.create_user(username="flight_record_other_pilot", password="pass1234", status=1)
        other_pilot_staff = ensure_staff_profile(
            other_pilot_user,
            staff_no="FR-P-002",
            name="飞手B",
            employment_status=EmploymentStatus.ACTIVE,
        )
        other_tenant, _, _ = ensure_tenant_role_binding(
            other_user,
            tenant_code="flight_record_unique_other_tenant",
            role_code="flight_record_unique_other_role",
            role_name="飞行记录其他租户角色",
        )
        _other_pilot_tenant, other_pilot_member, _other_pilot_role = ensure_tenant_role_binding(
            other_pilot_user,
            tenant=other_tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(other_pilot_member, code="pilot_operator", name="飞手")
        other_route = Route.objects.create(tenant=other_tenant, name="其他租户航线")
        other_drone = Drone.objects.create(
            tenant=other_tenant,
            code="FR-OTHER-UNIQ-DRN-001",
            name="其他租户无人机",
            model="M300",
            device_sn="FR-OTHER-UNIQ-SN-001",
            status=DroneStatus.ENABLED,
        )
        other_mission = _persist_mission_fixture(
            tenant=other_tenant,
            name="其他租户任务",
            route=other_route,
            route_name=other_route.name,
            drone=other_drone,
            drone_name=other_drone.name,
            pilot=other_pilot_member,
            pilot_name=other_pilot_staff.name,
            status=MissionStatus.RUNNING,
        )
        FlightRecord.objects.create(
            tenant=other_tenant,
            flight_no=flight_no,
            mission=other_mission,
            mission_name=other_mission.name,
            route_name=other_route.name,
            airport_name="深圳宝安机场",
            drone=other_drone,
            drone_name=other_drone.name,
            pilot=other_pilot_member,
            pilot_name=other_pilot_staff.name,
            status=FlightRecordStatus.IN_PROGRESS,
        )

        response = self.client.post(
            "/api/v1/flight-records",
            {
                "flight_no": flight_no,
                "mission": self.mission.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["code"], "00000")
        self.assertEqual(response.data["data"]["flight_no"], flight_no)

    def test_create_flight_record_without_auth_should_return_permission_denied(self):
        response = self.client.post(
            "/api/v1/flight-records",
            {
                "flight_no": "YJ202603081111",
                "mission": self.mission.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )

        self.assertIn(response.status_code, (401, 403))
        self.assertIn(response.data["code"], {"A0401", "A0403"})
        self.assertEqual(response.data["code"], "A0401")

    def test_create_flight_record_without_permission_should_return_permission_denied(self):
        self.client.force_authenticate(self.viewer_user)

        response = self.client.post(
            "/api/v1/flight-records",
            {
                "flight_no": "YJ202603081222",
                "mission": self.mission.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn(response.data["code"], {"A0401", "A0403"})
        self.assertEqual(response.data["code"], "A0403")

    def test_list_flight_records_should_return_success(self):
        self._grant_permission("flight_record.view_flight_record")
        self.client.force_authenticate(self.viewer_user)
        self._create_flight_record()
        self._create_flight_record(status=FlightRecordStatus.ABORTED)

        response = self.client.get("/api/v1/flight-records")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "00000")
        self.assertEqual(response.data["msg"], "success")
        self.assertIn("list", response.data["data"])
        self.assertGreaterEqual(len(response.data["data"]["list"]), 2)

    def test_list_flight_records_with_status_filter_should_return_filtered_results(self):
        self._grant_permission("flight_record.view_flight_record")
        self.client.force_authenticate(self.viewer_user)
        self._create_flight_record(status=FlightRecordStatus.COMPLETED)
        self._create_flight_record(status=FlightRecordStatus.ABORTED)

        response = self.client.get("/api/v1/flight-records", {"status": FlightRecordStatus.ABORTED})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "00000")
        self.assertEqual(response.data["msg"], "success")
        self.assertEqual(len(response.data["data"]["list"]), 1)
        self.assertEqual(response.data["data"]["list"][0]["status"], FlightRecordStatus.ABORTED)

    def test_list_flight_records_without_auth_should_return_permission_denied(self):
        self._create_flight_record()

        response = self.client.get("/api/v1/flight-records")
        self.assertIn(response.status_code, (401, 403))
        self.assertIn(response.data["code"], {"A0401", "A0403"})
        self.assertEqual(response.data["code"], "A0401")

    def test_list_flight_records_without_permission_should_return_permission_denied(self):
        self._create_flight_record()
        self.client.force_authenticate(self.viewer_user)

        response = self.client.get("/api/v1/flight-records")
        self.assertEqual(response.status_code, 403)
        self.assertIn(response.data["code"], {"A0401", "A0403"})
        self.assertEqual(response.data["code"], "A0403")

    def test_retrieve_flight_record_should_return_success(self):
        self._grant_permission("flight_record.view_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()

        response = self.client.get(f"/api/v1/flight-records/{record.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "00000")
        self.assertEqual(response.data["msg"], "success")
        self.assertEqual(response.data["data"]["id"], record.id)
        self.assertEqual(response.data["data"]["flight_no"], record.flight_no)
        self.assertEqual(response.data["data"]["status"], FlightRecordStatus.COMPLETED)

    def test_retrieve_flight_record_not_found_should_return_resource_not_found(self):
        self._grant_permission("flight_record.view_flight_record")
        self.client.force_authenticate(self.viewer_user)

        response = self.client.get("/api/v1/flight-records/999999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "C0404")
        self.assertEqual(response.data["code"], "C0404")
    def test_retrieve_flight_record_without_auth_should_return_permission_denied(self):
        record = self._create_flight_record()

        response = self.client.get(f"/api/v1/flight-records/{record.id}")
        self.assertIn(response.status_code, (401, 403))
        self.assertIn(response.data["code"], {"A0401", "A0403"})
        self.assertEqual(response.data["code"], "A0401")

    def test_retrieve_flight_record_without_permission_should_return_permission_denied(self):
        record = self._create_flight_record()
        self.client.force_authenticate(self.viewer_user)

        response = self.client.get(f"/api/v1/flight-records/{record.id}")
        self.assertEqual(response.status_code, 403)
        self.assertIn(response.data["code"], {"A0401", "A0403"})
        self.assertEqual(response.data["code"], "A0403")

    def test_patch_flight_record_should_return_success(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record(status=FlightRecordStatus.IN_PROGRESS)
        new_end_time = record.end_time + timedelta(minutes=5)

        response = self.client.patch(
            f"/api/v1/flight-records/{record.id}",
            {
                "airport_name": "深圳宝安机场",
                "end_time": new_end_time.isoformat(),
                "photo_count": 16,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "00000")
        self.assertEqual(response.data["msg"], "success")
        self.assertEqual(response.data["data"]["airport_name"], "深圳宝安机场")
        self.assertEqual(response.data["data"]["photo_count"], 16)
        self.assertEqual(response.data["data"]["status"], FlightRecordStatus.IN_PROGRESS)
        record.refresh_from_db()
        self.assertEqual(record.airport_name, "深圳宝安机场")
        self.assertEqual(record.photo_count, 16)
        self.assertEqual(record.status, FlightRecordStatus.IN_PROGRESS)
        self.assertTrue(
            AuditLog.objects.filter(
                action="FLIGHT_RECORD_UPDATE",
                target_type="flight_record",
                target_id=str(record.id),
            ).exists()
        )

    def test_patch_flight_record_with_status_should_return_invalid_params(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record(status=FlightRecordStatus.IN_PROGRESS)

        response = self.client.patch(
            f"/api/v1/flight-records/{record.id}",
            {"status": FlightRecordStatus.COMPLETED},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["code"], "B0001")
        self.assertIn("status", response.data["data"])
        record.refresh_from_db()
        self.assertEqual(record.status, FlightRecordStatus.IN_PROGRESS)

    def test_patch_flight_record_empty_body_should_return_invalid_params(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()

        response = self.client.patch(
            f"/api/v1/flight-records/{record.id}",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["code"], "B0001")

    def test_patch_flight_record_invalid_params_should_return_invalid_params(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()

        response = self.client.patch(
            f"/api/v1/flight-records/{record.id}",
            {"end_time": "2026-03-08T07:00:00+08:00"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["code"], "B0001")
        self.assertIn("end_time", response.data["data"])

    def test_patch_flight_record_not_found_should_return_resource_not_found(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)

        response = self.client.patch(
            "/api/v1/flight-records/999999",
            {"airport_name": "不存在"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "C0404")
        self.assertEqual(response.data["code"], "C0404")

    def test_patch_flight_record_without_auth_should_return_permission_denied(self):
        record = self._create_flight_record()

        response = self.client.patch(
            f"/api/v1/flight-records/{record.id}",
            {"airport_name": "无权限修改"},
            format="json",
        )

        self.assertIn(response.status_code, (401, 403))
        self.assertIn(response.data["code"], {"A0401", "A0403"})
        self.assertEqual(response.data["code"], "A0401")

    def test_patch_flight_record_without_permission_should_return_permission_denied(self):
        record = self._create_flight_record()
        self.client.force_authenticate(self.viewer_user)

        response = self.client.patch(
            f"/api/v1/flight-records/{record.id}",
            {"airport_name": "无权限修改"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn(response.data["code"], {"A0401", "A0403"})
        self.assertEqual(response.data["code"], "A0403")

    def test_complete_flight_record_should_return_success(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record(status=FlightRecordStatus.IN_PROGRESS)

        response = self.client.post(f"/api/v1/flight-records/{record.id}/complete")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "00000")
        self.assertEqual(response.data["msg"], "success")
        self.assertEqual(response.data["data"]["status"], FlightRecordStatus.COMPLETED)

        record.refresh_from_db()
        self.assertEqual(record.status, FlightRecordStatus.COMPLETED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="FLIGHT_RECORD_COMPLETE",
                target_type="flight_record",
                target_id=str(record.id),
            ).exists()
        )

    def test_complete_completed_flight_record_should_be_idempotent_success(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record(status=FlightRecordStatus.COMPLETED)

        response = self.client.post(f"/api/v1/flight-records/{record.id}/complete")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "00000")
        self.assertEqual(response.data["msg"], "success")
        self.assertEqual(response.data["data"]["status"], FlightRecordStatus.COMPLETED)

    def test_complete_flight_record_with_body_should_return_invalid_params(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record(status=FlightRecordStatus.IN_PROGRESS)

        response = self.client.post(
            f"/api/v1/flight-records/{record.id}/complete",
            {"unexpected": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["code"], "B0001")
        record.refresh_from_db()
        self.assertEqual(record.status, FlightRecordStatus.IN_PROGRESS)

    def test_complete_flight_record_state_conflict_should_return_state_conflict(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record(status=FlightRecordStatus.ABORTED)

        response = self.client.post(f"/api/v1/flight-records/{record.id}/complete")

        self.assertEqual(response.status_code, 409)
        self.assertIn(response.data["code"], {"C0201", "C0202"})
        self.assertIn(response.data["code"], {"C0201", "C0202"})
        record.refresh_from_db()
        self.assertEqual(record.status, FlightRecordStatus.ABORTED)

    def test_complete_flight_record_not_found_should_return_resource_not_found(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)

        response = self.client.post("/api/v1/flight-records/999999/complete")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "C0404")
        self.assertEqual(response.data["code"], "C0404")

    def test_complete_flight_record_without_auth_should_return_permission_denied(self):
        record = self._create_flight_record(status=FlightRecordStatus.IN_PROGRESS)

        response = self.client.post(f"/api/v1/flight-records/{record.id}/complete")

        self.assertIn(response.status_code, (401, 403))
        self.assertIn(response.data["code"], {"A0401", "A0403"})
        self.assertEqual(response.data["code"], "A0401")

    def test_complete_flight_record_without_permission_should_return_permission_denied(self):
        record = self._create_flight_record(status=FlightRecordStatus.IN_PROGRESS)
        self.client.force_authenticate(self.viewer_user)

        response = self.client.post(f"/api/v1/flight-records/{record.id}/complete")

        self.assertEqual(response.status_code, 403)
        self.assertIn(response.data["code"], {"A0401", "A0403"})
        self.assertEqual(response.data["code"], "A0403")

    def test_abort_flight_record_should_return_success(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record(status=FlightRecordStatus.IN_PROGRESS)

        response = self.client.post(f"/api/v1/flight-records/{record.id}/abort")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "00000")
        self.assertEqual(response.data["msg"], "success")
        self.assertEqual(response.data["data"]["status"], FlightRecordStatus.ABORTED)

        record.refresh_from_db()
        self.assertEqual(record.status, FlightRecordStatus.ABORTED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="FLIGHT_RECORD_ABORT",
                target_type="flight_record",
                target_id=str(record.id),
            ).exists()
        )

    def test_abort_aborted_flight_record_should_be_idempotent_success(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record(status=FlightRecordStatus.ABORTED)

        response = self.client.post(f"/api/v1/flight-records/{record.id}/abort")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "00000")
        self.assertEqual(response.data["msg"], "success")
        self.assertEqual(response.data["data"]["status"], FlightRecordStatus.ABORTED)

    def test_abort_flight_record_with_body_should_return_invalid_params(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record(status=FlightRecordStatus.IN_PROGRESS)

        response = self.client.post(
            f"/api/v1/flight-records/{record.id}/abort",
            {"unexpected": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["code"], "B0001")
        record.refresh_from_db()
        self.assertEqual(record.status, FlightRecordStatus.IN_PROGRESS)

    def test_abort_flight_record_state_conflict_should_return_state_conflict(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record(status=FlightRecordStatus.COMPLETED)

        response = self.client.post(f"/api/v1/flight-records/{record.id}/abort")

        self.assertEqual(response.status_code, 409)
        self.assertIn(response.data["code"], {"C0201", "C0202"})
        self.assertIn(response.data["code"], {"C0201", "C0202"})
        record.refresh_from_db()
        self.assertEqual(record.status, FlightRecordStatus.COMPLETED)

    def test_abort_flight_record_not_found_should_return_resource_not_found(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)

        response = self.client.post("/api/v1/flight-records/999999/abort")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "C0404")
        self.assertEqual(response.data["code"], "C0404")

    def test_abort_flight_record_without_auth_should_return_permission_denied(self):
        record = self._create_flight_record(status=FlightRecordStatus.IN_PROGRESS)

        response = self.client.post(f"/api/v1/flight-records/{record.id}/abort")

        self.assertIn(response.status_code, (401, 403))
        self.assertIn(response.data["code"], {"A0401", "A0403"})
        self.assertEqual(response.data["code"], "A0401")

    def test_abort_flight_record_without_permission_should_return_permission_denied(self):
        record = self._create_flight_record(status=FlightRecordStatus.IN_PROGRESS)
        self.client.force_authenticate(self.viewer_user)

        response = self.client.post(f"/api/v1/flight-records/{record.id}/abort")

        self.assertEqual(response.status_code, 403)
        self.assertIn(response.data["code"], {"A0401", "A0403"})
        self.assertEqual(response.data["code"], "A0403")


class FlightRecordPilotScopeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self._flight_no_seq = 1

        self.pilot_user = User.objects.create_user(username="flight_record_scope_pilot", password="pass1234", status=1)
        self.pilot_staff = ensure_staff_profile(
            self.pilot_user,
            staff_no="FRS-P-001",
            name="飞行记录范围飞手A",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.tenant, self.pilot_member, self.pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant_code="flight_record_scope_tenant",
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")
        grant_role_permissions(
            self.pilot_role,
            {
                "flight_record.view_flight_record": ScopeType.ASSIGNED,
                "flight_record.manage_flight_record": ScopeType.ASSIGNED,
            },
            group_name="flight-record-scope-pilot-group",
        )

        self.other_pilot_user = User.objects.create_user(username="flight_record_scope_other", password="pass1234", status=1)
        self.other_pilot_staff = ensure_staff_profile(
            self.other_pilot_user,
            staff_no="FRS-P-002",
            name="飞行记录范围飞手B",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, self.other_pilot_member, _other_role = ensure_tenant_role_binding(
            self.other_pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.other_pilot_member, code="pilot_operator", name="飞手")

        self.route = Route.objects.create(tenant=self.tenant, name="飞行记录范围航线")
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="FRS-DRN-001",
            name="飞行记录范围无人机",
            model="M300",
            device_sn="FRS-SN-001",
            status=DroneStatus.ENABLED,
        )
        self.my_mission = _persist_mission_fixture(
            tenant=self.tenant,
            name="我的飞行任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name=self.pilot_staff.name,
            status=MissionStatus.RUNNING,
        )
        self.other_mission = _persist_mission_fixture(
            tenant=self.tenant,
            name="别人的飞行任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.other_pilot_member,
            pilot_name=self.other_pilot_staff.name,
            status=MissionStatus.RUNNING,
        )
        self.my_record = self._create_record("FRS202603080001", self.my_mission, self.pilot_member, self.pilot_staff.name)
        self.other_record = self._create_record(
            "FRS202603080002",
            self.other_mission,
            self.other_pilot_member,
            self.other_pilot_staff.name,
        )

        self.client.force_authenticate(self.pilot_user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    def _create_record(self, flight_no: str, mission: Mission, pilot_member, pilot_name: str) -> FlightRecord:
        start_time = timezone.now() - timedelta(minutes=10)
        end_time = timezone.now()
        return FlightRecord.objects.create(
            tenant=self.tenant,
            flight_no=flight_no,
            mission=mission,
            mission_name=mission.name,
            route_name=self.route.name,
            airport_name="珠海金湾机场",
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=pilot_member,
            pilot_name=pilot_name,
            start_time=start_time,
            end_time=end_time,
            flight_duration=600,
            status=FlightRecordStatus.COMPLETED,
        )

    def test_pilot_should_only_list_assigned_flight_records(self):
        response = self.client.get("/api/v1/flight-records")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["total"], 1)
        self.assertEqual(response.data["data"]["list"][0]["id"], self.my_record.id)

    def test_pilot_create_flight_record_for_other_mission_should_return_invalid_params(self):
        response = self.client.post(
            "/api/v1/flight-records",
            {
                "flight_no": "FRS202603080003",
                "mission": self.other_mission.id,
                "drone": self.drone.id,
                "airport_name": "珠海金湾机场",
                "start_time": "2026-03-08T08:00:00+08:00",
                "end_time": "2026-03-08T08:10:00+08:00",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertIn("pilot", response.data["data"])
