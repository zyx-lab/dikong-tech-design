from __future__ import annotations

import json
from io import StringIO
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from apps.access.models import EmploymentStatus
from apps.access.models import AuditLog
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
)
from apps.dji_bff.gateway import DjiGateway, GatewayResponse, DjiGatewayUpstreamError
from apps.dji_bff.models import DjiDeviceIndex, DjiWorkspaceConfig, SyncStatus, TenantMediaIndex
from apps.dji_bff.tasks import sync_device_indexes, sync_media_indexes
from apps.dji_mock.state import mock_dji_state
from apps.dji_mock.test_support import MockDjiUpstreamTestMixin
from apps.drone.models import Drone, DroneStatus
from apps.flight_record.models import FlightRecord
from apps.media_file.models import MediaFile, MediaType
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route

User = get_user_model()


class DjiGatewayPaginationTests(TestCase):
    def setUp(self):
        super().setUp()
        from apps.dji_bff.models import DjiWorkspaceConfig

        DjiWorkspaceConfig.objects.create(workspace_id="mock-workspace-001", access_token="mock-access-token")

    @staticmethod
    def _page(items, *, page: int, total: int, page_size: int):
        return GatewayResponse(
            status_code=200,
            headers={},
            data={
                "list": items,
                "pagination": {
                    "page": page,
                    "total": total,
                    "page_size": page_size,
                },
            },
        )

    def test_list_devices_should_collect_all_pages(self):
        gateway = DjiGateway(base_url="http://mock-dji")
        requests = []

        def fake_request(method, path, *, data=None, follow_redirects=True):
            requests.append(path)
            query = parse_qs(urlparse(path).query)
            page = int(query.get("page", ["1"])[0])
            responses = {
                1: self._page([{"device_sn": "DEVICE-001"}, {"device_sn": "DEVICE-002"}], page=1, total=3, page_size=2),
                2: self._page([{"device_sn": "DEVICE-003"}], page=2, total=3, page_size=2),
            }
            return responses[page]

        with patch.object(gateway, "_request_json", side_effect=fake_request):
            devices = gateway.list_devices()

        self.assertEqual([item["device_sn"] for item in devices], ["DEVICE-001", "DEVICE-002", "DEVICE-003"])
        self.assertEqual(len(requests), 2)
        self.assertIn("domain=0", requests[0])
        self.assertIn("page=2", requests[1])

    def test_list_jobs_should_collect_all_pages(self):
        gateway = DjiGateway(base_url="http://mock-dji")
        requests = []

        def fake_request(method, path, *, data=None, follow_redirects=True):
            requests.append(path)
            query = parse_qs(urlparse(path).query)
            page = int(query.get("page", ["1"])[0])
            responses = {
                1: self._page([{"job_id": "JOB-001"}, {"job_id": "JOB-002"}], page=1, total=3, page_size=2),
                2: self._page([{"job_id": "JOB-003"}], page=2, total=3, page_size=2),
            }
            return responses[page]

        with patch.object(gateway, "_request_json", side_effect=fake_request):
            jobs = gateway.list_jobs()

        self.assertEqual([item["job_id"] for item in jobs], ["JOB-001", "JOB-002", "JOB-003"])
        self.assertEqual(len(requests), 2)
        self.assertIn("page=2", requests[1])

    def test_list_media_files_should_collect_all_pages(self):
        gateway = DjiGateway(base_url="http://mock-dji")
        requests = []

        def fake_request(method, path, *, data=None, follow_redirects=True):
            requests.append(path)
            query = parse_qs(urlparse(path).query)
            page = int(query.get("page", ["1"])[0])
            responses = {
                1: self._page([{"file_id": "FILE-001"}, {"file_id": "FILE-002"}], page=1, total=3, page_size=2),
                2: self._page([{"file_id": "FILE-003"}], page=2, total=3, page_size=2),
            }
            return responses[page]

        with patch.object(gateway, "_request_json", side_effect=fake_request):
            files = gateway.list_media_files()

        self.assertEqual([item["file_id"] for item in files], ["FILE-001", "FILE-002", "FILE-003"])
        self.assertEqual(len(requests), 2)
        self.assertIn("page=2", requests[1])

    def test_upload_route_should_return_wayline_id_and_download_url(self):
        gateway = DjiGateway(base_url="http://mock-dji")
        upstream_payload = {
            "name": "route-a",
            "wayline_id": "wayline-id-001",
            "workspace_id": "mock-workspace-001",
            "download_url": "/api/v1/wayline/workspaces/mock-workspace-001/waylines/wayline-id-001/url",
        }

        with patch.object(gateway, "_workspace_id", return_value="mock-workspace-001"):
            with patch.object(
                gateway,
                "_request_multipart",
                return_value=GatewayResponse(status_code=200, headers={}, data=upstream_payload),
            ):
                payload = gateway.upload_route(route_name="route-a", file_obj=StringIO("kmz-bytes-placeholder"))

        self.assertEqual(payload["dji_wayline_id"], "wayline-id-001")
        self.assertEqual(
            payload["download_url"],
            "/api/v1/wayline/workspaces/mock-workspace-001/waylines/wayline-id-001/url",
        )

    def test_upload_route_should_raise_if_download_url_missing(self):
        gateway = DjiGateway(base_url="http://mock-dji")
        upstream_payload = {"wayline_id": "wayline-id-001"}

        with patch.object(gateway, "_workspace_id", return_value="mock-workspace-001"):
            with patch.object(
                gateway,
                "_request_multipart",
                return_value=GatewayResponse(status_code=200, headers={}, data=upstream_payload),
            ):
                with self.assertRaises(DjiGatewayUpstreamError) as exc_info:
                    gateway.upload_route(route_name="route-a", file_obj=StringIO("kmz-bytes-placeholder"))

        self.assertEqual(exc_info.exception.status_code, 502)
        self.assertEqual(exc_info.exception.data, upstream_payload)

    def test_create_mission_should_send_snake_case_flight_task_payload(self):
        gateway = DjiGateway(base_url="http://mock-dji")
        requests = []

        def fake_request(method, path, *, data=None, follow_redirects=True):
            requests.append((method, path, data))
            return GatewayResponse(status_code=200, headers={}, data={"job_id": "job-snake-case-001"})

        with patch.object(gateway, "_workspace_id", return_value="mock-workspace-001"):
            with patch.object(gateway, "_request_json", side_effect=fake_request):
                payload = gateway.create_mission(
                    mission_name="v2-task",
                    file_id="wayline-file-001",
                    dock_sn="GATEWAY-RC-001",
                )

        self.assertEqual(payload["dji_job_id"], "job-snake-case-001")
        self.assertEqual(requests[0][0], "POST")
        self.assertEqual(requests[0][1], "/api/v1/wayline/workspaces/mock-workspace-001/flight-tasks")
        self.assertEqual(
            requests[0][2],
            {
                "name": "v2-task",
                "file_id": "wayline-file-001",
                "dock_sn": "GATEWAY-RC-001",
                "wayline_type": 0,
                "task_type": 0,
                "rth_altitude": 30,
                "out_of_control_action": 0,
            },
        )

    def test_download_route_file_should_expand_relative_download_url(self):
        gateway = DjiGateway(base_url="http://mock-dji")
        upstream_response = GatewayResponse(
            status_code=200,
            headers={"Content-Type": "application/vnd.google-earth.kmz"},
            data=b"mock-kmz-binary",
        )

        with patch.object(gateway, "_ensure_authenticated", return_value=SimpleNamespace(access_token="mock-token")):
            with patch.object(gateway, "_request_raw", return_value=upstream_response) as request_mock:
                response = gateway.download_route_file("/downloads/route-001.kmz")

        self.assertEqual(response, upstream_response)
        self.assertEqual(request_mock.call_args.args[1], "http://mock-dji/downloads/route-001.kmz")
        self.assertEqual(request_mock.call_args.kwargs["data"], None)
        self.assertTrue(request_mock.call_args.kwargs["follow_redirects"])
        self.assertEqual(request_mock.call_args.kwargs["headers"]["x-auth-token"], "mock-token")
        self.assertEqual(request_mock.call_args.kwargs["headers"]["Accept"], "*/*")

    def test_download_route_file_should_not_authenticate_absolute_download_url(self):
        gateway = DjiGateway(base_url="http://mock-dji")
        upstream_response = GatewayResponse(
            status_code=200,
            headers={"Content-Type": "application/vnd.google-earth.kmz"},
            data=b"mock-kmz-binary",
        )

        with patch.object(gateway, "_ensure_authenticated") as auth_mock:
            with patch.object(gateway, "_request_raw", return_value=upstream_response) as request_mock:
                response = gateway.download_route_file("https://download.example/route-001.kmz")

        self.assertEqual(response, upstream_response)
        auth_mock.assert_not_called()
        self.assertEqual(request_mock.call_args.args[1], "https://download.example/route-001.kmz")
        self.assertEqual(request_mock.call_args.kwargs["headers"], {"Accept": "*/*"})

    def test_download_route_file_should_reject_blank_url(self):
        gateway = DjiGateway(base_url="http://mock-dji")

        with self.assertRaises(DjiGatewayUpstreamError) as exc_info:
            gateway.download_route_file("   ")

        self.assertEqual(exc_info.exception.status_code, 400)

    def test_get_media_playback_url_should_return_redirect_location(self):
        gateway = DjiGateway(base_url="http://mock-dji")
        upstream_response = GatewayResponse(
            status_code=302,
            headers={"Location": "https://playback.example/media-001.m3u8"},
            data=None,
        )

        with patch.object(gateway, "_workspace_id", return_value="mock-workspace-001"):
            with patch.object(gateway, "_request_json", return_value=upstream_response) as request_mock:
                playback_url = gateway.get_media_playback_url("media-001")

        self.assertEqual(playback_url, "https://playback.example/media-001.m3u8")
        self.assertEqual(
            request_mock.call_args.args,
            ("GET", "/api/v1/media/workspaces/mock-workspace-001/files/media-001/playback-url"),
        )
        self.assertFalse(request_mock.call_args.kwargs["follow_redirects"])

    def test_get_media_playback_url_should_accept_string_data_payload(self):
        gateway = DjiGateway(base_url="http://mock-dji")
        upstream_response = GatewayResponse(
            status_code=200,
            headers={},
            data="https://playback.example/media-001.m3u8",
        )

        with patch.object(gateway, "_workspace_id", return_value="mock-workspace-001"):
            with patch.object(gateway, "_request_json", return_value=upstream_response) as request_mock:
                playback_url = gateway.get_media_playback_url("media-001")

        self.assertEqual(playback_url, "https://playback.example/media-001.m3u8")
        self.assertEqual(
            request_mock.call_args.args,
            ("GET", "/api/v1/media/workspaces/mock-workspace-001/files/media-001/playback-url"),
        )
        self.assertFalse(request_mock.call_args.kwargs["follow_redirects"])

    def test_get_media_preview_url_should_return_redirect_location(self):
        gateway = DjiGateway(base_url="http://mock-dji")
        upstream_response = GatewayResponse(
            status_code=302,
            headers={"Location": "https://preview.example/media-001.jpg"},
            data=None,
        )

        with patch.object(gateway, "_workspace_id", return_value="mock-workspace-001"):
            with patch.object(gateway, "_request_json", return_value=upstream_response) as request_mock:
                preview_url = gateway.get_media_preview_url("media-001")

        self.assertEqual(preview_url, "https://preview.example/media-001.jpg")
        self.assertEqual(
            request_mock.call_args.args,
            ("GET", "/api/v1/media/workspaces/mock-workspace-001/files/media-001/preview-url"),
        )
        self.assertFalse(request_mock.call_args.kwargs["follow_redirects"])

    def test_request_should_wrap_timeout_as_upstream_error(self):
        gateway = DjiGateway(base_url="http://mock-dji")

        with patch("apps.dji_bff.gateway.urlopen", side_effect=TimeoutError):
            with self.assertRaises(DjiGatewayUpstreamError) as exc_info:
                gateway._request("GET", "/timeout", data=None, headers={}, follow_redirects=True)

        self.assertEqual(exc_info.exception.status_code, 502)
        self.assertEqual(str(exc_info.exception), "DJI upstream timed out")

    def test_request_raw_should_wrap_timeout_as_upstream_error(self):
        gateway = DjiGateway(base_url="http://mock-dji")

        with patch("apps.dji_bff.gateway.urlopen", side_effect=TimeoutError):
            with self.assertRaises(DjiGatewayUpstreamError) as exc_info:
                gateway._request_raw("GET", "/timeout", data=None, headers={}, follow_redirects=True)

        self.assertEqual(exc_info.exception.status_code, 502)
        self.assertEqual(str(exc_info.exception), "DJI upstream timed out")

