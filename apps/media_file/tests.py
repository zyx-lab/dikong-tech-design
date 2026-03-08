from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import EmploymentStatus, GroupPermissionScope, ScopeStatus, ScopeType, StaffProfile, StaffType, StaffTypeGroup
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

        self.viewer_staff_type = StaffType.objects.create(code="media_file_viewer_test", name="媒体查看员", status=1)
        self.pilot_staff_type = StaffType.objects.create(code="pilot_operator", name="飞手", status=1)

        self.viewer_user = User.objects.create_user(username="media_file_viewer", password="pass1234", status=1)
        self.viewer_staff = StaffProfile.objects.create(
            user=self.viewer_user,
            staff_no="MF-001",
            name="媒体查看员A",
            employment_status=EmploymentStatus.ACTIVE,
            staff_type=self.viewer_staff_type,
        )

        self.pilot_user = User.objects.create_user(username="media_file_pilot", password="pass1234", status=1)
        self.pilot_staff = StaffProfile.objects.create(
            user=self.pilot_user,
            staff_no="MF-P-001",
            name="媒体飞手A",
            employment_status=EmploymentStatus.ACTIVE,
            staff_type=self.pilot_staff_type,
        )

        self.route = Route.objects.create(name="媒体测试航线", status=RouteStatus.ACTIVE)
        self.drone = Drone.objects.create(
            code="MF-DRN-001",
            name="媒体测试机",
            model="M300",
            serial_no="SN-MF-001",
            status=DroneStatus.ENABLED,
        )
        self.mission = Mission.objects.create(
            name="媒体测试任务",
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

    def _create_flight_record(self) -> FlightRecord:
        flight_no = f"MF20260308{self._flight_no_seq:04d}"
        self._flight_no_seq += 1
        start_time = timezone.now() - timedelta(minutes=20)
        end_time = timezone.now()
        return FlightRecord.objects.create(
            flight_no=flight_no,
            mission=self.mission,
            mission_name=self.mission.name,
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
            flight_record=flight_record,
            media_type=media_type,
            file_name=file_name,
            file_url=f"https://example.com/{file_name}",
            thumbnail_url=f"https://example.com/thumb/{file_name}",
            file_size=1024,
            captured_at=timezone.now() - timedelta(minutes=3),
            is_deleted=is_deleted,
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
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_list_media_files_without_permission_should_return_permission_denied(self):
        flight_record = self._create_flight_record()
        self._create_media_file(flight_record=flight_record)
        self.client.force_authenticate(self.viewer_user)

        response = self.client.get("/api/v1/media-files")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

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
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_retrieve_media_file_without_permission_should_return_permission_denied(self):
        flight_record = self._create_flight_record()
        media_file = self._create_media_file(flight_record=flight_record)
        self.client.force_authenticate(self.viewer_user)

        response = self.client.get(f"/api/v1/media-files/{media_file.id}")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

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
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_delete_media_file_without_permission_should_return_permission_denied(self):
        flight_record = self._create_flight_record()
        media_file = self._create_media_file(flight_record=flight_record, file_name="IMG_DELETE_FORBIDDEN.JPG")
        self.client.force_authenticate(self.viewer_user)

        response = self.client.delete(f"/api/v1/media-files/{media_file.id}")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})
