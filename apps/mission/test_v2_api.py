from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from datetime import timedelta
from unittest.mock import patch

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.dji_bff.models import TenantRouteIndex
from apps.drone.models import Drone
from apps.flight_record.models import FlightRecord
from apps.media_file.models import MediaFile
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route

User = get_user_model()


def DjiCloudPlatform():
    return apps.get_model("dji_bff", "DjiCloudPlatform")


class FakeV2MissionMediaGateway:
    media_by_platform_id = {}
    calls = []

    def __init__(self, *, platform=None, **kwargs):
        self.platform = platform
        self.calls.append(platform.id if platform is not None else None)

    def _workspace_id(self):
        return f"workspace-{self.platform.id}"

    def list_media_files(self):
        return list(self.media_by_platform_id.get(self.platform.id, []))


class V2MissionApiTests(TestCase):
    def setUp(self):
        super().setUp()
        FakeV2MissionMediaGateway.media_by_platform_id = {}
        FakeV2MissionMediaGateway.calls = []
        self.client = APIClient()
        self.user = User.objects.create_user(username="v2_mission_admin", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="V2 任务调度员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="v2_mission_tenant",
            role_code="v2_mission_role",
            role_name="V2 任务角色",
        )
        grant_role_permissions(
            self.role,
            {
                "mission.view_mission": ScopeType.ALL,
                "mission.manage_mission": ScopeType.ALL,
            },
        )
        self.pilot_user = User.objects.create_user(username="v2_mission_pilot", password="pass1234", status=1)
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
        )
        self.platform_b = DjiCloudPlatform().objects.create(
            tenant=self.tenant,
            name="平台 B",
            base_url="https://b.example.test",
            username="admin-b",
            password="secret",
        )
        self.route_a = Route.objects.create(tenant=self.tenant, name="平台 A 航线")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=self.route_a,
            dji_platform=self.platform_a,
            workspace_id="workspace-a",
            dji_wayline_id="wayline-a",
            download_url="https://a.example.test/wayline-a.kmz",
            is_published=True,
        )
        self.drone_a = Drone.objects.create(
            tenant=self.tenant,
            dji_platform=self.platform_a,
            code="DRONE-A",
            name="平台 A 无人机",
            model="M30",
            device_sn="SN-A",
        )
        self.drone_b = Drone.objects.create(
            tenant=self.tenant,
            dji_platform=self.platform_b,
            code="DRONE-B",
            name="平台 B 无人机",
            model="M30",
            device_sn="SN-B",
        )
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    def test_create_should_reject_route_not_dispatched_to_drone_platform(self):
        response = self.client.post(
            "/api/v2/missions",
            {
                "name": "未下发到无人机平台任务",
                "route": self.route_a.id,
                "drone": self.drone_b.id,
                "pilot": self.pilot_member.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, getattr(response, "data", response.content))
        self.assertIn("route", response.data["data"])

    def test_create_should_reject_route_without_any_platform_dispatch(self):
        route = Route.objects.create(tenant=self.tenant, name="未下发航线")

        response = self.client.post(
            "/api/v2/missions",
            {
                "name": "未下发任务",
                "route": route.id,
                "drone": self.drone_a.id,
                "pilot": self.pilot_member.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, getattr(response, "data", response.content))
        self.assertIn("route", response.data["data"])

    def test_create_should_bind_mission_to_route_and_drone_platform(self):
        response = self.client.post(
            "/api/v2/missions",
            {
                "name": "平台一致任务",
                "route": self.route_a.id,
                "drone": self.drone_a.id,
                "pilot": self.pilot_member.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["dji_platform"], self.platform_a.id)

    @patch("apps.api_v2.views.DjiGateway", FakeV2MissionMediaGateway)
    def test_complete_should_refresh_platform_media_and_bind_to_created_flight_record(self):
        started_at = timezone.now() - timedelta(minutes=10)
        captured_at = started_at + timedelta(minutes=2)
        mission = Mission.objects.create(
            tenant=self.tenant,
            dji_platform=self.platform_a,
            name="完成后刷新媒体任务",
            route=self.route_a,
            drone=self.drone_a,
            pilot=self.pilot_member,
            status=MissionStatus.RUNNING,
            started_at=started_at,
        )
        FakeV2MissionMediaGateway.media_by_platform_id = {
            self.platform_a.id: [
                {
                    "file_id": "mission-photo-001",
                    "file_name": "MISSION_PHOTO.JPG",
                    "device_sn": self.drone_a.device_sn,
                    "media_type": 1,
                    "captured_at": captured_at.isoformat(),
                },
                {
                    "file_id": "mission-video-001",
                    "file_name": "MISSION_VIDEO.MP4",
                    "device_sn": self.drone_a.device_sn,
                    "media_type": 2,
                    "captured_at": captured_at.isoformat(),
                },
            ]
        }

        response = self.client.post(f"/api/v2/missions/{mission.id}/advance")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(FakeV2MissionMediaGateway.calls, [self.platform_a.id])
        flight_record = FlightRecord.objects.get(mission=mission)
        self.assertEqual(flight_record.dji_platform_id, self.platform_a.id)
        self.assertEqual(flight_record.photo_count, 1)
        self.assertEqual(flight_record.video_count, 1)
        media_files = MediaFile.objects.filter(mission=mission, flight_record=flight_record).order_by("file_name")
        self.assertEqual(list(media_files.values_list("file_name", flat=True)), ["MISSION_PHOTO.JPG", "MISSION_VIDEO.MP4"])
