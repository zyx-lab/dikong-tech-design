import json
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import DirectoryStatus
from apps.dji_cloud.gateway import DjiGatewayUpstreamError
from apps.iam_v2.models import (
    Department,
    FixedRole,
    ResourceShareGroup,
    ResourceShareGroupTargetDepartment,
    V2AccountProfile,
    V2AccountQualification,
    V2AccountRoleAssignment,
)
from apps.inspection_v2.models import (
    CloudMediaFile,
    FlightSession,
    InspectionMission,
    LiveStreamStatus,
    MissionCloudExecution,
    MissionStatus,
    WaypointRoute,
)
from apps.inspection_v2.services import apply_cloud_execution_event, apply_osd_telemetry
from apps.inspection_v2.management.commands.run_v2_dji_worker import V2DjiWorker
from apps.resource_v2.models import (
    BindingStatus,
    DjiConnection,
    DroneTelemetrySnapshot,
    DroneResource,
    GatewayResource,
    MqttConnectionHealth,
    MqttLatestMessage,
    ResourceBinding,
    ResourceSharePermission,
    ResourceType,
)
from apps.workforce_v2.models import PilotProfile

User = get_user_model()


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

    def authenticate(self, user):
        self.client.force_authenticate(user)

    def create_pilot(self, account_profile, name):
        pilot = PilotProfile.objects.create(account_profile=account_profile, display_name=name)
        V2AccountQualification.objects.create(
            account_profile=account_profile,
            role_code=FixedRole.PILOT,
            qualification_type="多旋翼巡检",
            certificate_no=f"CERT-{pilot.id}",
            issued_at=timezone.now().date(),
            expires_at=timezone.now().date().replace(year=timezone.now().date().year + 1),
            status=DirectoryStatus.ACTIVE,
            remark="当前有效",
        )
        return pilot

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

    def create_route_by_api(self, user, name="一号航线"):
        self.authenticate(user)
        response = self.client.post("/api/v2/inspection/routes", self.route_payload(name), format="json")
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

    def create_mission_by_api(self, user, *, route_id, drone_id, pilot_id, executor_id=None, name="一号任务"):
        self.authenticate(user)
        payload = {
            "name": name,
            "routeId": route_id,
            "droneId": drone_id,
            "pilotId": pilot_id,
            "remark": "首版闭环任务",
        }
        if executor_id is not None:
            payload["executorId"] = executor_id
        response = self.client.post(
            "/api/v2/inspection/missions",
            payload,
            format="json",
        )
        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        return response.data["data"]

    def upload_route_kmz_by_api(self, route_id, connection, *, wayline_type=0):
        self.authenticate(self.owner_dispatcher)
        kmz_file = SimpleUploadedFile("route.kmz", b"fake-kmz-content", content_type="application/vnd.google-earth.kmz")
        with patch(
            "apps.inspection_v2.views.DjiConnectionGateway.upload_route",
            return_value={"dji_wayline_id": "wayline-kmz-001", "download_url": "/waylines/wayline-kmz-001/url"},
        ):
            response = self.client.post(
                f"/api/v2/inspection/routes/{route_id}/kmz",
                {"djiConnectionId": connection.id, "waylineType": wayline_type, "kmzFile": kmz_file},
                format="multipart",
            )
        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        return response.data["data"]

    def prepare_route_for_cloud_execution(self, route, *, gateway_sn="GATEWAY-TEST-001"):
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        connection.workspace_id = f"workspace-{gateway_sn.lower()}"
        connection.save(update_fields=["workspace_id", "updated_at"])
        executor = self.bind_gateway(self.owner_department, self.owner_admin, gateway_sn, connection=connection)
        self.upload_route_kmz_by_api(route["id"], connection)
        return connection, executor

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
        ), patch("apps.inspection_v2.services.DjiConnectionGateway.create_mission", return_value={"dji_job_id": dji_job_id}):
            response = self.client.post(f"/api/v2/inspection/missions/{mission_id}/start", {}, format="json")
        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        return response

    def test_route_json_create_should_preserve_existing_contract(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="JSON 基线航线")

        self.assertEqual(route["name"], "JSON 基线航线")
        self.assertEqual(route["defaultAltitude"], "120.00")
        self.assertEqual(route["defaultSpeed"], "8.50")
        self.assertEqual(len(route["waypoints"]), 2)

    def test_route_json_update_should_preserve_existing_contract(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="JSON 更新基线航线")
        payload = self.route_payload(name="JSON 更新后航线")
        payload["defaultAltitude"] = "130.00"

        self.authenticate(self.owner_dispatcher)
        response = self.client.put(f"/api/v2/inspection/routes/{route['id']}", payload, format="json")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["name"], "JSON 更新后航线")
        self.assertEqual(response.data["data"]["defaultAltitude"], "130.00")
        self.assertEqual(len(response.data["data"]["waypoints"]), 2)

    def test_route_save_should_accept_cover_image_and_return_cover_image_url(self):
        self.authenticate(self.owner_dispatcher)
        payload = self.route_payload(name="封面航线")
        payload["waypoints"] = json.dumps(payload["waypoints"])
        payload["coverImage"] = SimpleUploadedFile("cover.jpg", b"\xff\xd8\xff\xd9", content_type="image/jpeg")

        with tempfile.TemporaryDirectory() as media_root, self.storage_settings(media_root):
            response = self.client.post("/api/v2/inspection/routes", payload, format="multipart")

        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        self.assertIn("coverImageUrl", response.data["data"])
        self.assertRegex(response.data["data"]["coverImageUrl"], r"\.(jpg|jpeg|png|webp)$")

    def test_route_save_should_reject_non_image_cover_file(self):
        self.authenticate(self.owner_dispatcher)
        payload = self.route_payload(name="非法封面航线")
        payload["waypoints"] = json.dumps(payload["waypoints"])
        payload["coverImage"] = SimpleUploadedFile("cover.txt", b"not an image", content_type="text/plain")

        with tempfile.TemporaryDirectory() as media_root, self.storage_settings(media_root):
            response = self.client.post("/api/v2/inspection/routes", payload, format="multipart")

        self.assertEqual(response.status_code, 400, getattr(response, "data", response.content))
        self.assertIn("coverImage", str(response.data))
        self.assertIn("只支持上传 jpg/jpeg/png/webp 图片，且大小不能超过 5MB", str(response.data))

    def test_route_multipart_should_reject_invalid_waypoints_json(self):
        self.authenticate(self.owner_dispatcher)
        payload = self.route_payload(name="非法航点航线")
        payload["waypoints"] = "[invalid-json"
        payload["coverImage"] = SimpleUploadedFile("cover.jpg", b"\xff\xd8\xff\xd9", content_type="image/jpeg")

        with tempfile.TemporaryDirectory() as media_root, self.storage_settings(media_root):
            response = self.client.post("/api/v2/inspection/routes", payload, format="multipart")

        self.assertEqual(response.status_code, 400, getattr(response, "data", response.content))
        self.assertIn("waypoints", str(response.data))

    def test_route_update_should_replace_cover_image_and_preserve_when_omitted(self):
        self.authenticate(self.owner_dispatcher)
        create_payload = self.route_payload(name="封面替换航线")
        create_payload["waypoints"] = json.dumps(create_payload["waypoints"])
        create_payload["coverImage"] = SimpleUploadedFile("cover-a.jpg", b"\xff\xd8\xff\xd9", content_type="image/jpeg")

        with tempfile.TemporaryDirectory() as media_root, self.storage_settings(media_root):
            create_response = self.client.post("/api/v2/inspection/routes", create_payload, format="multipart")
            self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
            route_id = create_response.data["data"]["id"]
            first_url = create_response.data["data"]["coverImageUrl"]
            route = WaypointRoute.objects.get(pk=route_id)
            first_cover_name = route.cover_image.name

            json_payload = self.route_payload(name="封面保留航线")
            preserve_response = self.client.put(f"/api/v2/inspection/routes/{route_id}", json_payload, format="json")
            self.assertEqual(preserve_response.status_code, 200, getattr(preserve_response, "data", preserve_response.content))
            self.assertEqual(preserve_response.data["data"]["coverImageUrl"], first_url)

            empty_cover_payload = self.route_payload(name="封面空字段保留航线")
            empty_cover_payload["waypoints"] = json.dumps(empty_cover_payload["waypoints"])
            empty_cover_payload["coverImage"] = ""
            empty_cover_response = self.client.put(f"/api/v2/inspection/routes/{route_id}", empty_cover_payload, format="multipart")
            self.assertEqual(empty_cover_response.status_code, 200, getattr(empty_cover_response, "data", empty_cover_response.content))
            self.assertEqual(empty_cover_response.data["data"]["coverImageUrl"], first_url)

            replace_payload = self.route_payload(name="封面替换后航线")
            replace_payload["waypoints"] = json.dumps(replace_payload["waypoints"])
            replace_payload["coverImage"] = SimpleUploadedFile("cover-b.png", b"\x89PNG\r\n\x1a\n", content_type="image/png")
            replace_response = self.client.put(f"/api/v2/inspection/routes/{route_id}", replace_payload, format="multipart")

            self.assertEqual(replace_response.status_code, 200, getattr(replace_response, "data", replace_response.content))
            self.assertIn("coverImageUrl", replace_response.data["data"])
            self.assertNotEqual(replace_response.data["data"]["coverImageUrl"], first_url)
            route.refresh_from_db()
            self.assertNotEqual(route.cover_image.name, first_cover_name)
            self.assertFalse(route.cover_image.storage.exists(first_cover_name))

    def test_route_json_save_should_remain_backward_compatible_without_cover_image(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="无封面 JSON 航线")
        self.assertEqual(route["coverImageUrl"], "")

        payload = self.route_payload(name="无封面 JSON 更新航线")
        self.authenticate(self.owner_dispatcher)
        response = self.client.put(f"/api/v2/inspection/routes/{route['id']}", payload, format="json")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["coverImageUrl"], "")

    def test_mission_route_snapshot_should_include_route_cover_image_url(self):
        self.authenticate(self.owner_dispatcher)
        route_payload = self.route_payload(name="任务封面航线")
        route_payload["waypoints"] = json.dumps(route_payload["waypoints"])
        route_payload["coverImage"] = SimpleUploadedFile("mission-cover.webp", b"RIFFxxxxWEBP", content_type="image/webp")

        with tempfile.TemporaryDirectory() as media_root, self.storage_settings(media_root):
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

    def test_mission_lifecycle_should_create_session_record_and_cloud_media(self):
        route = self.create_route_by_api(self.owner_dispatcher)
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-LIFECYCLE-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
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
                "docks": 0,
                "gateways": 1,
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

    def test_route_kmz_upload_should_publish_to_selected_dji_connection(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="KMZ 航线")
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        connection.workspace_id = "workspace-kmz-001"
        connection.save(update_fields=["workspace_id", "updated_at"])

        self.authenticate(self.owner_dispatcher)
        kmz_file = SimpleUploadedFile("route.kmz", b"fake-kmz-content", content_type="application/vnd.google-earth.kmz")
        with patch(
            "apps.inspection_v2.views.DjiConnectionGateway.upload_route",
            return_value={"dji_wayline_id": "wayline-kmz-001", "download_url": "/waylines/wayline-kmz-001/url"},
        ) as upload_route:
            response = self.client.post(
                f"/api/v2/inspection/routes/{route['id']}/kmz",
                {"djiConnectionId": connection.id, "waylineType": 2, "kmzFile": kmz_file},
                format="multipart",
            )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["djiFileId"], "wayline-kmz-001")
        self.assertEqual(response.data["data"]["workspaceId"], "workspace-kmz-001")
        self.assertEqual(response.data["data"]["waylineType"], 2)
        upload_route.assert_called_once()
        upload_kwargs = upload_route.call_args.kwargs
        self.assertNotIn("_", upload_kwargs["route_name"])
        self.assertNotIn("_", upload_kwargs["file_obj"].name)
        self.assertRegex(upload_kwargs["route_name"], r"^v2-route-\d+-[0-9a-f]{8}$")

    def test_route_kmz_upload_should_require_wayline_type(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="缺少航线类型")
        connection = DjiConnection.objects.get(owner_department=self.owner_department)

        self.authenticate(self.owner_dispatcher)
        kmz_file = SimpleUploadedFile("route.kmz", b"fake-kmz-content", content_type="application/vnd.google-earth.kmz")
        response = self.client.post(
            f"/api/v2/inspection/routes/{route['id']}/kmz",
            {"djiConnectionId": connection.id, "kmzFile": kmz_file},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400, getattr(response, "data", response.content))

        invalid_file = SimpleUploadedFile("route.kmz", b"fake-kmz-content", content_type="application/vnd.google-earth.kmz")
        invalid_response = self.client.post(
            f"/api/v2/inspection/routes/{route['id']}/kmz",
            {"djiConnectionId": connection.id, "waylineType": 9, "kmzFile": invalid_file},
            format="multipart",
        )

        self.assertEqual(invalid_response.status_code, 400, getattr(invalid_response, "data", invalid_response.content))

    def test_mission_start_should_create_dji_immediate_job_from_route_kmz_and_executor(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="执行航线")
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        connection.workspace_id = "workspace-job-001"
        connection.save(update_fields=["workspace_id", "updated_at"])
        executor = self.bind_gateway(self.owner_department, self.owner_admin, "GATEWAY-JOB-001", connection=connection)
        self.upload_route_kmz_by_api(route["id"], connection, wayline_type=3)
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
            return_value={"rtmp_url": "rtmp://live.example.test/app", "webrtc_url": "https://live.example.test/webrtc"},
        ) as start_live, patch(
            "apps.inspection_v2.services.DjiConnectionGateway.create_mission",
            return_value={"dji_job_id": "dji-job-001"},
        ) as create_job:
            start_response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")

        self.assertEqual(start_response.status_code, 200, getattr(start_response, "data", start_response.content))
        self.assertEqual(start_response.data["data"]["status"], MissionStatus.RUNNING)
        self.assertEqual(start_response.data["data"]["cloudExecution"]["djiJobId"], "dji-job-001")
        self.assertEqual(start_response.data["data"]["cloudExecution"]["liveStatus"], LiveStreamStatus.RUNNING)
        self.assertEqual(start_response.data["data"]["cloudExecution"]["liveVideoId"], f"{self.drone.device_sn}/88-0-0/normal-0")
        self.assertEqual(start_response.data["data"]["cloudExecution"]["liveUrls"]["webrtc_url"], "https://live.example.test/webrtc")
        execution = MissionCloudExecution.objects.get(mission_id=mission["id"])
        self.assertEqual(execution.dji_job_id, "dji-job-001")
        self.assertEqual(execution.executor_sn, "GATEWAY-JOB-001")
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
        self.assertEqual(execution.raw_request["wayline_type"], 3)
        self.assertEqual(execution.raw_request["task_type"], 0)
        self.assertEqual(execution.raw_request["live"]["video_id"], f"{self.drone.device_sn}/88-0-0/normal-0")

    def test_mission_start_upstream_failure_should_keep_local_task_pending(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="执行失败航线")
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-FAIL-001")
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
            return_value={"rtmp_url": "rtmp://live.example.test/app"},
        ) as start_live, patch(
            "apps.inspection_v2.services.DjiConnectionGateway.create_mission",
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

    def test_mission_start_live_failure_should_not_create_dji_job(self):
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
        ) as start_live, patch("apps.inspection_v2.services.DjiConnectionGateway.create_mission") as create_job:
            response = self.client.post(f"/api/v2/inspection/missions/{mission['id']}/start", {}, format="json")

        self.assertEqual(response.status_code, 502, getattr(response, "data", response.content))
        persisted = InspectionMission.objects.get(pk=mission["id"])
        self.assertEqual(persisted.status, MissionStatus.PENDING)
        self.assertFalse(FlightSession.objects.filter(mission_id=mission["id"]).exists())
        self.assertFalse(MissionCloudExecution.objects.filter(mission_id=mission["id"]).exists())
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

        with patch("apps.inspection_v2.services.DjiConnectionGateway.create_mission") as create_job:
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
        executor = self.bind_gateway(self.owner_department, self.owner_admin, "GATEWAY-EVENT-001", connection=connection)
        self.upload_route_kmz_by_api(route["id"], connection)
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
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

    @override_settings(DJI_INTERNAL_API_TOKEN="internal-sync-token")
    def test_media_upload_callback_with_job_id_should_bind_v2_mission_exactly(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="回调航线")
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-CALLBACK-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
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

    def test_osd_event_should_update_running_flight_telemetry(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="OSD 航线")
        connection = DjiConnection.objects.get(owner_department=self.owner_department)
        connection.workspace_id = "workspace-osd-001"
        connection.save(update_fields=["workspace_id", "updated_at"])
        executor = self.bind_gateway(self.owner_department, self.owner_admin, "GATEWAY-OSD-001", connection=connection)
        self.upload_route_kmz_by_api(route["id"], connection)
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
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
        executor = self.bind_gateway(self.owner_department, self.owner_admin, "GATEWAY-CANCEL-001", connection=connection)
        self.upload_route_kmz_by_api(route["id"], connection)
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
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

    def test_v2_dji_worker_command_should_support_once_mode(self):
        with patch(
            "apps.inspection_v2.management.commands.run_v2_dji_worker.V2DjiWorker.run_once",
            return_value={"connections": 0},
        ) as run_once:
            call_command("run_v2_dji_worker", "--once")
        run_once.assert_called_once()

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
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-ACTIVE-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
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
        self.authenticate(self.other_dispatcher)
        denied_response = self.client.post(
            "/api/v2/inspection/missions",
            {
                "name": "缺少 use 的任务",
                "routeId": route["id"],
                "droneId": self.drone.id,
                "pilotId": self.other_pilot.id,
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
                "pilotId": self.other_pilot.id,
            },
            format="json",
        )
        self.assertEqual(allowed_response.status_code, 201, getattr(allowed_response, "data", allowed_response.content))
        self.assertEqual(allowed_response.data["data"]["creatorDepartmentId"], self.other_department.id)
        self.assertEqual(allowed_response.data["data"]["primaryResourceOwnerDepartmentId"], self.owner_department.id)

    def test_live_start_should_require_active_flight_and_proxy_dji_control(self):
        route = self.create_route_by_api(self.owner_dispatcher, name="直播航线")
        _connection, executor = self.prepare_route_for_cloud_execution(route, gateway_sn="GATEWAY-LIVE-001")
        mission = self.create_mission_by_api(
            self.owner_dispatcher,
            route_id=route["id"],
            drone_id=self.drone.id,
            executor_id=executor.id,
            pilot_id=self.owner_pilot.id,
            name="直播任务",
        )
        inactive_response = self.client.post(
            "/api/v2/inspection/live/start",
            {"droneId": self.drone.id, "video_id": f"{self.drone.device_sn}/88-0-0/normal-0"},
            format="json",
        )
        self.assertEqual(inactive_response.status_code, 409, getattr(inactive_response, "data", inactive_response.content))

        self.start_cloud_mission_by_api(mission["id"], dji_job_id="dji-job-live")
        with patch(
            "apps.inspection_v2.views.DjiConnectionGateway.start_live",
            return_value={"webrtc_url": "https://live.example.test/webrtc", "rtmp_url": "rtmp://live.example.test/app"},
        ) as start_live:
            live_response = self.client.post(
                "/api/v2/inspection/live/start",
                {"droneId": self.drone.id, "video_id": f"{self.drone.device_sn}/88-0-0/normal-0", "url_type": 1},
                format="json",
            )

        self.assertEqual(live_response.status_code, 200, getattr(live_response, "data", live_response.content))
        self.assertEqual(live_response.data["data"]["webrtc_url"], "https://live.example.test/webrtc")
        start_live.assert_called_once()
