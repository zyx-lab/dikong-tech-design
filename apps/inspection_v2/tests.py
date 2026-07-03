import json
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import timedelta, timezone as dt_timezone
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.storage import Storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import DirectoryStatus
from apps.iam_v2.models import (
    Department,
    FixedRole,
    ResourceShareGroup,
    ResourceShareGroupTargetDepartment,
    V2AccountQualification,
    V2AccountProfile,
    V2AccountRoleProfile,
    V2AccountRoleAssignment,
)
from apps.inspection_v2.models import (
    CameraOperation,
    CameraOperationStatus,
    CloudExecutionStatus,
    CloudMediaType,
    CloudMediaFile,
    FlightRecordMediaSyncStatus,
    FlightSession,
    InspectionFlightRecord,
    InspectionFlightRecordMediaSyncState,
    InspectionMission,
    LiveStreamStatus,
    MissionCloudExecution,
    MissionResourceAssignment,
    MissionStatus,
    WaypointRoute,
    WaypointRouteCloudFile,
)
from apps.inspection_v2.services import apply_cloud_execution_event, apply_osd_telemetry
from apps.inspection_v2.management.commands.run_v2_dji_worker import V2DjiWorker
from apps.resource_v2.models import (
    BindingStatus,
    DjiConnection,
    DockResource,
    DroneTelemetrySnapshot,
    DroneResource,
    GatewayResource,
    MqttConnectionHealth,
    MqttLatestMessage,
    ResourceBinding,
    ResourceSharePermission,
    ResourceType,
)
from apps.resource_v2.gateway import DjiGatewayUpstreamError
User = get_user_model()


class MemoryObjectStorage(Storage):
    saved_files: dict[str, bytes] = {}

    def _save(self, name, content):
        self.saved_files[name] = content.read()
        return name

    def exists(self, name):
        return name in self.saved_files

    def url(self, name):
        return f"https://storage.example.test/{name}"


def create_v2_actor(*, username: str, role_code: str | None, department: Department):
    user = User.objects.create_user(username=username, password="pass1234", status=1)
    profile = V2AccountProfile.objects.create(
        user=user,
        department=department,
        name=username,
        phone=f"138{user.id:08d}",
        email=f"{username}@example.test",
    )
    if role_code is not None:
        V2AccountRoleAssignment.objects.create(account_profile=profile, role_code=role_code, assigned_by_user=user)
    return user, profile


class InspectionV2ApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.root = Department.objects.create(name="总部")
        self.owner_department = Department.objects.create(name="资源队", parent=self.root)
        self.other_department = Department.objects.create(name="任务队", parent=self.root)
        self.owner_dispatcher, _ = create_v2_actor(
            username="owner_dispatcher",
            role_code=FixedRole.TASK_MONITOR_DISPATCHER,
            department=self.owner_department,
        )
        self.owner_admin, _ = create_v2_actor(
            username="owner_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=self.owner_department,
        )
        self.other_dispatcher, _ = create_v2_actor(
            username="other_dispatcher",
            role_code=FixedRole.TASK_MONITOR_DISPATCHER,
            department=self.other_department,
        )
        self.owner_pilot_user, self.owner_pilot_account = create_v2_actor(
            username="owner_pilot",
            role_code=FixedRole.PILOT,
            department=self.owner_department,
        )
        self.owner_pilot = self.create_pilot(self.owner_pilot_account, "资源队飞手")
        self.other_pilot_user, self.other_pilot_account = create_v2_actor(
            username="other_pilot",
            role_code=FixedRole.PILOT,
            department=self.other_department,
        )
        self.other_pilot = self.create_pilot(self.other_pilot_account, "任务队飞手")
        self.drone = self.bind_drone(self.owner_department, self.owner_admin, "V2-DRONE-001")
        self.route_upload_counter = 0

    def authenticate(self, user):
        self.client.force_authenticate(user)

    def create_pilot(self, account_profile, name):
        V2AccountRoleProfile.objects.create(
            account_profile=account_profile,
            profile_type=FixedRole.PILOT,
            display_name=name,
            level="A1",
            status=DirectoryStatus.ACTIVE,
            remark="当前有效",
        )
        V2AccountQualification.objects.create(
            account_profile=account_profile,
            profile_type=FixedRole.PILOT,
            qualification_type="多旋翼巡检",
            certificate_no=f"CERT-{account_profile.id}",
            issued_at=timezone.now().date(),
            expires_at=timezone.now().date().replace(year=timezone.now().date().year + 1),
            status=DirectoryStatus.ACTIVE,
            remark="当前有效",
        )
        return account_profile

    def bind_drone(self, department, actor, device_sn):
        connection = DjiConnection.objects.create(
            owner_department=department,
            name=f"{device_sn} connection",
            base_url="https://dji.example.test",
            username="admin",
            password="secret",
            workspace_id="workspace-001",
            access_token="token",
            created_by_user=actor,
        )
        drone = DroneResource.objects.create(device_sn=device_sn, name=f"{device_sn} 无人机", model="M30", online_status=True)
        ResourceBinding.objects.create(
            resource_type=ResourceType.DRONE,
            resource_object_id=drone.id,
            owner_department=department,
            dji_connection=connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=actor,
        )
        return drone

    def bind_gateway(self, department, actor, device_sn, *, connection):
        gateway = GatewayResource.objects.create(device_sn=device_sn, name=f"{device_sn} 执行端", model="RC Plus", online_status=True)
        ResourceBinding.objects.create(
            resource_type=ResourceType.GATEWAY,
            resource_object_id=gateway.id,
            owner_department=department,
            dji_connection=connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=actor,
        )
        return gateway

    def bind_dock(self, department, actor, device_sn, *, connection):
        dock = DockResource.objects.create(device_sn=device_sn, name=f"{device_sn} 机场", model="Dock 2", online_status=True)
        ResourceBinding.objects.create(
            resource_type=ResourceType.DOCK,
            resource_object_id=dock.id,
            owner_department=department,
            dji_connection=connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=actor,
        )
        return dock

    def route_payload(self, name="一号航线"):
        return {
            "name": name,
            "defaultAltitude": "120.00",
            "defaultSpeed": "8.50",
            "waypoints": [
                {
                    "sequence": 1,
                    "latitude": "31.23040000",
                    "longitude": "121.47370000",
                    "altitude": "120.00",
                    "speed": "8.50",
                    "heading": "90.00",
                    "hoverSeconds": 3,
                },
                {
                    "sequence": 2,
                    "latitude": "31.23140000",
                    "longitude": "121.47470000",
                    "altitude": "125.00",
                    "speed": "8.50",
                    "heading": "180.00",
                    "hoverSeconds": 0,
                },
            ],
        }

    def kmz_file(self, name="route.kmz", *, wayline_type=0):
        template_type = {
            0: "waypoint",
            1: "mapping2d",
            2: "mapping3d",
            3: "mappingStrip",
        }[wayline_type]
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "wpmz/template.kml",
                f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="http://www.dji.com/wpmz/1.0.6">
  <Document>
    <Folder>
      <wpml:templateType>{template_type}</wpml:templateType>
      <wpml:autoFlightSpeed>8.50</wpml:autoFlightSpeed>
    </Folder>
  </Document>
</kml>
""",
            )
            archive.writestr(
                "wpmz/waylines.wpml",
                """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="http://www.dji.com/wpmz/1.0.6">
  <Document>
    <Folder>
      <wpml:autoFlightSpeed>8.50</wpml:autoFlightSpeed>
      <Placemark>
        <Point><coordinates>121.47370000,31.23040000</coordinates></Point>
        <wpml:index>0</wpml:index>
        <wpml:executeHeight>120.00</wpml:executeHeight>
        <wpml:waypointSpeed>8.50</wpml:waypointSpeed>
        <wpml:waypointHeadingParam><wpml:waypointHeadingAngle>90.00</wpml:waypointHeadingAngle></wpml:waypointHeadingParam>
      </Placemark>
      <Placemark>
        <Point><coordinates>121.47470000,31.23140000</coordinates></Point>
        <wpml:index>1</wpml:index>
        <wpml:executeHeight>120.00</wpml:executeHeight>
        <wpml:waypointSpeed>8.50</wpml:waypointSpeed>
        <wpml:waypointHeadingParam><wpml:waypointHeadingAngle>180.00</wpml:waypointHeadingAngle></wpml:waypointHeadingParam>
      </Placemark>
    </Folder>
  </Document>
