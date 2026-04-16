from datetime import timedelta
from unittest.mock import patch

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
from apps.media_file.serializers import MediaFileReadSerializer
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
        _tenant, self.pilot_member, self.pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")

        self.route = Route.objects.create(tenant=self.tenant, name="媒体航线")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=self.route,
            dji_wayline_id="media-wayline",
            is_published=True,
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
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.PENDING,
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
            mission=self.mission,
            device_sn=device_sn,
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
            job_id="",
        )
        return media_file

    def _create_mission_only_media(self, *, mission: Mission, file_name: str) -> MediaFile:
        media_file = MediaFile.objects.create(
            tenant=self.tenant,
            mission=mission,
            device_sn=self.drone.device_sn,
            media_type=MediaType.PHOTO,
            file_name=file_name,
            file_url=f"https://example.com/{file_name}",
            captured_at=timezone.now(),
        )
        TenantMediaIndex.objects.create(
            tenant=self.tenant,
            media_file=media_file,
            dji_file_id=f"dji-{file_name}",
            device_sn=self.drone.device_sn,
            mission=mission,
            sync_status=SyncStatus.SYNCED,
            last_sync_at=timezone.now(),
        )
        return media_file

    def test_list_should_filter_by_device_sn(self):
        self._create_media(file_name="IMG_A.JPG", device_sn="MEDIA-SN-001")
        self._create_media(file_name="IMG_B.JPG", device_sn="MEDIA-SN-002")

        response = self.client.get("/api/v1/media-files", {"device_sn": "MEDIA-SN-001"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["total"], 1)
        self.assertEqual(response.data["data"]["list"][0]["device_sn"], "MEDIA-SN-001")

    def test_list_should_expose_media_read_model_fields(self):
        media_file = self._create_media(file_name="IMG_FIELDS.JPG", device_sn="MEDIA-SN-001")

        response = self.client.get("/api/v1/media-files")

        self.assertEqual(response.status_code, 200)
        payload = response.data["data"]["list"][0]
        self.assertEqual(payload["id"], media_file.id)
        self.assertEqual(payload["mission_id"], self.mission.id)
        self.assertEqual(payload["device_sn"], "MEDIA-SN-001")
        self.assertEqual(payload["dji_file_id"], "dji-IMG_FIELDS.JPG")
        self.assertEqual(payload["sync_status"], SyncStatus.SYNCED)
        self.assertIsNotNone(payload["last_sync_at"])

    def test_read_serializer_should_expose_empty_sync_fields_when_dji_index_missing(self):
        media_file = MediaFile.objects.create(
            tenant=self.tenant,
            flight_record=self.flight_record,
            mission=self.mission,
            device_sn="MEDIA-SN-001",
            media_type=MediaType.PHOTO,
            file_name="IMG_NO_INDEX.JPG",
            file_url="https://example.com/IMG_NO_INDEX.JPG",
            captured_at=timezone.now(),
        )

        payload = MediaFileReadSerializer(media_file).data

        self.assertEqual(payload["mission_id"], self.mission.id)
        self.assertEqual(payload["device_sn"], "MEDIA-SN-001")
        self.assertEqual(payload["dji_file_id"], "")
        self.assertEqual(payload["sync_status"], "")
        self.assertIsNone(payload["last_sync_at"])

    def test_download_should_redirect_to_dji_url(self):
        media_file = self._create_media(file_name="IMG_DL.JPG", device_sn="MEDIA-SN-001")

        response = self.client.get(f"/api/v1/media-files/{media_file.id}/download")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/__mock-dji__/_downloads/media/dji-IMG_DL.JPG")

    def test_playback_should_redirect_to_dji_playback_url_for_video(self):
        media_file = self._create_media(file_name="VID_PLAYBACK.MP4", device_sn="MEDIA-SN-001")
        media_file.media_type = MediaType.VIDEO
        media_file.save(update_fields=["media_type"])

        with patch(
            "apps.media_file.views.DjiGateway.get_media_playback_url",
            return_value="/__mock-dji__/playback-only/dji-VID_PLAYBACK.MP4",
        ) as get_media_playback_url:
            response = self.client.get(f"/api/v1/media-files/{media_file.id}/playback")

        get_media_playback_url.assert_called_once_with("dji-VID_PLAYBACK.MP4")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/__mock-dji__/playback-only/dji-VID_PLAYBACK.MP4")

    def test_playback_should_redirect_to_mock_dji_download_url_via_real_gateway(self):
        media_file = self._create_media(file_name="VID_PLAYBACK.MP4", device_sn="MEDIA-SN-001")
        media_file.media_type = MediaType.VIDEO
        media_file.save(update_fields=["media_type"])

        response = self.client.get(f"/api/v1/media-files/{media_file.id}/playback")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/__mock-dji__/_downloads/media/dji-VID_PLAYBACK.MP4")

    def test_playback_url_should_return_standard_json_for_video(self):
        media_file = self._create_media(file_name="VID_PLAYBACK_URL.MP4", device_sn="MEDIA-SN-001")
        media_file.media_type = MediaType.VIDEO
        media_file.save(update_fields=["media_type"])

        with patch(
            "apps.media_file.views.DjiGateway.get_media_playback_url",
            return_value="https://playback.example/dji-VID_PLAYBACK_URL.MP4.m3u8",
        ) as get_media_playback_url:
            response = self.client.get(f"/api/v1/media-files/{media_file.id}/playback-url")

        get_media_playback_url.assert_called_once_with("dji-VID_PLAYBACK_URL.MP4")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "00000")
        self.assertEqual(response.data["msg"], "success")
        self.assertEqual(
            response.data["data"],
            {"playback_url": "https://playback.example/dji-VID_PLAYBACK_URL.MP4.m3u8"},
        )

    def test_playback_url_should_reject_photo_media_file(self):
        media_file = self._create_media(file_name="IMG_PLAYBACK_URL.JPG", device_sn="MEDIA-SN-001")

        response = self.client.get(f"/api/v1/media-files/{media_file.id}/playback-url")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["data"], {"media_type": ["该媒体不支持 playback"]})

    def test_playback_should_reject_photo_media_file(self):
        media_file = self._create_media(file_name="IMG_PLAYBACK.JPG", device_sn="MEDIA-SN-001")

        response = self.client.get(f"/api/v1/media-files/{media_file.id}/playback")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["data"], {"media_type": ["该媒体不支持 playback"]})

    def test_media_api_should_reject_create_and_update(self):
        media_file = self._create_media(file_name="IMG_READONLY.JPG", device_sn="MEDIA-SN-001")

        create_response = self.client.post("/api/v1/media-files", {}, format="json")
        update_response = self.client.patch(f"/api/v1/media-files/{media_file.id}", {"file_name": "NEW.JPG"}, format="json")

        self.assertIn(create_response.status_code, (403, 405))
        self.assertIn(update_response.status_code, (403, 405))

    def test_delete_should_soft_delete_media_file_and_hide_it_from_api(self):
        grant_role_permissions(self.role, {"media_file.manage_media_file": ScopeType.ALL})
        media_file = self._create_media(file_name="IMG_DELETE.JPG", device_sn="MEDIA-SN-001")

        delete_response = self.client.delete(f"/api/v1/media-files/{media_file.id}")

        self.assertEqual(delete_response.status_code, 200)
        media_file.refresh_from_db()
        self.assertTrue(media_file.is_deleted)
        self.assertIsNotNone(media_file.deleted_at)

        list_response = self.client.get("/api/v1/media-files")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["data"]["total"], 0)

        detail_response = self.client.get(f"/api/v1/media-files/{media_file.id}")
        self.assertEqual(detail_response.status_code, 404)
        self.assertEqual(detail_response.data["code"], "C0404")

    def test_delete_should_recalculate_flight_record_video_count_for_bound_dji_video(self):
        grant_role_permissions(self.role, {"media_file.manage_media_file": ScopeType.ALL})
        media_file = self._create_media(file_name="VID_DELETE.MP4", device_sn="MEDIA-SN-001")
        media_file.media_type = MediaType.VIDEO
        media_file.save(update_fields=["media_type"])
        self.flight_record.video_count = 1
        self.flight_record.save()

        delete_response = self.client.delete(f"/api/v1/media-files/{media_file.id}")

        self.assertEqual(delete_response.status_code, 200)
        media_file.refresh_from_db()
        self.assertTrue(media_file.is_deleted)
        self.flight_record.refresh_from_db()
        self.assertEqual(self.flight_record.video_count, 0)

    def test_delete_should_reject_request_body(self):
        grant_role_permissions(self.role, {"media_file.manage_media_file": ScopeType.ALL})
        media_file = self._create_media(file_name="IMG_DELETE_BODY.JPG", device_sn="MEDIA-SN-001")

        response = self.client.delete(
            f"/api/v1/media-files/{media_file.id}",
            {"unexpected": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")

    def test_assigned_scope_pilot_should_list_media_bound_by_mission_without_flight_record(self):
        grant_role_permissions(self.pilot_role, {"media_file.view_media_file": ScopeType.ASSIGNED})
        self.client.force_authenticate(self.pilot_user)

        mission_only_media = self._create_mission_only_media(mission=self.mission, file_name="IMG_MISSION_ONLY.JPG")

        other_pilot_user = User.objects.create_user(username="media_other_pilot", password="pass1234", status=1)
        ensure_staff_profile(other_pilot_user, name="其他飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, other_pilot_member, _other_pilot_role = ensure_tenant_role_binding(
            other_pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator_other_media",
            role_name="其他飞手",
        )
        ensure_tenant_member_position(other_pilot_member, code="pilot_operator", name="飞手")
        other_mission = Mission.objects.create(
            tenant=self.tenant,
            name="其他媒体任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=other_pilot_member,
            pilot_name="其他飞手",
            status=MissionStatus.PENDING,
        )
        self._create_mission_only_media(mission=other_mission, file_name="IMG_OTHER_MISSION.JPG")

        response = self.client.get("/api/v1/media-files")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["total"], 1)
        self.assertEqual(response.data["data"]["list"][0]["id"], mission_only_media.id)

    def test_assigned_scope_pilot_should_retrieve_media_bound_by_mission_without_flight_record(self):
        grant_role_permissions(self.pilot_role, {"media_file.view_media_file": ScopeType.ASSIGNED})
        self.client.force_authenticate(self.pilot_user)
        mission_only_media = self._create_mission_only_media(mission=self.mission, file_name="IMG_MISSION_DETAIL.JPG")

        response = self.client.get(f"/api/v1/media-files/{mission_only_media.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["id"], mission_only_media.id)

    def test_assigned_scope_pilot_should_soft_delete_media_bound_by_mission_without_flight_record(self):
        grant_role_permissions(self.pilot_role, {"media_file.manage_media_file": ScopeType.ASSIGNED})
        self.client.force_authenticate(self.pilot_user)
        mission_only_media = self._create_mission_only_media(mission=self.mission, file_name="IMG_MISSION_DELETE.JPG")

        response = self.client.delete(f"/api/v1/media-files/{mission_only_media.id}")

        self.assertEqual(response.status_code, 200)
        mission_only_media.refresh_from_db()
        self.assertTrue(mission_only_media.is_deleted)

    def test_bind_mission_should_assign_multiple_media_to_pending_mission(self):
        grant_role_permissions(self.role, {"media_file.manage_media_file": ScopeType.ALL})
        media_a = self._create_mission_only_media(mission=self.mission, file_name="IMG_BIND_A.JPG")
        media_b = self._create_mission_only_media(mission=self.mission, file_name="IMG_BIND_B.JPG")
        media_a.mission = None
        media_a.save(update_fields=["mission"])
        media_a.dji_index.mission = None
        media_a.dji_index.save(update_fields=["mission", "updated_at"])
        media_b.mission = None
        media_b.save(update_fields=["mission"])
        media_b.dji_index.mission = None
        media_b.dji_index.save(update_fields=["mission", "updated_at"])

        response = self.client.post(
            "/api/v1/media-files/bind-mission",
            {"mission_id": self.mission.id, "media_file_ids": [media_a.id, media_b.id]},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        media_a.refresh_from_db()
        media_b.refresh_from_db()
        self.assertEqual(media_a.mission_id, self.mission.id)
        self.assertEqual(media_b.mission_id, self.mission.id)
        self.assertEqual(media_a.dji_index.mission_id, self.mission.id)
        self.assertEqual(media_b.dji_index.mission_id, self.mission.id)

    def test_bind_mission_should_reject_mission_without_device_sn(self):
        grant_role_permissions(self.role, {"media_file.manage_media_file": ScopeType.ALL})
        self.mission.drone = None
        self.mission.device_sn = ""
        self.mission.drone_name = ""
        self.mission.save(update_fields=["drone", "device_sn", "drone_name", "updated_at"])
        media_file = self._create_mission_only_media(mission=self.mission, file_name="IMG_UNBOUND.JPG")
        media_file.mission = None
        media_file.save(update_fields=["mission"])
        media_file.dji_index.mission = None
        media_file.dji_index.save(update_fields=["mission", "updated_at"])

        response = self.client.post(
            "/api/v1/media-files/bind-mission",
            {"mission_id": self.mission.id, "media_file_ids": [media_file.id]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertIn("mission_id", response.data["data"])

    def test_bind_mission_should_reject_device_sn_mismatch(self):
        grant_role_permissions(self.role, {"media_file.manage_media_file": ScopeType.ALL})
        media_file = self._create_media(file_name="IMG_MISMATCH.JPG", device_sn="OTHER-SN-001")

        response = self.client.post(
            "/api/v1/media-files/bind-mission",
            {"mission_id": self.mission.id, "media_file_ids": [media_file.id]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertIn("media_file_ids", response.data["data"])

    def test_bind_mission_should_reject_cross_tenant_mission(self):
        grant_role_permissions(self.role, {"media_file.manage_media_file": ScopeType.ALL})
        other_user = User.objects.create_user(username="media_bind_other_tenant", password="pass1234", status=1)
        ensure_staff_profile(other_user, name="其他租户用户", employment_status=EmploymentStatus.ACTIVE)
        other_tenant, other_member, _other_role = ensure_tenant_role_binding(
            other_user,
            tenant_code="media_bind_other_tenant",
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(other_member, code="pilot_operator", name="飞手")
        other_route = Route.objects.create(tenant=other_tenant, name="跨租户航线")
        other_drone = Drone.objects.create(
            tenant=other_tenant,
            code="OTHER-TENANT-DRONE-001",
            name="跨租户无人机",
            model="M30",
            device_sn="OTHER-TENANT-SN-001",
        )
        other_mission = Mission.objects.create(
            tenant=other_tenant,
            name="跨租户任务",
            route=other_route,
            route_name=other_route.name,
            drone=other_drone,
            device_sn=other_drone.device_sn,
            drone_name=other_drone.name,
            pilot=other_member,
            pilot_name="其他租户飞手",
            status=MissionStatus.PENDING,
        )
        media_file = self._create_media(file_name="IMG_CROSS_TENANT.JPG", device_sn=self.drone.device_sn)

        response = self.client.post(
            "/api/v1/media-files/bind-mission",
            {"mission_id": other_mission.id, "media_file_ids": [media_file.id]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertIn("mission_id", response.data["data"])

    def test_bind_mission_should_overwrite_existing_mission_binding(self):
        grant_role_permissions(self.role, {"media_file.manage_media_file": ScopeType.ALL})
        other_mission = Mission.objects.create(
            tenant=self.tenant,
            name="同机改绑任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.PENDING,
        )
        media_file = self._create_mission_only_media(mission=self.mission, file_name="IMG_OVERWRITE.JPG")

        response = self.client.post(
            "/api/v1/media-files/bind-mission",
            {"mission_id": other_mission.id, "media_file_ids": [media_file.id]},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        media_file.refresh_from_db()
        self.assertEqual(media_file.mission_id, other_mission.id)
        self.assertEqual(media_file.dji_index.mission_id, other_mission.id)

    def test_bind_mission_should_reject_assigned_scope_targeting_other_pilot_mission(self):
        grant_role_permissions(self.pilot_role, {"media_file.manage_media_file": ScopeType.ASSIGNED})
        self.client.force_authenticate(self.pilot_user)
        media_file = self._create_mission_only_media(mission=self.mission, file_name="IMG_ASSIGNED_SCOPE.JPG")

        other_pilot_user = User.objects.create_user(username="media_bind_other_pilot", password="pass1234", status=1)
        ensure_staff_profile(other_pilot_user, name="其他飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, other_pilot_member, _other_pilot_role = ensure_tenant_role_binding(
            other_pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator_other_bind",
            role_name="其他飞手",
        )
        ensure_tenant_member_position(other_pilot_member, code="pilot_operator", name="飞手")
        other_mission = Mission.objects.create(
            tenant=self.tenant,
            name="其他飞手任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=other_pilot_member,
            pilot_name="其他飞手",
            status=MissionStatus.PENDING,
        )

        response = self.client.post(
            "/api/v1/media-files/bind-mission",
            {"mission_id": other_mission.id, "media_file_ids": [media_file.id]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertIn("mission_id", response.data["data"])

    def test_bind_mission_should_reject_media_without_dji_index_visibility(self):
        grant_role_permissions(self.role, {"media_file.manage_media_file": ScopeType.ALL})
        media_file = MediaFile.objects.create(
            tenant=self.tenant,
            mission=None,
            device_sn=self.drone.device_sn,
            media_type=MediaType.PHOTO,
            file_name="IMG_NO_INDEX.JPG",
            file_url="https://example.com/IMG_NO_INDEX.JPG",
            captured_at=timezone.now(),
        )

        response = self.client.post(
            "/api/v1/media-files/bind-mission",
            {"mission_id": self.mission.id, "media_file_ids": [media_file.id]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertIn("media_file_ids", response.data["data"])
