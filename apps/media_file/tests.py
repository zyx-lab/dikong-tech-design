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
from apps.media_file.models import MediaFile, MediaType
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route, RouteStatus

User = get_user_model()


class MediaFileApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self._flight_no_seq = 1

        self.viewer_user = User.objects.create_user(username="media_file_viewer", password="pass1234", status=1)
        self.viewer_staff = ensure_staff_profile(
            self.viewer_user,
            staff_no="MF-001",
            name="媒体查看员A",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.viewer_user,
            tenant_code="media_file_test_tenant",
            role_code="media_file_test_role",
            role_name="媒体文件测试角色",
        )
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

        self.pilot_user = User.objects.create_user(username="media_file_pilot", password="pass1234", status=1)
        self.pilot_staff = ensure_staff_profile(
            self.pilot_user,
            staff_no="MF-P-001",
            name="媒体飞手A",
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

        self.route = Route.objects.create(tenant=self.tenant, name="媒体测试航线", status=RouteStatus.ACTIVE)
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="MF-DRN-001",
            name="媒体测试机",
            model="M300",
            serial_no="SN-MF-001",
            status=DroneStatus.ENABLED,
        )
        self.mission = Mission.objects.create(
            tenant=self.tenant,
            name="媒体测试任务",
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

    def _create_flight_record(self) -> FlightRecord:
        flight_no = f"MF20260308{self._flight_no_seq:04d}"
        self._flight_no_seq += 1
        start_time = timezone.now() - timedelta(minutes=20)
        end_time = timezone.now()
        return FlightRecord.objects.create(
            tenant=self.tenant,
            flight_no=flight_no,
            mission=self.mission,
            mission_name=self.mission.name,
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
            status=FlightRecordStatus.COMPLETED,
        )

    def _create_media_file(
        self,
        *,
        flight_record: FlightRecord,
        media_type: int = MediaType.PHOTO,
        file_name: str = "IMG_0001.JPG",
        is_deleted: bool = False,
    ) -> MediaFile:
        return MediaFile.objects.create(
            tenant=self.tenant,
            flight_record=flight_record,
            media_type=media_type,
            file_name=file_name,
            file_url=f"https://example.com/{file_name}",
            thumbnail_url=f"https://example.com/thumb/{file_name}",
            file_size=1024,
            captured_at=timezone.now() - timedelta(minutes=3),
            is_deleted=is_deleted,
            deleted_at=timezone.now() if is_deleted else None,
        )

    def test_list_media_files_should_return_success(self):
        self._grant_permission("media_file.view_media_file")
        self.client.force_authenticate(self.viewer_user)
        flight_record = self._create_flight_record()
        self._create_media_file(flight_record=flight_record, media_type=MediaType.PHOTO, file_name="IMG_A.JPG")
        self._create_media_file(flight_record=flight_record, media_type=MediaType.VIDEO, file_name="VID_A.MP4")

        response = self.client.get("/api/v1/media-files")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertIn("results", response.data)
        self.assertEqual(len(response.data["results"]), 2)

    def test_create_media_file_should_return_success(self):
        self._grant_permission("media_file.manage_media_file")
        self.client.force_authenticate(self.viewer_user)
        flight_record = self._create_flight_record()

        response = self.client.post(
            "/api/v1/media-files",
            {
                "flight_record": flight_record.id,
                "media_type": MediaType.PHOTO,
                "file_name": "IMG_CREATE_OK.JPG",
                "file_url": "https://example.com/IMG_CREATE_OK.JPG",
                "thumbnail_url": "https://example.com/thumb/IMG_CREATE_OK.JPG",
                "file_size": 4096,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["file_name"], "IMG_CREATE_OK.JPG")
        self.assertFalse(response.data["is_deleted"])

        media_file = MediaFile.objects.get(id=response.data["id"])
        self.assertEqual(media_file.flight_record_id, flight_record.id)
        self.assertFalse(media_file.is_deleted)
        self.assertIsNone(media_file.deleted_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action="MEDIA_FILE_CREATE",
                target_type="media_file",
                target_id=str(media_file.id),
            ).exists()
        )

    def test_create_media_file_with_cross_tenant_flight_record_should_return_invalid_params(self):
        self._grant_permission("media_file.manage_media_file")
        self.client.force_authenticate(self.viewer_user)
        other_tenant, _, _ = ensure_tenant_role_binding(
            self.viewer_user,
            tenant_code="media_file_other_tenant",
            role_code="media_file_other_role",
            role_name="媒体文件其他租户角色",
        )
        other_record = FlightRecord.objects.create(
            tenant=other_tenant,
            flight_no="MF202603089999",
            airport_name="其他租户机场",
            status=FlightRecordStatus.COMPLETED,
        )

        response = self.client.post(
            "/api/v1/media-files",
            {
                "flight_record": other_record.id,
                "media_type": MediaType.PHOTO,
                "file_name": "IMG_OTHER_TENANT.JPG",
                "file_url": "https://example.com/IMG_OTHER_TENANT.JPG",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertIn("flight_record", response.data)

    def test_model_should_reject_cross_tenant_flight_record(self):
        other_tenant, _, _ = ensure_tenant_role_binding(
            self.viewer_user,
            tenant_code="media_file_model_other_tenant",
            role_code="media_file_model_other_role",
            role_name="媒体文件模型其他租户角色",
        )
        _other_pilot_tenant, other_pilot_member, _other_pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=other_tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(other_pilot_member, code="pilot_operator", name="飞手")
        other_route = Route.objects.create(tenant=other_tenant, name="其他租户航线", status=RouteStatus.ACTIVE)
        other_drone = Drone.objects.create(
            tenant=other_tenant,
            code="MF-OTHER-MODEL-DRONE",
            name="其他租户无人机",
            model="M300",
            serial_no="MF-OTHER-MODEL-SN",
            status=DroneStatus.ENABLED,
        )
        other_mission = Mission.objects.create(
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
        other_record = FlightRecord.objects.create(
            tenant=other_tenant,
            flight_no="MF202603089998",
            mission=other_mission,
            mission_name=other_mission.name,
            route_name=other_route.name,
            airport_name="其他租户机场",
            drone=other_drone,
            drone_name=other_drone.name,
            pilot=other_pilot_member,
            pilot_name=self.pilot_staff.name,
            start_time=timezone.now() - timedelta(minutes=8),
            end_time=timezone.now(),
            flight_duration=480,
            status=FlightRecordStatus.COMPLETED,
        )

        with self.assertRaises(ValidationError):
            MediaFile.objects.create(
                tenant=self.tenant,
                flight_record=other_record,
                media_type=MediaType.PHOTO,
                file_name="CROSS_TENANT.JPG",
                file_url="https://example.com/CROSS_TENANT.JPG",
            )

    def test_model_should_reject_deleted_without_deleted_at(self):
        flight_record = self._create_flight_record()

        with self.assertRaises(ValidationError):
            MediaFile.objects.create(
                tenant=self.tenant,
                flight_record=flight_record,
                media_type=MediaType.PHOTO,
                file_name="MISSING_DELETED_AT.JPG",
                file_url="https://example.com/MISSING_DELETED_AT.JPG",
                is_deleted=True,
            )

    def test_create_media_file_invalid_params_should_return_invalid_params(self):
        self._grant_permission("media_file.manage_media_file")
        self.client.force_authenticate(self.viewer_user)
        flight_record = self._create_flight_record()

        response = self.client.post(
            "/api/v1/media-files",
            {
                "flight_record": flight_record.id,
                "media_type": MediaType.PHOTO,
                "file_url": "https://example.com/IMG_MISSING_NAME.JPG",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("file_name", response.data)

    def test_create_media_file_without_auth_should_return_permission_denied(self):
        flight_record = self._create_flight_record()

        response = self.client.post(
            "/api/v1/media-files",
            {
                "flight_record": flight_record.id,
                "media_type": MediaType.PHOTO,
                "file_name": "IMG_CREATE_NOAUTH.JPG",
                "file_url": "https://example.com/IMG_CREATE_NOAUTH.JPG",
            },
            format="json",
        )
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_create_media_file_without_permission_should_return_permission_denied(self):
        flight_record = self._create_flight_record()
        self.client.force_authenticate(self.viewer_user)

        response = self.client.post(
            "/api/v1/media-files",
            {
                "flight_record": flight_record.id,
                "media_type": MediaType.PHOTO,
                "file_name": "IMG_CREATE_FORBIDDEN.JPG",
                "file_url": "https://example.com/IMG_CREATE_FORBIDDEN.JPG",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "FORBIDDEN")

    def test_list_media_files_with_filter_should_return_filtered_results(self):
        self._grant_permission("media_file.view_media_file")
        self.client.force_authenticate(self.viewer_user)
        flight_record = self._create_flight_record()
        self._create_media_file(flight_record=flight_record, media_type=MediaType.PHOTO, file_name="IMG_FILTER_A.JPG")
        self._create_media_file(flight_record=flight_record, media_type=MediaType.VIDEO, file_name="VID_FILTER_B.MP4")

        response = self.client.get("/api/v1/media-files", {"media_type": MediaType.VIDEO})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["media_type"], MediaType.VIDEO)

    def test_list_media_files_should_exclude_deleted_records(self):
        self._grant_permission("media_file.view_media_file")
        self.client.force_authenticate(self.viewer_user)
        flight_record = self._create_flight_record()
        self._create_media_file(flight_record=flight_record, file_name="IMG_OK.JPG", is_deleted=False)
        self._create_media_file(flight_record=flight_record, file_name="IMG_DELETED.JPG", is_deleted=True)

        response = self.client.get("/api/v1/media-files")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["file_name"], "IMG_OK.JPG")

    def test_list_media_files_without_auth_should_return_permission_denied(self):
        flight_record = self._create_flight_record()
        self._create_media_file(flight_record=flight_record)

        response = self.client.get("/api/v1/media-files")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_list_media_files_without_permission_should_return_permission_denied(self):
        flight_record = self._create_flight_record()
        self._create_media_file(flight_record=flight_record)
        self.client.force_authenticate(self.viewer_user)

        response = self.client.get("/api/v1/media-files")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "FORBIDDEN")

    def test_retrieve_media_file_should_return_success(self):
        self._grant_permission("media_file.view_media_file")
        self.client.force_authenticate(self.viewer_user)
        flight_record = self._create_flight_record()
        media_file = self._create_media_file(flight_record=flight_record, file_name="IMG_DETAIL_OK.JPG")

        response = self.client.get(f"/api/v1/media-files/{media_file.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["id"], media_file.id)
        self.assertEqual(response.data["file_name"], "IMG_DETAIL_OK.JPG")

    def test_retrieve_media_file_not_found_should_return_resource_not_found(self):
        self._grant_permission("media_file.view_media_file")
        self.client.force_authenticate(self.viewer_user)

        response = self.client.get("/api/v1/media-files/999999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")
    def test_retrieve_media_file_deleted_should_return_resource_not_found(self):
        self._grant_permission("media_file.view_media_file")
        self.client.force_authenticate(self.viewer_user)
        flight_record = self._create_flight_record()
        media_file = self._create_media_file(
            flight_record=flight_record,
            file_name="IMG_DELETED_DETAIL.JPG",
            is_deleted=True,
        )

        response = self.client.get(f"/api/v1/media-files/{media_file.id}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_retrieve_media_file_without_auth_should_return_permission_denied(self):
        flight_record = self._create_flight_record()
        media_file = self._create_media_file(flight_record=flight_record)

        response = self.client.get(f"/api/v1/media-files/{media_file.id}")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_retrieve_media_file_without_permission_should_return_permission_denied(self):
        flight_record = self._create_flight_record()
        media_file = self._create_media_file(flight_record=flight_record)
        self.client.force_authenticate(self.viewer_user)

        response = self.client.get(f"/api/v1/media-files/{media_file.id}")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "FORBIDDEN")

    def test_delete_media_file_should_return_success_and_soft_delete(self):
        self._grant_permission("media_file.manage_media_file")
        self.client.force_authenticate(self.viewer_user)
        flight_record = self._create_flight_record()
        media_file = self._create_media_file(flight_record=flight_record, file_name="IMG_DELETE_OK.JPG")

        response = self.client.delete(f"/api/v1/media-files/{media_file.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["id"], media_file.id)
        self.assertTrue(response.data["is_deleted"])

        media_file.refresh_from_db()
        self.assertTrue(media_file.is_deleted)
        self.assertIsNotNone(media_file.deleted_at)

    def test_delete_media_file_not_found_should_return_resource_not_found(self):
        self._grant_permission("media_file.manage_media_file")
        self.client.force_authenticate(self.viewer_user)

        response = self.client.delete("/api/v1/media-files/999999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_delete_media_file_deleted_should_return_resource_not_found(self):
        self._grant_permission("media_file.manage_media_file")
        self.client.force_authenticate(self.viewer_user)
        flight_record = self._create_flight_record()
        media_file = self._create_media_file(
            flight_record=flight_record,
            file_name="IMG_DELETE_ALREADY.JPG",
            is_deleted=True,
        )

        response = self.client.delete(f"/api/v1/media-files/{media_file.id}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_delete_media_file_without_auth_should_return_permission_denied(self):
        flight_record = self._create_flight_record()
        media_file = self._create_media_file(flight_record=flight_record, file_name="IMG_DELETE_NOAUTH.JPG")

        response = self.client.delete(f"/api/v1/media-files/{media_file.id}")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_delete_media_file_without_permission_should_return_permission_denied(self):
        flight_record = self._create_flight_record()
        media_file = self._create_media_file(flight_record=flight_record, file_name="IMG_DELETE_FORBIDDEN.JPG")
        self.client.force_authenticate(self.viewer_user)

        response = self.client.delete(f"/api/v1/media-files/{media_file.id}")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "FORBIDDEN")

    def test_patch_media_file_should_return_success(self):
        self._grant_permission("media_file.manage_media_file")
        self.client.force_authenticate(self.viewer_user)
        flight_record = self._create_flight_record()
        next_flight_record = self._create_flight_record()
        media_file = self._create_media_file(flight_record=flight_record, file_name="IMG_PATCH_OLD.JPG")

        response = self.client.patch(
            f"/api/v1/media-files/{media_file.id}",
            {
                "flight_record": next_flight_record.id,
                "media_type": MediaType.VIDEO,
                "file_name": "IMG_PATCH_NEW.MP4",
                "file_url": "https://example.com/IMG_PATCH_NEW.MP4",
                "thumbnail_url": "https://example.com/thumb/IMG_PATCH_NEW.MP4",
                "file_size": 8192,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["flight_record"], next_flight_record.id)
        self.assertEqual(response.data["media_type"], MediaType.VIDEO)
        self.assertEqual(response.data["file_name"], "IMG_PATCH_NEW.MP4")
        self.assertEqual(response.data["file_size"], 8192)
        media_file.refresh_from_db()
        self.assertEqual(media_file.flight_record_id, next_flight_record.id)
        self.assertEqual(media_file.media_type, MediaType.VIDEO)
        self.assertEqual(media_file.file_name, "IMG_PATCH_NEW.MP4")
        self.assertEqual(media_file.file_url, "https://example.com/IMG_PATCH_NEW.MP4")
        self.assertEqual(media_file.thumbnail_url, "https://example.com/thumb/IMG_PATCH_NEW.MP4")
        self.assertEqual(media_file.file_size, 8192)
        self.assertTrue(
            AuditLog.objects.filter(
                action="MEDIA_FILE_UPDATE",
                target_type="media_file",
                target_id=str(media_file.id),
            ).exists()
        )

    def test_patch_media_file_empty_body_should_return_invalid_params(self):
        self._grant_permission("media_file.manage_media_file")
        self.client.force_authenticate(self.viewer_user)
        flight_record = self._create_flight_record()
        media_file = self._create_media_file(flight_record=flight_record, file_name="IMG_PATCH_EMPTY.JPG")

        response = self.client.patch(
            f"/api/v1/media-files/{media_file.id}",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        media_file.refresh_from_db()
        self.assertEqual(media_file.file_name, "IMG_PATCH_EMPTY.JPG")

    def test_patch_media_file_not_found_should_return_resource_not_found(self):
        self._grant_permission("media_file.manage_media_file")
        self.client.force_authenticate(self.viewer_user)

        response = self.client.patch(
            "/api/v1/media-files/999999",
            {"file_name": "IMG_PATCH_NOT_FOUND.JPG"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_patch_media_file_without_auth_should_return_permission_denied(self):
        flight_record = self._create_flight_record()
        media_file = self._create_media_file(flight_record=flight_record, file_name="IMG_PATCH_NOAUTH.JPG")

        response = self.client.patch(
            f"/api/v1/media-files/{media_file.id}",
            {"file_name": "IMG_PATCH_DENIED.JPG"},
            format="json",
        )

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_patch_media_file_without_permission_should_return_permission_denied(self):
        flight_record = self._create_flight_record()
        media_file = self._create_media_file(flight_record=flight_record, file_name="IMG_PATCH_FORBIDDEN.JPG")
        self.client.force_authenticate(self.viewer_user)

        response = self.client.patch(
            f"/api/v1/media-files/{media_file.id}",
            {"file_name": "IMG_PATCH_DENIED.JPG"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "FORBIDDEN")


class MediaFilePilotScopeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self._flight_no_seq = 1

        self.pilot_user = User.objects.create_user(username="media_scope_pilot", password="pass1234", status=1)
        self.pilot_staff = ensure_staff_profile(
            self.pilot_user,
            staff_no="MFS-P-001",
            name="媒体范围飞手A",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.tenant, self.pilot_member, self.pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant_code="media_scope_tenant",
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")
        grant_role_permissions(
            self.pilot_role,
            {
                "media_file.view_media_file": ScopeType.ASSIGNED,
                "media_file.manage_media_file": ScopeType.ASSIGNED,
            },
            group_name="media-scope-pilot-group",
        )

        self.other_pilot_user = User.objects.create_user(username="media_scope_other", password="pass1234", status=1)
        self.other_pilot_staff = ensure_staff_profile(
            self.other_pilot_user,
            staff_no="MFS-P-002",
            name="媒体范围飞手B",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, self.other_pilot_member, _other_role = ensure_tenant_role_binding(
            self.other_pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.other_pilot_member, code="pilot_operator", name="飞手")

        self.route = Route.objects.create(tenant=self.tenant, name="媒体范围航线", status=RouteStatus.ACTIVE)
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="MFS-DRN-001",
            name="媒体范围无人机",
            model="M300",
            serial_no="MFS-SN-001",
            status=DroneStatus.ENABLED,
        )
        self.my_mission = Mission.objects.create(
            tenant=self.tenant,
            name="我的媒体任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name=self.pilot_staff.name,
            status=MissionStatus.RUNNING,
        )
        self.other_mission = Mission.objects.create(
            tenant=self.tenant,
            name="别人的媒体任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.other_pilot_member,
            pilot_name=self.other_pilot_staff.name,
            status=MissionStatus.RUNNING,
        )
        self.my_record = self._create_record("MFS202603080001", self.my_mission, self.pilot_member, self.pilot_staff.name)
        self.other_record = self._create_record(
            "MFS202603080002",
            self.other_mission,
            self.other_pilot_member,
            self.other_pilot_staff.name,
        )
        self.my_media = self._create_media_file(self.my_record, "MY_SCOPE.JPG")
        self.other_media = self._create_media_file(self.other_record, "OTHER_SCOPE.JPG")

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

    def _create_media_file(self, flight_record: FlightRecord, file_name: str) -> MediaFile:
        return MediaFile.objects.create(
            tenant=self.tenant,
            flight_record=flight_record,
            media_type=MediaType.PHOTO,
            file_name=file_name,
            file_url=f"https://example.com/{file_name}",
            thumbnail_url=f"https://example.com/thumb/{file_name}",
            file_size=1024,
            captured_at=timezone.now() - timedelta(minutes=3),
            is_deleted=False,
            deleted_at=None,
        )

    def test_pilot_should_only_list_assigned_media_files(self):
        response = self.client.get("/api/v1/media-files")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], self.my_media.id)

    def test_pilot_create_media_for_other_flight_record_should_return_invalid_params(self):
        response = self.client.post(
            "/api/v1/media-files",
            {
                "flight_record": self.other_record.id,
                "media_type": MediaType.PHOTO,
                "file_name": "CROSS_SCOPE.JPG",
                "file_url": "https://example.com/CROSS_SCOPE.JPG",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertIn("flight_record", response.data)