@override_settings(
    DJI_UPSTREAM_USERNAME="mock-admin",
    DJI_UPSTREAM_PASSWORD="mock-password",
    DJI_UPSTREAM_LOGIN_FLAG=1,
)
class DjiGatewayAutoAuthTests(MockDjiUpstreamTestMixin, TestCase):
    def test_gateway_should_login_and_persist_workspace_config_when_config_is_missing(self):
        DjiWorkspaceConfig.objects.all().delete()

        devices = DjiGateway().list_devices()

        self.assertEqual(len(devices), 2)
        config = DjiWorkspaceConfig.objects.get()
        self.assertEqual(config.workspace_id, mock_dji_state.current_workspace_payload()["workspace_id"])
        self.assertEqual(config.dji_user_id, mock_dji_state.current_user_payload()["user_id"])
        self.assertEqual(config.dji_username, mock_dji_state.current_user_payload()["username"])
        self.assertEqual(config.access_token, mock_dji_state.access_token)

    def test_gateway_should_refresh_expired_token_before_request(self):
        old_token = self.dji_workspace_config.access_token
        self.dji_workspace_config.expires_at = timezone.now() - timedelta(minutes=1)
        self.dji_workspace_config.save(update_fields=["expires_at", "updated_at"])

        payload = DjiGateway().get_current_user()

        self.assertEqual(payload["username"], mock_dji_state.current_user_payload()["username"])
        self.dji_workspace_config.refresh_from_db()
        self.assertNotEqual(self.dji_workspace_config.access_token, old_token)
        self.assertEqual(self.dji_workspace_config.access_token, mock_dji_state.access_token)

    def test_gateway_should_relogin_after_unauthorized_response_and_retry_once(self):
        self.dji_workspace_config.access_token = "stale-token"
        self.dji_workspace_config.expires_at = timezone.now() + timedelta(minutes=10)
        self.dji_workspace_config.save(update_fields=["access_token", "expires_at", "updated_at"])

        payload = DjiGateway().get_current_user()

        self.assertEqual(payload["user_id"], mock_dji_state.current_user_payload()["user_id"])
        self.dji_workspace_config.refresh_from_db()
        self.assertEqual(self.dji_workspace_config.access_token, mock_dji_state.access_token)


