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
from apps.dji_bff.models import SyncStatus, TenantMediaIndex
from apps.drone.models import Drone, DroneStatus
from apps.flight_record.models import FlightRecord, FlightRecordStatus
from apps.media_file.models import MediaFile, MediaType
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route

User = get_user_model()


def _persist_mission_fixture(**kwargs) -> Mission:
    mission = Mission(**kwargs)
    Mission.objects.bulk_create([mission])
    return Mission.objects.get(pk=mission.pk)


class FlightRecordApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self._flight_no_seq = 1
        self._mission_seq = 1

        self.viewer_user = User.objects.create_user(username="flight_record_viewer", password="pass1234", status=1)
        ensure_staff_profile(
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
            status=DroneStatus.CLAIMED,
        )

    def _grant_permission(self, permission_code: str, *, scope: ScopeType = ScopeType.ALL):
        grant_role_permissions(
            self.role,
            {permission_code: scope},
            group_name=f"{permission_code}-{scope}-group",
        )

    def _next_flight_no(self) -> str:
        flight_no = f"FR20260415{self._flight_no_seq:04d}"
        self._flight_no_seq += 1
        return flight_no

    def _create_mission(
        self,
        *,
        tenant=None,
        route=None,
        drone=None,
        pilot=None,
        pilot_name=None,
        name: str | None = None,
        status: int = MissionStatus.PENDING,
        started_at=None,
        finished_at=None,
    ) -> Mission:
        tenant = tenant or self.tenant
        route = route or self.route
        drone = drone or self.drone
        pilot = pilot or self.pilot_member
        pilot_name = pilot_name or self.pilot_staff.name
        name = name or f"飞行任务{self._mission_seq}"
        self._mission_seq += 1
        return _persist_mission_fixture(
            tenant=tenant,
            name=name,
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=pilot,
            pilot_name=pilot_name,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
        )

    def _create_flight_record(
        self,
        *,
        mission: Mission | None = None,
        flight_no: str | None = None,
        status: int = FlightRecordStatus.COMPLETED,
        is_deleted: bool = False,
        deleted_at=None,
    ) -> FlightRecord:
        mission = mission or self._create_mission()
        start_time = timezone.now() - timedelta(minutes=20)
        end_time = timezone.now()
        return FlightRecord.objects.create(
            tenant=self.tenant,
            flight_no=flight_no or self._next_flight_no(),
            mission=mission,
            mission_name=mission.name,
            route_name=mission.route_name,
            airport_name="珠海金湾机场",
            drone=mission.drone,
            device_sn=mission.device_sn,
            drone_name=mission.drone_name,
            pilot=mission.pilot,
            pilot_name=mission.pilot_name,
            start_time=start_time,
            end_time=end_time,
            flight_duration=int((end_time - start_time).total_seconds()),
            photo_count=0,
            video_count=3,
            status=status,
            is_deleted=is_deleted,
            deleted_at=deleted_at,
        )

    def _create_media_file(
        self,
        *,
        flight_record: FlightRecord,
        mission: Mission,
        file_name: str,
        captured_at=None,
        is_deleted: bool = False,
        with_dji_index: bool = True,
    ) -> MediaFile:
        media_file = MediaFile.objects.create(
            tenant=self.tenant,
            flight_record=flight_record,
            mission=mission,
            device_sn=mission.device_sn,
            media_type=MediaType.VIDEO,
            file_name=file_name,
            file_url=f"https://example.com/{file_name}",
            thumbnail_url=f"https://example.com/thumb/{file_name}",
            file_size=2048,
            captured_at=captured_at or timezone.now(),
            is_deleted=is_deleted,
            deleted_at=timezone.now() if is_deleted else None,
        )
        if with_dji_index:
            TenantMediaIndex.objects.create(
                tenant=self.tenant,
                media_file=media_file,
                dji_file_id=f"dji-{file_name}",
                device_sn=mission.device_sn,
                mission=mission,
                sync_status=SyncStatus.SYNCED,
                last_sync_at=timezone.now(),
            )
        return media_file

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
            status=DroneStatus.CLAIMED,
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
            device_sn=other_drone.device_sn,
            drone_name=other_drone.name,
            pilot=other_pilot_member,
            pilot_name=self.pilot_staff.name,
            status=MissionStatus.PENDING,
        )

        with self.assertRaises(ValidationError):
            FlightRecord.objects.create(
                tenant=self.tenant,
                flight_no=self._next_flight_no(),
                mission=other_mission,
                mission_name=other_mission.name,
                route_name=other_route.name,
                airport_name="跨租户机场",
                drone=self.drone,
                device_sn=self.drone.device_sn,
                drone_name=self.drone.name,
                pilot=self.pilot_member,
                pilot_name=self.pilot_staff.name,
                start_time=timezone.now() - timedelta(minutes=5),
                end_time=timezone.now(),
                flight_duration=300,
                status=FlightRecordStatus.COMPLETED,
            )

    def test_model_should_reject_end_time_before_start_time(self):
        mission = self._create_mission()
        with self.assertRaises(ValidationError):
            FlightRecord.objects.create(
                tenant=self.tenant,
                flight_no=self._next_flight_no(),
                mission=mission,
                mission_name=mission.name,
                route_name=mission.route_name,
                airport_name="珠海金湾机场",
                drone=mission.drone,
                device_sn=mission.device_sn,
                drone_name=mission.drone_name,
                pilot=mission.pilot,
                pilot_name=mission.pilot_name,
                start_time=timezone.now(),
                end_time=timezone.now() - timedelta(minutes=1),
                status=FlightRecordStatus.COMPLETED,
            )

    def test_model_should_reject_inactive_pilot(self):
        inactive_user = User.objects.create_user(username="flight_record_inactive_pilot", password="pass1234", status=1)
        ensure_staff_profile(
            inactive_user,
            staff_no="FR-P-999",
            name="离职飞手",
            employment_status=EmploymentStatus.INACTIVE,
        )
        _tenant, inactive_member, _role = ensure_tenant_role_binding(
            inactive_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(inactive_member, code="pilot_operator", name="飞手")
        mission = self._create_mission()

        with self.assertRaises(ValidationError):
            FlightRecord.objects.create(
                tenant=self.tenant,
                flight_no=self._next_flight_no(),
                mission=mission,
                mission_name=mission.name,
                route_name=mission.route_name,
                airport_name="珠海金湾机场",
                drone=mission.drone,
                device_sn=mission.device_sn,
                drone_name=mission.drone_name,
                pilot=inactive_member,
                pilot_name="离职飞手",
                start_time=timezone.now() - timedelta(minutes=5),
                end_time=timezone.now(),
                status=FlightRecordStatus.COMPLETED,
            )

    def test_model_should_reject_duplicate_mission_snapshot(self):
        mission = self._create_mission()
        self._create_flight_record(mission=mission)

        with self.assertRaises(ValidationError):
            self._create_flight_record(mission=mission)

    def test_model_should_reject_soft_delete_restore(self):
        record = self._create_flight_record(is_deleted=True, deleted_at=timezone.now())
        record.is_deleted = False
        record.deleted_at = None

        with self.assertRaises(ValidationError):
            record.save()

    def test_create_flight_record_should_return_method_not_allowed(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)

        response = self.client.post(
            "/api/v1/flight-records",
            {"flight_no": "FR202604150999"},
            format="json",
        )

        self.assertEqual(response.status_code, 405, response.data)

    def test_complete_action_should_return_method_not_allowed(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()

        response = self.client.post(f"/api/v1/flight-records/{record.id}/complete")

        self.assertEqual(response.status_code, 405, response.data)

    def test_abort_action_should_return_method_not_allowed(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()

        response = self.client.post(f"/api/v1/flight-records/{record.id}/abort")

        self.assertEqual(response.status_code, 405, response.data)

    def test_list_flight_records_should_return_only_not_deleted_records(self):
        self._grant_permission("flight_record.view_flight_record")
        self.client.force_authenticate(self.viewer_user)
        visible = self._create_flight_record(status=FlightRecordStatus.COMPLETED)
        self._create_flight_record(
            status=FlightRecordStatus.ABORTED,
            is_deleted=True,
            deleted_at=timezone.now(),
        )

        response = self.client.get("/api/v1/flight-records")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "00000")
        self.assertEqual(response.data["data"]["total"], 1)
        self.assertEqual(response.data["data"]["list"][0]["id"], visible.id)

    def test_list_flight_records_with_status_filter_should_return_filtered_results(self):
        self._grant_permission("flight_record.view_flight_record")
        self.client.force_authenticate(self.viewer_user)
        self._create_flight_record(status=FlightRecordStatus.COMPLETED)
        aborted = self._create_flight_record(status=FlightRecordStatus.ABORTED)

        response = self.client.get("/api/v1/flight-records", {"status": FlightRecordStatus.ABORTED})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["total"], 1)
        self.assertEqual(response.data["data"]["list"][0]["id"], aborted.id)

    def test_list_flight_records_without_auth_should_return_permission_denied(self):
        self._create_flight_record()

        response = self.client.get("/api/v1/flight-records")

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["code"], "A0401")

    def test_list_flight_records_without_permission_should_return_permission_denied(self):
        self._create_flight_record()
        self.client.force_authenticate(self.viewer_user)

        response = self.client.get("/api/v1/flight-records")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "A0403")

    def test_retrieve_flight_record_should_return_success(self):
        self._grant_permission("flight_record.view_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()

        response = self.client.get(f"/api/v1/flight-records/{record.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["id"], record.id)
        self.assertEqual(response.data["data"]["device_sn"], self.drone.device_sn)

    def test_retrieve_flight_record_should_include_only_current_downloadable_media_files(self):
        self._grant_permission("flight_record.view_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()
        visible = self._create_media_file(
            flight_record=record,
            mission=record.mission,
            file_name="VISIBLE.MP4",
            captured_at=timezone.now() - timedelta(minutes=1),
        )
        self._create_media_file(
            flight_record=record,
            mission=record.mission,
            file_name="DELETED.MP4",
            is_deleted=True,
        )
        self._create_media_file(
            flight_record=record,
            mission=record.mission,
            file_name="NOINDEX.MP4",
            with_dji_index=False,
        )
        other_record = self._create_flight_record(flight_no=self._next_flight_no())
        self._create_media_file(
            flight_record=other_record,
            mission=other_record.mission,
            file_name="OTHER.MP4",
        )

        response = self.client.get(f"/api/v1/flight-records/{record.id}")

        self.assertEqual(response.status_code, 200)
        payload = response.data["data"]
        self.assertIn("media_files", payload)
        self.assertEqual([item["id"] for item in payload["media_files"]], [visible.id])
        self.assertEqual(payload["media_files"][0]["media_type"], MediaType.VIDEO)
        self.assertEqual(payload["media_files"][0]["file_name"], "VISIBLE.MP4")
        self.assertEqual(payload["media_files"][0]["download_url"], f"/api/v1/media-files/{visible.id}/download")
        self.assertEqual(payload["media_files"][0]["playback_url"], f"/api/v1/media-files/{visible.id}/playback-url")

    def test_retrieve_flight_record_should_expose_blank_playback_url_for_photo_media(self):
        self._grant_permission("flight_record.view_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()
        photo = self._create_media_file(
            flight_record=record,
            mission=record.mission,
            file_name="VISIBLE.JPG",
        )
        photo.media_type = MediaType.PHOTO
        photo.save(update_fields=["media_type"])

        response = self.client.get(f"/api/v1/flight-records/{record.id}")

        self.assertEqual(response.status_code, 200)
        payload = response.data["data"]
        self.assertEqual([item["id"] for item in payload["media_files"]], [photo.id])
        self.assertEqual(payload["media_files"][0]["download_url"], f"/api/v1/media-files/{photo.id}/download")
        self.assertEqual(payload["media_files"][0]["playback_url"], "")

    def test_list_flight_records_should_not_include_media_files_field(self):
        self._grant_permission("flight_record.view_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()
        self._create_media_file(
            flight_record=record,
            mission=record.mission,
            file_name="LIST-HIDDEN.MP4",
        )

        response = self.client.get("/api/v1/flight-records")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("media_files", response.data["data"]["list"][0])

    def test_retrieve_deleted_flight_record_should_return_resource_not_found(self):
        self._grant_permission("flight_record.view_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record(is_deleted=True, deleted_at=timezone.now())

        response = self.client.get(f"/api/v1/flight-records/{record.id}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "C0404")

    def test_put_flight_record_should_only_update_summary_fields(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record(status=FlightRecordStatus.COMPLETED)
        original_video_count = record.video_count

        response = self.client.put(
            f"/api/v1/flight-records/{record.id}",
            {
                "mission_name": "修正后的任务名",
                "airport_name": "深圳宝安机场",
                "photo_count": 0,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["mission_name"], "修正后的任务名")
        self.assertEqual(response.data["data"]["airport_name"], "深圳宝安机场")
        self.assertEqual(response.data["data"]["video_count"], original_video_count)
        record.refresh_from_db()
        self.assertEqual(record.mission_name, "修正后的任务名")
        self.assertEqual(record.airport_name, "深圳宝安机场")
        self.assertEqual(record.video_count, original_video_count)
        self.assertEqual(record.device_sn, self.drone.device_sn)
        self.assertTrue(
            AuditLog.objects.filter(
                action="FLIGHT_RECORD_UPDATE",
                target_type="flight_record",
                target_id=str(record.id),
            ).exists()
        )

    def test_put_flight_record_with_video_count_should_return_invalid_params(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()

        response = self.client.put(
            f"/api/v1/flight-records/{record.id}",
            {"video_count": 5},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["data"], {"video_count": ["该字段在此接口不可写"]})

    def test_put_flight_record_with_anchor_field_should_return_invalid_params(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()

        response = self.client.put(
            f"/api/v1/flight-records/{record.id}",
            {"end_time": "2026-03-08T07:00:00+08:00"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["data"], {"end_time": ["该字段在此接口不可写"]})

    def test_put_flight_record_empty_body_should_return_invalid_params(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()

        response = self.client.put(
            f"/api/v1/flight-records/{record.id}",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")

    def test_put_flight_record_not_found_should_return_resource_not_found(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)

        response = self.client.put(
            "/api/v1/flight-records/999999",
            {"airport_name": "深圳宝安机场"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "C0404")

    def test_put_flight_record_without_auth_should_return_permission_denied(self):
        record = self._create_flight_record()

        response = self.client.put(
            f"/api/v1/flight-records/{record.id}",
            {"airport_name": "深圳宝安机场"},
            format="json",
        )

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["code"], "A0401")

    def test_put_flight_record_without_permission_should_return_permission_denied(self):
        record = self._create_flight_record()
        self.client.force_authenticate(self.viewer_user)

        response = self.client.put(
            f"/api/v1/flight-records/{record.id}",
            {"airport_name": "深圳宝安机场"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "A0403")

    def test_delete_should_soft_delete_flight_record_and_hide_it_from_api(self):
        self._grant_permission("flight_record.manage_flight_record")
        self._grant_permission("flight_record.view_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()

        response = self.client.delete(f"/api/v1/flight-records/{record.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"], {"id": record.id, "deleted": True})
        record.refresh_from_db()
        self.assertTrue(record.is_deleted)
        self.assertIsNotNone(record.deleted_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action="FLIGHT_RECORD_DELETE",
                target_type="flight_record",
                target_id=str(record.id),
            ).exists()
        )
        list_response = self.client.get("/api/v1/flight-records")
        self.assertEqual(list_response.data["data"]["total"], 0)
        detail_response = self.client.get(f"/api/v1/flight-records/{record.id}")
        self.assertEqual(detail_response.status_code, 404)

    def test_delete_should_reject_request_body(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()

        response = self.client.delete(
            f"/api/v1/flight-records/{record.id}",
            {"reason": "manual"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")

    def test_patch_should_return_method_not_allowed(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()

        response = self.client.patch(
            f"/api/v1/flight-records/{record.id}",
            {"airport_name": "深圳宝安机场"},
            format="json",
        )

        self.assertEqual(response.status_code, 405, response.data)


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
            status=DroneStatus.CLAIMED,
        )
        self.my_mission = _persist_mission_fixture(
            tenant=self.tenant,
            name="我的飞行任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name=self.pilot_staff.name,
            status=MissionStatus.PENDING,
        )
        self.other_mission = _persist_mission_fixture(
            tenant=self.tenant,
            name="别人的飞行任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.other_pilot_member,
            pilot_name=self.other_pilot_staff.name,
            status=MissionStatus.PENDING,
        )
        self.my_record = self._create_record("FRS202604150001", self.my_mission, self.pilot_member, self.pilot_staff.name)
        self.other_record = self._create_record(
            "FRS202604150002",
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
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=pilot_member,
            pilot_name=pilot_name,
            start_time=start_time,
            end_time=end_time,
            flight_duration=600,
            photo_count=0,
            video_count=1,
            status=FlightRecordStatus.COMPLETED,
        )

    def test_pilot_should_only_list_assigned_flight_records(self):
        response = self.client.get("/api/v1/flight-records")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["total"], 1)
        self.assertEqual(response.data["data"]["list"][0]["id"], self.my_record.id)

    def test_pilot_should_not_retrieve_other_flight_record(self):
        response = self.client.get(f"/api/v1/flight-records/{self.other_record.id}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "C0404")

    def test_pilot_should_not_update_other_flight_record(self):
        response = self.client.put(
            f"/api/v1/flight-records/{self.other_record.id}",
            {"airport_name": "深圳宝安机场"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "C0404")

    def test_pilot_create_should_return_method_not_allowed(self):
        response = self.client.post(
            "/api/v1/flight-records",
            {"flight_no": "FRS202604150003"},
            format="json",
        )

        self.assertEqual(response.status_code, 405, response.data)
