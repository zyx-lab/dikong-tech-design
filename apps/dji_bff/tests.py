from __future__ import annotations

import json
from io import StringIO
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.access.models import EmploymentStatus
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
)
from apps.dji_bff.gateway import DjiGateway, GatewayResponse
from apps.dji_bff.models import DjiDeviceIndex, SyncStatus, TenantMediaIndex, TenantMissionIndex, TenantRouteIndex
from apps.dji_bff.tasks import sync_device_indexes, sync_media_indexes, sync_mission_indexes
from apps.dji_mock.state import mock_dji_state
from apps.dji_mock.test_support import MockDjiUpstreamTestMixin
from apps.drone.models import Drone, DroneStatus
from apps.media_file.models import MediaFile
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

    def test_sync_device_indexes_should_refresh_shared_pool_and_claimed_drone_status(self):
        visible_drone = Drone.objects.create(
            tenant=self.tenant,
            code="SYNC-DRONE-001",
            name="可见无人机",
            model="M30",
            device_sn="MOCK-DRONE-001",
            status=DroneStatus.DISABLED,
        )
        missing_drone = Drone.objects.create(
            tenant=self.tenant,
            code="SYNC-DRONE-002",
            name="丢失无人机",
            model="M30",
            device_sn="MISSING-DRONE-001",
            status=DroneStatus.ENABLED,
        )

        summary = sync_device_indexes()

        self.assertGreaterEqual(summary["synced_count"], 2)
        visible_drone.refresh_from_db()
        missing_drone.refresh_from_db()
        self.assertEqual(visible_drone.status, DroneStatus.ENABLED)
        self.assertEqual(missing_drone.status, DroneStatus.DISABLED)

    def test_sync_mission_indexes_should_update_execution_status_and_local_status(self):
        route = Route.objects.create(tenant=self.tenant, name="同步任务航线")
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MISSION-SYNC-DRONE",
            name="任务同步无人机",
            model="M30",
            device_sn="MISSION-SYNC-SN",
        )
        job = mock_dji_state.create_job({"name": "同步任务", "dock_sn": "DOCK-001"})
        mock_dji_state.jobs[job["job_id"]]["status"] = "SUCCESS"

        mission = Mission.objects.create(
            tenant=self.tenant,
            name="同步任务",
            route=route,
            route_name=route.name,
            drone=drone,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.PENDING,
            dji_job_id=job["job_id"],
        )
        mission_index = TenantMissionIndex.objects.create(
            tenant=self.tenant,
            mission=mission,
            dji_job_id=job["job_id"],
            execution_status="READY",
            sync_status=SyncStatus.PENDING,
        )

        summary = sync_mission_indexes()

        self.assertEqual(summary["synced_count"], 1)
        mission.refresh_from_db()
        mission_index.refresh_from_db()
        self.assertEqual(mission.status, MissionStatus.COMPLETED)
        self.assertEqual(mission_index.execution_status, "SUCCESS")
        self.assertEqual(mission_index.sync_status, SyncStatus.SYNCED)

    def test_sync_device_indexes_should_disable_all_claimed_drones_when_bound_pool_is_empty(self):
        visible_drone = Drone.objects.create(
            tenant=self.tenant,
            code="SYNC-EMPTY-DRONE-001",
            name="已认领无人机",
            model="M30",
            device_sn="MOCK-DRONE-001",
            status=DroneStatus.ENABLED,
        )
        mock_dji_state.bound_device_sns = set()

        summary = sync_device_indexes()

        self.assertEqual(summary["synced_count"], 0)
        visible_drone.refresh_from_db()
        self.assertEqual(visible_drone.status, DroneStatus.DISABLED)

    def test_sync_device_indexes_should_delete_stale_shared_device_indexes(self):
        DjiDeviceIndex.objects.create(device_sn="STALE-DRONE-001", last_payload={"name": "stale"})
        DjiDeviceIndex.objects.create(device_sn="MOCK-DRONE-001", last_payload={"name": "old visible"})
        mock_dji_state.bound_device_sns = {"MOCK-DRONE-001"}

        summary = sync_device_indexes()

        self.assertEqual(summary["synced_count"], 1)
        self.assertFalse(DjiDeviceIndex.objects.filter(device_sn="STALE-DRONE-001").exists())
        self.assertTrue(DjiDeviceIndex.objects.filter(device_sn="MOCK-DRONE-001").exists())

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
        self.assertTrue(MediaFile.objects.filter(id=media_index.media_file_id, tenant=self.tenant).exists())

    def test_internal_sync_endpoint_should_require_system_token(self):
        response = self.client.post("/api/v1/__internal__/dji/sync/devices")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "A0403")

    def test_internal_sync_and_callback_endpoints_should_follow_minimal_contract(self):
        route = Route.objects.create(tenant=self.tenant, name="回调航线")
        route_index = TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="callback-wayline-001",
            is_published=False,
        )

        sync_response = self.client.post(
            "/api/v1/__internal__/dji/sync/devices",
            HTTP_X_DJI_INTERNAL_TOKEN="internal-sync-token",
        )
        self.assertEqual(sync_response.status_code, 200)
        self.assertGreaterEqual(sync_response.json()["data"]["synced_count"], 2)

        callback_response = self.client.post(
            "/api/v1/__internal__/dji/callbacks/wayline-upload",
            data=json.dumps(
                {
                    "name": "回调航线",
                    "metadata": {"dji_wayline_id": "callback-wayline-001"},
                    "object_key": "waylines/callback-wayline-001.kmz",
                }
            ),
            content_type="application/json",
            HTTP_X_DJI_INTERNAL_TOKEN="internal-sync-token",
        )
        self.assertEqual(callback_response.status_code, 200)
        route_index.refresh_from_db()
        self.assertTrue(route_index.is_published)

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