class DjiMqttWatcherTests(TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="dji_mqtt_dispatcher", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="MQTT 调度员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="dji_mqtt_tenant",
            role_code="dji_mqtt_role",
            role_name="DJI MQTT 角色",
        )
        self.pilot_user = User.objects.create_user(username="dji_mqtt_pilot", password="pass1234", status=1)
        ensure_staff_profile(self.pilot_user, name="飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, self.pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")
        self.route = Route.objects.create(tenant=self.tenant, name="MQTT 航线")
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="MQTT-DRONE-001",
            name="MQTT 无人机",
            model="M30",
            device_sn="MQTT-SN-001",
        )

    def test_watcher_should_collect_running_mission_device_sns_only(self):
        from apps.dji_bff.mqtt_watcher import DjiMqttWatcher

        Mission.objects.create(
            tenant=self.tenant,
            name="执行中任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.RUNNING,
            started_at=timezone.now() - timedelta(minutes=1),
        )
        Mission.objects.create(
            tenant=self.tenant,
            name="已完成任务",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn="MQTT-DONE-SN",
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=timezone.now() - timedelta(minutes=5),
            finished_at=timezone.now() - timedelta(minutes=1),
        )

        watcher = DjiMqttWatcher()

        self.assertEqual(watcher._target_device_sns(), {self.drone.device_sn})

    def test_watcher_should_update_registry_from_osd_mode_code(self):
        from apps.dji_bff.mqtt_watcher import DjiMqttWatcher
        from apps.mission.flight_state import flight_state_registry

        flight_state_registry.clear()
        watcher = DjiMqttWatcher()

        watcher._handle_osd_message(
            topic=f"thing/product/{self.drone.device_sn}/osd",
            payload={"data": {"mode_code": 5}},
        )

        snapshot = flight_state_registry.get(self.drone.device_sn)
        self.assertIsNotNone(snapshot)
        self.assertTrue(snapshot.is_airborne)
        self.assertEqual(snapshot.mode_code, 5)

    def test_watcher_on_connect_should_accept_paho_reason_code_object(self):
        from apps.dji_bff.mqtt_watcher import DjiMqttWatcher

        watcher = DjiMqttWatcher()
        success = ReasonCode(PacketTypes.CONNACK, "Success")

        watcher._on_connect(client=None, userdata=None, flags=None, reason_code=success, properties=None)

        self.assertTrue(watcher._connected_event.is_set())

    @override_settings(DJI_MQTT_WATCHER_ENABLED=True)
    def test_should_start_in_process_watcher_should_reject_test_command(self):
        from apps.dji_bff.mqtt_watcher import should_start_in_process_watcher

        with patch("apps.dji_bff.mqtt_watcher.sys.argv", ["manage.py", "test"]):
            self.assertFalse(should_start_in_process_watcher())