</kml>
""",
            )
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="application/vnd.google-earth.kmz")

    def next_route_upload_payload(self):
        self.route_upload_counter += 1
        wayline_id = f"wayline-kmz-{self.route_upload_counter:03d}"
        return {"dji_wayline_id": wayline_id, "download_url": f"/waylines/{wayline_id}/url"}

    def signed_route_download_url(self, wayline_id: str, *, issued_at=None, expires: int = 3600) -> str:
        issued_at = issued_at or timezone.now()
        amz_date = issued_at.astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return (
            f"https://dji-download.example.test/waylines/{wayline_id}.kmz"
            f"?X-Amz-Date={amz_date}&X-Amz-Expires={expires}&X-Amz-Signature=test-signature"
        )

    def signed_media_preview_url(self, *, issued_at=None, expires: int = 21600) -> str:
        issued_at = issued_at or timezone.now()
        amz_date = issued_at.astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return (
            "https://media.example.test/preview.jpg"
            f"?X-Amz-Date={amz_date}&X-Amz-Expires={expires}&X-Amz-Signature=test-signature"
        )

    def signed_media_playback_url(self, *, issued_at=None, expires: int = 21600) -> str:
        issued_at = issued_at or timezone.now()
        amz_date = issued_at.astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return (
            "https://media.example.test/playback.m3u8"
            f"?X-Amz-Date={amz_date}&X-Amz-Expires={expires}&X-Amz-Signature=test-signature"
        )

    @contextmanager
    def route_upload_mock(self, upload_payload: dict | None = None):
        upload_payload = upload_payload or self.next_route_upload_payload()
        with patch(
            "apps.inspection_v2.views.DjiConnectionGateway.upload_route",
            return_value=upload_payload,
        ) as upload_route, patch(
            "apps.inspection_v2.views.DjiConnectionGateway.get_route_download_url",
            return_value=self.signed_route_download_url(upload_payload["dji_wayline_id"]),
        ) as get_download_url:
            yield upload_route, get_download_url

    def dji_connection_for_user(self, user):
        department = user.v2_account_profile.department
        connection = DjiConnection.objects.filter(owner_department=department).first()
        if connection is not None:
            return connection
        return DjiConnection.objects.create(
            owner_department=department,
            name=f"{department.id} test connection",
            base_url="https://dji.example.test",
            username="admin",
            password="secret",
            workspace_id=f"workspace-{department.id}",
            access_token="token",
            created_by_user=user,
        )

    def route_multipart_payload(self, user, name="一号航线", *, wayline_type=0):
        payload = {"name": name}
        payload["djiConnectionId"] = self.dji_connection_for_user(user).id
        payload["kmzFile"] = self.kmz_file(wayline_type=wayline_type)
        return payload

    def create_route_by_api(self, user, name="一号航线"):
        self.authenticate(user)
        with self.route_upload_mock():
            response = self.client.post("/api/v2/inspection/routes", self.route_multipart_payload(user, name), format="multipart")
        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        return response.data["data"]

    def storage_settings(self, media_root):
        return self.settings(
            MEDIA_ROOT=media_root,
            STORAGES={
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                    "OPTIONS": {
                        "location": media_root,
                        "base_url": "/media/",
                    },
                },
                "staticfiles": {
                    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
                },
            },
        )

    def create_mission_by_api(self, user, *, route_id, drone_id, pilot_id, dock_id=None, executor_id=None, name="一号任务"):
        self.authenticate(user)
        payload = {
            "name": name,
            "routeId": route_id,
            "droneId": drone_id,
            "pilotAccountProfileId": pilot_id,
            "remark": "首版闭环任务",
        }
        if dock_id is None and executor_id is None:
            connection = WaypointRouteCloudFile.objects.get(route_id=route_id).dji_connection
            executor = self.bind_gateway(
                self.owner_department,
                self.owner_admin,
                f"GATEWAY-AUTO-{route_id}-{drone_id}-{pilot_id}",
                connection=connection,
            )
            executor_id = executor.id
        if dock_id is not None:
            payload["dockId"] = dock_id
        if executor_id is not None:
            payload["executorId"] = executor_id
        response = self.client.post(
            "/api/v2/inspection/missions",
            payload,
            format="json",
        )
        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        return response.data["data"]

    def create_visible_media_file(
        self,
        *,
        cloud_file_id: str,
        preview_url: str = "",
        playback_url: str = "",
        thumbnail_url: str = "",
        media_type: str = CloudMediaType.PHOTO,
        file_name: str | None = None,
    ):
        route = self.create_route_by_api(self.owner_dispatcher, name=f"媒体预览 {cloud_file_id}")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            pilot_id=self.owner_pilot.id,
        )
        return CloudMediaFile.objects.create(
            workspace_id="workspace-001",
            mission_id=mission["id"],
            device_sn=self.drone.device_sn,
            cloud_file_id=cloud_file_id,
            media_type=media_type,
            file_name=file_name or f"{cloud_file_id}.jpg",
            thumbnail_url=thumbnail_url,
            preview_url=preview_url,
            playback_url=playback_url,
        )

    def upload_route_kmz_by_api(self, route_id, connection, *, wayline_type=0):
        self.authenticate(self.owner_dispatcher)
        route = WaypointRoute.objects.get(pk=route_id)
        payload = {
            "name": route.name,
            "djiConnectionId": connection.id,
            "kmzFile": self.kmz_file(wayline_type=wayline_type),
        }
        with self.route_upload_mock():
            response = self.client.put(f"/api/v2/inspection/routes/{route_id}", payload, format="multipart")
        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        return response.data["data"]["djiFile"]

    def prepare_route_for_cloud_execution(self, route, *, gateway_sn="GATEWAY-TEST-001"):
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        connection.workspace_id = f"workspace-{gateway_sn.lower()}"
        connection.save(update_fields=["workspace_id", "updated_at"])
        executor = self.bind_gateway(self.owner_department, self.owner_admin, gateway_sn, connection=connection)
        self.upload_route_kmz_by_api(route["id"], connection)
        return connection, executor

    def prepare_route_for_dock_execution(self, route, *, dock_sn="DOCK-TEST-001"):
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        connection.workspace_id = f"workspace-{dock_sn.lower()}"
        connection.save(update_fields=["workspace_id", "updated_at"])
        dock = self.bind_dock(self.owner_department, self.owner_admin, dock_sn, connection=connection)
        self.upload_route_kmz_by_api(route["id"], connection)
        return connection, dock

    def start_cloud_mission_by_api(self, mission_id, *, dji_job_id):
        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_live_capacity",
            return_value={"cameras_list": [{"index": "88-0-0", "videos_list": [{"index": "normal-0"}]}]},
        ), patch(
            "apps.inspection_v2.services.DjiConnectionGateway.start_live",
            return_value={
                "rtmp_url": "rtmp://live.example.test/app",
                "webrtc_url": "https://live.example.test/webrtc",
            },
        ), patch("apps.inspection_v2.services.DjiConnectionGateway.create_dock_flight_task", return_value={"dji_job_id": dji_job_id}):
            response = self.client.post(f"/api/v2/inspection/missions/{mission_id}/start", {}, format="json")
        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        return response

    def test_route_create_should_upload_kmz_and_return_dji_file(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="合并创建航线")

        self.assertEqual(route["name"], "合并创建航线")
        self.assertEqual(route["defaultAltitude"], "120.00")
        self.assertEqual(route["defaultSpeed"], "8.50")
        self.assertEqual(len(route["waypoints"]), 2)
        self.assertEqual(route["djiFile"]["djiConnectionId"], self.dji_connection_for_user(self.owner_dispatcher).id)
        self.assertRegex(route["djiFile"]["djiFileId"], r"^wayline-kmz-\d{3}$")

    def test_route_create_should_return_absolute_dji_download_url_and_expiry(self):
        self.authenticate(self.owner_dispatcher)
        payload = self.route_multipart_payload(self.owner_dispatcher, name="直链航线")
        upload_payload = {"dji_wayline_id": "wayline-direct-001", "download_url": "/api/v1/wayline/workspaces/ws/waylines/wayline-direct-001/url"}
        absolute_url = self.signed_route_download_url("wayline-direct-001")

        with patch(
            "apps.inspection_v2.views.DjiConnectionGateway.upload_route",
            return_value=upload_payload,
        ) as upload_route, patch(
            "apps.inspection_v2.views.DjiConnectionGateway.get_route_download_url",
            return_value=absolute_url,
        ) as get_download_url:
            response = self.client.post("/api/v2/inspection/routes", payload, format="multipart")

        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        dji_file = response.data["data"]["djiFile"]
        self.assertEqual(dji_file["downloadUrl"], absolute_url)
        self.assertTrue(dji_file["downloadUrlExpiresAt"])
        persisted = WaypointRouteCloudFile.objects.get(route_id=response.data["data"]["id"])
        self.assertEqual(persisted.download_url, absolute_url)
        self.assertIsNotNone(persisted.download_url_expires_at)
        upload_route.assert_called_once()
        get_download_url.assert_called_once_with("wayline-direct-001")

    def test_route_detail_should_refresh_relative_dji_download_url(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="详情刷新航线")
        cloud_file = WaypointRouteCloudFile.objects.get(route_id=route["id"])
        cloud_file.download_url = "/api/v1/wayline/workspaces/ws/waylines/wayline-refresh-001/url"
        cloud_file.download_url_expires_at = None
        cloud_file.save(update_fields=["download_url", "download_url_expires_at", "updated_at"])
        absolute_url = self.signed_route_download_url(cloud_file.dji_file_id)

        self.authenticate(self.owner_dispatcher)
        with patch(
            "apps.inspection_v2.views.DjiConnectionGateway.get_route_download_url",
            return_value=absolute_url,
        ) as get_download_url:
            response = self.client.get(f"/api/v2/inspection/routes/{route['id']}")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["djiFile"]["downloadUrl"], absolute_url)
        self.assertTrue(response.data["data"]["djiFile"]["downloadUrlExpiresAt"])
        cloud_file.refresh_from_db()
        self.assertEqual(cloud_file.download_url, absolute_url)
        self.assertIsNotNone(cloud_file.download_url_expires_at)
        get_download_url.assert_called_once_with(cloud_file.dji_file_id)

    def test_route_list_should_not_refresh_cached_dji_download_url(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="列表不刷新航线")
        cloud_file = WaypointRouteCloudFile.objects.get(route_id=route["id"])
        cloud_file.download_url = "/api/v1/wayline/workspaces/ws/waylines/wayline-list-001/url"
        cloud_file.download_url_expires_at = None
        cloud_file.save(update_fields=["download_url", "download_url_expires_at", "updated_at"])

        self.authenticate(self.owner_dispatcher)
        with patch("apps.inspection_v2.views.DjiConnectionGateway.get_route_download_url") as get_download_url:
            response = self.client.get("/api/v2/inspection/routes", {"keywords": "列表不刷新航线"})

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["list"][0]["djiFile"]["downloadUrl"], cloud_file.download_url)
        get_download_url.assert_not_called()

    def test_route_create_should_roll_back_when_dji_download_url_refresh_fails(self):
        self.authenticate(self.owner_dispatcher)
        payload = self.route_multipart_payload(self.owner_dispatcher, name="直链失败航线")

        with patch(
            "apps.inspection_v2.views.DjiConnectionGateway.upload_route",
            return_value={"dji_wayline_id": "wayline-refresh-fail", "download_url": "/waylines/wayline-refresh-fail/url"},
        ), patch(
            "apps.inspection_v2.views.DjiConnectionGateway.get_route_download_url",
            side_effect=DjiGatewayUpstreamError("未获取到航线下载地址", status_code=502),
        ), patch("apps.inspection_v2.views.DjiConnectionGateway.delete_route", return_value={}) as delete_route:
            response = self.client.post("/api/v2/inspection/routes", payload, format="multipart")

        self.assertEqual(response.status_code, 502, getattr(response, "data", response.content))
        self.assertFalse(WaypointRoute.objects.filter(name="直链失败航线").exists())
        self.assertFalse(WaypointRouteCloudFile.objects.filter(dji_file_id="wayline-refresh-fail").exists())
        delete_route.assert_called_once_with("wayline-refresh-fail")

    def test_migrate_route_covers_to_object_storage_should_upload_existing_local_covers(self):
        MemoryObjectStorage.saved_files = {}
        cover_name = "inspection/routes/covers/route-new/local-cover.png"
        WaypointRoute.objects.create(
            owner_department=self.owner_department,
            name="旧封面迁移航线",
            cover_image=cover_name,
            created_by_user=self.owner_dispatcher,
        )

        with tempfile.TemporaryDirectory() as media_root, self.settings(
            MEDIA_ROOT=Path(media_root),
            STORAGES={
                "default": {"BACKEND": "apps.inspection_v2.tests.MemoryObjectStorage"},
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            },
        ):
            local_cover = Path(media_root) / cover_name
            local_cover.parent.mkdir(parents=True, exist_ok=True)
            local_cover.write_bytes(b"\x89PNG\r\n\x1a\n")

            call_command("migrate_route_covers_to_object_storage", stdout=StringIO())

        self.assertEqual(MemoryObjectStorage.saved_files[cover_name], b"\x89PNG\r\n\x1a\n")

    def test_refresh_route_kmz_download_urls_should_update_existing_relative_urls(self):
        route = WaypointRoute.objects.create(
            owner_department=self.owner_department,
            name="旧 KMZ 刷新航线",
            created_by_user=self.owner_dispatcher,
        )
        connection = self.dji_connection_for_user(self.owner_dispatcher)
        cloud_file = WaypointRouteCloudFile.objects.create(
            route=route,
            dji_connection=connection,
            workspace_id=connection.workspace_id,
            dji_file_id="wayline-refresh-command",
            wayline_type=0,
            download_url="/api/v1/wayline/workspaces/ws/waylines/wayline-refresh-command/url",
        )
        absolute_url = self.signed_route_download_url(cloud_file.dji_file_id)

        with patch(
            "apps.inspection_v2.management.commands.refresh_route_kmz_download_urls.DjiConnectionGateway.get_route_download_url",
            return_value=absolute_url,
        ) as get_download_url:
            call_command("refresh_route_kmz_download_urls", stdout=StringIO())

        cloud_file.refresh_from_db()
        self.assertEqual(cloud_file.download_url, absolute_url)
        self.assertIsNotNone(cloud_file.download_url_expires_at)
        get_download_url.assert_called_once_with("wayline-refresh-command")

    def test_route_json_create_should_be_rejected_because_kmz_is_required(self):
        self.authenticate(self.owner_dispatcher)
        response = self.client.post(
            "/api/v2/inspection/routes",
            {"name": "JSON 创建航线", "djiConnectionId": self.dji_connection_for_user(self.owner_dispatcher).id},
            format="json",
        )

        self.assertEqual(response.status_code, 400, getattr(response, "data", response.content))
        self.assertIn("kmzFile", str(response.data))

    def test_route_json_update_should_allow_display_metadata_only(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="JSON 更新基线航线")
        payload = {"name": "JSON 更新后航线", "remark": "只改展示字段", "status": DirectoryStatus.ACTIVE}

        self.authenticate(self.owner_dispatcher)
        response = self.client.put(f"/api/v2/inspection/routes/{route['id']}", payload, format="json")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["name"], "JSON 更新后航线")
        self.assertEqual(response.data["data"]["defaultAltitude"], "120.00")
        self.assertEqual(response.data["data"]["remark"], "只改展示字段")
        self.assertEqual(len(response.data["data"]["waypoints"]), 2)

    def test_route_metadata_only_update_should_reject_execution_fields_without_kmz(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="无 KMZ 更新航线")
        payload = self.route_payload(name="无 KMZ 更新后航线")

        self.authenticate(self.owner_dispatcher)
        response = self.client.put(f"/api/v2/inspection/routes/{route['id']}", payload, format="json")

        self.assertEqual(response.status_code, 400, getattr(response, "data", response.content))
        self.assertIn("kmzFile", str(response.data))

    def test_route_save_should_accept_cover_image_and_return_cover_image_url(self):
        self.authenticate(self.owner_dispatcher)
        payload = self.route_multipart_payload(self.owner_dispatcher, name="封面航线")
        payload["coverImage"] = SimpleUploadedFile("cover.jpg", b"\xff\xd8\xff\xd9", content_type="image/jpeg")

        with tempfile.TemporaryDirectory() as media_root, self.storage_settings(media_root):
            with self.route_upload_mock():
                response = self.client.post("/api/v2/inspection/routes", payload, format="multipart")

        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        self.assertIn("coverImageUrl", response.data["data"])
        self.assertRegex(response.data["data"]["coverImageUrl"], r"\.(jpg|jpeg|png|webp)$")

    def test_route_save_should_reject_non_image_cover_file(self):
        self.authenticate(self.owner_dispatcher)
        payload = self.route_multipart_payload(self.owner_dispatcher, name="非法封面航线")
        payload["coverImage"] = SimpleUploadedFile("cover.txt", b"not an image", content_type="text/plain")

        with tempfile.TemporaryDirectory() as media_root, self.storage_settings(media_root):
            response = self.client.post("/api/v2/inspection/routes", payload, format="multipart")

        self.assertEqual(response.status_code, 400, getattr(response, "data", response.content))
        self.assertIn("coverImage", str(response.data))
        self.assertIn("只支持上传 jpg/jpeg/png/webp 图片，且大小不能超过 5MB", str(response.data))

    def test_route_multipart_should_reject_client_supplied_waypoints(self):
        self.authenticate(self.owner_dispatcher)
        payload = self.route_multipart_payload(self.owner_dispatcher, name="前端航点字段")
        payload["waypoints"] = json.dumps(self.route_payload()["waypoints"])
        payload["coverImage"] = SimpleUploadedFile("cover.jpg", b"\xff\xd8\xff\xd9", content_type="image/jpeg")

        with tempfile.TemporaryDirectory() as media_root, self.storage_settings(media_root):
            response = self.client.post("/api/v2/inspection/routes", payload, format="multipart")

        self.assertEqual(response.status_code, 400, getattr(response, "data", response.content))
        self.assertIn("waypoints", str(response.data))

    def test_route_update_should_replace_cover_image_and_preserve_when_omitted(self):
        self.authenticate(self.owner_dispatcher)
        create_payload = self.route_multipart_payload(self.owner_dispatcher, name="封面替换航线")
        create_payload["coverImage"] = SimpleUploadedFile("cover-a.jpg", b"\xff\xd8\xff\xd9", content_type="image/jpeg")

        with tempfile.TemporaryDirectory() as media_root, self.storage_settings(media_root):
            with self.route_upload_mock():
                create_response = self.client.post("/api/v2/inspection/routes", create_payload, format="multipart")
            self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
            route_id = create_response.data["data"]["id"]
            first_url = create_response.data["data"]["coverImageUrl"]
            route = WaypointRoute.objects.get(pk=route_id)
            first_cover_name = route.cover_image.name

            json_payload = {"name": "封面保留航线"}
            preserve_response = self.client.put(f"/api/v2/inspection/routes/{route_id}", json_payload, format="json")
            self.assertEqual(preserve_response.status_code, 200, getattr(preserve_response, "data", preserve_response.content))
            self.assertEqual(preserve_response.data["data"]["coverImageUrl"], first_url)

            empty_cover_payload = {"name": "封面空字段保留航线"}
            empty_cover_payload["coverImage"] = ""
            empty_cover_response = self.client.put(f"/api/v2/inspection/routes/{route_id}", empty_cover_payload, format="multipart")
            self.assertEqual(empty_cover_response.status_code, 200, getattr(empty_cover_response, "data", empty_cover_response.content))
            self.assertEqual(empty_cover_response.data["data"]["coverImageUrl"], first_url)

            replace_payload = {"name": "封面替换后航线"}
            replace_payload["coverImage"] = SimpleUploadedFile("cover-b.png", b"\x89PNG\r\n\x1a\n", content_type="image/png")
            replace_response = self.client.put(f"/api/v2/inspection/routes/{route_id}", replace_payload, format="multipart")

            self.assertEqual(replace_response.status_code, 200, getattr(replace_response, "data", replace_response.content))
            self.assertIn("coverImageUrl", replace_response.data["data"])
            self.assertNotEqual(replace_response.data["data"]["coverImageUrl"], first_url)
            route.refresh_from_db()
            self.assertNotEqual(route.cover_image.name, first_cover_name)
            self.assertFalse(route.cover_image.storage.exists(first_cover_name))

    def test_route_json_metadata_update_should_preserve_cover_when_omitted(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="无封面 JSON 航线")
        self.assertEqual(route["coverImageUrl"], "")

        payload = {"name": "无封面 JSON 更新航线"}
        self.authenticate(self.owner_dispatcher)
        response = self.client.put(f"/api/v2/inspection/routes/{route['id']}", payload, format="json")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["coverImageUrl"], "")

    def test_mission_route_snapshot_should_include_route_cover_image_url(self):
        self.authenticate(self.owner_dispatcher)
        route_payload = self.route_multipart_payload(self.owner_dispatcher, name="任务封面航线")
        route_payload["coverImage"] = SimpleUploadedFile("mission-cover.webp", b"RIFFxxxxWEBP", content_type="image/webp")

        with tempfile.TemporaryDirectory() as media_root, self.storage_settings(media_root):
            with self.route_upload_mock():
                route_response = self.client.post("/api/v2/inspection/routes", route_payload, format="multipart")
            self.assertEqual(route_response.status_code, 201, getattr(route_response, "data", route_response.content))
            route = route_response.data["data"]

            mission = self.create_mission_by_api(
                self.owner_dispatcher,
                route_id=route["id"],
                drone_id=self.drone.id,
                pilot_id=self.owner_pilot.id,
                name="任务封面快照",
            )

        self.assertEqual(mission["routeSnapshot"]["coverImageUrl"], route["coverImageUrl"])
        self.assertEqual(mission["routeSnapshot"]["djiFile"]["djiFileId"], route["djiFile"]["djiFileId"])
        self.assertEqual(mission["executionMode"], "PILOT2_MANUAL")

    def test_mission_create_should_reject_route_cloud_file_from_different_dji_connection(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="连接不一致航线")
        drone_connection = ResourceBinding.objects.get(resource_type=ResourceType.DRONE, resource_object_id=self.drone.id).dji_connection
        other_connection = DjiConnection.objects.create(
            owner_department=self.owner_department,
            name="route other connection",
            base_url="https://dji-other.example.test",
            username="admin",
            password="secret",
            workspace_id="workspace-route-other",
            access_token="token",
            created_by_user=self.owner_admin,
        )
        self.upload_route_kmz_by_api(route["id"], other_connection)
        executor = self.bind_gateway(self.owner_department, self.owner_admin, "GATEWAY-ROUTE-MISMATCH-001", connection=drone_connection)

        self.authenticate(self.owner_dispatcher)
        response = self.client.post(
            "/api/v2/inspection/missions",
            {
                "name": "连接不一致任务",
                "routeId": route["id"],
                "droneId": self.drone.id,
                "executorId": executor.id,
                "pilotAccountProfileId": self.owner_pilot.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 409, getattr(response, "data", response.content))
        self.assertIn("任务航线尚未同步到当前 DJI 连接", str(response.data))

    def test_mission_create_should_reject_dock_from_different_dji_connection(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="机场连接不一致航线")
        other_connection = DjiConnection.objects.create(
            owner_department=self.owner_department,
            name="dock other connection",
            base_url="https://dji-dock-other.example.test",
            username="admin",
            password="secret",
            workspace_id="workspace-dock-other",
            access_token="token",
            created_by_user=self.owner_admin,
        )
        dock = self.bind_dock(self.owner_department, self.owner_admin, "DOCK-MISMATCH-001", connection=other_connection)

        self.authenticate(self.owner_dispatcher)
        response = self.client.post(
            "/api/v2/inspection/missions",
            {
                "name": "机场连接不一致任务",
                "routeId": route["id"],
                "droneId": self.drone.id,
                "dockId": dock.id,
                "pilotAccountProfileId": self.owner_pilot.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 409, getattr(response, "data", response.content))
        self.assertIn("机场必须与无人机属于同一个 DJI 连接", str(response.data))

    def test_mission_lifecycle_should_create_session_record_and_cloud_media(self):
        route = self.create_route_by_api(self.owner_dispatcher)
        _connection, dock = self.prepare_route_for_dock_execution(route, dock_sn="DOCK-LIFECYCLE-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            dock_id=dock.id,
            pilot_id=self.owner_pilot.id,
        )

        start_response = self.start_cloud_mission_by_api(mission["id"], dji_job_id="dji-job-lifecycle")
        self.assertEqual(start_response.data["data"]["status"], MissionStatus.RUNNING)

        active_response = self.client.get("/api/v2/inspection/active-flights")
        self.assertEqual(active_response.status_code, 200, getattr(active_response, "data", active_response.content))
        self.assertEqual(active_response.data["data"]["total"], 1)
        session_id = active_response.data["data"]["list"][0]["id"]
        self.assertEqual(active_response.data["data"]["list"][0]["liveStatus"], LiveStreamStatus.RUNNING)
        self.assertEqual(
            active_response.data["data"]["list"][0]["liveVideoId"],
            f"{self.drone.device_sn}/88-0-0/normal-0",
        )

        summary_response = self.client.get("/api/v2/resource/summary")
        self.assertEqual(summary_response.status_code, 200, getattr(summary_response, "data", summary_response.content))
        self.assertEqual(summary_response.data["data"]["drones"]["occupied"], 1)
        self.assertEqual(summary_response.data["data"]["pilots"]["total"], 1)
        self.assertEqual(
            summary_response.data["data"]["departments"][0],
            {
                "departmentId": self.owner_department.id,
                "departmentName": self.owner_department.name,
                "departmentPath": self.owner_department.path,
                "drones": 1,
                "docks": 1,
                "gateways": 0,
                "payloads": 0,
                "pilots": 1,
            },
        )

        telemetry_response = self.client.post(
            "/api/v2/inspection/telemetry/snapshots",
            {
                "sessionId": session_id,
                "latitude": "31.23040000",
                "longitude": "121.47370000",
                "altitude": "120.00",
                "speed": "9.10",
                "heading": "91.00",
                "batteryPercent": 88,
            },
            format="json",
        )
        self.assertEqual(telemetry_response.status_code, 200, getattr(telemetry_response, "data", telemetry_response.content))
        self.assertEqual(telemetry_response.data["data"]["batteryPercent"], 88)

        captured_at = timezone.now().isoformat()
        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            return_value=[
                {
                    "file_id": "cloud-photo-001",
                    "job_id": "dji-job-lifecycle",
                    "device_sn": self.drone.device_sn,
                    "fileName": "inspection.jpg",
                    "mediaType": "photo",
                    "capturedAt": captured_at,
                    "thumbnailUrl": "https://media.example.test/inspection-thumb.jpg",
                    "previewUrl": "https://media.example.test/inspection-preview.jpg",
                    "downloadUrl": "https://media.example.test/inspection.jpg",
                }
            ],
        ), patch("apps.inspection_v2.services.DjiConnectionGateway.stop_live", return_value={}) as stop_live:
            complete_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/complete", {}, format="json")

        self.assertEqual(complete_response.status_code, 200, getattr(complete_response, "data", complete_response.content))
        self.assertEqual(complete_response.data["data"]["photoCount"], 1)
        self.assertEqual(CloudMediaFile.objects.filter(cloud_file_id="cloud-photo-001").count(), 1)
        stop_live.assert_called_once_with(self.drone.device_sn, video_id=f"{self.drone.device_sn}/88-0-0/normal-0")

        media_response = self.client.get("/api/v2/inspection/media-files")
        self.assertEqual(media_response.status_code, 200, getattr(media_response, "data", media_response.content))
        self.assertEqual(media_response.data["data"]["total"], 1)

    def test_route_create_should_publish_to_selected_dji_connection(self):
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        connection.workspace_id = "workspace-kmz-001"
        connection.save(update_fields=["workspace_id", "updated_at"])

        self.authenticate(self.owner_dispatcher)
        payload = self.route_multipart_payload(self.owner_dispatcher, name="KMZ 航线", wayline_type=2)
        with self.route_upload_mock({"dji_wayline_id": "wayline-kmz-001", "download_url": "/waylines/wayline-kmz-001/url"}) as (
            upload_route,
            _get_download_url,
        ):
            response = self.client.post("/api/v2/inspection/routes", payload, format="multipart")

        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["djiFile"]["djiFileId"], "wayline-kmz-001")
        self.assertEqual(response.data["data"]["djiFile"]["workspaceId"], "workspace-kmz-001")
        self.assertEqual(response.data["data"]["djiFile"]["waylineType"], 2)
        upload_route.assert_called_once()
        upload_kwargs = upload_route.call_args.kwargs
        self.assertNotIn("_", upload_kwargs["route_name"])
        self.assertNotIn("_", upload_kwargs["file_obj"].name)
        self.assertRegex(upload_kwargs["route_name"], r"^v2-route-\d+-[0-9a-f]{8}$")

    def test_route_create_should_reject_old_execution_fields_and_invalid_kmz(self):
        self.authenticate(self.owner_dispatcher)
        old_payload = self.route_multipart_payload(self.owner_dispatcher, name="旧字段航线")
        old_payload["waylineType"] = 0
        old_payload["waypoints"] = json.dumps(self.route_payload()["waypoints"])
        old_payload["defaultAltitude"] = "120.00"
        old_response = self.client.post("/api/v2/inspection/routes", old_payload, format="multipart")

        self.assertEqual(old_response.status_code, 400, getattr(old_response, "data", old_response.content))
        self.assertIn("waylineType", str(old_response.data))
        self.assertIn("waypoints", str(old_response.data))
        self.assertIn("defaultAltitude", str(old_response.data))

        invalid_kmz_payload = self.route_multipart_payload(self.owner_dispatcher, name="非法 KMZ")
        invalid_kmz_payload["kmzFile"] = SimpleUploadedFile(
            "route.kmz",
            b"not-a-zip",
            content_type="application/vnd.google-earth.kmz",
        )
        invalid_kmz_response = self.client.post("/api/v2/inspection/routes", invalid_kmz_payload, format="multipart")

        self.assertEqual(invalid_kmz_response.status_code, 400, getattr(invalid_kmz_response, "data", invalid_kmz_response.content))
        self.assertIn("有效的 KMZ/ZIP 文件", str(invalid_kmz_response.data))

    def test_route_kmz_endpoint_should_be_removed(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="旧 KMZ 接口航线")

        self.authenticate(self.owner_dispatcher)
        response = self.client.post(
            f"/api/v2/inspection/routes/{route['id']}/kmz",
            {
                "djiConnectionId": self.dji_connection_for_user(self.owner_dispatcher).id,
                "waylineType": 0,
                "kmzFile": self.kmz_file(),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 404, getattr(response, "data", response.content))

    def test_route_put_with_kmz_should_replace_dji_file_and_cleanup_old_file(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="替换 KMZ 航线")
        route_id = route["id"]
        old_file_id = route["djiFile"]["djiFileId"]
        payload = self.route_multipart_payload(self.owner_dispatcher, name="替换 KMZ 后航线", wayline_type=3)

        self.authenticate(self.owner_dispatcher)
        with self.route_upload_mock(
            {"dji_wayline_id": "wayline-kmz-replaced", "download_url": "/waylines/wayline-kmz-replaced/url"}
        ) as (upload_route, _get_download_url), patch(
            "apps.inspection_v2.views.DjiConnectionGateway.delete_route", return_value={}
        ) as delete_route:
            response = self.client.put(f"/api/v2/inspection/routes/{route_id}", payload, format="multipart")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["name"], "替换 KMZ 后航线")
        self.assertEqual(response.data["data"]["defaultAltitude"], "120.00")
        self.assertEqual(response.data["data"]["djiFile"]["waylineType"], 3)
        self.assertEqual(response.data["data"]["djiFile"]["djiFileId"], "wayline-kmz-replaced")
        upload_route.assert_called_once()
        delete_route.assert_called_once_with(old_file_id)
        self.assertEqual(WaypointRouteCloudFile.objects.get(route_id=route_id).dji_file_id, "wayline-kmz-replaced")

    def test_route_update_should_be_rejected_when_pending_or_running_mission_references_route(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="任务引用航线")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            pilot_id=self.owner_pilot.id,
        )

        self.authenticate(self.owner_dispatcher)
        metadata_response = self.client.put(
            f"/api/v2/inspection/routes/{route['id']}",
            {"name": "被任务引用后的航线名"},
            format="json",
        )

        self.assertEqual(metadata_response.status_code, 409, getattr(metadata_response, "data", metadata_response.content))
        self.assertIn("待执行或执行中的任务", str(metadata_response.data))

        kmz_payload = self.route_multipart_payload(self.owner_dispatcher, name="被任务引用后的 KMZ 航线")
        with patch("apps.inspection_v2.views.DjiConnectionGateway.upload_route") as upload_route:
            kmz_response = self.client.put(f"/api/v2/inspection/routes/{route['id']}", kmz_payload, format="multipart")

        self.assertEqual(kmz_response.status_code, 409, getattr(kmz_response, "data", kmz_response.content))
        upload_route.assert_not_called()
        self.assertEqual(InspectionMission.objects.get(pk=mission["id"]).status, MissionStatus.PENDING)

    def test_route_delete_should_remove_local_route_and_cleanup_dji_file(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="删除航线")
        route_id = route["id"]
        dji_file_id = route["djiFile"]["djiFileId"]

        self.authenticate(self.owner_dispatcher)
        with patch("apps.inspection_v2.views.DjiConnectionGateway.delete_route", return_value={}) as delete_route:
            response = self.client.delete(f"/api/v2/inspection/routes/{route_id}")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["id"], route_id)
        self.assertTrue(response.data["data"]["deleted"])
        self.assertFalse(WaypointRoute.objects.filter(pk=route_id).exists())
        self.assertFalse(WaypointRouteCloudFile.objects.filter(route_id=route_id).exists())
        delete_route.assert_called_once_with(dji_file_id)

    def test_route_delete_should_reject_route_referenced_by_any_mission(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="被任务引用不能删")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            pilot_id=self.owner_pilot.id,
        )
        InspectionMission.objects.filter(pk=mission["id"]).update(status=MissionStatus.COMPLETED)

        self.authenticate(self.owner_dispatcher)
        with patch("apps.inspection_v2.views.DjiConnectionGateway.delete_route") as delete_route:
            response = self.client.delete(f"/api/v2/inspection/routes/{route['id']}")

        self.assertEqual(response.status_code, 409, getattr(response, "data", response.content))
        self.assertIn("任务引用", str(response.data))
        self.assertTrue(WaypointRoute.objects.filter(pk=route["id"]).exists())
        delete_route.assert_not_called()

    def test_route_delete_should_require_edit_permission_on_owner_department(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="他人不可删航线")

        self.authenticate(self.other_dispatcher)
        response = self.client.delete(f"/api/v2/inspection/routes/{route['id']}")

        self.assertEqual(response.status_code, 404, getattr(response, "data", response.content))
        self.assertTrue(WaypointRoute.objects.filter(pk=route["id"]).exists())

    def test_mission_delete_should_remove_pending_mission_without_canceling_dji_job(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="删除任务航线")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            pilot_id=self.owner_pilot.id,
        )
        mission_id = mission["id"]
        self.assertTrue(MissionResourceAssignment.objects.filter(mission_id=mission_id).exists())

        self.authenticate(self.owner_dispatcher)
        with patch("apps.inspection_v2.views.DjiConnectionGateway.cancel_mission") as cancel_mission:
            response = self.client.delete(f"/api/v2/inspection/missions/{mission_id}")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"], {"id": mission_id, "deleted": True})
        self.assertFalse(InspectionMission.objects.filter(pk=mission_id).exists())
        self.assertFalse(MissionResourceAssignment.objects.filter(mission_id=mission_id).exists())
        cancel_mission.assert_not_called()

    def test_mission_delete_should_reject_non_pending_mission(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="终态任务不能删航线")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            pilot_id=self.owner_pilot.id,
        )
        InspectionMission.objects.filter(pk=mission["id"]).update(status=MissionStatus.COMPLETED)

        self.authenticate(self.owner_dispatcher)
        response = self.client.delete(f"/api/v2/inspection/missions/{mission['id']}")

        self.assertEqual(response.status_code, 409, getattr(response, "data", response.content))
        self.assertIn("待执行任务", str(response.data))
        self.assertTrue(InspectionMission.objects.filter(pk=mission["id"]).exists())

    def test_mission_delete_should_require_creator_department_dispatcher(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="创建部门才能删任务航线")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            pilot_id=self.owner_pilot.id,
        )
        root_dispatcher, _ = create_v2_actor(
            username="root_dispatcher",
            role_code=FixedRole.TASK_MONITOR_DISPATCHER,
            department=self.root,
        )

        self.authenticate(root_dispatcher)
        response = self.client.delete(f"/api/v2/inspection/missions/{mission['id']}")

        self.assertEqual(response.status_code, 403, getattr(response, "data", response.content))
        self.assertTrue(InspectionMission.objects.filter(pk=mission["id"]).exists())

    def test_mission_delete_should_reject_non_dispatcher(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="飞手不能删任务航线")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            pilot_id=self.owner_pilot.id,
        )

        self.authenticate(self.owner_pilot_user)
        response = self.client.delete(f"/api/v2/inspection/missions/{mission['id']}")

        self.assertEqual(response.status_code, 403, getattr(response, "data", response.content))
        self.assertTrue(InspectionMission.objects.filter(pk=mission["id"]).exists())

    def test_dock_mission_start_should_create_dji_immediate_job_from_route_kmz_and_dock(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="执行航线")
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        connection.workspace_id = "workspace-job-001"
        connection.save(update_fields=["workspace_id", "updated_at"])
        dock = self.bind_dock(self.owner_department, self.owner_admin, "DOCK-JOB-001", connection=connection)
        self.upload_route_kmz_by_api(route["id"], connection, wayline_type=3)
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            dock_id=dock.id,
            pilot_id=self.owner_pilot.id,
        )

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_live_capacity",
            return_value={"cameras_list": [{"index": "88-0-0", "videos_list": [{"index": "normal-0"}]}]},
        ) as get_capacity, patch(
            "apps.inspection_v2.services.DjiConnectionGateway.start_live",
            return_value={"rtmp_url": "rtmp://live.example.test/app", "webrtc_url": "https://live.example.test/webrtc"},
        ) as start_live, patch(
            "apps.inspection_v2.services.DjiConnectionGateway.create_dock_flight_task",
            return_value={"dji_job_id": "dji-job-001"},
        ) as create_job:
            start_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")

        self.assertEqual(start_response.status_code, 200, getattr(start_response, "data", start_response.content))
        self.assertEqual(start_response.data["data"]["status"], MissionStatus.RUNNING)
        self.assertEqual(start_response.data["data"]["executionMode"], "DOCK_AUTO")
        self.assertEqual(start_response.data["data"]["cloudExecution"]["executionMode"], "DOCK_AUTO")
        self.assertEqual(start_response.data["data"]["cloudExecution"]["djiJobId"], "dji-job-001")
        self.assertEqual(start_response.data["data"]["cloudExecution"]["liveStatus"], LiveStreamStatus.RUNNING)
        self.assertEqual(start_response.data["data"]["cloudExecution"]["liveVideoId"], f"{self.drone.device_sn}/88-0-0/normal-0")
        self.assertEqual(start_response.data["data"]["cloudExecution"]["liveUrls"]["webrtc_url"], "https://live.example.test/webrtc")
        execution = MissionCloudExecution.objects.get(mission_id=mission["id"])
        self.assertEqual(execution.execution_mode, "DOCK_AUTO")
        self.assertEqual(execution.dji_job_id, "dji-job-001")
        self.assertEqual(execution.executor_sn, "DOCK-JOB-001")
        self.assertEqual(execution.live_video_id, f"{self.drone.device_sn}/88-0-0/normal-0")
        create_job.assert_called_once()
        get_capacity.assert_called_once_with(self.drone.device_sn)
        start_live.assert_called_once_with(
            self.drone.device_sn,
            video_id=f"{self.drone.device_sn}/88-0-0/normal-0",
            url_type=1,
            video_quality=1,
        )
        self.assertEqual(create_job.call_args.kwargs["wayline_type"], 3)
        self.assertEqual(create_job.call_args.kwargs["dock_sn"], "DOCK-JOB-001")
        self.assertEqual(execution.raw_request["wayline_type"], 3)
        self.assertEqual(execution.raw_request["task_type"], 0)
        self.assertEqual(execution.raw_request["live"]["video_id"], f"{self.drone.device_sn}/88-0-0/normal-0")

    def test_pilot2_mission_start_should_create_local_execution_without_flight_task(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="Pilot2 手动航线")
        connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="RC-PILOT2-START-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_live_capacity",
            return_value={"cameras_list": [{"index": "88-0-0", "videos_list": [{"index": "normal-0"}]}]},
        ) as get_capacity, patch(
            "apps.inspection_v2.services.DjiConnectionGateway.start_live",
            return_value={"webrtc_url": "https://live.example.test/pilot2"},
        ) as start_live, patch(
            "apps.inspection_v2.services.DjiConnectionGateway.create_dock_flight_task"
        ) as create_dock_task, patch("apps.inspection_v2.services.DjiConnectionGateway.create_mission") as create_mission:
            start_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")

        self.assertEqual(start_response.status_code, 200, getattr(start_response, "data", start_response.content))
        data = start_response.data["data"]
        self.assertEqual(data["status"], MissionStatus.RUNNING)
        self.assertEqual(data["executionMode"], "PILOT2_MANUAL")
        self.assertEqual(data["cloudExecution"]["executionMode"], "PILOT2_MANUAL")
        self.assertEqual(data["cloudExecution"]["djiJobId"], "")
        self.assertEqual(data["cloudExecution"]["liveStatus"], LiveStreamStatus.RUNNING)
        execution = MissionCloudExecution.objects.get(mission_id=mission["id"])
        self.assertEqual(execution.execution_mode, "PILOT2_MANUAL")
        self.assertEqual(execution.dji_job_id, "")
        self.assertEqual(execution.workspace_id, connection.workspace_id)
        self.assertEqual(execution.executor_sn, executor.device_sn)
        create_dock_task.assert_not_called()
        create_mission.assert_not_called()
        get_capacity.assert_called_once_with(self.drone.device_sn)
        start_live.assert_called_once()

    def test_mission_preflight_check_should_report_ready_without_starting_cloud_execution(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="预检可执行航线")
        connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-PREFLIGHT-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_live_capacity",
            return_value={"cameras_list": [{"index": "88-0-0", "videos_list": [{"index": "normal-0"}]}]},
        ) as get_capacity, patch("apps.inspection_v2.services.DjiConnectionGateway.start_live") as start_live, patch(
            "apps.inspection_v2.services.DjiConnectionGateway.create_mission"
        ) as create_job:
            response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/preflight-check", {}, format="json")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        data = response.data["data"]
        self.assertTrue(data["canStart"])
        self.assertEqual(data["status"], "READY")
        self.assertEqual(data["blockingReasons"], [])
        self.assertEqual(data["execution"]["executionMode"], "PILOT2_MANUAL")
        self.assertEqual(data["execution"]["djiConnectionId"], connection.id)
        self.assertEqual(data["execution"]["executorId"], executor.id)
        self.assertEqual(data["execution"]["executorSn"], executor.device_sn)
        self.assertEqual(data["execution"]["routeDjiFileId"], WaypointRouteCloudFile.objects.get(route_id=route["id"]).dji_file_id)
        self.assertEqual(data["execution"]["selectedLiveVideoId"], f"{self.drone.device_sn}/88-0-0/normal-0")
        self.assertNotIn("WAYLINE_TASK_SUPPORT_UNVERIFIED", {check["code"] for check in data["checks"]})
        self.assertEqual(data["warnings"], [])
        self.assertFalse(FlightSession.objects.filter(mission_id=mission["id"]).exists())
        self.assertFalse(MissionCloudExecution.objects.filter(mission_id=mission["id"]).exists())
        get_capacity.assert_called_once_with(self.drone.device_sn)
        start_live.assert_not_called()
        create_job.assert_not_called()

    def test_mission_create_should_require_exactly_one_execution_resource(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="预检缺执行端航线")
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        dock = self.bind_dock(self.owner_department, self.owner_admin, "DOCK-XOR-001", connection=connection)
        executor = self.bind_gateway(self.owner_department, self.owner_admin, "GATEWAY-XOR-001", connection=connection)
        self.authenticate(self.owner_dispatcher)

        base_payload = {
            "name": "缺执行资源任务",
            "routeId": route["id"],
            "droneId": self.drone.id,
            "pilotAccountProfileId": self.owner_pilot.id,
        }
        missing_response = self.client.post("/api/v2/inspection/missions", base_payload, format="json")
        both_response = self.client.post(
            "/api/v2/inspection/missions",
            {**base_payload, "name": "双执行资源任务", "dockId": dock.id, "executorId": executor.id},
            format="json",
        )

        self.assertEqual(missing_response.status_code, 400, getattr(missing_response, "data", missing_response.content))
        self.assertEqual(both_response.status_code, 400, getattr(both_response, "data", both_response.content))
        self.assertIn("dockId", str(missing_response.data))
        self.assertIn("executorId", str(missing_response.data))
        self.assertIn("二选一", str(both_response.data))

    def test_mission_preflight_check_should_report_offline_resources_without_calling_dji(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="预检离线航线")
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-PREFLIGHT-OFFLINE-001")
        self.drone.online_status = False
        self.drone.save(update_fields=["online_status", "updated_at"])
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )

        with patch("apps.inspection_v2.services.DjiConnectionGateway.get_live_capacity") as get_capacity:
            response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/preflight-check", {}, format="json")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        data = response.data["data"]
        self.assertFalse(data["canStart"])
        self.assertIn("DRONE_ONLINE", [reason["code"] for reason in data["blockingReasons"]])
        self.assertEqual({check["code"]: check["status"] for check in data["checks"]}["LIVE_CAPACITY_AVAILABLE"], "SKIPPED")
        get_capacity.assert_not_called()

    def test_pilot2_preflight_check_should_warn_when_live_capacity_upstream_fails(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="预检直播失败航线")
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-PREFLIGHT-LIVE-FAIL-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_live_capacity",
            side_effect=DjiGatewayUpstreamError("live capacity failed", status_code=502, data={"code": "E0001", "msg": "upstream failed"}),
        ):
            response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/preflight-check", {}, format="json")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        data = response.data["data"]
        self.assertTrue(data["canStart"])
        live_check = {check["code"]: check for check in data["checks"]}["LIVE_CAPACITY_AVAILABLE"]
        self.assertEqual(live_check["status"], "WARNING")
        self.assertEqual(live_check["detail"]["upstreamStatus"], 502)
        self.assertEqual(live_check["detail"]["upstream"]["code"], "E0001")
        self.assertEqual(data["blockingReasons"], [])
        self.assertIn("LIVE_CAPACITY_AVAILABLE", [warning["code"] for warning in data["warnings"]])

    def test_mission_preflight_check_should_use_same_operator_permission_as_start(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="预检权限航线")
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-PREFLIGHT-PERM-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )

        self.authenticate(self.owner_admin)
        response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/preflight-check", {}, format="json")

        self.assertEqual(response.status_code, 403, getattr(response, "data", response.content))

    def test_mission_start_upstream_failure_should_keep_local_task_pending(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="执行失败航线")
        _connection, dock = self.prepare_route_for_dock_execution(route, dock_sn="DOCK-FAIL-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            dock_id=dock.id,
            pilot_id=self.owner_pilot.id,
        )

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_live_capacity",
            return_value={"cameras_list": [{"index": "88-0-0", "videos_list": [{"index": "normal-0"}]}]},
        ), patch(
            "apps.inspection_v2.services.DjiConnectionGateway.start_live",
            return_value={"rtmp_url": "rtmp://live.example.test/app"},
        ) as start_live, patch(
            "apps.inspection_v2.services.DjiConnectionGateway.create_dock_flight_task",
            side_effect=DjiGatewayUpstreamError(
                "DJI upstream business error",
                status_code=502,
                data={"code": "E0001", "msg": "210003 device does not support flight task"},
            ),
        ) as create_job, patch("apps.inspection_v2.services.DjiConnectionGateway.stop_live", return_value={}) as stop_live:
            response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")

        self.assertEqual(response.status_code, 409, getattr(response, "data", response.content))
        persisted = InspectionMission.objects.get(pk=mission["id"])
        self.assertEqual(persisted.status, MissionStatus.PENDING)
        self.assertFalse(FlightSession.objects.filter(mission_id=mission["id"]).exists())
        self.assertFalse(MissionCloudExecution.objects.filter(mission_id=mission["id"]).exists())
        start_live.assert_called_once()
        create_job.assert_called_once()
        stop_live.assert_called_once_with(self.drone.device_sn, video_id=f"{self.drone.device_sn}/88-0-0/normal-0")

    def test_pilot2_mission_start_live_failure_should_still_create_local_execution_without_dji_job(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="直播失败航线")
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-LIVE-FAIL-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_live_capacity",
            return_value={"cameras_list": [{"index": "88-0-0", "videos_list": [{"index": "normal-0"}]}]},
        ), patch(
            "apps.inspection_v2.services.DjiConnectionGateway.start_live",
            side_effect=DjiGatewayUpstreamError("live start failed", status_code=502, data={"code": "E0001"}),
        ) as start_live, patch("apps.inspection_v2.services.DjiConnectionGateway.create_dock_flight_task") as create_job:
            response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        persisted = InspectionMission.objects.get(pk=mission["id"])
        self.assertEqual(persisted.status, MissionStatus.RUNNING)
        self.assertTrue(FlightSession.objects.filter(mission_id=mission["id"]).exists())
        execution = MissionCloudExecution.objects.get(mission_id=mission["id"])
        self.assertEqual(execution.execution_mode, "PILOT2_MANUAL")
        self.assertEqual(execution.dji_job_id, "")
        self.assertEqual(execution.live_status, LiveStreamStatus.FAILED)
        self.assertIn("live start failed", execution.live_error_message)
        start_live.assert_called_once()
        create_job.assert_not_called()

    def test_mission_start_should_require_online_drone_and_executor_before_upstream_call(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="离线航线")
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-OFFLINE-001")
        self.drone.online_status = False
        self.drone.save(update_fields=["online_status", "updated_at"])
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )

        with patch("apps.inspection_v2.services.DjiConnectionGateway.create_dock_flight_task") as create_job:
            response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")

        self.assertEqual(response.status_code, 409, getattr(response, "data", response.content))
        create_job.assert_not_called()
        persisted = InspectionMission.objects.get(pk=mission["id"])
        self.assertEqual(persisted.status, MissionStatus.PENDING)

    def test_cloud_execution_success_event_should_finish_mission_and_archive_media(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="事件航线")
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        connection.workspace_id = "workspace-event-001"
        connection.save(update_fields=["workspace_id", "updated_at"])
        dock = self.bind_dock(self.owner_department, self.owner_admin, "DOCK-EVENT-001", connection=connection)
        self.upload_route_kmz_by_api(route["id"], connection)
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            dock_id=dock.id,
            pilot_id=self.owner_pilot.id,
        )
        self.start_cloud_mission_by_api(mission["id"], dji_job_id="dji-job-event")

        captured_at = timezone.now().isoformat()
        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            return_value=[
                {
                    "file_id": "event-photo-001",
                    "jobId": "dji-job-event",
                    "device_sn": self.drone.device_sn,
                    "fileName": "event.jpg",
                    "mediaType": "photo",
                    "capturedAt": captured_at,
                    "downloadUrl": "https://media.example.test/event.jpg",
                },
                {
                    "file_id": "event-other-job-photo",
                    "jobId": "dji-job-other",
                    "device_sn": self.drone.device_sn,
                    "fileName": "other.jpg",
                    "mediaType": "photo",
                    "capturedAt": captured_at,
                }
            ],
        ), patch("apps.inspection_v2.services.DjiConnectionGateway.stop_live", return_value={}) as stop_live:
            result = apply_cloud_execution_event(
                dji_job_id="dji-job-event",
                status="ok",
                payload={"data": {"output": {"progress": 100, "result_code": 0}}},
            )

        self.assertEqual(result["missionStatus"], MissionStatus.COMPLETED)
        self.assertEqual(result["media"]["photoCount"], 1)
        self.assertTrue(CloudMediaFile.objects.filter(cloud_file_id="event-photo-001").exists())
        self.assertFalse(CloudMediaFile.objects.filter(cloud_file_id="event-other-job-photo").exists())
        execution = MissionCloudExecution.objects.get(dji_job_id="dji-job-event")
        self.assertEqual(execution.progress_percent, 100)
        self.assertEqual(execution.live_status, LiveStreamStatus.STOPPED)
        stop_live.assert_called_once_with(self.drone.device_sn, video_id=f"{self.drone.device_sn}/88-0-0/normal-0")

    def test_cloud_execution_refresh_should_update_running_job_without_finishing_record(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="刷新执行中航线")
        _connection, dock = self.prepare_route_for_dock_execution(route, dock_sn="DOCK-REFRESH-RUNNING-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            dock_id=dock.id,
            pilot_id=self.owner_pilot.id,
        )
        self.start_cloud_mission_by_api(mission["id"], dji_job_id="dji-job-refresh-running")

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_jobs",
            return_value=[
                {"job_id": "dji-job-refresh-other", "status": 3, "progress": 100},
                {"job_id": "dji-job-refresh-running", "status": 2, "progress": 45},
            ],
        ) as list_jobs:
            response = self.client.post(
                f"/api/v2/inspection/missions/{mission['id']}/cloud-execution/refresh",
                {},
                format="json",
            )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        data = response.data["data"]
        self.assertEqual(data["status"], MissionStatus.RUNNING)
        self.assertEqual(data["cloudExecution"]["status"], CloudExecutionStatus.RUNNING)
        self.assertEqual(data["cloudExecution"]["progressPercent"], 45)
        self.assertFalse(InspectionFlightRecord.objects.filter(mission_id=mission["id"]).exists())
        list_jobs.assert_called_once()

    def test_cloud_execution_refresh_should_finish_terminal_job_and_archive_media(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="刷新完成航线")
        _connection, dock = self.prepare_route_for_dock_execution(route, dock_sn="DOCK-REFRESH-DONE-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            dock_id=dock.id,
            pilot_id=self.owner_pilot.id,
        )
        self.start_cloud_mission_by_api(mission["id"], dji_job_id="dji-job-refresh-done")
        captured_at = timezone.now().isoformat()

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_jobs",
            return_value=[{"job_id": "dji-job-refresh-done", "status": 3, "progress": 100}],
        ) as list_jobs, patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            return_value=[
                {
                    "file_id": "refresh-done-photo-001",
                    "jobId": "dji-job-refresh-done",
                    "device_sn": self.drone.device_sn,
                    "fileName": "refresh-done.jpg",
                    "mediaType": "photo",
                    "capturedAt": captured_at,
                    "downloadUrl": "https://media.example.test/refresh-done.jpg",
                }
            ],
        ) as list_media_files, patch("apps.inspection_v2.services.DjiConnectionGateway.stop_live", return_value={}) as stop_live:
            response = self.client.post(
                f"/api/v2/inspection/missions/{mission['id']}/cloud-execution/refresh",
                {},
                format="json",
            )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        data = response.data["data"]
        self.assertEqual(data["status"], MissionStatus.COMPLETED)
        self.assertEqual(data["cloudExecution"]["status"], CloudExecutionStatus.COMPLETED)
        self.assertEqual(data["cloudExecution"]["progressPercent"], 100)
        self.assertTrue(InspectionFlightRecord.objects.filter(mission_id=mission["id"]).exists())
        self.assertTrue(CloudMediaFile.objects.filter(cloud_file_id="refresh-done-photo-001").exists())
        list_jobs.assert_called_once()
        list_media_files.assert_called_once()
        stop_live.assert_called_once_with(self.drone.device_sn, video_id=f"{self.drone.device_sn}/88-0-0/normal-0")

    def test_dock_complete_without_dji_job_should_not_bind_no_job_session_window_media(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="机场无 job 媒体航线")
        connection, dock = self.prepare_route_for_dock_execution(route, dock_sn="DOCK-NO-JOB-MEDIA-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            dock_id=dock.id,
            pilot_id=self.owner_pilot.id,
        )
        self.start_cloud_mission_by_api(mission["id"], dji_job_id="dji-job-will-be-cleared")
        execution = MissionCloudExecution.objects.get(mission_id=mission["id"])
        execution.dji_job_id = ""
        execution.save(update_fields=["dji_job_id", "updated_at"])
        session = FlightSession.objects.get(mission_id=mission["id"])
        local_media = CloudMediaFile.objects.create(
            workspace_id=connection.workspace_id,
            device_sn=self.drone.device_sn,
            cloud_file_id="dock-no-job-local-photo",
            file_name="dock-no-job-local.jpg",
            captured_at=session.started_at,
        )

        with patch("apps.inspection_v2.services.DjiConnectionGateway.list_media_files", return_value=[]) as list_media_files, patch(
            "apps.inspection_v2.services.DjiConnectionGateway.stop_live",
            return_value={},
        ):
            complete_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/complete", {}, format="json")

        self.assertEqual(complete_response.status_code, 200, getattr(complete_response, "data", complete_response.content))
        local_media.refresh_from_db()
        self.assertIsNone(local_media.mission_id)
        self.assertIsNone(local_media.flight_record_id)
        self.assertEqual(complete_response.data["data"]["photoCount"], 0)
        list_media_files.assert_not_called()

    def test_cloud_execution_refresh_should_reject_missing_execution_without_calling_dji(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="刷新无执行记录航线")
        _connection, dock = self.prepare_route_for_dock_execution(route, dock_sn="DOCK-REFRESH-MISSING-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            dock_id=dock.id,
            pilot_id=self.owner_pilot.id,
        )

        with patch("apps.inspection_v2.services.DjiConnectionGateway.list_jobs") as list_jobs:
            response = self.client.post(
                f"/api/v2/inspection/missions/{mission['id']}/cloud-execution/refresh",
                {},
                format="json",
            )

        self.assertEqual(response.status_code, 409, getattr(response, "data", response.content))
        list_jobs.assert_not_called()

    def test_pilot2_cloud_execution_refresh_should_reject_without_calling_dji_jobs(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="Pilot2 刷新航线")
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-REFRESH-PILOT2-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )
        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_live_capacity",
            side_effect=DjiGatewayUpstreamError("live capacity unavailable", status_code=502),
        ), patch("apps.inspection_v2.services.DjiConnectionGateway.create_dock_flight_task") as create_job:
            start_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")
        self.assertEqual(start_response.status_code, 200, getattr(start_response, "data", start_response.content))
        create_job.assert_not_called()

        with patch("apps.inspection_v2.services.DjiConnectionGateway.list_jobs") as list_jobs:
            response = self.client.post(
                f"/api/v2/inspection/missions/{mission['id']}/cloud-execution/refresh",
                {},
                format="json",
            )

        self.assertEqual(response.status_code, 409, getattr(response, "data", response.content))
        self.assertIn("Pilot2", str(response.data))
        list_jobs.assert_not_called()

    def test_pilot2_cancel_should_not_call_dji_job_cancel(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="Pilot2 取消航线")
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-CANCEL-PILOT2-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )
        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_live_capacity",
            return_value={"cameras_list": [{"index": "88-0-0", "videos_list": [{"index": "normal-0"}]}]},
        ), patch("apps.inspection_v2.services.DjiConnectionGateway.start_live", return_value={"webrtc_url": "https://live.example.test/pilot2"}):
            start_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")
        self.assertEqual(start_response.status_code, 200, getattr(start_response, "data", start_response.content))

        with patch("apps.inspection_v2.services.DjiConnectionGateway.cancel_mission") as cancel_job, patch(
            "apps.inspection_v2.services.DjiConnectionGateway.stop_live",
            return_value={},
        ) as stop_live:
            cancel_response = self.client.post(
                f"/api/v2/inspection/missions/{mission['id']}/cancel",
                {"reason": "飞手遥控器取消"},
                format="json",
            )

        self.assertEqual(cancel_response.status_code, 200, getattr(cancel_response, "data", cancel_response.content))
        self.assertEqual(cancel_response.data["data"]["status"], MissionStatus.CANCELED)
        cancel_job.assert_not_called()
        stop_live.assert_called_once_with(self.drone.device_sn, video_id=f"{self.drone.device_sn}/88-0-0/normal-0")

    def test_pilot2_complete_should_bind_unassigned_media_by_workspace_device_and_session_window(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="Pilot2 媒体航线")
        connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-MEDIA-PILOT2-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )
        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_live_capacity",
            side_effect=DjiGatewayUpstreamError("live capacity unavailable", status_code=502),
        ):
            start_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")
        self.assertEqual(start_response.status_code, 200, getattr(start_response, "data", start_response.content))
        session = FlightSession.objects.get(mission_id=mission["id"])
        inside_captured_at = session.started_at
        outside_captured_at = session.started_at - timedelta(seconds=10)
        inside = CloudMediaFile.objects.create(
            workspace_id=connection.workspace_id,
            device_sn=self.drone.device_sn,
            cloud_file_id="pilot2-inside-photo",
            file_name="inside.jpg",
            captured_at=inside_captured_at,
        )
        outside = CloudMediaFile.objects.create(
            workspace_id=connection.workspace_id,
            device_sn=self.drone.device_sn,
            cloud_file_id="pilot2-outside-photo",
            file_name="outside.jpg",
            captured_at=outside_captured_at,
        )
        no_time = CloudMediaFile.objects.create(
            workspace_id=connection.workspace_id,
            device_sn=self.drone.device_sn,
            cloud_file_id="pilot2-no-time-photo",
            file_name="no-time.jpg",
            captured_at=None,
        )

        with patch("apps.inspection_v2.services.DjiConnectionGateway.list_media_files", return_value=[]) as list_media_files:
            complete_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/complete", {}, format="json")

        self.assertEqual(complete_response.status_code, 200, getattr(complete_response, "data", complete_response.content))
        inside.refresh_from_db()
        outside.refresh_from_db()
        no_time.refresh_from_db()
        self.assertEqual(inside.mission_id, mission["id"])
        self.assertEqual(inside.flight_record_id, complete_response.data["data"]["id"])
        self.assertIsNone(outside.mission_id)
        self.assertIsNone(no_time.mission_id)
        list_media_files.assert_called_once()

    def test_pilot2_refresh_media_should_pull_replay_video_from_dji_media_list(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="Pilot2 回放媒体航线")
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-REPLAY-PILOT2-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )
        started_at = timezone.datetime(2026, 6, 12, 10, 1, 24, 292646, tzinfo=dt_timezone.utc)
        ended_at = timezone.datetime(2026, 6, 12, 10, 2, 3, 911428, tzinfo=dt_timezone.utc)
        with patch("apps.inspection_v2.services.timezone.now", return_value=started_at), patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_live_capacity",
            side_effect=DjiGatewayUpstreamError("live capacity unavailable", status_code=502),
        ):
            start_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")
        self.assertEqual(start_response.status_code, 200, getattr(start_response, "data", start_response.content))

        replay_payload = {
            "file_id": "pilot2-replay-video-001",
            "file_name": "2026-06-12_10-01-24-797904.mp4",
            "file_path": "/live/replay/pilot2-replay-video-001_2026-06-12_10-01-24-797904.mp4",
            "object_key": "live/replay/pilot2-replay-video-001_2026-06-12_10-01-24-797904.mp4",
            "drone": self.drone.device_sn,
            "create_time": "2026-06-12 18:02:05",
            "job_id": "",
        }
        with patch("apps.inspection_v2.services.timezone.now", return_value=ended_at), patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            return_value=[replay_payload],
        ) as list_media_files, patch("apps.inspection_v2.services.DjiConnectionGateway.stop_live", return_value={}):
            complete_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/complete", {}, format="json")

        self.assertEqual(complete_response.status_code, 200, getattr(complete_response, "data", complete_response.content))
        list_media_files.assert_called_once()
        media = CloudMediaFile.objects.get(cloud_file_id="pilot2-replay-video-001")
        self.assertEqual(media.mission_id, mission["id"])
        self.assertEqual(media.flight_record_id, complete_response.data["data"]["id"])
        self.assertEqual(media.media_type, "VIDEO")
        self.assertEqual(media.captured_at, timezone.datetime(2026, 6, 12, 10, 1, 24, 797904, tzinfo=dt_timezone.utc))
        self.assertEqual(complete_response.data["data"]["videoCount"], 1)

    def test_pilot2_refresh_media_should_use_dji_video_filename_time_for_window_match(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="Pilot2 DJI 视频时间航线")
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-DJI-VIDEO-TIME-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )
        started_at = timezone.datetime(2026, 6, 15, 6, 22, 22, 982028, tzinfo=dt_timezone.utc)
        ended_at = timezone.datetime(2026, 6, 15, 6, 23, 13, 866043, tzinfo=dt_timezone.utc)
        with patch("apps.inspection_v2.services.timezone.now", return_value=started_at), patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_live_capacity",
            side_effect=DjiGatewayUpstreamError("live capacity unavailable", status_code=502),
        ):
            start_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")
        self.assertEqual(start_response.status_code, 200, getattr(start_response, "data", start_response.content))

        video_payload = {
            "file_id": "pilot2-dji-video-after-end-001",
            "file_name": "DJI_20260615142251_0003_V.MP4",
            "object_key": "wayline/DJI_20260615142251_0003_V.MP4",
            "drone": self.drone.device_sn,
            "create_time": "2026-06-15 14:23:25",
            "job_id": "",
        }
        with patch("apps.inspection_v2.services.timezone.now", return_value=ended_at), patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            return_value=[video_payload],
        ) as list_media_files:
            complete_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/complete", {}, format="json")

        self.assertEqual(complete_response.status_code, 200, getattr(complete_response, "data", complete_response.content))
        list_media_files.assert_called_once()
        media = CloudMediaFile.objects.get(cloud_file_id="pilot2-dji-video-after-end-001")
        self.assertEqual(media.mission_id, mission["id"])
        self.assertEqual(media.flight_record_id, complete_response.data["data"]["id"])
        self.assertEqual(media.media_type, CloudMediaType.VIDEO)
        self.assertEqual(media.captured_at, timezone.datetime(2026, 6, 15, 6, 22, 51, tzinfo=dt_timezone.utc))
        self.assertEqual(complete_response.data["data"]["videoCount"], 1)

    def _completed_pilot2_record(self, *, route_name: str, gateway_sn: str):
        route = self.create_route_by_api(self.owner_dispatcher, name=route_name)
        connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn=gateway_sn)
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )
        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_live_capacity",
            side_effect=DjiGatewayUpstreamError("live capacity unavailable", status_code=502),
        ):
            start_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")
        self.assertEqual(start_response.status_code, 200, getattr(start_response, "data", start_response.content))
        with patch("apps.inspection_v2.services.DjiConnectionGateway.list_media_files", return_value=[]):
            complete_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/complete", {}, format="json")
        self.assertEqual(complete_response.status_code, 200, getattr(complete_response, "data", complete_response.content))
        return connection, InspectionMission.objects.get(pk=mission["id"]), InspectionFlightRecord.objects.get(pk=complete_response.data["data"]["id"])

    def _delayed_replay_payload_for_record(self, record, *, cloud_file_id: str, captured_at=None):
        replay_started_at = captured_at or record.start_time + ((record.end_time - record.start_time) / 2)
        replay_name = replay_started_at.astimezone(dt_timezone.utc).strftime("%Y-%m-%d_%H-%M-%S-%f.mp4")
        return {
            "file_id": cloud_file_id,
            "file_name": replay_name,
            "file_path": f"/live/replay/{cloud_file_id}_{replay_name}",
            "object_key": f"live/replay/{cloud_file_id}_{replay_name}",
            "drone": self.drone.device_sn,
            "create_time": (
                record.end_time + timedelta(seconds=30)
            ).astimezone(timezone.get_current_timezone()).strftime("%Y-%m-%d %H:%M:%S"),
            "job_id": "",
        }

    def _dji_video_payload_for_record(self, record, *, cloud_file_id: str, captured_at=None, sequence: int = 16):
        captured_at = captured_at or record.start_time + ((record.end_time - record.start_time) / 2)
        local_captured_at = captured_at.astimezone(timezone.get_current_timezone())
        file_name = f"DJI_{local_captured_at.strftime('%Y%m%d%H%M%S')}_{sequence:04d}_V.MP4"
        return {
            "file_id": cloud_file_id,
            "file_name": file_name,
            "file_path": "DJI_202606151635_006_JNU-Low-altitude-Group",
            "object_key": f"wayline/{file_name}",
            "drone": self.drone.device_sn,
            "create_time": (
                record.end_time + timedelta(seconds=30)
            ).astimezone(timezone.get_current_timezone()).strftime("%Y-%m-%d %H:%M:%S"),
            "job_id": "",
        }

    def _create_record_video_media(self, connection, mission, record, *, cloud_file_id: str):
        captured_at = record.start_time + ((record.end_time - record.start_time) / 3)
        media = CloudMediaFile.objects.create(
            workspace_id=connection.workspace_id,
            mission=mission,
            flight_record=record,
            device_sn=self.drone.device_sn,
            cloud_file_id=cloud_file_id,
            media_type=CloudMediaType.VIDEO,
            file_name=f"{cloud_file_id}.mp4",
            captured_at=captured_at,
            playback_url="https://media.example.test/existing-playback.m3u8",
        )
        record.video_count = record.media_files.filter(media_type=CloudMediaType.VIDEO).count()
        record.save(update_fields=["video_count", "updated_at"])
        return media

    def test_complete_mission_should_create_pending_media_sync_state(self):
        _connection, _mission, record = self._completed_pilot2_record(
            route_name="Pilot2 媒体同步状态航线",
            gateway_sn="GATEWAY-MEDIA-SYNC-STATE-001",
        )

        state = InspectionFlightRecordMediaSyncState.objects.get(flight_record=record)
        self.assertEqual(state.status, FlightRecordMediaSyncStatus.PENDING)
        self.assertEqual(state.attempt_count, 0)
        self.assertIsNotNone(state.next_run_at)
        self.assertIsNotNone(state.deadline_at)
        self.assertGreaterEqual(state.deadline_at, record.end_time + timedelta(minutes=29, seconds=50))
        self.assertLessEqual(state.deadline_at, record.end_time + timedelta(minutes=30, seconds=10))

    def test_v2_dji_worker_once_should_sync_due_media_state_and_continue_after_existing_video(self):
        connection, mission, record = self._completed_pilot2_record(
            route_name="Pilot2 worker 补齐多个延迟视频航线",
            gateway_sn="GATEWAY-WORKER-MEDIA-DELAYED-001",
        )
        record.start_time = timezone.datetime(2026, 6, 15, 9, 48, 45, 569607, tzinfo=dt_timezone.utc)
        record.end_time = timezone.datetime(2026, 6, 15, 9, 49, 5, 310086, tzinfo=dt_timezone.utc)
        record.save(update_fields=["start_time", "end_time", "updated_at"])
        existing_payload = self._dji_video_payload_for_record(
            record,
            cloud_file_id="mission-29-dji-video-001",
            captured_at=timezone.datetime(2026, 6, 15, 9, 48, 46, tzinfo=dt_timezone.utc),
            sequence=17,
        )
        replay_payload = self._delayed_replay_payload_for_record(
            record,
            cloud_file_id="mission-29-replay-video-001",
            captured_at=timezone.datetime(2026, 6, 15, 9, 48, 47, 43129, tzinfo=dt_timezone.utc),
        )
        CloudMediaFile.objects.create(
            workspace_id=connection.workspace_id,
            mission=mission,
            flight_record=record,
            device_sn=self.drone.device_sn,
            cloud_file_id="mission-29-dji-video-001",
            media_type=CloudMediaType.VIDEO,
            file_name="DJI_20260615174846_0017_V.MP4",
            captured_at=timezone.datetime(2026, 6, 15, 9, 48, 46, tzinfo=dt_timezone.utc),
        )
        record.video_count = 1
        record.save(update_fields=["video_count", "updated_at"])
        state = InspectionFlightRecordMediaSyncState.objects.get(flight_record=record)
        state.next_run_at = timezone.now() - timedelta(seconds=1)
        state.deadline_at = timezone.now() + timedelta(minutes=30)
        state.save(update_fields=["next_run_at", "deadline_at", "updated_at"])

        worker = V2DjiWorker()
        with patch(
            "apps.inspection_v2.management.commands.run_v2_dji_worker.sync_connection_resources_from_upstream",
            return_value={"drones": 1},
        ), patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            return_value=[existing_payload, replay_payload],
        ) as list_media_files:
            summary = worker.run_once()

        self.assertEqual(summary["mediaSyncProcessed"], 1)
        self.assertEqual(summary["mediaSyncSucceeded"], 1)
        self.assertEqual(summary["mediaSyncFailed"], 0)
        list_media_files.assert_called_once()
        record.refresh_from_db()
        self.assertEqual(record.video_count, 2)
        self.assertTrue(
            CloudMediaFile.objects.filter(
                cloud_file_id="mission-29-replay-video-001",
                mission_id=mission.id,
                flight_record_id=record.id,
            ).exists()
        )
        state.refresh_from_db()
        self.assertEqual(state.status, FlightRecordMediaSyncStatus.PENDING)
        self.assertEqual(state.attempt_count, 1)
        self.assertEqual(state.last_video_count, 2)
        self.assertIsNotNone(state.last_synced)

    def test_v2_dji_worker_once_should_record_media_sync_failure_and_retry(self):
        _connection, _mission, record = self._completed_pilot2_record(
            route_name="Pilot2 worker 媒体同步失败重试航线",
            gateway_sn="GATEWAY-WORKER-MEDIA-FAIL-001",
        )
        state = InspectionFlightRecordMediaSyncState.objects.get(flight_record=record)
        now = timezone.now()
        state.next_run_at = now - timedelta(seconds=1)
        state.deadline_at = now + timedelta(minutes=30)
        state.save(update_fields=["next_run_at", "deadline_at", "updated_at"])

        worker = V2DjiWorker()
        with patch(
            "apps.inspection_v2.management.commands.run_v2_dji_worker.sync_connection_resources_from_upstream",
            return_value={"drones": 1},
        ), patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            side_effect=DjiGatewayUpstreamError("media list failed", status_code=502),
        ):
            summary = worker.run_once()

        self.assertEqual(summary["mediaSyncProcessed"], 1)
        self.assertEqual(summary["mediaSyncSucceeded"], 0)
        self.assertEqual(summary["mediaSyncFailed"], 1)
        state.refresh_from_db()
        self.assertEqual(state.status, FlightRecordMediaSyncStatus.PENDING)
        self.assertEqual(state.attempt_count, 1)
        self.assertIn("media list failed", state.last_error)
        self.assertGreater(state.next_run_at, now)
        self.assertLessEqual(state.next_run_at, now + timedelta(seconds=31))

    def test_v2_dji_worker_once_should_mark_due_media_sync_failed_after_deadline(self):
        _connection, _mission, record = self._completed_pilot2_record(
            route_name="Pilot2 worker 媒体同步截止失败航线",
            gateway_sn="GATEWAY-WORKER-MEDIA-DEADLINE-FAIL-001",
        )
        state = InspectionFlightRecordMediaSyncState.objects.get(flight_record=record)
        state.next_run_at = timezone.now() - timedelta(seconds=1)
        state.deadline_at = timezone.now() - timedelta(seconds=1)
        state.save(update_fields=["next_run_at", "deadline_at", "updated_at"])

        worker = V2DjiWorker()
        with patch(
            "apps.inspection_v2.management.commands.run_v2_dji_worker.sync_connection_resources_from_upstream",
            return_value={"drones": 1},
        ), patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            side_effect=DjiGatewayUpstreamError("media list failed", status_code=502),
        ):
            summary = worker.run_once()

        self.assertEqual(summary["mediaSyncProcessed"], 1)
        self.assertEqual(summary["mediaSyncSucceeded"], 0)
        self.assertEqual(summary["mediaSyncFailed"], 1)
        state.refresh_from_db()
        self.assertEqual(state.status, FlightRecordMediaSyncStatus.FAILED)
        self.assertIn("media list failed", state.last_error)

    def test_v2_dji_worker_once_should_mark_due_media_sync_completed_after_deadline_success(self):
        _connection, _mission, record = self._completed_pilot2_record(
            route_name="Pilot2 worker 媒体同步截止成功航线",
            gateway_sn="GATEWAY-WORKER-MEDIA-DEADLINE-SUCCESS-001",
        )
        state = InspectionFlightRecordMediaSyncState.objects.get(flight_record=record)
        state.next_run_at = timezone.now() - timedelta(seconds=1)
        state.deadline_at = timezone.now() - timedelta(seconds=1)
        state.save(update_fields=["next_run_at", "deadline_at", "updated_at"])

        worker = V2DjiWorker()
        with patch(
            "apps.inspection_v2.management.commands.run_v2_dji_worker.sync_connection_resources_from_upstream",
            return_value={"drones": 1},
        ), patch("apps.inspection_v2.services.DjiConnectionGateway.list_media_files", return_value=[]):
            summary = worker.run_once()

        self.assertEqual(summary["mediaSyncProcessed"], 1)
        self.assertEqual(summary["mediaSyncSucceeded"], 1)
        self.assertEqual(summary["mediaSyncFailed"], 0)
        state.refresh_from_db()
        self.assertEqual(state.status, FlightRecordMediaSyncStatus.COMPLETED)
        self.assertEqual(state.last_video_count, 0)
        self.assertIsNotNone(state.last_synced)

    def test_flight_record_list_should_not_sync_delayed_video_media_for_zero_video_records(self):
        _first_connection, _first_mission, first_record = self._completed_pilot2_record(
            route_name="Pilot2 飞行记录列表延迟媒体航线 A",
            gateway_sn="GATEWAY-RECORD-LIST-DELAYED-001",
        )
        _second_connection, _second_mission, second_record = self._completed_pilot2_record(
            route_name="Pilot2 飞行记录列表延迟媒体航线 B",
            gateway_sn="GATEWAY-RECORD-LIST-DELAYED-002",
        )
        first_record.start_time = timezone.datetime(2026, 6, 15, 6, 0, 0, tzinfo=dt_timezone.utc)
        first_record.end_time = timezone.datetime(2026, 6, 15, 6, 1, 0, tzinfo=dt_timezone.utc)
        first_record.save(update_fields=["start_time", "end_time", "updated_at"])
        second_record.start_time = timezone.datetime(2026, 6, 15, 7, 0, 0, tzinfo=dt_timezone.utc)
        second_record.end_time = timezone.datetime(2026, 6, 15, 7, 1, 0, tzinfo=dt_timezone.utc)
        second_record.save(update_fields=["start_time", "end_time", "updated_at"])

        payloads = [
            self._delayed_replay_payload_for_record(first_record, cloud_file_id="flight-record-list-video-001"),
            self._delayed_replay_payload_for_record(second_record, cloud_file_id="flight-record-list-video-002"),
        ]
        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            return_value=payloads,
        ) as list_media_files:
            response = self.client.get("/api/v2/inspection/flight-records")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        items = {item["id"]: item for item in response.data["data"]["list"]}
        self.assertEqual(items[first_record.id]["videoCount"], 0)
        self.assertEqual(items[second_record.id]["videoCount"], 0)
        first_record.refresh_from_db()
        second_record.refresh_from_db()
        self.assertEqual(first_record.video_count, 0)
        self.assertEqual(second_record.video_count, 0)
        list_media_files.assert_not_called()

    def test_flight_record_detail_should_sync_due_delayed_video_media_for_zero_video_record(self):
        _connection, _mission, record = self._completed_pilot2_record(
            route_name="Pilot2 飞行记录详情延迟媒体航线",
            gateway_sn="GATEWAY-RECORD-DETAIL-DELAYED-001",
        )
        self.assertEqual(record.video_count, 0)

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            return_value=[
                self._delayed_replay_payload_for_record(record, cloud_file_id="flight-record-detail-video-001")
            ],
        ) as list_media_files:
            response = self.client.get(f"/api/v2/inspection/flight-records/{record.id}")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["videoCount"], 1)
        self.assertEqual(response.data["data"]["mediaSyncStatus"], FlightRecordMediaSyncStatus.PENDING)
        self.assertIsNotNone(response.data["data"]["mediaSyncLastSyncedAt"])
        self.assertIsNotNone(response.data["data"]["mediaSyncNextRunAt"])
        record.refresh_from_db()
        self.assertEqual(record.video_count, 1)
        self.assertTrue(
            CloudMediaFile.objects.filter(
                cloud_file_id="flight-record-detail-video-001",
                mission_id=record.mission_id,
                flight_record_id=record.id,
            ).exists()
        )
        list_media_files.assert_called_once()

    def test_flight_record_detail_should_sync_additional_due_delayed_videos_when_record_already_has_video(self):
        connection, mission, record = self._completed_pilot2_record(
            route_name="Pilot2 飞行记录详情补齐多个延迟视频航线",
            gateway_sn="GATEWAY-RECORD-DETAIL-EXTRA-DELAYED-001",
        )
        self._create_record_video_media(
            connection,
            mission,
            record,
            cloud_file_id="flight-record-detail-existing-video-001",
        )
        record.refresh_from_db()
        self.assertEqual(record.video_count, 1)

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            return_value=[
                self._delayed_replay_payload_for_record(
                    record,
                    cloud_file_id="flight-record-detail-extra-live-video-001",
                ),
                self._dji_video_payload_for_record(
                    record,
                    cloud_file_id="flight-record-detail-extra-dji-video-001",
                ),
            ],
        ) as list_media_files:
            response = self.client.get(f"/api/v2/inspection/flight-records/{record.id}")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["videoCount"], 3)
        self.assertTrue(
            CloudMediaFile.objects.filter(
                cloud_file_id="flight-record-detail-extra-live-video-001",
                mission_id=mission.id,
                flight_record_id=record.id,
            ).exists()
        )
        self.assertTrue(
            CloudMediaFile.objects.filter(
                cloud_file_id="flight-record-detail-extra-dji-video-001",
                mission_id=mission.id,
                flight_record_id=record.id,
            ).exists()
        )
        list_media_files.assert_called_once()

    def test_flight_record_list_with_mission_id_should_not_sync_additional_delayed_videos_when_record_already_has_video(self):
        connection, mission, record = self._completed_pilot2_record(
            route_name="Pilot2 飞行记录任务过滤补齐多个延迟视频航线",
            gateway_sn="GATEWAY-RECORD-LIST-MISSION-EXTRA-DELAYED-001",
        )
        self._create_record_video_media(
            connection,
            mission,
            record,
            cloud_file_id="flight-record-list-mission-existing-video-001",
        )
        record.refresh_from_db()
        self.assertEqual(record.video_count, 1)

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            return_value=[
                self._delayed_replay_payload_for_record(
                    record,
                    cloud_file_id="flight-record-list-mission-extra-live-video-001",
                ),
                self._dji_video_payload_for_record(
                    record,
                    cloud_file_id="flight-record-list-mission-extra-dji-video-001",
                ),
            ],
        ) as list_media_files:
            response = self.client.get(f"/api/v2/inspection/flight-records?missionId={mission.id}")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["total"], 1)
        self.assertEqual(response.data["data"]["list"][0]["videoCount"], 1)
        record.refresh_from_db()
        self.assertEqual(record.video_count, 1)
        list_media_files.assert_not_called()

    def test_refresh_media_should_sync_replay_started_just_before_record_start(self):
        _connection, _mission, record = self._completed_pilot2_record(
            route_name="Pilot2 飞行记录详情起点容差航线",
            gateway_sn="GATEWAY-RECORD-DETAIL-START-TOLERANCE-001",
        )
        replay_started_at = record.start_time - timedelta(milliseconds=500)

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            return_value=[
                self._delayed_replay_payload_for_record(
                    record,
                    cloud_file_id="flight-record-detail-start-tolerance-video-001",
                    captured_at=replay_started_at,
                )
            ],
        ) as list_media_files:
            response = self.client.post(f"/api/v2/inspection/flight-records/{record.id}/refresh-media", {}, format="json")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["videoCount"], 1)
        media = CloudMediaFile.objects.get(cloud_file_id="flight-record-detail-start-tolerance-video-001")
        self.assertEqual(media.flight_record_id, record.id)
        self.assertEqual(media.mission_id, record.mission_id)
        self.assertEqual(media.captured_at, replay_started_at)
        list_media_files.assert_called_once()

    def test_refresh_media_should_update_media_sync_state(self):
        _connection, _mission, record = self._completed_pilot2_record(
            route_name="Pilot2 显式刷新媒体同步状态航线",
            gateway_sn="GATEWAY-REFRESH-MEDIA-STATE-001",
        )
        state = InspectionFlightRecordMediaSyncState.objects.get(flight_record=record)
        self.assertEqual(state.attempt_count, 0)

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            return_value=[
                self._delayed_replay_payload_for_record(
                    record,
                    cloud_file_id="refresh-media-state-video-001",
                )
            ],
        ):
            response = self.client.post(f"/api/v2/inspection/flight-records/{record.id}/refresh-media", {}, format="json")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        state.refresh_from_db()
        self.assertEqual(state.status, FlightRecordMediaSyncStatus.PENDING)
        self.assertEqual(state.attempt_count, 1)
        self.assertEqual(state.last_video_count, 1)
        self.assertIsNotNone(state.last_synced)
        self.assertEqual(state.last_error, "")

    def test_flight_record_detail_should_return_local_record_when_due_media_sync_fails(self):
        _connection, _mission, record = self._completed_pilot2_record(
            route_name="Pilot2 飞行记录详情延迟媒体失败航线",
            gateway_sn="GATEWAY-RECORD-DETAIL-DELAYED-FAIL-001",
        )
        self.assertEqual(record.video_count, 0)
        state = InspectionFlightRecordMediaSyncState.objects.get(flight_record=record)
        self.assertEqual(state.attempt_count, 0)

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            side_effect=DjiGatewayUpstreamError("media list failed", status_code=502),
        ) as list_media_files:
            response = self.client.get(f"/api/v2/inspection/flight-records/{record.id}")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["videoCount"], 0)
        record.refresh_from_db()
        self.assertEqual(record.video_count, 0)
        state.refresh_from_db()
        self.assertEqual(state.status, FlightRecordMediaSyncStatus.PENDING)
        self.assertEqual(state.attempt_count, 1)
        self.assertIn("media list failed", state.last_error)
        list_media_files.assert_called_once()

    def test_refresh_v2_flight_record_media_dry_run_all_completed_should_not_write_media(self):
        connection, _mission, record = self._completed_pilot2_record(
            route_name="Pilot2 回填 dry run 航线",
            gateway_sn="GATEWAY-BACKFILL-DRY-RUN-001",
        )
        out = StringIO()
        local_media = CloudMediaFile.objects.create(
            workspace_id=connection.workspace_id,
            device_sn=self.drone.device_sn,
            cloud_file_id="backfill-dry-run-photo",
            file_name="backfill-dry-run.jpg",
            captured_at=record.start_time,
        )

        with patch(
            "apps.inspection_v2.management.commands.refresh_v2_flight_record_media.sync_media_for_record",
            return_value={"synced": 1, "photoCount": 0, "videoCount": 1},
        ) as sync_media:
            call_command("refresh_v2_flight_record_media", "--all-completed", "--dry-run", stdout=out)

        sync_media.assert_not_called()
        local_media.refresh_from_db()
        self.assertIsNone(local_media.mission_id)
        self.assertIsNone(local_media.flight_record_id)
        output = out.getvalue()
        self.assertIn(f"would refresh flight_record_id={record.id}", output)
        self.assertIn("processed=1", output)
        self.assertIn("planned=1", output)
        self.assertIn("synced=0", output)

    def test_refresh_v2_flight_record_media_mission_id_should_process_only_selected_record(self):
        _connection, selected_mission, selected_record = self._completed_pilot2_record(
            route_name="Pilot2 回填指定任务航线",
            gateway_sn="GATEWAY-BACKFILL-MISSION-001",
        )
        self._completed_pilot2_record(
            route_name="Pilot2 回填其它任务航线",
            gateway_sn="GATEWAY-BACKFILL-MISSION-002",
        )
        out = StringIO()

        with patch(
            "apps.inspection_v2.management.commands.refresh_v2_flight_record_media.sync_media_for_record",
            return_value={"synced": 2, "photoCount": 1, "videoCount": 1},
        ) as sync_media:
            call_command("refresh_v2_flight_record_media", "--mission-id", str(selected_mission.id), stdout=out)

        sync_media.assert_called_once()
        self.assertEqual(sync_media.call_args.kwargs["record"].id, selected_record.id)
        output = out.getvalue()
        self.assertIn("processed=1", output)
        self.assertIn("synced=2", output)
        self.assertIn("photos=1", output)
        self.assertIn("videos=1", output)

    def test_refresh_v2_flight_record_media_should_continue_after_single_failure_and_return_nonzero(self):
        _first_connection, _first_mission, first_record = self._completed_pilot2_record(
            route_name="Pilot2 回填失败航线",
            gateway_sn="GATEWAY-BACKFILL-FAIL-001",
        )
        _second_connection, _second_mission, second_record = self._completed_pilot2_record(
            route_name="Pilot2 回填成功航线",
            gateway_sn="GATEWAY-BACKFILL-FAIL-002",
        )
        out = StringIO()

        def sync_side_effect(*, record):
            if record.id == first_record.id:
                raise DjiGatewayUpstreamError("media list failed", status_code=502)
            return {"synced": 1, "photoCount": 0, "videoCount": 1}

        with patch(
            "apps.inspection_v2.management.commands.refresh_v2_flight_record_media.sync_media_for_record",
            side_effect=sync_side_effect,
        ) as sync_media:
            with self.assertRaises(CommandError):
                call_command("refresh_v2_flight_record_media", "--all-completed", stdout=out)

        self.assertEqual(sync_media.call_count, 2)
        self.assertEqual({call.kwargs["record"].id for call in sync_media.call_args_list}, {first_record.id, second_record.id})
        output = out.getvalue()
        self.assertIn(f"failed flight_record_id={first_record.id}", output)
        self.assertIn(f"refreshed flight_record_id={second_record.id}", output)
        self.assertIn("processed=2", output)
        self.assertIn("synced=1", output)
        self.assertIn("failures=1", output)

    def test_media_file_refresh_url_should_update_download_preview_and_playback(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="媒体 URL 刷新航线")
        _connection, dock = self.prepare_route_for_dock_execution(route, dock_sn="DOCK-MEDIA-URL-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            dock_id=dock.id,
            pilot_id=self.owner_pilot.id,
        )
        self.start_cloud_mission_by_api(mission["id"], dji_job_id="dji-job-media-url")
        execution = MissionCloudExecution.objects.get(mission_id=mission["id"])
        media = CloudMediaFile.objects.create(
            workspace_id=execution.workspace_id,
            mission_id=mission["id"],
            device_sn=self.drone.device_sn,
            dji_job_id=execution.dji_job_id,
            cloud_file_id="media-refresh-url-001",
            file_name="media-refresh-url.mp4",
        )

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_url",
            return_value="https://media.example.test/download.mp4",
        ) as get_download_url:
            download_response = self.client.post(
                f"/api/v2/inspection/media-files/{media.id}/refresh-url",
                {"urlType": "download"},
                format="json",
            )
        self.assertEqual(download_response.status_code, 200, getattr(download_response, "data", download_response.content))
        self.assertEqual(download_response.data["data"]["downloadUrl"], "https://media.example.test/download.mp4")
        get_download_url.assert_called_once_with("media-refresh-url-001")

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_preview_url",
            return_value="https://media.example.test/preview.jpg",
        ) as get_preview_url:
            preview_response = self.client.post(
                f"/api/v2/inspection/media-files/{media.id}/refresh-url",
                {"urlType": "preview"},
                format="json",
            )
        self.assertEqual(preview_response.status_code, 200, getattr(preview_response, "data", preview_response.content))
        self.assertEqual(preview_response.data["data"]["previewUrl"], "https://media.example.test/preview.jpg")
        get_preview_url.assert_called_once_with("media-refresh-url-001")

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_playback_url",
            return_value="https://media.example.test/playback.m3u8",
        ) as get_playback_url:
            playback_response = self.client.post(
                f"/api/v2/inspection/media-files/{media.id}/refresh-url",
                {"urlType": "playback"},
                format="json",
            )
        self.assertEqual(playback_response.status_code, 200, getattr(playback_response, "data", playback_response.content))
        self.assertEqual(playback_response.data["data"]["playbackUrl"], "https://media.example.test/playback.m3u8")
        get_playback_url.assert_called_once_with("media-refresh-url-001")

    def test_media_file_detail_should_auto_refresh_missing_preview_url(self):
        media = self.create_visible_media_file(cloud_file_id="media-auto-preview-missing")

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_preview_url",
            return_value="https://media.example.test/preview.jpg",
        ) as get_preview_url:
            response = self.client.get(f"/api/v2/inspection/media-files/{media.id}")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["previewUrl"], "https://media.example.test/preview.jpg")
        media.refresh_from_db()
        self.assertEqual(media.preview_url, "https://media.example.test/preview.jpg")
        get_preview_url.assert_called_once_with("media-auto-preview-missing")

    def test_media_file_detail_should_not_refresh_valid_preview_url(self):
        preview_url = self.signed_media_preview_url(issued_at=timezone.now() + timedelta(hours=1))
        media = self.create_visible_media_file(cloud_file_id="media-valid-preview", preview_url=preview_url)

        with patch("apps.inspection_v2.services.DjiConnectionGateway.get_media_preview_url") as get_preview_url:
            response = self.client.get(f"/api/v2/inspection/media-files/{media.id}")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["previewUrl"], preview_url)
        get_preview_url.assert_not_called()

    def test_media_file_detail_should_refresh_expired_preview_url(self):
        expired_preview_url = self.signed_media_preview_url(issued_at=timezone.now() - timedelta(hours=7))
        media = self.create_visible_media_file(cloud_file_id="media-expired-preview", preview_url=expired_preview_url)

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_preview_url",
            return_value="https://media.example.test/fresh-preview.jpg",
        ) as get_preview_url:
            response = self.client.get(f"/api/v2/inspection/media-files/{media.id}")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["previewUrl"], "https://media.example.test/fresh-preview.jpg")
        media.refresh_from_db()
        self.assertEqual(media.preview_url, "https://media.example.test/fresh-preview.jpg")
        get_preview_url.assert_called_once_with("media-expired-preview")

    def test_media_file_detail_should_return_detail_when_auto_preview_refresh_fails(self):
        media = self.create_visible_media_file(cloud_file_id="media-preview-fails")

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_preview_url",
            side_effect=DjiGatewayUpstreamError("preview failed", status_code=502, data={"error": "boom"}),
        ) as get_preview_url:
            response = self.client.get(f"/api/v2/inspection/media-files/{media.id}")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["previewUrl"], "")
        media.refresh_from_db()
        self.assertEqual(media.preview_url, "")
        get_preview_url.assert_called_once_with("media-preview-fails")

    def test_media_file_list_should_auto_refresh_missing_preview_url(self):
        media = self.create_visible_media_file(cloud_file_id="media-list-no-preview")

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_preview_url",
            return_value="https://media.example.test/list-preview.jpg",
        ) as get_preview_url:
            response = self.client.get("/api/v2/inspection/media-files")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        item = next(item for item in response.data["data"]["list"] if item["id"] == media.id)
        self.assertEqual(item["previewUrl"], "https://media.example.test/list-preview.jpg")
        media.refresh_from_db()
        self.assertEqual(media.preview_url, "https://media.example.test/list-preview.jpg")
        get_preview_url.assert_called_once_with("media-list-no-preview")

    def test_media_file_list_should_not_refresh_valid_preview_url(self):
        preview_url = self.signed_media_preview_url(issued_at=timezone.now() + timedelta(hours=1))
        media = self.create_visible_media_file(cloud_file_id="media-list-valid-preview", preview_url=preview_url)

        with patch("apps.inspection_v2.services.DjiConnectionGateway.get_media_preview_url") as get_preview_url:
            response = self.client.get("/api/v2/inspection/media-files")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        item = next(item for item in response.data["data"]["list"] if item["id"] == media.id)
        self.assertEqual(item["previewUrl"], preview_url)
        get_preview_url.assert_not_called()

    def test_media_file_list_should_refresh_expired_preview_url(self):
        expired_preview_url = self.signed_media_preview_url(issued_at=timezone.now() - timedelta(hours=7))
        media = self.create_visible_media_file(cloud_file_id="media-list-expired-preview", preview_url=expired_preview_url)

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_preview_url",
            return_value="https://media.example.test/list-fresh-preview.jpg",
        ) as get_preview_url:
            response = self.client.get("/api/v2/inspection/media-files")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        item = next(item for item in response.data["data"]["list"] if item["id"] == media.id)
        self.assertEqual(item["previewUrl"], "https://media.example.test/list-fresh-preview.jpg")
        media.refresh_from_db()
        self.assertEqual(media.preview_url, "https://media.example.test/list-fresh-preview.jpg")
        get_preview_url.assert_called_once_with("media-list-expired-preview")

    def test_media_file_list_should_not_request_preview_for_video_media(self):
        media = self.create_visible_media_file(
            cloud_file_id="media-list-video-no-preview",
            media_type=CloudMediaType.VIDEO,
            file_name="media-list-video-no-preview.mp4",
            playback_url=self.signed_media_playback_url(issued_at=timezone.now() + timedelta(hours=1)),
        )

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_preview_url",
            side_effect=DjiGatewayUpstreamError(
                "preview failed",
                status_code=502,
                data={"code": "E0001", "msg": "The file is not a supported preview image."},
            ),
        ) as get_preview_url:
            response = self.client.get("/api/v2/inspection/media-files")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        item = next(item for item in response.data["data"]["list"] if item["id"] == media.id)
        self.assertEqual(item["mediaType"], "VIDEO")
        self.assertEqual(item["previewUrl"], "")
        get_preview_url.assert_not_called()

    def test_media_file_list_should_auto_refresh_missing_playback_url_for_video_media(self):
        media = self.create_visible_media_file(
            cloud_file_id="media-list-video-no-playback",
            media_type=CloudMediaType.VIDEO,
            file_name="media-list-video-no-playback.mp4",
        )

        with patch("apps.inspection_v2.services.DjiConnectionGateway.get_media_preview_url") as get_preview_url, patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_playback_url",
            return_value="https://media.example.test/list-playback.m3u8",
        ) as get_playback_url:
            response = self.client.get("/api/v2/inspection/media-files")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        item = next(item for item in response.data["data"]["list"] if item["id"] == media.id)
        self.assertEqual(item["mediaType"], "VIDEO")
        self.assertEqual(item["playbackUrl"], "https://media.example.test/list-playback.m3u8")
        media.refresh_from_db()
        self.assertEqual(media.playback_url, "https://media.example.test/list-playback.m3u8")
        get_preview_url.assert_not_called()
        get_playback_url.assert_called_once_with("media-list-video-no-playback")

    def test_media_file_list_should_not_sync_delayed_video_media_for_filtered_flight_record(self):
        _connection, mission, record = self._completed_pilot2_record(
            route_name="Pilot2 延迟媒体查询航线",
            gateway_sn="GATEWAY-DELAYED-MEDIA-QUERY-001",
        )
        self.assertEqual(record.video_count, 0)

        replay_payload = self._delayed_replay_payload_for_record(
            record,
            cloud_file_id="delayed-media-query-video-001",
        )
        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            return_value=[replay_payload],
        ) as list_media_files, patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_playback_url",
            return_value="https://media.example.test/delayed-playback.m3u8",
        ) as get_playback_url:
            response = self.client.get(
                f"/api/v2/inspection/media-files?flightRecordId={record.id}&missionId={mission.id}"
            )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["total"], 0)
        record.refresh_from_db()
        self.assertEqual(record.video_count, 0)
        list_media_files.assert_not_called()
        get_playback_url.assert_not_called()

    def test_media_file_list_should_not_sync_additional_delayed_videos_when_record_already_has_video(self):
        connection, mission, record = self._completed_pilot2_record(
            route_name="Pilot2 媒体列表补齐多个延迟视频航线",
            gateway_sn="GATEWAY-MEDIA-LIST-EXTRA-DELAYED-001",
        )
        existing_media = self._create_record_video_media(
            connection,
            mission,
            record,
            cloud_file_id="media-list-existing-video-001",
        )
        record.refresh_from_db()
        self.assertEqual(record.video_count, 1)

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.list_media_files",
            return_value=[
                self._delayed_replay_payload_for_record(
                    record,
                    cloud_file_id="media-list-extra-live-video-001",
                ),
                self._dji_video_payload_for_record(
                    record,
                    cloud_file_id="media-list-extra-dji-video-001",
                ),
            ],
        ) as list_media_files, patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_playback_url",
            return_value="https://media.example.test/extra-playback.m3u8",
        ) as get_playback_url:
            response = self.client.get(
                f"/api/v2/inspection/media-files?flightRecordId={record.id}&missionId={mission.id}"
            )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["total"], 1)
        cloud_file_ids = {item["cloudFileId"] for item in response.data["data"]["list"]}
        self.assertEqual(
            cloud_file_ids,
            {
                existing_media.cloud_file_id,
            },
        )
        record.refresh_from_db()
        self.assertEqual(record.video_count, 1)
        list_media_files.assert_not_called()
        get_playback_url.assert_not_called()

    def test_media_file_detail_should_auto_refresh_missing_playback_url_for_video_media(self):
        media = self.create_visible_media_file(
            cloud_file_id="media-detail-video-no-playback",
            media_type=CloudMediaType.VIDEO,
            file_name="media-detail-video-no-playback.mp4",
        )

        with patch("apps.inspection_v2.services.DjiConnectionGateway.get_media_preview_url") as get_preview_url, patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_playback_url",
            return_value="https://media.example.test/detail-playback.m3u8",
        ) as get_playback_url:
            response = self.client.get(f"/api/v2/inspection/media-files/{media.id}")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["mediaType"], "VIDEO")
        self.assertEqual(response.data["data"]["playbackUrl"], "https://media.example.test/detail-playback.m3u8")
        media.refresh_from_db()
        self.assertEqual(media.playback_url, "https://media.example.test/detail-playback.m3u8")
        get_preview_url.assert_not_called()
        get_playback_url.assert_called_once_with("media-detail-video-no-playback")

    def test_media_file_list_should_not_refresh_valid_video_playback_url(self):
        playback_url = self.signed_media_playback_url(issued_at=timezone.now() + timedelta(hours=1))
        media = self.create_visible_media_file(
            cloud_file_id="media-list-video-valid-playback",
            media_type=CloudMediaType.VIDEO,
            file_name="media-list-video-valid-playback.mp4",
            playback_url=playback_url,
        )

        with patch("apps.inspection_v2.services.DjiConnectionGateway.get_media_playback_url") as get_playback_url:
            response = self.client.get("/api/v2/inspection/media-files")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        item = next(item for item in response.data["data"]["list"] if item["id"] == media.id)
        self.assertEqual(item["playbackUrl"], playback_url)
        get_playback_url.assert_not_called()

    def test_media_file_list_should_refresh_expired_video_playback_url(self):
        expired_playback_url = self.signed_media_playback_url(issued_at=timezone.now() - timedelta(hours=7))
        media = self.create_visible_media_file(
            cloud_file_id="media-list-video-expired-playback",
            media_type=CloudMediaType.VIDEO,
            file_name="media-list-video-expired-playback.mp4",
            playback_url=expired_playback_url,
        )

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_playback_url",
            return_value="https://media.example.test/list-fresh-playback.m3u8",
        ) as get_playback_url:
            response = self.client.get("/api/v2/inspection/media-files")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        item = next(item for item in response.data["data"]["list"] if item["id"] == media.id)
        self.assertEqual(item["playbackUrl"], "https://media.example.test/list-fresh-playback.m3u8")
        media.refresh_from_db()
        self.assertEqual(media.playback_url, "https://media.example.test/list-fresh-playback.m3u8")
        get_playback_url.assert_called_once_with("media-list-video-expired-playback")

    def test_media_file_list_should_fail_when_video_playback_refresh_fails(self):
        self.create_visible_media_file(
            cloud_file_id="media-list-video-playback-fails",
            media_type=CloudMediaType.VIDEO,
            file_name="media-list-video-playback-fails.mp4",
        )

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_playback_url",
            side_effect=DjiGatewayUpstreamError("playback failed", status_code=502, data={"error": "boom"}),
        ) as get_playback_url:
            response = self.client.get("/api/v2/inspection/media-files")

        self.assertEqual(response.status_code, 502, getattr(response, "data", response.content))
        self.assertEqual(response.data["code"], "E0001")
        self.assertNotIn("list", response.data.get("data") or {})
        get_playback_url.assert_called_once_with("media-list-video-playback-fails")

    def test_media_file_list_should_mark_video_unavailable_when_playback_url_missing_upstream(self):
        media = self.create_visible_media_file(
            cloud_file_id="media-list-video-playback-unavailable",
            media_type=CloudMediaType.VIDEO,
            file_name="media-list-video-playback-unavailable.mp4",
        )

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_playback_url",
            side_effect=DjiGatewayUpstreamError("未获取到媒体播放地址", status_code=502, data={}),
        ) as get_playback_url:
            response = self.client.get("/api/v2/inspection/media-files")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        item = next(item for item in response.data["data"]["list"] if item["id"] == media.id)
        self.assertEqual(item["mediaType"], "VIDEO")
        self.assertEqual(item["playbackUrl"], "")
        self.assertEqual(item["playbackStatus"], "UNAVAILABLE")
        self.assertEqual(item["playbackError"], "未获取到媒体播放地址")
        get_playback_url.assert_called_once_with("media-list-video-playback-unavailable")

    def test_media_file_detail_should_fail_when_video_playback_refresh_fails(self):
        media = self.create_visible_media_file(
            cloud_file_id="media-detail-video-playback-fails",
            media_type=CloudMediaType.VIDEO,
            file_name="media-detail-video-playback-fails.mp4",
        )

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_playback_url",
            side_effect=DjiGatewayUpstreamError("playback failed", status_code=502, data={"error": "boom"}),
        ) as get_playback_url:
            response = self.client.get(f"/api/v2/inspection/media-files/{media.id}")

        self.assertEqual(response.status_code, 502, getattr(response, "data", response.content))
        self.assertEqual(response.data["code"], "E0001")
        get_playback_url.assert_called_once_with("media-detail-video-playback-fails")

    def test_media_file_detail_should_mark_video_unavailable_when_playback_url_missing_upstream(self):
        media = self.create_visible_media_file(
            cloud_file_id="media-detail-video-playback-unavailable",
            media_type=CloudMediaType.VIDEO,
            file_name="media-detail-video-playback-unavailable.mp4",
        )

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_playback_url",
            side_effect=DjiGatewayUpstreamError("未获取到媒体播放地址", status_code=502, data={}),
        ) as get_playback_url:
            response = self.client.get(f"/api/v2/inspection/media-files/{media.id}")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["mediaType"], "VIDEO")
        self.assertEqual(response.data["data"]["playbackUrl"], "")
        self.assertEqual(response.data["data"]["playbackStatus"], "UNAVAILABLE")
        self.assertEqual(response.data["data"]["playbackError"], "未获取到媒体播放地址")
        get_playback_url.assert_called_once_with("media-detail-video-playback-unavailable")

    def test_media_file_list_and_detail_should_return_existing_thumbnail_url(self):
        media = self.create_visible_media_file(
            cloud_file_id="media-video-with-thumbnail",
            media_type=CloudMediaType.VIDEO,
            file_name="media-video-with-thumbnail.mp4",
            playback_url=self.signed_media_playback_url(issued_at=timezone.now() + timedelta(hours=1)),
            thumbnail_url="https://media.example.test/video-thumb.jpg",
        )

        list_response = self.client.get("/api/v2/inspection/media-files")
        self.assertEqual(list_response.status_code, 200, getattr(list_response, "data", list_response.content))
        item = next(item for item in list_response.data["data"]["list"] if item["id"] == media.id)
        self.assertEqual(item["thumbnailUrl"], "https://media.example.test/video-thumb.jpg")

        detail_response = self.client.get(f"/api/v2/inspection/media-files/{media.id}")
        self.assertEqual(detail_response.status_code, 200, getattr(detail_response, "data", detail_response.content))
        self.assertEqual(detail_response.data["data"]["thumbnailUrl"], "https://media.example.test/video-thumb.jpg")

    def test_media_file_list_should_fail_when_preview_refresh_fails(self):
        self.create_visible_media_file(cloud_file_id="media-list-preview-fails")

        with patch(
            "apps.inspection_v2.services.DjiConnectionGateway.get_media_preview_url",
            side_effect=DjiGatewayUpstreamError("preview failed", status_code=502, data={"error": "boom"}),
        ) as get_preview_url:
            response = self.client.get("/api/v2/inspection/media-files")

        self.assertEqual(response.status_code, 502, getattr(response, "data", response.content))
        self.assertEqual(response.data["code"], "E0001")
        self.assertNotIn("list", response.data.get("data") or {})
        get_preview_url.assert_called_once_with("media-list-preview-fails")

    def test_media_file_refresh_url_should_hide_invisible_media(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="媒体 URL 权限航线")
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-MEDIA-URL-PERM-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
        )
        media = CloudMediaFile.objects.create(
            workspace_id="workspace-hidden-media",
            mission_id=mission["id"],
            device_sn=self.drone.device_sn,
            cloud_file_id="media-hidden-001",
        )

        self.authenticate(self.other_dispatcher)
        with patch("apps.inspection_v2.services.DjiConnectionGateway.get_media_url") as get_media_url:
            response = self.client.post(
                f"/api/v2/inspection/media-files/{media.id}/refresh-url",
                {"urlType": "download"},
                format="json",
            )

        self.assertEqual(response.status_code, 404, getattr(response, "data", response.content))
        get_media_url.assert_not_called()

    def test_media_file_refresh_url_should_reject_when_dji_connection_missing(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="媒体 URL 无连接航线")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            pilot_id=self.owner_pilot.id,
        )
        media = CloudMediaFile.objects.create(
            workspace_id="",
            mission_id=mission["id"],
            device_sn="UNKNOWN-MEDIA-DEVICE",
            cloud_file_id="media-no-connection-001",
        )

        with patch("apps.inspection_v2.services.DjiConnectionGateway.get_media_url") as get_media_url:
            response = self.client.post(
                f"/api/v2/inspection/media-files/{media.id}/refresh-url",
                {"urlType": "download"},
                format="json",
            )

        self.assertEqual(response.status_code, 409, getattr(response, "data", response.content))
        get_media_url.assert_not_called()

    @override_settings(DJI_INTERNAL_API_TOKEN="internal-sync-token")
    def test_media_upload_callback_with_job_id_should_bind_v2_mission_exactly(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="回调航线")
        _connection, dock = self.prepare_route_for_dock_execution(route, dock_sn="DOCK-CALLBACK-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            dock_id=dock.id,
            pilot_id=self.owner_pilot.id,
        )
        self.start_cloud_mission_by_api(mission["id"], dji_job_id="dji-job-callback")
        captured_at = timezone.now().isoformat()

        response = self.client.post(
            "/api/internal/dji/callbacks/media-upload",
            data=json.dumps(
                {
                    "ext": {"sn": self.drone.device_sn, "job_id": "dji-job-callback", "file_id": "callback-photo-001"},
                    "file_group_id": "callback-group-001",
                    "fingerprint": "callback-fingerprint-001",
                    "object_key": "media/callback-photo-001.jpg",
                    "metadata": {"created_time": captured_at},
                    "name": "CALLBACK_PHOTO.JPG",
                }
            ),
            content_type="application/json",
            HTTP_X_DJI_INTERNAL_TOKEN="internal-sync-token",
        )

        self.assertEqual(response.status_code, 200, response.content)
        media = CloudMediaFile.objects.get(cloud_file_id="callback-photo-001")
        self.assertEqual(media.mission_id, mission["id"])
        self.assertIsNone(media.flight_record_id)
        self.assertEqual(media.dji_job_id, "dji-job-callback")
        self.assertEqual(media.object_key, "media/callback-photo-001.jpg")
        self.assertEqual(media.file_group_id, "callback-group-001")

    @override_settings(DJI_INTERNAL_API_TOKEN="internal-sync-token")
    def test_media_upload_callback_without_job_id_should_store_unassigned_v2_media_only(self):
        response = self.client.post(
            "/api/internal/dji/callbacks/media-upload",
            data=json.dumps(
                {
                    "ext": {"sn": self.drone.device_sn, "file_id": "callback-unassigned-001"},
                    "object_key": "media/callback-unassigned-001.jpg",
                    "metadata": {"created_time": timezone.now().isoformat()},
                    "name": "CALLBACK_UNASSIGNED.JPG",
                }
            ),
            content_type="application/json",
            HTTP_X_DJI_INTERNAL_TOKEN="internal-sync-token",
        )

        self.assertEqual(response.status_code, 200, response.content)
        media = CloudMediaFile.objects.get(cloud_file_id="callback-unassigned-001")
        self.assertIsNone(media.mission_id)
        self.assertIsNone(media.flight_record_id)
        self.assertEqual(media.dji_job_id, "")
        self.assertEqual(media.workspace_id, DjiConnection.objects.get(owner_department=self.owner_department).workspace_id)

    @override_settings(DJI_INTERNAL_API_TOKEN="internal-sync-token")
    def test_media_upload_callback_without_job_id_should_bind_unique_pilot2_record_window(self):
        _connection, mission, record = self._completed_pilot2_record(
            route_name="Pilot2 回调唯一窗口航线",
            gateway_sn="GATEWAY-CALLBACK-PILOT2-UNIQUE-001",
        )
        replay_payload = self._delayed_replay_payload_for_record(
            record,
            cloud_file_id="callback-pilot2-replay-001",
        )

        response = self.client.post(
            "/api/internal/dji/callbacks/media-upload",
            data=json.dumps(replay_payload),
            content_type="application/json",
            HTTP_X_DJI_INTERNAL_TOKEN="internal-sync-token",
        )

        self.assertEqual(response.status_code, 200, response.content)
        media = CloudMediaFile.objects.get(cloud_file_id="callback-pilot2-replay-001")
        self.assertEqual(media.mission_id, mission.id)
        self.assertEqual(media.flight_record_id, record.id)
        self.assertEqual(media.media_type, CloudMediaType.VIDEO)

    def test_osd_event_should_update_running_flight_telemetry(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="OSD 航线")
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        connection.workspace_id = "workspace-osd-001"
        connection.save(update_fields=["workspace_id", "updated_at"])
        dock = self.bind_dock(self.owner_department, self.owner_admin, "DOCK-OSD-001", connection=connection)
        self.upload_route_kmz_by_api(route["id"], connection)
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            dock_id=dock.id,
            pilot_id=self.owner_pilot.id,
        )
        self.start_cloud_mission_by_api(mission["id"], dji_job_id="dji-job-osd")

        result = apply_osd_telemetry(
            device_sn=self.drone.device_sn,
            payload={
                "timestamp": int(timezone.now().timestamp() * 1000),
                "data": {
                    "latitude": 31.2304,
                    "longitude": 121.4737,
                    "height": 120.5,
                    "horizontal_speed": 8.2,
                    "attitude_head": 91.0,
                    "battery": {"capacity_percent": 87},
                },
            },
        )

        self.assertEqual(result["updated"], 1)
        active_response = self.client.get("/api/v2/inspection/active-flights")
        telemetry = active_response.data["data"]["list"][0]["telemetry"]
        self.assertEqual(telemetry["batteryPercent"], 87)
        self.assertEqual(telemetry["latitude"], "31.23040000")

    def test_cancel_running_cloud_mission_should_cancel_dji_job_before_local_close(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="取消航线")
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        connection.workspace_id = "workspace-cancel-001"
        connection.save(update_fields=["workspace_id", "updated_at"])
        dock = self.bind_dock(self.owner_department, self.owner_admin, "DOCK-CANCEL-001", connection=connection)
        self.upload_route_kmz_by_api(route["id"], connection)
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            dock_id=dock.id,
            pilot_id=self.owner_pilot.id,
        )
        self.start_cloud_mission_by_api(mission["id"], dji_job_id="dji-job-cancel")

        with patch("apps.inspection_v2.services.DjiConnectionGateway.cancel_mission", return_value={}) as cancel_job, patch(
            "apps.inspection_v2.services.DjiConnectionGateway.stop_live", return_value={}
        ) as stop_live:
            cancel_response = self.client.post(
                f"/api/v2/inspection/missions/{mission['id']}/cancel",
                {"reason": "调度取消"},
                format="json",
            )

        self.assertEqual(cancel_response.status_code, 200, getattr(cancel_response, "data", cancel_response.content))
        self.assertEqual(cancel_response.data["data"]["status"], MissionStatus.CANCELED)
        cancel_job.assert_called_once_with("dji-job-cancel")
        stop_live.assert_called_once_with(self.drone.device_sn, video_id=f"{self.drone.device_sn}/88-0-0/normal-0")

    def test_v2_dji_worker_should_dispatch_osd_and_flighttask_progress_messages(self):
        worker = V2DjiWorker()

        with patch("apps.inspection_v2.management.commands.run_v2_dji_worker.apply_osd_telemetry") as osd_handler:
            worker.handle_message(
                "thing/product/DRONE-WORKER-001/osd",
                {"data": {"latitude": 31.1}},
            )
        osd_handler.assert_called_once_with(device_sn="DRONE-WORKER-001", payload={"data": {"latitude": 31.1}})

        with patch("apps.inspection_v2.management.commands.run_v2_dji_worker.apply_cloud_execution_event") as progress_handler:
            worker.handle_message(
                "thing/product/DRONE-WORKER-001/events",
                {"method": "flighttask_progress", "data": {"job_id": "job-worker-001", "status": "ok", "progress": 100}},
            )
        progress_handler.assert_called_once_with(
            dji_job_id="job-worker-001",
            status="ok",
            payload={"method": "flighttask_progress", "data": {"job_id": "job-worker-001", "status": "ok", "progress": 100}},
        )

        with patch("apps.inspection_v2.management.commands.run_v2_dji_worker.apply_device_status_event") as status_handler:
            worker.handle_message(
                "sys/product/DRONE-WORKER-001/status",
                {"status": "offline"},
            )
        status_handler.assert_called_once_with(device_sn="DRONE-WORKER-001", payload={"status": "offline"})

    def test_v2_dji_worker_should_persist_latest_mqtt_message_and_drone_snapshot(self):
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        worker = V2DjiWorker()

        result = worker.handle_message(
            "thing/product/V2-DRONE-001/osd",
            {
                "timestamp": int(timezone.now().timestamp() * 1000),
                "data": {
                    "latitude": 31.2304,
                    "longitude": 121.4737,
                    "height": 120.5,
                    "horizontal_speed": 8.2,
                    "attitude_head": 91.0,
                    "battery": {"capacity_percent": 87},
                },
            },
            connection=connection,
        )

        self.assertEqual(result["message"]["topicKind"], "osd")
        latest = MqttLatestMessage.objects.get(dji_connection=connection, device_sn="V2-DRONE-001", topic_kind="osd")
        self.assertEqual(latest.raw_payload["data"]["latitude"], 31.2304)
        snapshot = DroneTelemetrySnapshot.objects.get(drone=self.drone)
        self.assertEqual(str(snapshot.latitude), "31.23040000")
        self.assertEqual(snapshot.battery_percent, 87)
        health = MqttConnectionHealth.objects.get(dji_connection=connection)
        self.assertEqual(health.message_count, 1)
        self.assertEqual(health.status, "MESSAGE_RECEIVED")

    def test_v2_dji_worker_should_record_mqtt_addr_when_subscribed(self):
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        worker = V2DjiWorker()
        mqtt_config = SimpleNamespace(
            mqtt_addr="tcp://broker.example.test:1883",
            mqtt_username="mqtt-user",
            mqtt_password="mqtt-pass",
        )

        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.on_connect = None
                self.on_message = None
                self.userdata = None

            def user_data_set(self, userdata):
                self.userdata = userdata

            def username_pw_set(self, username, password):
                pass

            def connect(self, host, port, keepalive):
                self.on_connect(self, self.userdata, None, 0, None)

            def subscribe(self, topic):
                pass

            def loop(self, timeout=1.0):
                worker.stop_event.set()
                return 0

            def disconnect(self):
                pass

        with patch(
            "apps.inspection_v2.management.commands.run_v2_dji_worker.DjiConnectionGateway.get_workspace_config",
            return_value=mqtt_config,
        ), patch("paho.mqtt.client.Client", FakeClient):
            worker._run_connection(connection)

        health = MqttConnectionHealth.objects.get(dji_connection=connection)
        self.assertEqual(health.status, "SUBSCRIBED")
        self.assertEqual(health.mqtt_addr, "tcp://broker.example.test:1883")

    def test_v2_dji_worker_should_mark_error_when_mqtt_loop_reports_connection_loss(self):
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        worker = V2DjiWorker()
        mqtt_config = SimpleNamespace(
            mqtt_addr="tcp://broker.example.test:1883",
            mqtt_username="mqtt-user",
            mqtt_password="mqtt-pass",
        )

        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.on_connect = None
                self.on_message = None
                self.userdata = None

            def user_data_set(self, userdata):
                self.userdata = userdata

            def username_pw_set(self, username, password):
                pass

            def connect(self, host, port, keepalive):
                self.on_connect(self, self.userdata, None, 0, None)

            def subscribe(self, topic):
                pass

            def loop(self, timeout=1.0):
                worker.stop_event.set()
                return 7

            def disconnect(self):
                pass

        with patch(
            "apps.inspection_v2.management.commands.run_v2_dji_worker.DjiConnectionGateway.get_workspace_config",
            return_value=mqtt_config,
        ), patch("paho.mqtt.client.Client", FakeClient):
            worker._run_connection(connection)

        health = MqttConnectionHealth.objects.get(dji_connection=connection)
        self.assertEqual(health.status, "ERROR")
        self.assertIn("MQTT loop returned", health.last_error)

    def test_v2_dji_worker_command_should_support_once_mode(self):
        with patch(
            "apps.inspection_v2.management.commands.run_v2_dji_worker.V2DjiWorker.run_once",
            return_value={"connections": 0},
        ) as run_once:
            call_command("run_v2_dji_worker", "--once")
        run_once.assert_called_once()

    def test_v2_dji_worker_once_should_sync_resource_status_for_active_connections(self):
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        worker = V2DjiWorker()

        with patch(
            "apps.inspection_v2.management.commands.run_v2_dji_worker.sync_connection_resources_from_upstream",
            return_value={"drones": 1, "docks": 0, "gateways": 0, "payloads": 0},
        ) as sync_resources:
            summary = worker.run_once()

        sync_resources.assert_called_once_with(connection)
        self.assertEqual(summary["connections"], 1)
        self.assertEqual(summary["resourceSyncSucceeded"], 1)
        self.assertEqual(summary["resourceSyncFailed"], 0)

    def test_v2_dji_worker_once_should_report_resource_sync_errors_without_failing(self):
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        worker = V2DjiWorker()

        with patch(
            "apps.inspection_v2.management.commands.run_v2_dji_worker.sync_connection_resources_from_upstream",
            side_effect=RuntimeError("upstream unavailable"),
        ) as sync_resources:
            summary = worker.run_once()

        sync_resources.assert_called_once_with(connection)
        self.assertEqual(summary["connections"], 1)
        self.assertEqual(summary["resourceSyncSucceeded"], 0)
        self.assertEqual(summary["resourceSyncFailed"], 1)

    def test_pilot_should_only_see_own_execution_data_and_cannot_cancel_task(self):
        second_pilot_user, second_pilot_account = create_v2_actor(
            username="owner_second_pilot",
            role_code=FixedRole.PILOT,
            department=self.owner_department,
        )
        second_pilot = self.create_pilot(second_pilot_account, "资源队二号飞手")
        first_route = self.create_route_by_api(self.owner_dispatcher, name="飞手一号航线")
        first_mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=first_route["id"],
            drone_id=self.drone.id,
            pilot_id=self.owner_pilot.id,
            name="飞手一号任务",
        )
        second_route = self.create_route_by_api(self.owner_dispatcher, name="飞手二号航线")
        second_mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=second_route["id"],
            drone_id=self.drone.id,
            pilot_id=second_pilot.id,
            name="飞手二号任务",
        )

        self.authenticate(self.owner_pilot_user)
        missions_response = self.client.get("/api/v2/inspection/missions")
        routes_response = self.client.get("/api/v2/inspection/routes")
        cancel_response = self.client.post(
            f"/api/v2/inspection/missions/{first_mission['id']}/cancel",
            {"reason": "飞手不能取消任务"},
            format="json",
        )

        self.assertEqual(missions_response.status_code, 200, getattr(missions_response, "data", missions_response.content))
        self.assertEqual([item["id"] for item in missions_response.data["data"]["list"]], [first_mission["id"]])
        self.assertEqual(routes_response.status_code, 200, getattr(routes_response, "data", routes_response.content))
        self.assertEqual([item["id"] for item in routes_response.data["data"]["list"]], [first_route["id"]])
        self.assertIn("coverImageUrl", routes_response.data["data"]["list"][0])
        self.assertEqual(cancel_response.status_code, 403, getattr(cancel_response, "data", cancel_response.content))

        self.authenticate(second_pilot_user)
        second_missions_response = self.client.get("/api/v2/inspection/missions")
        self.assertEqual([item["id"] for item in second_missions_response.data["data"]["list"]], [second_mission["id"]])

    def test_active_flights_should_be_limited_to_dispatchers_and_assigned_pilot(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="实时权限航线")
        _connection, dock = self.prepare_route_for_dock_execution(route, dock_sn="DOCK-ACTIVE-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            dock_id=dock.id,
            pilot_id=self.owner_pilot.id,
            name="实时权限任务",
        )
        self.start_cloud_mission_by_api(mission["id"], dji_job_id="dji-job-active")

        self.authenticate(self.owner_admin)
        admin_response = self.client.get("/api/v2/inspection/active-flights")
        self.assertEqual(admin_response.status_code, 403, getattr(admin_response, "data", admin_response.content))

        self.authenticate(self.other_pilot_user)
        other_pilot_response = self.client.get("/api/v2/inspection/active-flights")
        self.assertEqual(other_pilot_response.status_code, 200, getattr(other_pilot_response, "data", other_pilot_response.content))
        self.assertEqual(other_pilot_response.data["data"]["total"], 0)

        self.authenticate(self.owner_pilot_user)
        assigned_pilot_response = self.client.get("/api/v2/inspection/active-flights")
        self.assertEqual(assigned_pilot_response.status_code, 200, getattr(assigned_pilot_response, "data", assigned_pilot_response.content))
        self.assertEqual(assigned_pilot_response.data["data"]["total"], 1)

    def test_shared_resource_should_require_use_permission_for_new_mission(self):
        group = ResourceShareGroup.objects.create(owner_department=self.owner_department, name="任务共享")
        ResourceShareGroupTargetDepartment.objects.create(share_group=group, department=self.other_department)
        share = ResourceSharePermission.objects.create(
            share_group=group,
            resource_type=ResourceType.DRONE,
            resource_object_id=self.drone.id,
            permissions=["view", "monitor"],
        )
        route = self.create_route_by_api(self.other_dispatcher, name="共享资源航线")
        owner_connection = ResourceBinding.objects.get(resource_type=ResourceType.DRONE, resource_object_id=self.drone.id).dji_connection
        WaypointRouteCloudFile.objects.filter(route_id=route["id"]).update(
            dji_connection=owner_connection,
            workspace_id=owner_connection.workspace_id,
        )
        executor = self.bind_gateway(self.other_department, self.other_dispatcher, "GATEWAY-SHARED-001", connection=owner_connection)
        self.authenticate(self.other_dispatcher)
        denied_response = self.client.post(
            "/api/v2/inspection/missions",
            {
                "name": "缺少 use 的任务",
                "routeId": route["id"],
                "droneId": self.drone.id,
                "executorId": executor.id,
                "pilotAccountProfileId": self.other_pilot.id,
            },
            format="json",
        )
        self.assertEqual(denied_response.status_code, 403, getattr(denied_response, "data", denied_response.content))

        share.permissions = ["view", "monitor", "use"]
        share.save(update_fields=["permissions", "updated_at"])
        allowed_response = self.client.post(
            "/api/v2/inspection/missions",
            {
                "name": "具备 use 的任务",
                "routeId": route["id"],
                "droneId": self.drone.id,
                "executorId": executor.id,
                "pilotAccountProfileId": self.other_pilot.id,
            },
            format="json",
        )
        self.assertEqual(allowed_response.status_code, 201, getattr(allowed_response, "data", allowed_response.content))
        self.assertEqual(allowed_response.data["data"]["creatorDepartmentId"], self.other_department.id)
        self.assertEqual(allowed_response.data["data"]["primaryResourceOwnerDepartmentId"], self.owner_department.id)

    def test_live_start_should_allow_online_resource_without_active_flight_and_proxy_dji_control(self):
        self.authenticate(self.owner_dispatcher)
        self.assertFalse(FlightSession.objects.filter(drone=self.drone).exists())

        with patch(
            "apps.inspection_v2.views.DjiConnectionGateway.start_live",
            return_value={"webrtc_url": "https://live.example.test/webrtc", "rtmp_url": "rtmp://live.example.test/app"},
        ) as start_live:
            live_response = self.client.post(
                "/api/v2/inspection/live/start",
                {"droneId": self.drone.id, "videoId": f"{self.drone.device_sn}/88-0-0/normal-0", "urlType": 1},
                format="json",
            )

        self.assertEqual(live_response.status_code, 200, getattr(live_response, "data", live_response.content))
        self.assertEqual(live_response.data["data"]["webrtc_url"], "https://live.example.test/webrtc")
        start_live.assert_called_once_with(self.drone.device_sn, video_id=f"{self.drone.device_sn}/88-0-0/normal-0", url_type=1)

    def test_live_start_should_reject_snake_case_request_fields(self):
        self.authenticate(self.owner_dispatcher)
        with patch("apps.inspection_v2.views.DjiConnectionGateway.start_live") as start_live:
            response = self.client.post(
                "/api/v2/inspection/live/start",
                {"droneId": self.drone.id, "video_id": f"{self.drone.device_sn}/88-0-0/normal-0"},
                format="json",
            )

        self.assertEqual(response.status_code, 400, getattr(response, "data", response.content))
        start_live.assert_not_called()

    def test_live_capacity_should_allow_visible_online_drone_without_active_flight(self):
        self.authenticate(self.owner_dispatcher)
        self.assertFalse(FlightSession.objects.filter(drone=self.drone).exists())

        with patch(
            "apps.inspection_v2.views.DjiConnectionGateway.get_live_capacity",
            return_value={"sn": self.drone.device_sn, "cameras_list": [{"index": "88-0-0"}]},
        ) as get_capacity:
            response = self.client.get("/api/v2/inspection/live/capacity", {"droneId": self.drone.id})

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["sn"], self.drone.device_sn)
        get_capacity.assert_called_once_with(self.drone.device_sn)

    def test_live_switch_should_proxy_camel_case_video_type_to_dji(self):
        self.authenticate(self.owner_dispatcher)
        video_id = f"{self.drone.device_sn}/88-0-0/wide-0"

        with patch("apps.inspection_v2.views.DjiConnectionGateway.switch_live", return_value={}) as switch_live:
            response = self.client.post(
                "/api/v2/inspection/live/switch",
                {"droneId": self.drone.id, "videoId": video_id, "videoType": "wide"},
                format="json",
            )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        switch_live.assert_called_once_with(self.drone.device_sn, video_id=video_id, video_type="wide")

    def test_camera_actions_should_translate_supported_dji_payload_commands_and_record_operation(self):
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        executor = self.bind_gateway(self.owner_department, self.owner_admin, "GATEWAY-CAMERA-001", connection=connection)
        self.authenticate(self.owner_dispatcher)
        events = []

        def grab_authority(gateway_sn, payload_index):
            events.append(("authority", gateway_sn, payload_index))
            return {"granted": True}

        def send_command(gateway_sn, action, data):
            events.append(("command", gateway_sn, action, data))
            return {"accepted": True, "cmd": action}

        cases = [
            ("camera_photo_take", {}, {"payload_index": "88-0-0"}),
            ("camera_recording_start", {}, {"payload_index": "88-0-0"}),
            ("camera_recording_stop", {}, {"payload_index": "88-0-0"}),
            ("camera_mode_switch", {"cameraMode": 1}, {"payload_index": "88-0-0", "camera_mode": 1}),
            (
                "camera_focal_length_set",
                {"cameraType": "zoom", "zoomFactor": 12.5},
                {"payload_index": "88-0-0", "camera_type": "zoom", "zoom_factor": 12.5},
            ),
            (
                "camera_aim",
                {"cameraType": "wide", "locked": False, "x": 0.25, "y": 0.75},
                {"payload_index": "88-0-0", "camera_type": "wide", "locked": False, "x": 0.25, "y": 0.75},
            ),
            ("gimbal_reset", {"resetMode": 0}, {"payload_index": "88-0-0", "reset_mode": 0}),
        ]

        with patch("apps.inspection_v2.views.DjiConnectionGateway.grab_payload_authority", side_effect=grab_authority), patch(
            "apps.inspection_v2.views.DjiConnectionGateway.send_payload_command", side_effect=send_command
        ):
            for action, extra_payload, expected_dji_data in cases:
                with self.subTest(action=action):
                    response = self.client.post(
                        "/api/v2/inspection/camera/actions",
                        {
                            "droneId": self.drone.id,
                            "executorId": executor.id,
                            "payloadIndex": "88-0-0",
                            "action": action,
                            **extra_payload,
                        },
                        format="json",
                    )

                    self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
                    data = response.data["data"]
                    self.assertEqual(data["status"], CameraOperationStatus.SUCCEEDED)
                    self.assertEqual(data["action"], action)
                    self.assertEqual(data["droneSn"], self.drone.device_sn)
                    self.assertEqual(data["gatewaySn"], executor.device_sn)
                    operation = CameraOperation.objects.get(pk=data["operationId"])
                    self.assertEqual(operation.status, CameraOperationStatus.SUCCEEDED)
                    self.assertEqual(operation.payload_index, "88-0-0")
                    self.assertEqual(operation.upstream_request["command"]["data"], expected_dji_data)

        expected_events = []
        for action, _extra_payload, expected_dji_data in cases:
            expected_events.append(("authority", executor.device_sn, "88-0-0"))
            expected_events.append(("command", executor.device_sn, action, expected_dji_data))
        self.assertEqual(events, expected_events)

    def test_camera_action_validation_should_reject_invalid_action_payloads_before_upstream_call(self):
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        executor = self.bind_gateway(self.owner_department, self.owner_admin, "GATEWAY-CAMERA-VALIDATE-001", connection=connection)
        self.authenticate(self.owner_dispatcher)
        base_payload = {"droneId": self.drone.id, "executorId": executor.id, "payloadIndex": "88-0-0"}
        invalid_payloads = [
            {**base_payload, "action": "camera_unknown"},
            {"droneId": self.drone.id, "executorId": executor.id, "payload_index": "88-0-0", "action": "camera_photo_take"},
            {**base_payload, "action": "camera_mode_switch"},
            {**base_payload, "action": "camera_mode_switch", "camera_mode": 1},
            {**base_payload, "action": "camera_mode_switch", "cameraMode": 9},
            {**base_payload, "action": "camera_focal_length_set", "cameraType": "wide", "zoomFactor": 10},
            {**base_payload, "action": "camera_focal_length_set", "camera_type": "zoom", "zoom_factor": 10},
            {**base_payload, "action": "camera_focal_length_set", "cameraType": "ir", "zoomFactor": 21},
            {**base_payload, "action": "camera_aim", "cameraType": "zoom", "locked": True, "x": 1.2, "y": 0.5},
            {**base_payload, "action": "camera_aim", "camera_type": "zoom", "locked": True, "x": 0.5, "y": 0.5},
            {**base_payload, "action": "gimbal_reset"},
            {**base_payload, "action": "gimbal_reset", "reset_mode": 0},
            {"droneId": self.drone.id, "executorId": executor.id, "action": "camera_photo_take"},
        ]

        with patch("apps.inspection_v2.views.DjiConnectionGateway.grab_payload_authority") as grab_authority, patch(
            "apps.inspection_v2.views.DjiConnectionGateway.send_payload_command"
        ) as send_command:
            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    response = self.client.post("/api/v2/inspection/camera/actions", payload, format="json")
                    self.assertEqual(response.status_code, 400, getattr(response, "data", response.content))

        grab_authority.assert_not_called()
        send_command.assert_not_called()

    def test_camera_actions_should_require_operator_permission_online_resources_and_same_dji_connection(self):
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        executor = self.bind_gateway(self.owner_department, self.owner_admin, "GATEWAY-CAMERA-PERM-001", connection=connection)
        payload = {
            "droneId": self.drone.id,
            "executorId": executor.id,
            "payloadIndex": "88-0-0",
            "action": "camera_photo_take",
        }

        no_control_user, _profile = create_v2_actor(username="camera_no_control", role_code=None, department=self.owner_department)
        self.authenticate(no_control_user)
        no_control_response = self.client.post("/api/v2/inspection/camera/actions", payload, format="json")
        self.assertEqual(no_control_response.status_code, 403, getattr(no_control_response, "data", no_control_response.content))

        self.authenticate(self.owner_dispatcher)
        self.drone.online_status = False
        self.drone.save(update_fields=["online_status", "updated_at"])
        with patch("apps.inspection_v2.views.DjiConnectionGateway.grab_payload_authority") as grab_authority:
            offline_drone_response = self.client.post("/api/v2/inspection/camera/actions", payload, format="json")
        self.assertEqual(offline_drone_response.status_code, 409, getattr(offline_drone_response, "data", offline_drone_response.content))
        grab_authority.assert_not_called()

        self.drone.online_status = True
        self.drone.save(update_fields=["online_status", "updated_at"])
        executor.online_status = False
        executor.save(update_fields=["online_status", "updated_at"])
        offline_executor_response = self.client.post("/api/v2/inspection/camera/actions", payload, format="json")
        self.assertEqual(offline_executor_response.status_code, 409, getattr(offline_executor_response, "data", offline_executor_response.content))

        executor.online_status = True
        executor.save(update_fields=["online_status", "updated_at"])
        other_connection = DjiConnection.objects.create(
            owner_department=self.owner_department,
            name="camera second connection",
            base_url="https://dji-second.example.test",
            username="admin",
            password="secret",
            workspace_id="workspace-second",
            access_token="token-second",
            created_by_user=self.owner_admin,
        )
        other_executor = self.bind_gateway(
            self.owner_department,
            self.owner_admin,
            "GATEWAY-CAMERA-DIFF-001",
            connection=other_connection,
        )
        mismatch_response = self.client.post(
            "/api/v2/inspection/camera/actions",
            {**payload, "executorId": other_executor.id},
            format="json",
        )
        self.assertEqual(mismatch_response.status_code, 409, getattr(mismatch_response, "data", mismatch_response.content))

    def test_camera_action_upstream_failures_should_record_failed_operation(self):
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        executor = self.bind_gateway(self.owner_department, self.owner_admin, "GATEWAY-CAMERA-FAIL-001", connection=connection)
        self.authenticate(self.owner_dispatcher)
        payload = {
            "droneId": self.drone.id,
            "executorId": executor.id,
            "payloadIndex": "88-0-0",
            "action": "camera_photo_take",
        }

        with patch(
            "apps.inspection_v2.views.DjiConnectionGateway.grab_payload_authority",
            side_effect=DjiGatewayUpstreamError("authority failed", status_code=502, data={"code": "E0001"}),
        ) as grab_authority, patch("apps.inspection_v2.views.DjiConnectionGateway.send_payload_command") as send_command:
            authority_response = self.client.post("/api/v2/inspection/camera/actions", payload, format="json")

        self.assertEqual(authority_response.status_code, 502, getattr(authority_response, "data", authority_response.content))
        authority_operation = CameraOperation.objects.latest("id")
        self.assertEqual(authority_operation.status, CameraOperationStatus.FAILED)
        self.assertIn("authority failed", authority_operation.error_message)
        grab_authority.assert_called_once_with(executor.device_sn, "88-0-0")
        send_command.assert_not_called()

        with patch("apps.inspection_v2.views.DjiConnectionGateway.grab_payload_authority", return_value={"granted": True}), patch(
            "apps.inspection_v2.views.DjiConnectionGateway.send_payload_command",
            side_effect=DjiGatewayUpstreamError(
                "DJI upstream business error",
                status_code=200,
                data={"code": "E0001", "msg": "The device is offline."},
            ),
        ) as send_command:
            command_response = self.client.post("/api/v2/inspection/camera/actions", payload, format="json")

        self.assertEqual(command_response.status_code, 502, getattr(command_response, "data", command_response.content))
        command_operation = CameraOperation.objects.latest("id")
        self.assertEqual(command_operation.status, CameraOperationStatus.FAILED)
        self.assertEqual(command_operation.upstream_response["authority"], {"granted": True})
        self.assertEqual(command_operation.upstream_response["error"]["upstream"]["msg"], "The device is offline.")
        send_command.assert_called_once()

    def test_live_start_should_reject_non_control_actor(self):
        no_control_user, _profile = create_v2_actor(username="live_no_control", role_code=None, department=self.owner_department)
        self.authenticate(no_control_user)
        response = self.client.post(
            "/api/v2/inspection/live/start",
            {"droneId": self.drone.id, "videoId": f"{self.drone.device_sn}/88-0-0/normal-0"},
            format="json",
        )
        self.assertEqual(response.status_code, 403, getattr(response, "data", response.content))
