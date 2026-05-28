from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
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
from apps.drone.models import Drone
from apps.flight_record.models import FlightRecord
from apps.media_file.models import MediaFile
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route

User = get_user_model()


def DjiCloudPlatform():
    return apps.get_model("dji_bff", "DjiCloudPlatform")


class FakeV2FlightRecordMediaGateway:
    media_by_platform_id = {}
    calls = []

    def __init__(self, *, platform=None, **kwargs):
        self.platform = platform
        self.calls.append(platform.id if platform is not None else None)

    def _workspace_id(self):
        return f"workspace-{self.platform.id}"

    def list_media_files(self):
        return list(self.media_by_platform_id.get(self.platform.id, []))


class V2FlightRecordApiTests(TestCase):
    def setUp(self):
        super().setUp()
        FakeV2FlightRecordMediaGateway.media_by_platform_id = {}
        FakeV2FlightRecordMediaGateway.calls = []
        self.client = APIClient()
        self.user = User.objects.create_user(username="v2_flight_record_admin", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="V2 飞行记录管理员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="v2_flight_record_tenant",
            role_code="v2_flight_record_role",
            role_name="V2 飞行记录角色",
        )
        grant_role_permissions(self.role, {"flight_record.view_flight_record": ScopeType.ALL})
        self.pilot_user = User.objects.create_user(username="v2_flight_record_pilot", password="pass1234", status=1)
        ensure_staff_profile(self.pilot_user, name="飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, self.pilot_member, _role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")
        self.platform = DjiCloudPlatform().objects.create(
            tenant=self.tenant,
            name="飞行记录平台",
            base_url="https://flight-record.example.test",
            username="admin",
            password="secret",
            workspace_id="workspace-v2-record",
        )
        self.route = Route.objects.create(tenant=self.tenant, dji_platform=self.platform, name="飞行记录航线")
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            dji_platform=self.platform,
            code="FR-DRONE",
            name="飞行记录无人机",
            model="M30",
            device_sn="FR-SN-001",
        )
        self.started_at = timezone.now() - timedelta(minutes=10)
        self.captured_at = self.started_at + timedelta(minutes=2)
        self.finished_at = timezone.now() - timedelta(minutes=1)
        self.mission = Mission.objects.create(
            tenant=self.tenant,
            dji_platform=self.platform,
            name="飞行记录任务",
            route=self.route,
            drone=self.drone,
            pilot=self.pilot_member,
            status=MissionStatus.COMPLETED,
            started_at=self.started_at,
            finished_at=self.finished_at,
        )
        self.flight_record = FlightRecord.create_from_completed_mission(mission=self.mission)
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    @patch("apps.api_v2.views.DjiGateway", FakeV2FlightRecordMediaGateway)
    def test_retrieve_should_refresh_platform_media_and_return_v2_media_urls(self):
        FakeV2FlightRecordMediaGateway.media_by_platform_id = {
            self.platform.id: [
                {
                    "file_id": "record-photo-001",
                    "file_name": "RECORD_PHOTO.JPG",
                    "device_sn": self.drone.device_sn,
                    "media_type": 1,
                    "captured_at": self.captured_at.isoformat(),
                },
                {
                    "file_id": "record-video-001",
                    "file_name": "RECORD_VIDEO.MP4",
                    "device_sn": self.drone.device_sn,
                    "media_type": 2,
                    "captured_at": self.captured_at.isoformat(),
                },
            ]
        }

        response = self.client.get(f"/api/v2/flight-records/{self.flight_record.id}")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(FakeV2FlightRecordMediaGateway.calls, [self.platform.id])
        self.assertEqual(MediaFile.objects.filter(flight_record=self.flight_record, dji_platform=self.platform).count(), 2)
        self.flight_record.refresh_from_db()
        self.assertEqual(self.flight_record.photo_count, 1)
        self.assertEqual(self.flight_record.video_count, 1)

        media_by_name = {item["file_name"]: item for item in response.data["data"]["media_files"]}
        self.assertEqual(set(media_by_name), {"RECORD_PHOTO.JPG", "RECORD_VIDEO.MP4"})
        self.assertEqual(
            media_by_name["RECORD_PHOTO.JPG"]["preview_url"],
            f"/api/v2/media-files/{MediaFile.objects.get(file_name='RECORD_PHOTO.JPG').id}/preview-url",
        )
        self.assertEqual(
            media_by_name["RECORD_VIDEO.MP4"]["playback_url"],
            f"/api/v2/media-files/{MediaFile.objects.get(file_name='RECORD_VIDEO.MP4').id}/playback-url",
        )