@override_settings(DJI_INTERNAL_API_TOKEN="internal-sync-token")
class DjiBffSyncAndInternalApiTests(MockDjiUpstreamTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="dji_bff_admin", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="DJI BFF 管理员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="dji_bff_tenant",
            role_code="dji_bff_role",
            role_name="DJI BFF 角色",
        )

        self.pilot_user = User.objects.create_user(username="dji_bff_pilot", password="pass1234", status=1)
        ensure_staff_profile(self.pilot_user, name="飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, self.pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")

    def test_sync_device_indexes_should_refresh_shared_pool_and_claimed_drone_online_state(self):
        visible_drone = Drone.objects.create(
            tenant=self.tenant,
            code="SYNC-DRONE-001",
            name="可见无人机",
            model="M30",
            device_sn="MOCK-DRONE-001",
            status=DroneStatus.CLAIMED,
            dji_online=False,
        )
        missing_drone = Drone.objects.create(
            tenant=self.tenant,
            code="SYNC-DRONE-002",
            name="丢失无人机",
            model="M30",
            device_sn="MISSING-DRONE-001",
            status=DroneStatus.CLAIMED,
            dji_online=True,
        )

        summary = sync_device_indexes()

        self.assertGreaterEqual(summary["synced_count"], 2)
        visible_drone.refresh_from_db()
        missing_drone.refresh_from_db()
        self.assertEqual(visible_drone.status, DroneStatus.CLAIMED)
        self.assertEqual(missing_drone.status, DroneStatus.CLAIMED)
        self.assertTrue(visible_drone.dji_online)
        self.assertFalse(missing_drone.dji_online)

    def test_sync_device_indexes_should_mark_all_claimed_drones_offline_when_bound_pool_is_empty(self):
        visible_drone = Drone.objects.create(
            tenant=self.tenant,
            code="SYNC-EMPTY-DRONE-001",
            name="已认领无人机",
            model="M30",
            device_sn="MOCK-DRONE-001",
            status=DroneStatus.CLAIMED,
            dji_online=True,
        )
        mock_dji_state.bound_device_sns = set()

        summary = sync_device_indexes()

        self.assertEqual(summary["synced_count"], 0)
        visible_drone.refresh_from_db()
        self.assertEqual(visible_drone.status, DroneStatus.CLAIMED)
        self.assertFalse(visible_drone.dji_online)

    def test_sync_device_indexes_should_mark_bound_status_false_device_offline(self):
        visible_drone = Drone.objects.create(
            tenant=self.tenant,
            code="SYNC-OFFLINE-DRONE-001",
            name="已关机无人机",
            model="M30",
            device_sn="MOCK-DRONE-001",
            status=DroneStatus.CLAIMED,
            dji_online=True,
        )
        mock_dji_state.devices["MOCK-DRONE-001"]["status"] = False

        summary = sync_device_indexes()

        self.assertGreaterEqual(summary["synced_count"], 1)
        visible_drone.refresh_from_db()
        self.assertEqual(visible_drone.status, DroneStatus.CLAIMED)
        self.assertFalse(visible_drone.dji_online)
        self.assertTrue(DjiDeviceIndex.objects.filter(device_sn="MOCK-DRONE-001").exists())

    def test_sync_device_indexes_should_delete_stale_shared_device_indexes(self):
        DjiDeviceIndex.objects.create(device_sn="STALE-DRONE-001", last_payload={"name": "stale"})
        DjiDeviceIndex.objects.create(device_sn="MOCK-DRONE-001", last_payload={"name": "old visible"})
        mock_dji_state.bound_device_sns = {"MOCK-DRONE-001"}

        summary = sync_device_indexes()

        self.assertEqual(summary["synced_count"], 1)
        self.assertFalse(DjiDeviceIndex.objects.filter(device_sn="STALE-DRONE-001").exists())
        self.assertTrue(DjiDeviceIndex.objects.filter(device_sn="MOCK-DRONE-001").exists())

    def test_sync_device_indexes_should_not_write_audit_log_row(self):
        sync_device_indexes()

        self.assertFalse(AuditLog.objects.filter(action="DJI_DEVICE_SYNC").exists())

    def test_sync_media_indexes_should_create_local_read_model_from_upstream_media(self):
        Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-SYNC-DRONE",
            name="媒体同步无人机",
            model="M30",
            device_sn="MOCK-DRONE-001",
        )

        summary = sync_media_indexes()

        self.assertEqual(summary["created_count"], 1)
        media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="mock-file-001")
        self.assertEqual(media_index.device_sn, "MOCK-DRONE-001")
        self.assertEqual(media_index.sync_status, SyncStatus.SYNCED)
        media_file = MediaFile.objects.get(id=media_index.media_file_id, tenant=self.tenant)
        self.assertEqual(media_file.device_sn, "MOCK-DRONE-001")

    def test_sync_media_indexes_should_not_write_audit_log_row(self):
        Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-SYNC-NO-AUDIT",
            name="媒体同步无人机",
            model="M30",
            device_sn="MOCK-DRONE-001",
        )

        sync_media_indexes()

        self.assertFalse(AuditLog.objects.filter(action="DJI_MEDIA_SYNC").exists())

    def test_sync_media_indexes_should_use_claimed_drone_when_released_and_claimed_share_device_sn(self):
        sync_user = User.objects.create_user(username="dji_bff_sync_user", password="pass1234", status=1)
        ensure_staff_profile(sync_user, name="同步用户", employment_status=EmploymentStatus.ACTIVE)
        claimed_tenant, _claimed_member, _claimed_role = ensure_tenant_role_binding(
            sync_user,
            tenant_code="dji_sync_claimed_tenant",
            role_code="dji_sync_claimed_role",
            role_name="DJI 同步角色",
        )

        Drone.objects.create(
            tenant=claimed_tenant,
            code="MEDIA-SYNC-CLAIMED",
            name="认领无人机",
            model="M30",
            device_sn="MEDIA-DUP-SN-001",
            status=DroneStatus.CLAIMED,
        )
        Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-SYNC-RELEASED",
            name="释放无人机",
            model="M30",
            device_sn="MEDIA-DUP-SN-001",
            status=DroneStatus.RELEASED,
        )
        mock_dji_state.seed_media_file(
            file_id="media-duplicate-sn-file",
            name="MEDIA_DUPLICATE_SN.JPG",
            device_sn="MEDIA-DUP-SN-001",
            job_id="ignored-job-id",
        )

        summary = sync_media_indexes()

        self.assertGreaterEqual(summary["created_count"], 1)
        self.assertTrue(
            TenantMediaIndex.objects.filter(tenant=claimed_tenant, dji_file_id="media-duplicate-sn-file").exists()
        )
        self.assertFalse(TenantMediaIndex.objects.filter(tenant=self.tenant, dji_file_id="media-duplicate-sn-file").exists())

    def test_sync_media_indexes_should_accept_upstream_drone_field_for_device_sn(self):
        Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-UPSTREAM-DRONE-FIELD",
            name="媒体同步无人机",
            model="M30",
            device_sn="MEDIA-UPSTREAM-DRONE-SN-001",
        )
        gateway = SimpleNamespace(
            list_media_files=lambda: [
                {
                    "file_id": "media-upstream-drone-field-file",
                    "file_name": "MEDIA_UPSTREAM_DRONE_FIELD.JPG",
                    "drone": "MEDIA-UPSTREAM-DRONE-SN-001",
                }
            ]
        )

        summary = sync_media_indexes(gateway=gateway)

        self.assertEqual(summary["created_count"], 1)
        media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="media-upstream-drone-field-file")
        self.assertEqual(media_index.device_sn, "MEDIA-UPSTREAM-DRONE-SN-001")
        self.assertEqual(media_index.media_file.device_sn, "MEDIA-UPSTREAM-DRONE-SN-001")

    def test_sync_media_indexes_should_normalize_naive_create_time_before_matching_mission_window(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-NAIVE-CREATE-TIME-DRONE",
            name="媒体同步无人机",
            model="M30",
            device_sn="MEDIA-NAIVE-CREATE-TIME-SN-001",
        )
        route = Route.objects.create(tenant=self.tenant, name="媒体同步航线")
        captured_at = timezone.make_aware(datetime(2026, 4, 15, 17, 4, 42), timezone.get_current_timezone())
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="应匹配无时区 create_time 的任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=captured_at - timedelta(minutes=1),
            finished_at=captured_at + timedelta(minutes=1),
        )
        gateway = SimpleNamespace(
            list_media_files=lambda: [
                {
                    "file_id": "media-naive-create-time-file",
                    "file_name": "MEDIA_NAIVE_CREATE_TIME.JPG",
                    "drone": drone.device_sn,
                    "create_time": "2026-04-15 17:04:42",
                }
            ]
        )

        summary = sync_media_indexes(gateway=gateway)

        self.assertEqual(summary["created_count"], 1)
        media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="media-naive-create-time-file")
        self.assertEqual(media_index.mission_id, mission.id)
        self.assertEqual(media_index.media_file.mission_id, mission.id)
        self.assertEqual(media_index.media_file.captured_at, captured_at)

    def test_sync_media_indexes_should_auto_bind_when_media_is_within_finished_at_grace_window(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-FINISH-GRACE-DRONE",
            name="结束宽限无人机",
            model="M30",
            device_sn="MEDIA-FINISH-GRACE-SN-001",
        )
        route = Route.objects.create(tenant=self.tenant, name="结束宽限航线")
        finished_at = timezone.now() - timedelta(minutes=1)
        captured_at = finished_at + timedelta(seconds=2)
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="结束宽限任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=finished_at - timedelta(minutes=3),
            finished_at=finished_at,
        )
        gateway = SimpleNamespace(
            list_media_files=lambda: [
                {
                    "file_id": "media-finish-grace-file",
                    "file_name": "MEDIA_FINISH_GRACE.JPG",
                    "drone": drone.device_sn,
                    "captured_at": captured_at.isoformat(),
                }
            ]
        )

        summary = sync_media_indexes(gateway=gateway)

        self.assertEqual(summary["created_count"], 1)
        media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="media-finish-grace-file")
        self.assertEqual(media_index.mission_id, mission.id)

    def test_sync_media_indexes_should_auto_bind_flight_record_for_new_media_when_mission_has_record(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-FLIGHT-RECORD-NEW-DRONE",
            name="新媒体飞行记录无人机",
            model="M30",
            device_sn="MEDIA-FLIGHT-RECORD-NEW-SN-001",
        )
        route = Route.objects.create(tenant=self.tenant, name="新媒体飞行记录航线")
        captured_at = timezone.now() - timedelta(minutes=1)
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="新媒体飞行记录任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=captured_at - timedelta(minutes=2),
            finished_at=captured_at + timedelta(minutes=2),
        )
        flight_record = FlightRecord.create_from_completed_mission(mission=mission)
        gateway = SimpleNamespace(
            list_media_files=lambda: [
                {
                    "file_id": "media-flight-record-new-file",
                    "file_name": "MEDIA_FLIGHT_RECORD_NEW.MP4",
                    "drone": drone.device_sn,
                    "captured_at": captured_at.isoformat(),
                }
            ]
        )

        summary = sync_media_indexes(gateway=gateway)

        self.assertEqual(summary["created_count"], 1)
        media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="media-flight-record-new-file")
        self.assertEqual(media_index.mission_id, mission.id)
        self.assertEqual(media_index.media_file.flight_record_id, flight_record.id)
        flight_record.refresh_from_db()
        self.assertEqual(flight_record.video_count, 1)

    def test_sync_media_indexes_should_backfill_flight_record_for_existing_mission_bound_media(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-FLIGHT-RECORD-BACKFILL-DRONE",
            name="回填飞行记录无人机",
            model="M30",
            device_sn="MEDIA-FLIGHT-RECORD-BACKFILL-SN-001",
        )
        route = Route.objects.create(tenant=self.tenant, name="回填飞行记录航线")
        captured_at = timezone.now() - timedelta(minutes=1)
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="回填飞行记录任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=captured_at - timedelta(minutes=2),
            finished_at=captured_at + timedelta(minutes=2),
        )
        flight_record = FlightRecord.create_from_completed_mission(mission=mission)
        media_file = MediaFile.objects.create(
            tenant=self.tenant,
            mission=mission,
            flight_record=None,
            device_sn=drone.device_sn,
            media_type=MediaType.VIDEO,
            file_name="MEDIA_FLIGHT_RECORD_BACKFILL.MP4",
            file_url="dji://media-flight-record-backfill-file",
            captured_at=captured_at,
        )
        TenantMediaIndex.objects.create(
            tenant=self.tenant,
            media_file=media_file,
            dji_file_id="media-flight-record-backfill-file",
            device_sn=drone.device_sn,
            mission=mission,
            sync_status=SyncStatus.SYNCED,
            last_sync_at=captured_at,
        )
        gateway = SimpleNamespace(
            list_media_files=lambda: [
                {
                    "file_id": "media-flight-record-backfill-file",
                    "file_name": "MEDIA_FLIGHT_RECORD_BACKFILL.MP4",
                    "drone": drone.device_sn,
                    "captured_at": captured_at.isoformat(),
                }
            ]
        )

        summary = sync_media_indexes(gateway=gateway)

        self.assertEqual(summary["updated_count"], 1)
        media_file.refresh_from_db()
        self.assertEqual(media_file.flight_record_id, flight_record.id)
        flight_record.refresh_from_db()
        self.assertEqual(flight_record.video_count, 1)

    def test_sync_media_indexes_should_preserve_existing_flight_record_binding_on_resync(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-FLIGHT-RECORD-PRESERVE-DRONE",
            name="保留飞行记录无人机",
            model="M30",
            device_sn="MEDIA-FLIGHT-RECORD-PRESERVE-SN-001",
        )
        route = Route.objects.create(tenant=self.tenant, name="保留飞行记录航线")
        preserved_captured_at = timezone.now() - timedelta(minutes=10)
        preserved_mission = Mission.objects.create(
            tenant=self.tenant,
            name="保留飞行记录任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=preserved_captured_at - timedelta(minutes=2),
            finished_at=preserved_captured_at + timedelta(minutes=2),
        )
        preserved_flight_record = FlightRecord.create_from_completed_mission(mission=preserved_mission)
        media_file = MediaFile.objects.create(
            tenant=self.tenant,
            mission=None,
            flight_record=preserved_flight_record,
            device_sn=drone.device_sn,
            media_type=MediaType.VIDEO,
            file_name="MEDIA_FLIGHT_RECORD_PRESERVE.MP4",
            file_url="dji://media-flight-record-preserve-file",
            captured_at=preserved_captured_at,
        )
        TenantMediaIndex.objects.create(
            tenant=self.tenant,
            media_file=media_file,
            dji_file_id="media-flight-record-preserve-file",
            device_sn=drone.device_sn,
            mission=None,
            sync_status=SyncStatus.SYNCED,
            last_sync_at=preserved_captured_at,
        )
        matched_captured_at = timezone.now() - timedelta(minutes=1)
        Mission.objects.create(
            tenant=self.tenant,
            name="新的自动匹配任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=matched_captured_at - timedelta(minutes=2),
            finished_at=matched_captured_at + timedelta(minutes=2),
        )
        gateway = SimpleNamespace(
            list_media_files=lambda: [
                {
                    "file_id": "media-flight-record-preserve-file",
                    "file_name": "MEDIA_FLIGHT_RECORD_PRESERVE.MP4",
                    "drone": drone.device_sn,
                    "captured_at": matched_captured_at.isoformat(),
                }
            ]
        )

        summary = sync_media_indexes(gateway=gateway)

        self.assertEqual(summary["updated_count"], 1)
        media_file.refresh_from_db()
        self.assertEqual(media_file.flight_record_id, preserved_flight_record.id)
        self.assertEqual(media_file.mission_id, preserved_mission.id)
        preserved_flight_record.refresh_from_db()
        self.assertEqual(preserved_flight_record.video_count, 1)

    def test_sync_media_indexes_should_leave_flight_record_empty_when_mission_has_no_record(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-FLIGHT-RECORD-NONE-DRONE",
            name="无飞行记录无人机",
            model="M30",
            device_sn="MEDIA-FLIGHT-RECORD-NONE-SN-001",
        )
        route = Route.objects.create(tenant=self.tenant, name="无飞行记录航线")
        captured_at = timezone.now() - timedelta(minutes=1)
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="无飞行记录任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=captured_at - timedelta(minutes=2),
            finished_at=captured_at + timedelta(minutes=2),
        )
        gateway = SimpleNamespace(
            list_media_files=lambda: [
                {
                    "file_id": "media-flight-record-none-file",
                    "file_name": "MEDIA_FLIGHT_RECORD_NONE.MP4",
                    "drone": drone.device_sn,
                    "captured_at": captured_at.isoformat(),
                }
            ]
        )

        summary = sync_media_indexes(gateway=gateway)

        self.assertEqual(summary["created_count"], 1)
        media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="media-flight-record-none-file")
        self.assertEqual(media_index.mission_id, mission.id)
        self.assertIsNone(media_index.media_file.flight_record_id)

    def test_sync_media_indexes_should_not_auto_bind_when_media_is_past_finished_at_grace_window(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-FINISH-GRACE-PAST-DRONE",
            name="超出宽限无人机",
            model="M30",
            device_sn="MEDIA-FINISH-GRACE-SN-002",
        )
        route = Route.objects.create(tenant=self.tenant, name="超出宽限航线")
        finished_at = timezone.now() - timedelta(minutes=1)
        captured_at = finished_at + timedelta(seconds=6)
        Mission.objects.create(
            tenant=self.tenant,
            name="超出宽限任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=finished_at - timedelta(minutes=3),
            finished_at=finished_at,
        )
        gateway = SimpleNamespace(
            list_media_files=lambda: [
                {
                    "file_id": "media-finish-grace-past-file",
                    "file_name": "MEDIA_FINISH_GRACE_PAST.JPG",
                    "drone": drone.device_sn,
                    "captured_at": captured_at.isoformat(),
                }
            ]
        )

        summary = sync_media_indexes(gateway=gateway)

        self.assertEqual(summary["created_count"], 1)
        media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="media-finish-grace-past-file")
        self.assertIsNone(media_index.mission_id)

    def test_sync_media_indexes_should_ignore_missions_without_finished_at_for_auto_bind(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-NO-FINISH-DRONE",
            name="无完成时间无人机",
            model="M30",
            device_sn="MEDIA-NO-FINISH-SN-001",
        )
        route = Route.objects.create(tenant=self.tenant, name="无完成时间航线")
        started_at = timezone.now() - timedelta(minutes=1)
        captured_at = started_at + timedelta(seconds=10)
        Mission.objects.create(
            tenant=self.tenant,
            name="无完成时间任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.RUNNING,
            started_at=started_at,
            finished_at=None,
        )
        gateway = SimpleNamespace(
            list_media_files=lambda: [
                {
                    "file_id": "media-no-finish-file",
                    "file_name": "MEDIA_NO_FINISH.JPG",
                    "drone": drone.device_sn,
                    "captured_at": captured_at.isoformat(),
                }
            ]
        )

        summary = sync_media_indexes(gateway=gateway)

        self.assertEqual(summary["created_count"], 1)
        media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="media-no-finish-file")
        self.assertIsNone(media_index.mission_id)

    def test_sync_media_indexes_should_auto_bind_to_unique_completed_mission_window(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-SYNC-DRONE-BOUND",
            name="媒体同步无人机",
            model="M30",
            device_sn="MEDIA-CROSS-SN-001",
        )
        route = Route.objects.create(tenant=self.tenant, name="媒体同步航线")
        captured_at = timezone.now() - timedelta(minutes=2)
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="应自动匹配的任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=captured_at - timedelta(minutes=3),
            finished_at=captured_at + timedelta(minutes=3),
        )
        mock_dji_state.seed_media_file(
            file_id="media-cross-mission-file",
            name="MEDIA_CROSS_MISSION.JPG",
            device_sn="MEDIA-CROSS-SN-001",
            job_id="legacy-job-id-that-should-be-ignored",
            captured_at=captured_at.isoformat(),
        )

        summary = sync_media_indexes()

        self.assertGreaterEqual(summary["created_count"], 1)
        media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="media-cross-mission-file")
        media_file = media_index.media_file
        self.assertEqual(media_file.mission_id, mission.id)
        self.assertIsNone(media_file.flight_record_id)
        self.assertEqual(media_index.mission_id, mission.id)

    def test_sync_media_indexes_should_preserve_existing_manual_mission_binding(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-MANUAL-DRONE",
            name="人工绑定无人机",
            model="M30",
            device_sn="MEDIA-MANUAL-SN-001",
        )
        route = Route.objects.create(tenant=self.tenant, name="人工绑定航线")
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="人工绑定任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.PENDING,
        )
        media_file = MediaFile.objects.create(
            tenant=self.tenant,
            mission=mission,
            device_sn=drone.device_sn,
            media_type=1,
            file_name="MEDIA_MANUAL.JPG",
            file_url="dji://media-manual-file",
        )
        TenantMediaIndex.objects.create(
            tenant=self.tenant,
            media_file=media_file,
            dji_file_id="media-manual-file",
            device_sn=drone.device_sn,
            mission=mission,
            sync_status=SyncStatus.SYNCED,
        )
        mock_dji_state.seed_media_file(
            file_id="media-manual-file",
            name="MEDIA_MANUAL.JPG",
            device_sn=drone.device_sn,
            job_id="ignored-job-id",
        )

        summary = sync_media_indexes()

        self.assertGreaterEqual(summary["updated_count"], 1)
        media_file.refresh_from_db()
        self.assertEqual(media_file.mission_id, mission.id)
        self.assertEqual(media_file.dji_index.mission_id, mission.id)

    def test_sync_media_indexes_should_leave_mission_empty_when_multiple_windows_match(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-AMBIGUOUS-DRONE",
            name="冲突窗口无人机",
            model="M30",
            device_sn="MEDIA-AMBIGUOUS-SN-001",
        )
        route = Route.objects.create(tenant=self.tenant, name="冲突窗口航线")
        captured_at = timezone.now() - timedelta(minutes=1)
        for idx in (1, 2):
            Mission.objects.create(
                tenant=self.tenant,
                name=f"冲突窗口任务{idx}",
                route=route,
                route_name=route.name,
                drone=drone,
                device_sn=drone.device_sn,
                drone_name=drone.name,
                pilot=self.pilot_member,
                pilot_name="飞手",
                status=MissionStatus.COMPLETED,
                started_at=captured_at - timedelta(minutes=5),
                finished_at=captured_at + timedelta(minutes=5),
            )
        mock_dji_state.seed_media_file(
            file_id="media-ambiguous-window-file",
            name="MEDIA_AMBIGUOUS.JPG",
            device_sn=drone.device_sn,
            job_id="ignored-job-id",
            captured_at=captured_at.isoformat(),
        )

        summary = sync_media_indexes()

        self.assertGreaterEqual(summary["created_count"], 1)
        media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="media-ambiguous-window-file")
        self.assertIsNone(media_index.mission_id)
        self.assertIsNone(media_index.media_file.mission_id)

    def test_sync_media_indexes_should_not_restore_soft_deleted_media_file(self):
        Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-SYNC-DRONE-DEL",
            name="媒体软删无人机",
            model="M30",
            device_sn="MOCK-DRONE-001",
        )
        mock_dji_state.seed_media_file(
            file_id="media-soft-delete-file",
            name="MEDIA_SOFT_DELETE.JPG",
            device_sn="MOCK-DRONE-001",
            job_id="ignored-job-id",
        )
        media_file = MediaFile.objects.create(
            tenant=self.tenant,
            media_type=1,
            file_name="MEDIA_SOFT_DELETE.JPG",
            file_url="dji://media-soft-delete-file",
            is_deleted=True,
            deleted_at=timezone.now(),
        )
        TenantMediaIndex.objects.create(
            tenant=self.tenant,
            media_file=media_file,
            dji_file_id="media-soft-delete-file",
            device_sn="MOCK-DRONE-001",
            sync_status=SyncStatus.SYNCED,
        )

        summary = sync_media_indexes()

        self.assertEqual(summary["updated_count"], 1)
        media_file.refresh_from_db()
        self.assertTrue(media_file.is_deleted)
        self.assertIsNotNone(media_file.deleted_at)

    def test_internal_sync_endpoint_should_require_system_token(self):
        response = self.client.post("/api/v1/__internal__/dji/sync/devices")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "A0403")

    def test_media_callback_should_reject_non_post(self):
        response = self.client.get(
            "/api/v1/__internal__/dji/callbacks/media-upload",
            HTTP_X_DJI_INTERNAL_TOKEN="internal-sync-token",
        )

        self.assertEqual(response.status_code, 404)

    def test_internal_sync_endpoints_should_follow_minimal_contract(self):
        sync_response = self.client.post(
            "/api/v1/__internal__/dji/sync/devices",
            HTTP_X_DJI_INTERNAL_TOKEN="internal-sync-token",
        )
        self.assertEqual(sync_response.status_code, 200)
        self.assertGreaterEqual(sync_response.json()["data"]["synced_count"], 2)

    def test_internal_sync_missions_endpoint_should_be_removed(self):
        response = self.client.post(
            "/api/v1/__internal__/dji/sync/missions",
            HTTP_X_DJI_INTERNAL_TOKEN="internal-sync-token",
        )

        self.assertEqual(response.status_code, 404)

    def test_media_callback_should_mark_existing_index_as_synced(self):
        media_file = MediaFile.objects.create(
            tenant=self.tenant,
            media_type=1,
            file_name="CALLBACK.JPG",
            file_url="dji://callback-file-001",
            captured_at=timezone.now() - timedelta(minutes=1),
        )
        media_index = TenantMediaIndex.objects.create(
            tenant=self.tenant,
            media_file=media_file,
            dji_file_id="callback-file-001",
            device_sn="MOCK-DRONE-001",
            sync_status=SyncStatus.PENDING,
        )

        response = self.client.post(
            "/api/v1/__internal__/dji/callbacks/media-upload",
            data=json.dumps({"ext": {"file_id": "callback-file-001"}, "name": "CALLBACK.JPG"}),
            content_type="application/json",
            HTTP_X_DJI_INTERNAL_TOKEN="internal-sync-token",
        )

        self.assertEqual(response.status_code, 200)
        media_index.refresh_from_db()
        self.assertEqual(media_index.sync_status, SyncStatus.SYNCED)

    def test_scheduler_command_should_run_one_sync_cycle(self):
        stdout = StringIO()
        Drone.objects.create(
            tenant=self.tenant,
            code="SCHEDULER-DRONE-001",
            name="调度器无人机",
            model="M30",
            device_sn="MOCK-DRONE-001",
        )

        call_command("run_dji_sync_scheduler", "--once", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("[cycle 1] start", output)
        self.assertIn("[cycle 1] done", output)
        self.assertTrue(TenantMediaIndex.objects.filter(tenant=self.tenant, dji_file_id="mock-file-001").exists())

    def test_scheduler_command_should_support_bounded_loop_mode(self):
        stdout = StringIO()

        call_command("run_dji_sync_scheduler", "--interval-seconds", "0", "--max-cycles", "2", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("[cycle 1] start", output)
        self.assertIn("[cycle 2] done", output)

    def test_scheduler_command_should_only_report_devices_and_media(self):
        stdout = StringIO()

        call_command("run_dji_sync_scheduler", "--once", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("devices=", output)
        self.assertIn("media=", output)
        self.assertNotIn("missions=", output)
