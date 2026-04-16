"""Media live HTTP smoke tests."""

from datetime import timedelta

from django.utils import timezone

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_live_base import LiveDjiGatewayApiTestCase, User
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.dji_bff.models import SyncStatus, TenantMediaIndex, TenantRouteIndex
from apps.drone.models import Drone
from apps.flight_record.models import FlightRecord, FlightRecordStatus
from apps.media_file.models import MediaFile, MediaType
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route
from apps.dji_mock.state import mock_dji_state


class LiveMediaFileApiTests(LiveDjiGatewayApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="media_live_viewer", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="实时媒体查看员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="media_live_tenant",
            role_code="media_live_role",
            role_name="实时媒体角色",
        )
        grant_role_permissions(
            self.role,
            {
                "media_file.view_media_file": ScopeType.ALL,
                "media_file.manage_media_file": ScopeType.ALL,
            },
        )

        self.pilot_user = User.objects.create_user(username="media_live_pilot", password="pass1234", status=1)
        ensure_staff_profile(self.pilot_user, name="飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, self.pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")

        self.route = Route.objects.create(tenant=self.tenant, name="实时媒体航线")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=self.route,
            dji_wayline_id="media-live-wayline",
            is_published=True,
        )
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-LIVE-DRONE-001",
            name="实时媒体无人机",
            model="M30",
            device_sn="MEDIA-LIVE-SN-001",
        )
        self.mission = Mission.objects.create(
            tenant=self.tenant,
            name="实时媒体任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.PENDING,
        )
        self.flight_record = FlightRecord.objects.create(
            tenant=self.tenant,
            flight_no="MEDIA-LIVE-FR-001",
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
        self.media_file = MediaFile.objects.create(
            tenant=self.tenant,
            flight_record=self.flight_record,
            mission=self.mission,
            device_sn="MEDIA-LIVE-SN-001",
            media_type=MediaType.PHOTO,
            file_name="MEDIA_LIVE.JPG",
            file_url="https://example.com/MEDIA_LIVE.JPG",
            thumbnail_url="https://example.com/thumb/MEDIA_LIVE.JPG",
            file_size=1024,
            captured_at=timezone.now(),
        )
        TenantMediaIndex.objects.create(
            tenant=self.tenant,
            media_file=self.media_file,
            dji_file_id="media-live-file",
            device_sn="MEDIA-LIVE-SN-001",
            mission=self.mission,
            sync_status=SyncStatus.SYNCED,
            last_sync_at=timezone.now(),
        )
        mock_dji_state.seed_media_file(
            file_id="media-live-file",
            name="MEDIA_LIVE.JPG",
            device_sn="MEDIA-LIVE-SN-001",
            job_id="",
        )

        self.login(username="media_live_viewer", password="pass1234", tenant_code=self.tenant.code)

    def test_list_download_and_delete_should_follow_live_http_contract(self):
        list_response = self.client.get("/api/v1/media-files", {"device_sn": "MEDIA-LIVE-SN-001"})
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["data"]["total"], 1)

        download_response = self.client.get(f"/api/v1/media-files/{self.media_file.id}/download")
        self.assertEqual(download_response.status_code, 200)
        self.assertIn("mock media binary", download_response.text)

        delete_response = self.client.delete(f"/api/v1/media-files/{self.media_file.id}")
        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.json()["data"]["deleted"])

        detail_response = self.client.get(f"/api/v1/media-files/{self.media_file.id}")
        self.assertEqual(detail_response.status_code, 404)

        create_response = self.client.post("/api/v1/media-files", {}, format="json")
        self.assertIn(create_response.status_code, (403, 405))

    def test_bind_mission_should_follow_live_http_contract(self):
        bind_response = self.client.post(
            "/api/v1/media-files/bind-mission",
            {"mission_id": self.mission.id, "media_file_ids": [self.media_file.id]},
            format="json",
        )
        self.assertEqual(bind_response.status_code, 200)
        self.assertEqual(bind_response.json()["data"]["updated_count"], 1)

    def test_playback_url_should_follow_live_http_contract(self):
        self.media_file.media_type = MediaType.VIDEO
        self.media_file.file_name = "MEDIA_LIVE.MP4"
        self.media_file.file_url = "https://example.com/MEDIA_LIVE.MP4"
        self.media_file.save(update_fields=["media_type", "file_name", "file_url"])

        response = self.client.get(f"/api/v1/media-files/{self.media_file.id}/playback-url")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["code"], "00000")
        self.assertEqual(response.json()["msg"], "success")
        self.assertEqual(
            response.json()["data"],
            {"playback_url": "/__mock-dji__/_downloads/media/media-live-file"},
        )
