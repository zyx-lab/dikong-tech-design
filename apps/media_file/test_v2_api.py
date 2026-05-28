from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from unittest.mock import patch
from datetime import timedelta

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.dji_bff.models import SyncStatus, TenantMediaIndex
from apps.drone.models import Drone
from apps.flight_record.models import FlightRecord
from apps.media_file.models import MediaFile, MediaType
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route

User = get_user_model()


def DjiCloudPlatform():
    return apps.get_model("dji_bff", "DjiCloudPlatform")


class FakeV2MediaGateway:
    media_by_platform_id = {}
    calls = []

    def __init__(self, *, platform=None, **kwargs):
        self.platform = platform
        self.calls.append(platform.id if platform is not None else None)

    def list_media_files(self):
        return list(self.media_by_platform_id.get(self.platform.id, []))


class V2MediaFileApiTests(TestCase):
    def setUp(self):
        super().setUp()
        FakeV2MediaGateway.media_by_platform_id = {}
        FakeV2MediaGateway.calls = []
        self.client = APIClient()
        self.user = User.objects.create_user(username="v2_media_admin", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="V2 媒体管理员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="v2_media_tenant",
            role_code="v2_media_role",
            role_name="V2 媒体角色",
        )
        grant_role_permissions(
            self.role,
            {
                "media_file.view_media_file": ScopeType.ALL,
                "media_file.manage_media_file": ScopeType.ALL,
            },
        )
        self.pilot_user = User.objects.create_user(username="v2_media_pilot", password="pass1234", status=1)
        ensure_staff_profile(self.pilot_user, name="飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, self.pilot_member, _role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")
        self.platform_a = DjiCloudPlatform().objects.create(
            tenant=self.tenant,
            name="平台 A",
            base_url="https://a.example.test",
            username="admin-a",
            password="secret",
            workspace_id="workspace-a",
        )
        self.platform_b = DjiCloudPlatform().objects.create(
            tenant=self.tenant,
            name="平台 B",
            base_url="https://b.example.test",
            username="admin-b",
            password="secret",
            workspace_id="workspace-b",
        )
        self.drone_a = Drone.objects.create(
            tenant=self.tenant,
            dji_platform=self.platform_a,
            code="DRONE-A",
            name="平台 A 无人机",
            model="M30",
            device_sn="SN-A",
        )
        Drone.objects.create(
            tenant=self.tenant,
            dji_platform=self.platform_b,
            code="DRONE-B",
            name="平台 B 无人机",
            model="M30",
            device_sn="SN-B",
        )
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    @patch("apps.api_v2.views.DjiGateway", FakeV2MediaGateway)
    def test_list_should_refresh_media_from_requested_platform_only(self):
        FakeV2MediaGateway.media_by_platform_id = {
            self.platform_a.id: [
                {
                    "file_id": "file-a-001",
                    "file_name": "A001.JPG",
                    "device_sn": "SN-A",
                    "media_type": 1,
                    "create_time": "2026-05-27T09:00:00Z",
                }
            ],
            self.platform_b.id: [
                {
                    "file_id": "file-b-001",
                    "file_name": "B001.JPG",
                    "device_sn": "SN-B",
                    "media_type": 1,
                }
            ],
        }

        response = self.client.get(f"/api/v2/media-files?platform_id={self.platform_a.id}")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(FakeV2MediaGateway.calls, [self.platform_a.id])
        self.assertEqual(response.data["data"]["total"], 1)
        self.assertEqual(response.data["data"]["list"][0]["dji_file_id"], "file-a-001")
        media_file = MediaFile.objects.get(tenant=self.tenant, dji_platform=self.platform_a)
        media_index = TenantMediaIndex.objects.get(media_file=media_file)
        self.assertEqual(media_file.device_sn, "SN-A")
        self.assertEqual(media_index.dji_platform_id, self.platform_a.id)
        self.assertFalse(MediaFile.objects.filter(dji_platform=self.platform_b).exists())

    def test_bind_mission_should_attach_completed_mission_flight_record_and_recount(self):
        route = Route.objects.create(tenant=self.tenant, dji_platform=self.platform_a, name="平台 A 航线")
        started_at = timezone.now() - timedelta(minutes=10)
        captured_at = started_at + timedelta(minutes=1)
        mission = Mission.objects.create(
            tenant=self.tenant,
            dji_platform=self.platform_a,
            name="手动绑定任务",
            route=route,
            drone=self.drone_a,
            pilot=self.pilot_member,
            status=MissionStatus.COMPLETED,
            started_at=started_at,
            finished_at=timezone.now(),
        )
        flight_record = FlightRecord.create_from_completed_mission(mission=mission)
        media_file = MediaFile.objects.create(
            tenant=self.tenant,
            dji_platform=self.platform_a,
            device_sn=self.drone_a.device_sn,
            media_type=MediaType.PHOTO,
            file_name="MANUAL_BIND.JPG",
            file_url="dji://manual-bind-photo",
            captured_at=captured_at,
        )
        TenantMediaIndex.objects.create(
            tenant=self.tenant,
            dji_platform=self.platform_a,
            media_file=media_file,
            workspace_id=self.platform_a.workspace_id,
            dji_file_id="manual-bind-photo",
            device_sn=self.drone_a.device_sn,
            sync_status=SyncStatus.SYNCED,
            last_sync_at=captured_at,
        )

        response = self.client.post(
            "/api/v2/media-files/bind-mission",
            {"mission_id": mission.id, "media_file_ids": [media_file.id]},
            format="json",
        )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        media_file.refresh_from_db()
        self.assertEqual(media_file.mission_id, mission.id)
        self.assertEqual(media_file.flight_record_id, flight_record.id)
        flight_record.refresh_from_db()
        self.assertEqual(flight_record.photo_count, 1)
        self.assertEqual(flight_record.video_count, 0)
