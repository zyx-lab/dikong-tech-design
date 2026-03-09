from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import AuditLog, EmploymentStatus, GroupPermissionScope, ScopeStatus, ScopeType, StaffProfile, StaffType, StaffTypeGroup
from apps.drone.models import Drone, DroneStatus
from apps.flight_record.models import FlightRecord, FlightRecordStatus
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route, RouteStatus

User = get_user_model()


class FlightRecordApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self._flight_no_seq = 1

        self.viewer_staff_type = StaffType.objects.create(code="flight_record_viewer_test", name="飞行记录查看员", status=1)
        self.pilot_staff_type = StaffType.objects.create(code="pilot_operator", name="飞手", status=1)

        self.viewer_user = User.objects.create_user(username="flight_record_viewer", password="pass1234", status=1)
        self.viewer_staff = StaffProfile.objects.create(
            user=self.viewer_user,
            staff_no="FR-001",
            name="记录查看员A",
            employment_status=EmploymentStatus.ACTIVE,
            staff_type=self.viewer_staff_type,
        )

        self.pilot_user = User.objects.create_user(username="flight_record_pilot", password="pass1234", status=1)
        self.pilot_staff = StaffProfile.objects.create(
            user=self.pilot_user,
            staff_no="FR-P-001",
            name="飞手A",
            employment_status=EmploymentStatus.ACTIVE,
            staff_type=self.pilot_staff_type,
        )

        self.route = Route.objects.create(name="飞行记录测试航线", status=RouteStatus.ACTIVE)
        self.drone = Drone.objects.create(
            code="FR-DRN-001",
            name="飞行记录测试机",
            model="M300",
            serial_no="SN-FR-001",
            status=DroneStatus.ENABLED,
        )
        self.mission = Mission.objects.create(
            name="飞行记录测试任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_staff,
            pilot_name=self.pilot_staff.name,
            status=MissionStatus.RUNNING,
        )

    def _grant_permission(self, permission_code: str):
        app_label, codename = permission_code.split(".", 1)
        perm = Permission.objects.get(content_type__app_label=app_label, codename=codename)
        group = Group.objects.create(name=f"{permission_code}-group")
        group.permissions.add(perm)
        StaffTypeGroup.objects.create(staff_type=self.viewer_staff_type, group=group, status=ScopeStatus.ACTIVE)
        GroupPermissionScope.objects.create(
            group=group,
            permission=perm,
            scope_type=ScopeType.ALL,
            status=ScopeStatus.ACTIVE,
        )

    def _create_flight_record(self, *, status: int = FlightRecordStatus.COMPLETED, mission: Mission | None = None) -> FlightRecord:
        current_mission = mission or self.mission
        flight_no = f"YJ20260308{self._flight_no_seq:04d}"
        self._flight_no_seq += 1
        start_time = timezone.now() - timedelta(minutes=20)
        end_time = timezone.now()
        return FlightRecord.objects.create(
            flight_no=flight_no,
            mission=current_mission,
            mission_name=current_mission.name,
            route_name=self.route.name,
            airport_name="珠海金湾机场",
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_staff,
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
                "pilot": self.pilot_staff.id,
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
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["flight_no"], "YJ202603080999")
        self.assertEqual(response.data["mission"], self.mission.id)
        self.assertEqual(response.data["drone"], self.drone.id)
        self.assertEqual(response.data["pilot"], self.pilot_staff.id)
        self.assertEqual(response.data["mission_name"], self.mission.name)
        self.assertEqual(response.data["drone_name"], self.drone.name)
        self.assertEqual(response.data["pilot_name"], self.pilot_staff.name)
        self.assertEqual(response.data["status"], FlightRecordStatus.COMPLETED)

    def test_create_flight_record_invalid_params_should_return_invalid_params(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)

        response = self.client.post(
            "/api/v1/flight-records",
            {
                "mission": self.mission.id,
                "drone": self.drone.id,
                "pilot": self.pilot_staff.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("flight_no", response.data)

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
                "pilot": self.pilot_staff.id,
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(response.data["business_detail_code"], "DUPLICATE_REQUEST")

    def test_create_flight_record_without_auth_should_return_permission_denied(self):
        response = self.client.post(
            "/api/v1/flight-records",
            {
                "flight_no": "YJ202603081111",
                "mission": self.mission.id,
                "drone": self.drone.id,
                "pilot": self.pilot_staff.id,
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_create_flight_record_without_permission_should_return_permission_denied(self):
        self.client.force_authenticate(self.viewer_user)

        response = self.client.post(
            "/api/v1/flight-records",
            {
                "flight_no": "YJ202603081222",
                "mission": self.mission.id,
                "drone": self.drone.id,
                "pilot": self.pilot_staff.id,
                "status": FlightRecordStatus.IN_PROGRESS,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_list_flight_records_should_return_success(self):
        self._grant_permission("flight_record.view_flight_record")
        self.client.force_authenticate(self.viewer_user)
        self._create_flight_record()
        self._create_flight_record(status=FlightRecordStatus.ABORTED)

        response = self.client.get("/api/v1/flight-records")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertIn("results", response.data)
        self.assertGreaterEqual(len(response.data["results"]), 2)

    def test_list_flight_records_with_status_filter_should_return_filtered_results(self):
        self._grant_permission("flight_record.view_flight_record")
        self.client.force_authenticate(self.viewer_user)
        self._create_flight_record(status=FlightRecordStatus.COMPLETED)
        self._create_flight_record(status=FlightRecordStatus.ABORTED)

        response = self.client.get("/api/v1/flight-records", {"status": FlightRecordStatus.ABORTED})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["status"], FlightRecordStatus.ABORTED)

    def test_list_flight_records_without_auth_should_return_permission_denied(self):
        self._create_flight_record()

        response = self.client.get("/api/v1/flight-records")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_list_flight_records_without_permission_should_return_permission_denied(self):
        self._create_flight_record()
        self.client.force_authenticate(self.viewer_user)

        response = self.client.get("/api/v1/flight-records")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_retrieve_flight_record_should_return_success(self):
        self._grant_permission("flight_record.view_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()

        response = self.client.get(f"/api/v1/flight-records/{record.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["id"], record.id)
        self.assertEqual(response.data["flight_no"], record.flight_no)
        self.assertEqual(response.data["status"], FlightRecordStatus.COMPLETED)

    def test_retrieve_flight_record_not_found_should_return_resource_not_found(self):
        self._grant_permission("flight_record.view_flight_record")
        self.client.force_authenticate(self.viewer_user)

        response = self.client.get("/api/v1/flight-records/999999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_retrieve_flight_record_without_auth_should_return_permission_denied(self):
        record = self._create_flight_record()

        response = self.client.get(f"/api/v1/flight-records/{record.id}")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_retrieve_flight_record_without_permission_should_return_permission_denied(self):
        record = self._create_flight_record()
        self.client.force_authenticate(self.viewer_user)

        response = self.client.get(f"/api/v1/flight-records/{record.id}")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

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
                "status": FlightRecordStatus.COMPLETED,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["airport_name"], "深圳宝安机场")
        self.assertEqual(response.data["photo_count"], 16)
        self.assertEqual(response.data["status"], FlightRecordStatus.COMPLETED)
        record.refresh_from_db()
        self.assertEqual(record.airport_name, "深圳宝安机场")
        self.assertEqual(record.photo_count, 16)
        self.assertEqual(record.status, FlightRecordStatus.COMPLETED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="FLIGHT_RECORD_UPDATE",
                target_type="flight_record",
                target_id=str(record.id),
            ).exists()
        )

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
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")

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
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("end_time", response.data)

    def test_patch_flight_record_not_found_should_return_resource_not_found(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)

        response = self.client.patch(
            "/api/v1/flight-records/999999",
            {"airport_name": "不存在"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_patch_flight_record_without_auth_should_return_permission_denied(self):
        record = self._create_flight_record()

        response = self.client.patch(
            f"/api/v1/flight-records/{record.id}",
            {"airport_name": "无权限修改"},
            format="json",
        )

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_patch_flight_record_without_permission_should_return_permission_denied(self):
        record = self._create_flight_record()
        self.client.force_authenticate(self.viewer_user)

        response = self.client.patch(
            f"/api/v1/flight-records/{record.id}",
            {"airport_name": "无权限修改"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})
