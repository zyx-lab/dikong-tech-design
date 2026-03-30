from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.dji_bff.models import SyncStatus, TenantMediaIndex, TenantRouteIndex
from apps.dji_mock.state import mock_dji_state
from apps.dji_mock.test_support import MockDjiUpstreamTestMixin
from apps.drone.models import Drone
from apps.flight_record.models import FlightRecord, FlightRecordStatus
from apps.media_file.models import MediaFile, MediaType
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route

User = get_user_model()


class MediaFileApiTests(MockDjiUpstreamTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.user = User.objects.create_user(username="media_viewer", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="媒体查看员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="media_test_tenant",
            role_code="media_test_role",
            role_name="媒体测试角色",
        )
        grant_role_permissions(self.role, {"media_file.view_media_file": ScopeType.ALL})
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

        self.pilot_user = User.objects.create_user(username="media_pilot", password="pass1234", status=1)
        ensure_staff_profile(self.pilot_user, name="飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, self.pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")

        self.route = Route.objects.create(tenant=self.tenant, name="媒体航线", creator_name="管理员")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=self.route,
            dji_wayline_id="media-wayline",
            sync_status=SyncStatus.SYNCED,
        )
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-DRONE-001",
            name="媒体无人机",
            model="M30",
            device_sn="MEDIA-SN-001",
        )
        self.mission = Mission.objects.create(
            tenant=self.tenant,
            name="媒体任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.RUNNING,
            dji_job_id="media-job-001",
        )
        self.flight_record = FlightRecord.objects.create(
            tenant=self.tenant,
            flight_no="MEDIA-FR-001",
            mission=self.mission,
            mission_name=self.mission.name,
            route_name=self.route.name,
            airport_name="机场",
            drone=self.drone,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            start_time=timezone.now() - timedelta(minutes=10),
            end_time=timezone.now(),
            flight_duration=600,
            status=FlightRecordStatus.COMPLETED,
        )

    def _create_media(self, *, file_name: str, device_sn: str) -> MediaFile:
        media_file = MediaFile.objects.create(
            tenant=self.tenant,
            flight_record=self.flight_record,
            media_type=MediaType.PHOTO,
            file_name=file_name,
            file_url=f"https://example.com/{file_name}",
            thumbnail_url=f"https://example.com/thumb/{file_name}",
            file_size=1024,
            captured_at=timezone.now(),
        )
        TenantMediaIndex.objects.create(
            tenant=self.tenant,
            media_file=media_file,
            dji_file_id=f"dji-{file_name}",
            device_sn=device_sn,
            mission=self.mission,
            sync_status=SyncStatus.SYNCED,
            last_sync_at=timezone.now(),
        )
        mock_dji_state.seed_media_file(
            file_id=f"dji-{file_name}",
            name=file_name,
            device_sn=device_sn,
            job_id=self.mission.dji_job_id,
        )
        return media_file

    def test_list_should_filter_by_device_sn(self):
        self._create_media(file_name="IMG_A.JPG", device_sn="MEDIA-SN-001")
        self._create_media(file_name="IMG_B.JPG", device_sn="MEDIA-SN-002")

        response = self.client.get("/api/v1/media-files", {"device_sn": "MEDIA-SN-001"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["total"], 1)
        self.assertEqual(response.data["data"]["list"][0]["device_sn"], "MEDIA-SN-001")

    def test_download_should_redirect_to_dji_url(self):
        media_file = self._create_media(file_name="IMG_DL.JPG", device_sn="MEDIA-SN-001")

        response = self.client.get(f"/api/v1/media-files/{media_file.id}/download")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/__mock-dji__/_downloads/media/dji-IMG_DL.JPG")

    def test_media_api_should_be_read_only(self):
        media_file = self._create_media(file_name="IMG_READONLY.JPG", device_sn="MEDIA-SN-001")

        create_response = self.client.post("/api/v1/media-files", {}, format="json")
        update_response = self.client.patch(f"/api/v1/media-files/{media_file.id}", {"file_name": "NEW.JPG"}, format="json")
        delete_response = self.client.delete(f"/api/v1/media-files/{media_file.id}")

        self.assertIn(create_response.status_code, (403, 405))
        self.assertIn(update_response.status_code, (403, 405))
        self.assertIn(delete_response.status_code, (403, 405))
