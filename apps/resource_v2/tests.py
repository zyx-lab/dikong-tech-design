from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import connection as db_connection
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import DirectoryStatus
from apps.audit_v2.models import V2AuditLog
from apps.dji_cloud.gateway import GatewayResponse
from apps.iam_v2.models import (
    Department,
    FixedRole,
    ResourceShareGroup,
    ResourceShareGroupTargetDepartment,
    V2AccountProfile,
    V2AccountRoleAssignment,
)
from apps.resource_v2.gateway import DjiConnectionGateway
from apps.resource_v2.models import (
    BindingActionType,
    BindingStatus,
    CameraResource,
    DjiConnection,
    DockResource,
    DroneTelemetrySnapshot,
    DroneResource,
    GatewayResource,
    HmsAlert,
    MqttConnectionHealth,
    MqttHealthStatus,
    MqttLatestMessage,
    PayloadResource,
    ResourceBinding,
    ResourceBindingHistory,
    ResourceSharePermission,
    ResourceType,
)
from apps.resource_v2.serializers import upsert_resource_from_payload
from apps.resource_v2.mqtt import osd_reported_at, upsert_drone_telemetry_from_osd

User = get_user_model()


def create_v2_actor(*, username: str, role_code: str | None, department: Department, is_platform_admin: bool = False):
    user = User.objects.create_user(username=username, password="pass1234", status=1, is_platform_admin=is_platform_admin)
    profile = V2AccountProfile.objects.create(
        user=user,
        department=department,
        name=username,
        phone=f"138{user.id:08d}",
        email=f"{username}@example.test",
    )
    if role_code is not None:
        V2AccountRoleAssignment.objects.create(account_profile=profile, role_code=role_code, assigned_by_user=user)
    return user


class FakeDiscoveryGateway:
    calls = []

    def __init__(self, connection):
        self.connection = connection

    def discover(self):
        self.calls.append(self.connection.id)
        return {
            "drones": [
                {
                    "device_sn": "DRONE-SN-001",
                    "name": "巡检无人机",
                    "model": "Matrice 30T",
                    "online_status": True,
                    "firmware_version": "v1.0.0",
                    "firmware_status": "1",
                    "last_payload": {"device_sn": "DRONE-SN-001"},
                }
            ],
            "docks": [
                {
                    "device_sn": "DOCK-SN-001",
                    "name": "机场一号",
                    "model": "Dock 2",
                    "online_status": True,
                    "firmware_version": "v1.0.0",
                    "firmware_status": "1",
                    "last_payload": {"device_sn": "DOCK-SN-001"},
                }
            ],
            "gateways": [
                {
                    "device_sn": "GATEWAY-SN-001",
                    "name": "网关一号",
                    "model": "RC Plus",
                    "online_status": True,
                    "firmware_version": "v1.0.0",
                    "firmware_status": "1",
                    "last_payload": {"device_sn": "GATEWAY-SN-001"},
                }
            ],
            "payloads": [
                {
                    "payload_sn": "PAYLOAD-SN-001",
                    "name": "可见光负载",
                    "model": "H20T",
                    "payload_type": "camera",
                    "online_status": True,
                    "firmware_version": "v1.0.0",
                    "firmware_status": "1",
                    "last_payload": {"payload_sn": "PAYLOAD-SN-001"},
                }
            ],
        }


class ResourceV2ApiTests(TestCase):
    def setUp(self):
        super().setUp()
        FakeDiscoveryGateway.calls = []
        self.client = APIClient()
        self.root = Department.objects.create(name="总部")
        self.child = Department.objects.create(name="飞行队", parent=self.root)
        self.other = Department.objects.create(name="保障队", parent=self.root)
        self.root_admin = create_v2_actor(
            username="root_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=self.root,
        )
        self.child_admin = create_v2_actor(
            username="child_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=self.child,
        )
        self.other_admin = create_v2_actor(
            username="other_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=self.other,
        )
        self.other_dispatcher = create_v2_actor(
            username="other_dispatcher",
            role_code=FixedRole.TASK_MONITOR_DISPATCHER,
            department=self.other,
        )
        self.no_role_user = create_v2_actor(
            username="no_role_user",
            role_code=None,
            department=self.child,
        )
        self.legacy_platform_admin = create_v2_actor(
            username="legacy_platform_admin",
            role_code=None,
            department=self.child,
            is_platform_admin=True,
        )
        self.platform_super = create_v2_actor(
            username="platform_super",
            role_code=FixedRole.PLATFORM_SUPER_ADMIN,
            department=self.root,
        )

    def authenticate(self, user):
        self.client.force_authenticate(user)

    def create_cached_dji_connection(self, *, name: str = "缓存 DJI"):
        now = timezone.now()
        connection = DjiConnection.objects.create(
            owner_department=self.child,
            name=name,
            base_url="https://old-dji.example.test",
            username="adminPC1",
            password="secret",
            login_flag=1,
            workspace_id="workspace-old",
            dji_user_id="dji-user-old",
            dji_username="dji-admin-old",
            dji_user_type="1",
            access_token="access-token-old",
            mqtt_username="mqtt-user-old",
            mqtt_password="mqtt-pass-old",
            mqtt_addr="tcp://old-mqtt.example.test:1883",
            expires_at=now + timedelta(days=1),
            last_checked_at=now,
            created_by_user=self.child_admin,
        )
        MqttConnectionHealth.objects.create(
            dji_connection=connection,
            status=MqttHealthStatus.ERROR,
            mqtt_addr=connection.mqtt_addr,
            subscribed_topics=["thing/product/+/osd"],
            last_connected_at=now,
            last_subscribed_at=now,
            last_message_at=now,
            last_heartbeat_at=now,
            last_error="timed out",
            message_count=7,
        )
        return connection

    def bind_v2_drone(self, *, department: Department, actor, device_sn: str):
        connection = DjiConnection.objects.create(
            owner_department=department,
            name=f"{device_sn} DJI",
            base_url=f"https://{device_sn.lower()}.example.test",
            username="adminPC1",
            password="secret",
            created_by_user=actor,
        )
        resource = DroneResource.objects.create(device_sn=device_sn, name=f"{device_sn} 资源", model="M30")
        ResourceBinding.objects.create(
            resource_type=ResourceType.DRONE,
            resource_object_id=resource.id,
            owner_department=department,
            dji_connection=connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=actor,
        )
        return resource

    def test_camera_should_register_claim_and_follow_permission_tree_without_role_gate(self):
        self.authenticate(self.child_admin)
        register_response = self.client.post(
            "/api/v2/resource/cameras",
            {
                "deviceSn": "CAMERA-101",
                "name": "一号固定摄像头",
                "model": "固定枪机",
                "webrtcUrl": "https://video.example.test/camera-101/whep",
                "resultsWsUrl": "wss://video.example.test/target.results",
                "apiUsername": "camera-user",
                "apiKey": "camera-secret",
            },
            format="json",
        )

        self.assertEqual(register_response.status_code, 201, getattr(register_response, "data", register_response.content))
        camera_id = register_response.data["data"]["resourceId"]
        self.assertNotIn("apiUsername", register_response.data["data"])
        self.assertNotIn("apiKey", register_response.data["data"])
        self.assertFalse(ResourceBinding.objects.filter(resource_type=ResourceType.CAMERA, resource_object_id=camera_id).exists())

        self.authenticate(self.no_role_user)
        forbidden_claim_response = self.client.post(
            "/api/v2/resource/bindings",
            {"resourceType": ResourceType.CAMERA, "resourceId": camera_id},
            format="json",
        )
        self.assertEqual(forbidden_claim_response.status_code, 403)

        self.authenticate(self.child_admin)
        claim_response = self.client.post(
            "/api/v2/resource/bindings",
            {"resourceType": ResourceType.CAMERA, "resourceId": camera_id},
            format="json",
        )

        self.assertEqual(claim_response.status_code, 201, getattr(claim_response, "data", claim_response.content))
        self.assertIsNone(claim_response.data["data"]["djiConnectionId"])
        self.assertEqual(claim_response.data["data"]["ownerDepartmentId"], self.child.id)

        self.authenticate(self.no_role_user)
        list_response = self.client.get("/api/v2/resource/cameras")
        detail_response = self.client.get(f"/api/v2/resource/cameras/{camera_id}")
        playback_response = self.client.get(f"/api/v2/resource/cameras/{camera_id}/playback")

        self.assertEqual(list_response.status_code, 200, getattr(list_response, "data", list_response.content))
        self.assertEqual(list_response.data["data"]["total"], 1)
        self.assertEqual(list_response.data["data"]["list"][0]["deviceSn"], "CAMERA-101")
        self.assertEqual(detail_response.status_code, 200, getattr(detail_response, "data", detail_response.content))
        self.assertEqual(playback_response.status_code, 200, getattr(playback_response, "data", playback_response.content))
        self.assertEqual(playback_response.data["data"]["video"]["protocol"], "WHEP")
        self.assertEqual(playback_response.data["data"]["video"]["url"], f"/api/v2/resource/cameras/{camera_id}/whep")
        self.assertEqual(playback_response.data["data"]["resultsWebSocketPath"], f"/ws/v2/cameras/{camera_id}/results")
        self.assertNotIn("camera-secret", str(list_response.data))
        self.assertNotIn("camera-secret", str(detail_response.data))
        self.assertNotIn("camera-secret", str(playback_response.data))

        self.authenticate(self.root_admin)
        parent_list_response = self.client.get("/api/v2/resource/cameras")
        self.assertEqual(parent_list_response.data["data"]["total"], 1)

        self.authenticate(self.other_dispatcher)
        hidden_list_response = self.client.get("/api/v2/resource/cameras")
        hidden_playback_response = self.client.get(f"/api/v2/resource/cameras/{camera_id}/playback")
        self.assertEqual(hidden_list_response.data["data"]["total"], 0)
        self.assertEqual(hidden_playback_response.status_code, 404)

        self.authenticate(self.child_admin)
        group_response = self.client.post("/api/v2/resource/share-groups", {"name": "摄像头共享"}, format="json")
        group_id = group_response.data["data"]["id"]
        self.client.post(
            f"/api/v2/resource/share-groups/{group_id}/departments",
            {"departmentId": self.other.id},
            format="json",
        )
        share_response = self.client.post(
            f"/api/v2/resource/share-groups/{group_id}/resources",
            {"resourceType": ResourceType.CAMERA, "resourceId": camera_id, "permissions": ["view"]},
            format="json",
        )
        self.assertEqual(share_response.status_code, 201, getattr(share_response, "data", share_response.content))

        self.authenticate(self.other_dispatcher)
        shared_list_response = self.client.get("/api/v2/resource/cameras")
        self.assertEqual(shared_list_response.data["data"]["total"], 1)

        camera = CameraResource.objects.get(pk=camera_id)
        self.assertEqual(camera.api_username, "camera-user")
        self.assertEqual(camera.api_key, "camera-secret")

    def test_camera_registration_should_require_api_username(self):
        self.authenticate(self.child_admin)
        for device_sn, api_username in (
            ("CAMERA-MISSING-USERNAME", None),
            ("CAMERA-BLANK-USERNAME", ""),
        ):
            payload = {
                "deviceSn": device_sn,
                "name": "缺账号摄像头",
                "webrtcUrl": "https://video.example.test/camera-missing/whep",
                "resultsWsUrl": "wss://video.example.test/target.results",
                "apiKey": "camera-secret",
            }
            if api_username is not None:
                payload["apiUsername"] = api_username

            with self.subTest(device_sn=device_sn):
                response = self.client.post("/api/v2/resource/cameras", payload, format="json")

                self.assertEqual(response.status_code, 400, getattr(response, "data", response.content))
                self.assertFalse(CameraResource.objects.filter(device_sn=device_sn).exists())

    def test_camera_whep_should_exchange_sdp_through_server_side_basic_auth(self):
        camera = CameraResource.objects.create(
            device_sn="CAMERA-WHEP-001",
            name="WHEP 摄像头",
            webrtc_url="http://110.42.32.122:18889/camera-101/whep",
            results_ws_url="ws://110.42.32.122:18081/target.results",
            api_username="camera-user",
            api_key="camera-secret",
        )
        ResourceBinding.objects.create(
            resource_type=ResourceType.CAMERA,
            resource_object_id=camera.id,
            owner_department=self.child,
            status=BindingStatus.ACTIVE,
            bound_by_user=self.child_admin,
        )
        self.authenticate(self.no_role_user)

        upstream = MagicMock()
        upstream.read.return_value = b"v=0\r\ns=upstream-answer\r\n"
        upstream.__enter__.return_value = upstream
        with patch("apps.resource_v2.gateway.urlopen", return_value=upstream) as urlopen:
            response = self.client.post(
                f"/api/v2/resource/cameras/{camera.id}/whep",
                {"offerSdp": "v=0\r\ns=browser-offer\r\n"},
                format="json",
            )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["answerSdp"], "v=0\r\ns=upstream-answer\r\n")
        upstream_request = urlopen.call_args.args[0]
        self.assertEqual(upstream_request.full_url, camera.webrtc_url)
        self.assertEqual(upstream_request.get_method(), "POST")
        self.assertEqual(upstream_request.data, b"v=0\r\ns=browser-offer\r\n")
        self.assertEqual(upstream_request.get_header("Authorization"), "Basic Y2FtZXJhLXVzZXI6Y2FtZXJhLXNlY3JldA==")

    @override_settings(DJI_MQTT_OSD_FRESHNESS_SECONDS=60)
    def test_drone_resource_should_include_latest_mqtt_telemetry_snapshot(self):
        drone = self.bind_v2_drone(department=self.child, actor=self.child_admin, device_sn="DRONE-MQTT-001")
        binding = ResourceBinding.objects.get(resource_type=ResourceType.DRONE, resource_object_id=drone.id)
        DroneTelemetrySnapshot.objects.create(
            drone=drone,
            dji_connection=binding.dji_connection,
            latitude="31.23040000",
            longitude="121.47370000",
            altitude="120.50",
            speed="8.20",
            heading="91.00",
            battery_percent=87,
            total_flight_time=128400,
            total_flight_distance="35240.50",
            total_flight_sorties=83,
            battery_cycles=[{"sn": "BATTERY-SN-LEFT", "index": 0, "loopTimes": 37}],
            reported_at=timezone.now(),
            raw_payload={"data": {"latitude": 31.2304, "longitude": 121.4737}},
        )

        self.authenticate(self.child_admin)
        list_response = self.client.get("/api/v2/resource/drones")
        detail_response = self.client.get(f"/api/v2/resource/drones/{drone.id}")

        self.assertEqual(list_response.status_code, 200, getattr(list_response, "data", list_response.content))
        list_item = list_response.data["data"]["list"][0]
        self.assertEqual(list_item["latestTelemetry"]["latitude"], "31.23040000")
        self.assertEqual(list_item["latestTelemetry"]["longitude"], "121.47370000")
        self.assertEqual(list_item["latestTelemetry"]["batteryPercent"], 87)
        self.assertEqual(list_item["latestTelemetry"]["totalFlightTime"], 128400)
        self.assertEqual(list_item["latestTelemetry"]["totalFlightDistance"], "35240.50")
        self.assertEqual(list_item["latestTelemetry"]["totalFlightSorties"], 83)
        self.assertEqual(
            list_item["latestTelemetry"]["batteryCycles"],
            [{"sn": "BATTERY-SN-LEFT", "index": 0, "loopTimes": 37}],
        )
        self.assertIsNotNone(list_item["latestTelemetry"]["updatedAt"])
        self.assertFalse(list_item["latestTelemetry"]["isStale"])
        self.assertEqual(list_item["latestTelemetry"]["rawPayload"]["data"]["latitude"], 31.2304)
        self.assertEqual(list_item["djiConnectionId"], binding.dji_connection_id)
        self.assertEqual(list_item["djiConnectionName"], binding.dji_connection.name)

        self.assertEqual(detail_response.status_code, 200, getattr(detail_response, "data", detail_response.content))
        self.assertEqual(detail_response.data["data"]["latestTelemetry"]["heading"], "91.00")
        self.assertEqual(detail_response.data["data"]["djiConnectionId"], binding.dji_connection_id)
        self.assertEqual(detail_response.data["data"]["djiConnectionName"], binding.dji_connection.name)

    def test_osd_should_persist_and_merge_aircraft_cumulative_properties(self):
        drone = self.bind_v2_drone(department=self.child, actor=self.child_admin, device_sn="DRONE-STATS-001")
        binding = ResourceBinding.objects.get(resource_type=ResourceType.DRONE, resource_object_id=drone.id)
        newer_at = timezone.now()
        older_at = newer_at - timedelta(seconds=1)

        snapshot = upsert_drone_telemetry_from_osd(
            connection=binding.dji_connection,
            device_sn=drone.device_sn,
            payload={
                "timestamp": int(newer_at.timestamp() * 1000),
                "data": {
                    "latitude": 31.2,
                    "total_flight_time": 120,
                    "total_flight_distance": 345.67,
                    "total_flight_sorties": 8,
                    "battery": {
                        "capacity_percent": 80,
                        "batteries": [
                            {"sn": "BAT-A", "index": 0, "loop_times": 12},
                            {"sn": "", "index": 1, "loop_times": 99},
                        ],
                    },
                },
            },
        )
        snapshot.refresh_from_db()

        self.assertEqual(snapshot.total_flight_time, 120)
        self.assertEqual(str(snapshot.total_flight_distance), "345.67")
        self.assertEqual(snapshot.total_flight_sorties, 8)
        self.assertEqual(snapshot.battery_cycles, [{"sn": "BAT-A", "index": 0, "loopTimes": 12}])
        accepted_updated_at = snapshot.updated_at

        sparse_at = newer_at + timedelta(seconds=1)
        snapshot = upsert_drone_telemetry_from_osd(
            connection=binding.dji_connection,
            device_sn=drone.device_sn,
            payload={
                "timestamp": int(sparse_at.timestamp() * 1000),
                "data": {
                    "longitude": 121.4,
                    "total_flight_time": 0,
                    "total_flight_distance": "invalid",
                    "total_flight_sorties": 7,
                    "battery": {"capacity_percent": 79},
                },
            },
        )
        snapshot.refresh_from_db()

        self.assertEqual(snapshot.total_flight_time, 120)
        self.assertEqual(str(snapshot.total_flight_distance), "345.67")
        self.assertEqual(snapshot.total_flight_sorties, 8)
        self.assertEqual(snapshot.battery_cycles, [{"sn": "BAT-A", "index": 0, "loopTimes": 12}])
        self.assertEqual(str(snapshot.longitude), "121.40000000")
        self.assertGreater(snapshot.updated_at, accepted_updated_at)
        accepted_updated_at = snapshot.updated_at

        stale = upsert_drone_telemetry_from_osd(
            connection=binding.dji_connection,
            device_sn=drone.device_sn,
            payload={
                "timestamp": int(older_at.timestamp() * 1000),
                "data": {"latitude": 0, "total_flight_time": 999, "total_flight_sorties": 999},
            },
        )
        stale.refresh_from_db()
        self.assertEqual(str(stale.longitude), "121.40000000")
        self.assertEqual(stale.total_flight_time, 120)
        self.assertEqual(stale.updated_at, accepted_updated_at)

    def test_osd_should_replace_current_batteries_without_decreasing_known_cycles(self):
        drone = self.bind_v2_drone(department=self.child, actor=self.child_admin, device_sn="DRONE-BATTERY-001")
        binding = ResourceBinding.objects.get(resource_type=ResourceType.DRONE, resource_object_id=drone.id)
        first_at = timezone.now()
        upsert_drone_telemetry_from_osd(
            connection=binding.dji_connection,
            device_sn=drone.device_sn,
            payload={
                "timestamp": int(first_at.timestamp() * 1000),
                "data": {
                    "total_flight_time": 0,
                    "total_flight_distance": 0,
                    "total_flight_sorties": 0,
                    "battery": {"batteries": [{"sn": "BAT-A", "index": 0, "loop_times": 12}]},
                },
            },
        )
        snapshot = upsert_drone_telemetry_from_osd(
            connection=binding.dji_connection,
            device_sn=drone.device_sn,
            payload={
                "timestamp": int((first_at + timedelta(seconds=1)).timestamp() * 1000),
                "data": {
                    "battery": {
                        "batteries": [
                            {"sn": "BAT-A", "index": 1, "loop_times": 10},
                            {"sn": "BAT-B", "index": 0, "loop_times": 3},
                            {"sn": "BAT-C", "index": 2, "loop_times": -1},
                        ]
                    }
                },
            },
        )

        self.assertEqual(
            snapshot.battery_cycles,
            [
                {"sn": "BAT-A", "index": 1, "loopTimes": 12},
                {"sn": "BAT-B", "index": 0, "loopTimes": 3},
            ],
        )
        self.assertEqual(snapshot.total_flight_time, 0)
        self.assertEqual(snapshot.total_flight_distance, 0)
        self.assertEqual(snapshot.total_flight_sorties, 0)
        self.assertIsNone(
            upsert_drone_telemetry_from_osd(
                connection=binding.dji_connection,
                device_sn="UNKNOWN-DRONE",
                payload={"data": {"total_flight_time": 1}},
            )
        )

    def test_osd_should_ignore_non_finite_cumulative_values_and_invalid_timestamp(self):
        drone = self.bind_v2_drone(department=self.child, actor=self.child_admin, device_sn="DRONE-STATS-INVALID-001")
        binding = ResourceBinding.objects.get(resource_type=ResourceType.DRONE, resource_object_id=drone.id)
        snapshot = upsert_drone_telemetry_from_osd(
            connection=binding.dji_connection,
            device_sn=drone.device_sn,
            payload={
                "data": {
                    "latitude": 31.2,
                    "total_flight_time": 10,
                    "total_flight_distance": 20,
                }
            },
        )

        snapshot = upsert_drone_telemetry_from_osd(
            connection=binding.dji_connection,
            device_sn=drone.device_sn,
            payload={
                "data": {
                    "latitude": 32.1,
                    "total_flight_time": "Infinity",
                    "total_flight_distance": "NaN",
                }
            },
        )
        snapshot.refresh_from_db()

        self.assertEqual(str(snapshot.latitude), "32.10000000")
        self.assertEqual(snapshot.total_flight_time, 10)
        self.assertEqual(snapshot.total_flight_distance, 20)
        self.assertLess(abs((osd_reported_at({"timestamp": float("inf")}) - timezone.now()).total_seconds()), 1)

    def test_mqtt_health_should_be_visible_to_connection_manager_only(self):
        child_connection = DjiConnection.objects.create(
            owner_department=self.child,
            name="child mqtt",
            base_url="https://child.example.test",
            username="admin",
            password="secret",
            created_by_user=self.child_admin,
        )
        other_connection = DjiConnection.objects.create(
            owner_department=self.other,
            name="other mqtt",
            base_url="https://other.example.test",
            username="admin",
            password="secret",
            created_by_user=self.other_admin,
        )
        MqttConnectionHealth.objects.create(
            dji_connection=child_connection,
            status="SUBSCRIBED",
            worker_id="worker-child",
            mqtt_addr="tcp://broker.example.test:1883",
            subscribed_topics=["thing/product/+/osd"],
            last_message_at=timezone.now(),
            message_count=3,
        )
        MqttConnectionHealth.objects.create(
            dji_connection=other_connection,
            status="ERROR",
            worker_id="worker-other",
            last_error="connect failed",
        )

        self.authenticate(self.child_admin)
        response = self.client.get("/api/v2/resource/dji-connections/mqtt-health")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["total"], 1)
        self.assertEqual(response.data["data"]["list"][0]["connectionId"], child_connection.id)
        self.assertEqual(response.data["data"]["list"][0]["status"], "SUBSCRIBED")
        self.assertEqual(response.data["data"]["list"][0]["messageCount"], 3)

        self.authenticate(self.other_dispatcher)
        forbidden_response = self.client.get("/api/v2/resource/dji-connections/mqtt-health")
        self.assertEqual(forbidden_response.status_code, 403)

    def test_mqtt_latest_messages_should_filter_by_connection_and_visible_device(self):
        drone = self.bind_v2_drone(department=self.child, actor=self.child_admin, device_sn="DRONE-LATEST-001")
        binding = ResourceBinding.objects.get(resource_type=ResourceType.DRONE, resource_object_id=drone.id)
        MqttLatestMessage.objects.create(
            dji_connection=binding.dji_connection,
            topic="thing/product/DRONE-LATEST-001/osd",
            topic_kind="osd",
            device_sn=drone.device_sn,
            received_at=timezone.now(),
            sequence=1,
            raw_payload={"data": {"latitude": 31.11}},
        )

        self.authenticate(self.child_admin)
        response = self.client.get(
            f"/api/v2/resource/dji-connections/{binding.dji_connection_id}/mqtt-messages/latest",
            {"deviceSn": drone.device_sn, "topicKind": "osd"},
        )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["total"], 1)
        self.assertEqual(response.data["data"]["list"][0]["deviceSn"], drone.device_sn)
        self.assertEqual(response.data["data"]["list"][0]["rawPayload"]["data"]["latitude"], 31.11)

        self.authenticate(self.other_dispatcher)
        forbidden_response = self.client.get(
            f"/api/v2/resource/dji-connections/{binding.dji_connection_id}/mqtt-messages/latest",
            {"deviceSn": drone.device_sn},
        )
        self.assertEqual(forbidden_response.status_code, 403)

    def test_hms_alerts_should_filter_paginate_and_serialize_for_connection_manager(self):
        connection = self.create_cached_dji_connection(name="HMS DJI")
        now = timezone.now()
        for index, resolved in enumerate((False, True, False), start=1):
            HmsAlert.objects.create(
                dji_connection=connection,
                gateway_sn="DOCK-HMS-001",
                from_sn="DOCK-HMS-001",
                alarm_key=f"alarm-{index}",
                code=f"CODE-{index}",
                device_domain=3,
                level=2 if index < 3 else 1,
                module=3,
                raw_item={"code": f"CODE-{index}"},
                first_reported_at=now - timedelta(minutes=index),
                last_reported_at=now,
                resolved_at=now if resolved else None,
            )
        other_connection = DjiConnection.objects.create(
            owner_department=self.other,
            name="other HMS",
            base_url="https://other-hms.example.test",
            username="admin",
            password="secret",
            created_by_user=self.other_admin,
        )
        HmsAlert.objects.create(
            dji_connection=other_connection,
            gateway_sn="OTHER-DOCK",
            from_sn="OTHER-DOCK",
            alarm_key="other-alarm",
            code="OTHER",
            raw_item={"code": "OTHER"},
            first_reported_at=now,
            last_reported_at=now,
        )

        self.authenticate(self.child_admin)
        response = self.client.get(
            f"/api/v2/resource/dji-connections/{connection.id}/hms-alerts",
            {"gatewaySn": "DOCK-HMS-001", "active": "true", "pageNum": 1, "pageSize": 1},
        )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["total"], 2)
        self.assertEqual(len(response.data["data"]["list"]), 1)
        item = response.data["data"]["list"][0]
        self.assertEqual(item["djiConnectionId"], connection.id)
        self.assertEqual(item["gatewaySn"], "DOCK-HMS-001")
        self.assertEqual(item["alarmKey"], "alarm-1")
        self.assertEqual(item["rawItem"], {"code": "CODE-1"})
        self.assertTrue(item["active"])
        self.assertIsNone(item["resolvedAt"])

        filtered = self.client.get(
            f"/api/v2/resource/dji-connections/{connection.id}/hms-alerts",
            {
                "code": "CODE-2",
                "level": 2,
                "active": "false",
                "firstReportedAfter": (now - timedelta(minutes=3)).isoformat(),
                "firstReportedBefore": now.isoformat(),
            },
        )
        self.assertEqual(filtered.status_code, 200, getattr(filtered, "data", filtered.content))
        self.assertEqual(filtered.data["data"]["total"], 1)
        self.assertEqual(filtered.data["data"]["list"][0]["code"], "CODE-2")

    def test_hms_alerts_should_validate_query_and_protect_connection_scope(self):
        connection = self.create_cached_dji_connection(name="HMS protected")
        endpoint = f"/api/v2/resource/dji-connections/{connection.id}/hms-alerts"

        self.authenticate(self.other_admin)
        self.assertEqual(self.client.get(endpoint).status_code, 404)

        self.authenticate(self.other_dispatcher)
        self.assertEqual(self.client.get(endpoint).status_code, 403)

        self.authenticate(self.platform_super)
        self.assertEqual(self.client.get(endpoint).status_code, 200)
        self.assertEqual(self.client.get("/api/v2/resource/dji-connections/999999/hms-alerts").status_code, 404)
        for params in (
            {"active": "maybe"},
            {"level": "high"},
            {"firstReportedAfter": "yesterday"},
            {"pageNum": 0},
            {"pageSize": 101},
        ):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(endpoint, params).status_code, 400)

    def test_v2_resource_endpoints_should_require_fixed_role_operation_permissions(self):
        self.authenticate(self.no_role_user)

        endpoints = [
            "/api/v2/resource/drones",
            "/api/v2/resource/docks",
            "/api/v2/resource/dji-connections",
            "/api/v2/resource/audit-logs",
        ]
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                response = self.client.get(endpoint)
                self.assertEqual(response.status_code, 403, getattr(response, "data", response.content))

        self.authenticate(self.legacy_platform_admin)
        response = self.client.get("/api/v2/resource/drones")
        self.assertEqual(response.status_code, 403, getattr(response, "data", response.content))

    def test_resource_v2_should_not_expose_inspection_business_routes(self):
        self.authenticate(self.other_dispatcher)

        for endpoint in (
            "/api/v2/resource/routes",
            "/api/v2/resource/missions",
            "/api/v2/resource/flight-records",
            "/api/v2/resource/media-files",
        ):
            with self.subTest(endpoint=endpoint):
                response = self.client.get(endpoint)
                self.assertEqual(response.status_code, 404, getattr(response, "data", response.content))

    def test_department_admin_should_manage_own_connection_and_audit_plaintext_credential_view(self):
        self.authenticate(self.child_admin)

        create_response = self.client.post(
            "/api/v2/resource/dji-connections",
            {
                "name": "飞行队 DJI",
                "baseUrl": "https://dji.example.test/",
                "username": "adminPC1",
                "password": "secret",
                "loginFlag": 1,
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        payload = create_response.data["data"]
        self.assertEqual(payload["baseUrl"], "https://dji.example.test")
        self.assertNotIn("password", payload)
        connection = DjiConnection.objects.get(owner_department=self.child, name="飞行队 DJI")
        self.assertEqual(connection.password, "secret")

        list_response = self.client.get("/api/v2/resource/dji-connections")
        self.assertEqual(list_response.status_code, 200, getattr(list_response, "data", list_response.content))
        self.assertNotIn("password", list_response.data["data"]["list"][0])

        default_detail_response = self.client.get(f"/api/v2/resource/dji-connections/{connection.id}")
        self.assertEqual(default_detail_response.status_code, 200, getattr(default_detail_response, "data", default_detail_response.content))
        self.assertNotIn("password", default_detail_response.data["data"])

        detail_response = self.client.get(f"/api/v2/resource/dji-connections/{connection.id}?includeCredentials=true")

        self.assertEqual(detail_response.status_code, 200, getattr(detail_response, "data", detail_response.content))
        self.assertEqual(detail_response.data["data"]["password"], "secret")
        self.assertTrue(
            V2AuditLog.objects.filter(
                action="view_plaintext_dji_credentials",
                actor_department=self.child,
                target_type="dji_connection",
                target_id=str(connection.id),
            ).exists()
        )

        V2AuditLog.objects.create(
            action="other_department_action",
            actor_user=self.other_admin,
            actor_department=self.other,
            target_type="manual",
        )
        audit_response = self.client.get("/api/v2/resource/audit-logs")
        self.assertEqual(audit_response.status_code, 200, getattr(audit_response, "data", audit_response.content))
        returned_actions = {item["action"] for item in audit_response.data["data"]["list"]}
        self.assertIn("view_plaintext_dji_credentials", returned_actions)
        self.assertNotIn("other_department_action", returned_actions)

        denied_create_response = self.client.post(
            "/api/v2/resource/dji-connections",
            {
                "ownerDepartmentId": self.other.id,
                "name": "越权 DJI",
                "baseUrl": "https://other.example.test",
                "username": "other",
                "password": "secret",
            },
            format="json",
        )
        self.assertEqual(
            denied_create_response.status_code,
            403,
            getattr(denied_create_response, "data", denied_create_response.content),
        )

        self.authenticate(self.other_admin)
        denied_response = self.client.put(
            f"/api/v2/resource/dji-connections/{connection.id}",
            {"name": "越权修改", "baseUrl": "https://other.example.test", "username": "x", "password": "y"},
            format="json",
        )

        self.assertEqual(denied_response.status_code, 403, getattr(denied_response, "data", denied_response.content))

    def test_platform_super_admin_should_maintain_all_department_connections(self):
        self.authenticate(self.platform_super)

        create_response = self.client.post(
            "/api/v2/resource/dji-connections",
            {
                "ownerDepartmentId": self.child.id,
                "name": "跨部门 DJI",
                "baseUrl": "https://dji.example.test/",
                "username": "adminPC1",
                "password": "secret",
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        connection = DjiConnection.objects.get(pk=create_response.data["data"]["id"])
        self.assertEqual(connection.owner_department_id, self.child.id)

        update_response = self.client.put(
            f"/api/v2/resource/dji-connections/{connection.id}",
            {
                "ownerDepartmentId": self.other.id,
                "name": "转归属 DJI",
                "baseUrl": "https://other.example.test/",
                "username": "other",
                "password": "changed",
            },
            format="json",
        )

        self.assertEqual(update_response.status_code, 200, getattr(update_response, "data", update_response.content))
        connection.refresh_from_db()
        self.assertEqual(connection.owner_department_id, self.other.id)
        self.assertEqual(connection.password, "changed")
        self.assertEqual(update_response.data["data"]["ownerDepartmentId"], self.other.id)
        self.assertTrue(
            V2AuditLog.objects.filter(
                action="create_dji_connection",
                actor_department=self.root,
                resource_owner_department=self.child,
                target_id=str(connection.id),
            ).exists()
        )
        self.assertTrue(
            V2AuditLog.objects.filter(
                action="update_dji_connection",
                actor_department=self.root,
                resource_owner_department=self.other,
                target_id=str(connection.id),
            ).exists()
        )

    def test_updating_dji_connection_session_inputs_should_clear_cached_session_and_health(self):
        cases = [
            ("baseUrl", "https://new-dji.example.test"),
            ("username", "adminPC2"),
            ("password", "changed-secret"),
            ("loginFlag", 2),
        ]
        for field, value in cases:
            with self.subTest(field=field):
                connection = self.create_cached_dji_connection(name=f"缓存 DJI {field}")
                self.authenticate(self.child_admin)
                payload = {
                    "name": connection.name,
                    "baseUrl": connection.base_url,
                    "username": connection.username,
                    "password": connection.password,
                    "loginFlag": connection.login_flag,
                }
                payload[field] = value

                response = self.client.put(f"/api/v2/resource/dji-connections/{connection.id}", payload, format="json")

                self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
                connection.refresh_from_db()
                self.assertEqual(connection.access_token, "")
                self.assertEqual(connection.workspace_id, "")
                self.assertEqual(connection.dji_user_id, "")
                self.assertEqual(connection.dji_username, "")
                self.assertEqual(connection.dji_user_type, "")
                self.assertEqual(connection.mqtt_addr, "")
                self.assertEqual(connection.mqtt_username, "")
                self.assertEqual(connection.mqtt_password, "")
                self.assertIsNone(connection.expires_at)
                self.assertIsNone(connection.last_checked_at)

                health = MqttConnectionHealth.objects.get(dji_connection=connection)
                self.assertEqual(health.status, MqttHealthStatus.CONNECTING)
                self.assertEqual(health.mqtt_addr, "")
                self.assertEqual(health.last_error, "")
                self.assertEqual(health.subscribed_topics, [])
                self.assertIsNone(health.last_connected_at)
                self.assertIsNone(health.last_subscribed_at)
                self.assertIsNotNone(health.last_message_at)
                self.assertEqual(health.message_count, 7)

    def test_updating_dji_connection_name_should_keep_cached_session_and_health(self):
        connection = self.create_cached_dji_connection(name="缓存 DJI name")
        self.authenticate(self.child_admin)

        response = self.client.put(
            f"/api/v2/resource/dji-connections/{connection.id}",
            {
                "name": "只改名称",
                "baseUrl": connection.base_url,
                "username": connection.username,
                "password": connection.password,
                "loginFlag": connection.login_flag,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        connection.refresh_from_db()
        self.assertEqual(connection.access_token, "access-token-old")
        self.assertEqual(connection.workspace_id, "workspace-old")
        self.assertEqual(connection.mqtt_addr, "tcp://old-mqtt.example.test:1883")

        health = MqttConnectionHealth.objects.get(dji_connection=connection)
        self.assertEqual(health.status, MqttHealthStatus.ERROR)
        self.assertEqual(health.mqtt_addr, "tcp://old-mqtt.example.test:1883")
        self.assertEqual(health.last_error, "timed out")

    def test_platform_super_admin_should_bind_resource_for_any_department_connection(self):
        self.authenticate(self.platform_super)
        connection = DjiConnection.objects.create(
            owner_department=self.child,
            name="飞行队 DJI",
            base_url="https://dji.example.test",
            username="adminPC1",
            password="secret",
            created_by_user=self.child_admin,
        )
        drone = DroneResource.objects.create(device_sn="SUPER-BIND-DRONE", name="超管绑定无人机", model="M30")

        response = self.client.post(
            "/api/v2/resource/bindings",
            {"resourceType": ResourceType.DRONE, "resourceId": drone.id, "djiConnectionId": connection.id},
            format="json",
        )

        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        binding = ResourceBinding.objects.get(pk=response.data["data"]["id"])
        self.assertEqual(binding.owner_department_id, self.child.id)
        self.assertEqual(binding.dji_connection_id, connection.id)
        self.assertTrue(
            ResourceBindingHistory.objects.filter(
                action_type=BindingActionType.BIND,
                resource_type=ResourceType.DRONE,
                resource_object_id=drone.id,
                new_department=self.child,
                actor_user=self.platform_super,
            ).exists()
        )

    @patch("apps.resource_v2.gateway.DjiConnectionGateway", FakeDiscoveryGateway)
    def test_discover_bind_conflict_visibility_share_and_unbind(self):
        self.authenticate(self.child_admin)
        connection = DjiConnection.objects.create(
            owner_department=self.child,
            name="飞行队 DJI",
            base_url="https://dji.example.test",
            username="adminPC1",
            password="secret",
            created_by_user=self.child_admin,
        )

        discover_response = self.client.post(f"/api/v2/resource/dji-connections/{connection.id}/discover", {}, format="json")

        self.assertEqual(discover_response.status_code, 200, getattr(discover_response, "data", discover_response.content))
        self.assertEqual(FakeDiscoveryGateway.calls, [connection.id])
        drone = DroneResource.objects.get(device_sn="DRONE-SN-001")
        dock = DockResource.objects.get(device_sn="DOCK-SN-001")
        gateway = GatewayResource.objects.get(device_sn="GATEWAY-SN-001")
        payload = PayloadResource.objects.get(payload_sn="PAYLOAD-SN-001")
        self.assertNotIn("dji_device_indexes", db_connection.introspection.table_names())
        self.assertIn("connectionId", discover_response.data["data"])
        self.assertEqual(discover_response.data["data"]["connectionId"], connection.id)
        self.assertEqual(discover_response.data["data"]["drones"][0]["id"], drone.id)
        self.assertIn("resourceId", discover_response.data["data"]["drones"][0])
        self.assertIn("resourceType", discover_response.data["data"]["drones"][0])
        self.assertIn("djiConnectionId", discover_response.data["data"]["drones"][0])
        self.assertEqual(discover_response.data["data"]["drones"][0]["resourceId"], drone.id)
        self.assertEqual(discover_response.data["data"]["drones"][0]["resourceType"], ResourceType.DRONE)
        self.assertEqual(discover_response.data["data"]["drones"][0]["djiConnectionId"], connection.id)
        self.assertEqual(discover_response.data["data"]["docks"][0]["id"], dock.id)
        self.assertEqual(discover_response.data["data"]["docks"][0]["resourceId"], dock.id)
        self.assertEqual(discover_response.data["data"]["docks"][0]["resourceType"], ResourceType.DOCK)
        self.assertEqual(discover_response.data["data"]["docks"][0]["djiConnectionId"], connection.id)
        self.assertEqual(discover_response.data["data"]["docks"][0]["deviceSn"], "DOCK-SN-001")
        self.assertEqual(discover_response.data["data"]["gateways"][0]["id"], gateway.id)
        self.assertEqual(discover_response.data["data"]["gateways"][0]["resourceId"], gateway.id)
        self.assertEqual(discover_response.data["data"]["gateways"][0]["resourceType"], ResourceType.GATEWAY)
        self.assertEqual(discover_response.data["data"]["gateways"][0]["djiConnectionId"], connection.id)
        self.assertEqual(discover_response.data["data"]["gateways"][0]["deviceSn"], "GATEWAY-SN-001")
        self.assertEqual(discover_response.data["data"]["payloads"][0]["resourceId"], payload.id)
        self.assertEqual(discover_response.data["data"]["payloads"][0]["resourceType"], ResourceType.PAYLOAD)
        self.assertEqual(discover_response.data["data"]["payloads"][0]["djiConnectionId"], connection.id)
        self.assertEqual(discover_response.data["data"]["payloads"][0]["payloadSn"], "PAYLOAD-SN-001")

        bind_response = self.client.post(
            "/api/v2/resource/bindings",
            {"resourceType": ResourceType.DRONE, "resourceId": drone.id, "djiConnectionId": connection.id},
            format="json",
        )

        self.assertEqual(bind_response.status_code, 201, getattr(bind_response, "data", bind_response.content))
        binding = ResourceBinding.objects.get(resource_type=ResourceType.DRONE, resource_object_id=drone.id)
        self.assertEqual(binding.owner_department_id, self.child.id)
        self.assertTrue(
            ResourceBindingHistory.objects.filter(
                action_type=BindingActionType.BIND,
                resource_type=ResourceType.DRONE,
                resource_object_id=drone.id,
                new_department=self.child,
            ).exists()
        )

        dock_bind_response = self.client.post(
            "/api/v2/resource/bindings",
            {"resourceType": ResourceType.DOCK, "resourceId": dock.id, "djiConnectionId": connection.id},
            format="json",
        )
        self.assertEqual(dock_bind_response.status_code, 201, getattr(dock_bind_response, "data", dock_bind_response.content))
        docks_response = self.client.get("/api/v2/resource/docks")
        self.assertEqual(docks_response.status_code, 200, getattr(docks_response, "data", docks_response.content))
        self.assertEqual(docks_response.data["data"]["total"], 1)
        self.assertEqual(docks_response.data["data"]["list"][0]["deviceSn"], "DOCK-SN-001")
        drone_detail_response = self.client.get(f"/api/v2/resource/drones/{drone.id}")
        dock_detail_response = self.client.get(f"/api/v2/resource/docks/{dock.id}")
        self.assertEqual(drone_detail_response.status_code, 200, getattr(drone_detail_response, "data", drone_detail_response.content))
        self.assertEqual(dock_detail_response.status_code, 200, getattr(dock_detail_response, "data", dock_detail_response.content))
        self.assertEqual(drone_detail_response.data["data"]["deviceSn"], "DRONE-SN-001")
        self.assertEqual(dock_detail_response.data["data"]["deviceSn"], "DOCK-SN-001")

        payload_bind_response = self.client.post(
            "/api/v2/resource/bindings",
            {"resourceType": ResourceType.PAYLOAD, "resourceId": payload.id, "djiConnectionId": connection.id},
            format="json",
        )
        self.assertEqual(payload_bind_response.status_code, 201, getattr(payload_bind_response, "data", payload_bind_response.content))
        payload_detail_response = self.client.get(f"/api/v2/resource/payloads/{payload.id}")
        self.assertEqual(payload_detail_response.status_code, 200, getattr(payload_detail_response, "data", payload_detail_response.content))
        self.assertEqual(payload_detail_response.data["data"]["deviceSn"], "PAYLOAD-SN-001")

        self.authenticate(self.other_admin)
        conflict_connection = DjiConnection.objects.create(
            owner_department=self.other,
            name="保障队 DJI",
            base_url="https://other.example.test",
            username="other",
            password="secret",
            created_by_user=self.other_admin,
        )
        conflict_response = self.client.post(
            "/api/v2/resource/bindings",
            {"resourceType": ResourceType.DRONE, "resourceId": drone.id, "djiConnectionId": conflict_connection.id},
            format="json",
        )

        self.assertEqual(conflict_response.status_code, 409, getattr(conflict_response, "data", conflict_response.content))
        self.assertEqual(conflict_response.data["data"]["occupyingDepartment"]["id"], self.child.id)

        self.authenticate(self.root_admin)
        parent_visible_response = self.client.get("/api/v2/resource/drones")

        self.assertEqual(parent_visible_response.status_code, 200, getattr(parent_visible_response, "data", parent_visible_response.content))
        self.assertEqual(parent_visible_response.data["data"]["total"], 1)
        self.assertNotIn("bind", parent_visible_response.data["data"]["list"][0]["effectivePermissions"])
        self.assertNotIn("unbind", parent_visible_response.data["data"]["list"][0]["effectivePermissions"])

        root_drone = DroneResource.objects.create(device_sn="ROOT-DRONE-001", name="总部无人机", model="M30")
        root_connection = DjiConnection.objects.create(
            owner_department=self.root,
            name="总部 DJI",
            base_url="https://root.example.test",
            username="root",
            password="secret",
            created_by_user=self.root_admin,
        )
        ResourceBinding.objects.create(
            resource_type=ResourceType.DRONE,
            resource_object_id=root_drone.id,
            owner_department=self.root,
            dji_connection=root_connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=self.root_admin,
        )

        self.authenticate(self.child_admin)
        child_visible_response = self.client.get("/api/v2/resource/drones")
        self.assertEqual(child_visible_response.status_code, 200, getattr(child_visible_response, "data", child_visible_response.content))
        child_visible_sns = {item["deviceSn"] for item in child_visible_response.data["data"]["list"]}
        self.assertIn("DRONE-SN-001", child_visible_sns)
        self.assertNotIn("ROOT-DRONE-001", child_visible_sns)

        group = ResourceShareGroup.objects.create(owner_department=self.child, name="保障共享")
        ResourceShareGroupTargetDepartment.objects.create(share_group=group, department=self.other)
        ResourceSharePermission.objects.create(
            share_group=group,
            resource_type=ResourceType.DRONE,
            resource_object_id=drone.id,
            permissions=["view", "monitor"],
        )

        self.authenticate(self.other_dispatcher)
        shared_response = self.client.get("/api/v2/resource/drones")

        self.assertEqual(shared_response.status_code, 200, getattr(shared_response, "data", shared_response.content))
        self.assertEqual(shared_response.data["data"]["total"], 1)
        self.assertEqual(shared_response.data["data"]["list"][0]["effectivePermissions"], ["view", "monitor"])

        self.authenticate(self.child_admin)
        unbind_response = self.client.delete(f"/api/v2/resource/bindings/{binding.id}")

        self.assertEqual(unbind_response.status_code, 200, getattr(unbind_response, "data", unbind_response.content))
        binding.refresh_from_db()
        self.assertEqual(binding.status, BindingStatus.UNBOUND)
        self.assertTrue(
            V2AuditLog.objects.filter(
                action="unbind_resource",
                actor_department=self.child,
                resource_owner_department=self.child,
                resource_type=ResourceType.DRONE,
                resource_object_id=str(drone.id),
            ).exists()
        )
        self.assertTrue(
            ResourceBindingHistory.objects.filter(
                action_type=BindingActionType.UNBIND,
                resource_type=ResourceType.DRONE,
                resource_object_id=drone.id,
                previous_department=self.child,
            ).exists()
        )

        rebind_response = self.client.post(
            "/api/v2/resource/bindings",
            {"resourceType": ResourceType.DRONE, "resourceId": drone.id, "djiConnectionId": connection.id},
            format="json",
        )
        self.assertEqual(rebind_response.status_code, 201, getattr(rebind_response, "data", rebind_response.content))
        rebound = ResourceBinding.objects.get(pk=rebind_response.data["data"]["id"])
        self.assertNotEqual(rebound.id, binding.id)
        self.assertEqual(rebound.status, BindingStatus.ACTIVE)

    def test_platform_super_admin_should_unbind_any_resource_and_query_global_audit_logs(self):
        connection = DjiConnection.objects.create(
            owner_department=self.child,
            name="飞行队 DJI",
            base_url="https://dji.example.test",
            username="adminPC1",
            password="secret",
            created_by_user=self.child_admin,
        )
        drone = DroneResource.objects.create(device_sn="SUPER-UNBIND-DRONE", name="超管解绑无人机", model="M30")
        binding = ResourceBinding.objects.create(
            resource_type=ResourceType.DRONE,
            resource_object_id=drone.id,
            owner_department=self.child,
            dji_connection=connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=self.child_admin,
        )
        V2AuditLog.objects.create(
            action="other_department_action",
            actor_user=self.other_admin,
            actor_department=self.other,
            resource_owner_department=self.other,
            target_type="manual",
        )

        self.authenticate(self.other_admin)
        denied_unbind_response = self.client.delete(f"/api/v2/resource/bindings/{binding.id}")
        self.assertEqual(
            denied_unbind_response.status_code,
            403,
            getattr(denied_unbind_response, "data", denied_unbind_response.content),
        )
        binding.refresh_from_db()
        self.assertEqual(binding.status, BindingStatus.ACTIVE)

        self.authenticate(self.platform_super)
        unbind_response = self.client.delete(f"/api/v2/resource/bindings/{binding.id}")

        self.assertEqual(unbind_response.status_code, 200, getattr(unbind_response, "data", unbind_response.content))
        binding.refresh_from_db()
        self.assertEqual(binding.status, BindingStatus.UNBOUND)

        audit_response = self.client.get("/api/v2/resource/audit-logs")
        self.assertEqual(audit_response.status_code, 200, getattr(audit_response, "data", audit_response.content))
        returned_actions = {item["action"] for item in audit_response.data["data"]["list"]}
        self.assertIn("other_department_action", returned_actions)
        self.assertIn("unbind_resource", returned_actions)

        filtered_response = self.client.get(
            f"/api/v2/resource/audit-logs?resourceType={ResourceType.DRONE}&resourceObjectId={drone.id}"
        )
        self.assertEqual(filtered_response.status_code, 200, getattr(filtered_response, "data", filtered_response.content))
        self.assertEqual(filtered_response.data["data"]["total"], 1)
        self.assertEqual(filtered_response.data["data"]["list"][0]["action"], "unbind_resource")

    def test_dji_connection_gateway_should_store_session_on_v2_connection_and_discover_resources(self):
        connection = DjiConnection.objects.create(
            owner_department=self.child,
            name="飞行队 DJI",
            base_url="https://dji.example.test",
            username="adminPC1",
            password="secret",
            created_by_user=self.child_admin,
        )
        calls = []

        def fake_request(method, path, *, data, headers, follow_redirects):
            calls.append((method, path, dict(headers)))
            if path == "/api/v1/manage/login":
                return GatewayResponse(
                    status_code=200,
                    headers={},
                    data={
                        "workspace_id": "workspace-v2-001",
                        "access_token": "mock-access-token",
                        "user_id": "dji-user-001",
                        "username": "dji-admin",
                        "user_type": "1",
                        "mqtt_username": "mqtt-user",
                        "mqtt_password": "mqtt-pass",
                        "mqtt_addr": "tcp://mqtt.example.test:1883",
                    },
                )
            if "domain=0" in path:
                return GatewayResponse(
                    status_code=200,
                    headers={},
                    data={
                        "list": [{"device_sn": "GATEWAY-DRONE-001", "device_name": "网关无人机"}],
                        "pagination": {"page": 1, "page_size": 100, "total": 1},
                    },
                )
            if "domain=3" in path:
                return GatewayResponse(
                    status_code=200,
                    headers={},
                    data={
                        "list": [{"device_sn": "GATEWAY-DOCK-001", "device_name": "网关机场"}],
                        "pagination": {"page": 1, "page_size": 100, "total": 1},
                    },
                )
            if "domain=2" in path:
                return GatewayResponse(
                    status_code=200,
                    headers={},
                    data={"list": [], "pagination": {"page": 1, "page_size": 100, "total": 0}},
                )
            if "/devices?" in path and "domain=" not in path:
                return GatewayResponse(
                    status_code=200,
                    headers={},
                    data={
                        "list": [
                            {
                                "device_sn": "GATEWAY-RC-001",
                                "device_name": "网关遥控端",
                                "children": {
                                    "device_sn": "GATEWAY-CHILD-DRONE-001",
                                    "device_name": "遥控器下挂无人机",
                                    "domain": 0,
                                    "type": 99,
                                    "status": False,
                                    "bound_status": False,
                                },
                            }
                        ],
                        "pagination": {"page": 1, "page_size": 100, "total": 1},
                    },
                )
            raise AssertionError(f"unexpected upstream request: {method} {path}")

        with patch.object(DjiConnectionGateway, "_request", side_effect=fake_request), patch.object(
            DjiConnectionGateway,
            "_validate_workspace",
            return_value=True,
        ):
            discovered = DjiConnectionGateway(connection).discover()

        connection.refresh_from_db()
        self.assertEqual(connection.workspace_id, "workspace-v2-001")
        self.assertEqual(connection.access_token, "mock-access-token")
        self.assertEqual(connection.mqtt_username, "mqtt-user")
        self.assertEqual(connection.status, "ACTIVE")
        self.assertEqual(discovered["drones"][0]["device_sn"], "GATEWAY-DRONE-001")
        self.assertEqual(discovered["drones"][1]["device_sn"], "GATEWAY-CHILD-DRONE-001")
        self.assertEqual(discovered["docks"][0]["device_sn"], "GATEWAY-DOCK-001")
        self.assertEqual(discovered["gateways"][0]["device_sn"], "GATEWAY-RC-001")
        self.assertNotIn("dji_device_indexes", db_connection.introspection.table_names())
        self.assertTrue(any(path == "/api/v1/manage/login" for _method, path, _headers in calls))
        self.assertTrue(any("domain=0" in path for _method, path, _headers in calls))
        self.assertTrue(any("domain=2" in path for _method, path, _headers in calls))
        self.assertTrue(any("domain=3" in path for _method, path, _headers in calls))
        self.assertTrue(any("/devices?" in path and "domain=" not in path for _method, path, _headers in calls))

    def test_dji_gateway_should_discover_offline_child_drone_from_bound_gateway(self):
        connection = DjiConnection.objects.create(
            owner_department=self.child,
            name="飞行队 DJI",
            base_url="https://dji.example.test",
            username="adminPC1",
            password="secret",
            created_by_user=self.child_admin,
            workspace_id="workspace-v2-001",
            access_token="mock-access-token",
            status="ACTIVE",
        )
        calls = []

        def fake_request(method, path, *, data, headers, follow_redirects):
            calls.append((method, path, dict(headers)))
            if "domain=0" in path:
                return GatewayResponse(status_code=200, headers={}, data={"list": []})
            if "domain=2" in path:
                return GatewayResponse(
                    status_code=200,
                    headers={},
                    data={
                        "list": [
                            {
                                "device_sn": "GATEWAY-RC-001",
                                "device_name": "网关遥控端",
                                "domain": 2,
                                "status": False,
                                "children": {
                                    "device_sn": "GATEWAY-CHILD-DRONE-001",
                                    "device_name": "遥控器下挂无人机",
                                    "domain": 0,
                                    "type": 99,
                                    "status": False,
                                    "bound_status": False,
                                },
                            }
                        ]
                    },
                )
            if "domain=3" in path:
                return GatewayResponse(status_code=200, headers={}, data={"list": []})
            if "/devices?" in path and "domain=" not in path:
                return GatewayResponse(status_code=200, headers={}, data={"list": []})
            raise AssertionError(f"unexpected upstream request: {method} {path}")

        with patch.object(DjiConnectionGateway, "_request", side_effect=fake_request), patch.object(
            DjiConnectionGateway,
            "_validate_workspace",
            return_value=True,
        ):
            discovered = DjiConnectionGateway(connection).discover()

        self.assertEqual(discovered["drones"][0]["device_sn"], "GATEWAY-CHILD-DRONE-001")
        self.assertIs(discovered["drones"][0]["status"], False)
        self.assertTrue(any("domain=2" in path for _method, path, _headers in calls))

    def test_upsert_resource_should_apply_explicit_offline_status_and_clear_last_seen(self):
        last_seen_at = timezone.now()
        drone = DroneResource.objects.create(
            device_sn="OFFLINE-SYNC-DRONE",
            name="缓存在线无人机",
            model="M30",
            online_status=True,
            last_seen_at=last_seen_at,
        )

        updated = upsert_resource_from_payload(
            ResourceType.DRONE,
            {
                "device_sn": drone.device_sn,
                "device_name": "缓存在线无人机",
                "type": 99,
                "status": False,
            },
        )

        updated.refresh_from_db()
        self.assertFalse(updated.online_status)
        self.assertIsNone(updated.last_seen_at)

    def test_upsert_resource_should_not_overwrite_online_status_without_status_field(self):
        last_seen_at = timezone.now()
        drone = DroneResource.objects.create(
            device_sn="NO-STATUS-DRONE",
            name="无状态 payload 无人机",
            model="M30",
            online_status=True,
            last_seen_at=last_seen_at,
        )

        updated = upsert_resource_from_payload(
            ResourceType.DRONE,
            {
                "device_sn": drone.device_sn,
                "device_name": "无状态 payload 无人机",
                "type": 99,
            },
        )

        updated.refresh_from_db()
        self.assertTrue(updated.online_status)
        self.assertEqual(updated.last_seen_at, last_seen_at)

    def test_sync_connection_resources_should_update_bound_drone_from_upstream_child_status(self):
        from apps.resource_v2.services import sync_connection_resources_from_upstream

        connection = DjiConnection.objects.create(
            owner_department=self.child,
            name="飞行队 DJI",
            base_url="https://dji.example.test",
            username="adminPC1",
            password="secret",
            created_by_user=self.child_admin,
        )
        drone = DroneResource.objects.create(
            device_sn="SYNC-CHILD-DRONE-001",
            name="缓存在线无人机",
            model="M30",
            online_status=True,
            last_seen_at=timezone.now(),
        )
        ResourceBinding.objects.create(
            resource_type=ResourceType.DRONE,
            resource_object_id=drone.id,
            owner_department=self.child,
            dji_connection=connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=self.child_admin,
        )

        with patch.object(
            DjiConnectionGateway,
            "discover",
            return_value={
                "drones": [{"device_sn": drone.device_sn, "device_name": drone.name, "domain": 0, "status": False}],
                "docks": [],
                "gateways": [],
                "payloads": [],
            },
        ):
            result = sync_connection_resources_from_upstream(connection)

        drone.refresh_from_db()
        self.assertEqual(len(result["drones"]), 1)
        self.assertFalse(drone.online_status)
        self.assertIsNone(drone.last_seen_at)

        self.authenticate(self.child_admin)
        response = self.client.get("/api/v2/resource/drones")
        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["total"], 1)
        self.assertEqual(response.data["data"]["list"][0]["deviceSn"], drone.device_sn)
        self.assertFalse(response.data["data"]["list"][0]["onlineStatus"])

    def test_share_group_api_should_manage_targets_resources_visibility_and_audit(self):
        connection = DjiConnection.objects.create(
            owner_department=self.child,
            name="飞行队 DJI",
            base_url="https://dji.example.test",
            username="adminPC1",
            password="secret",
            created_by_user=self.child_admin,
        )
        drone = DroneResource.objects.create(device_sn="SHARED-DRONE-001", name="共享无人机", model="M30")
        binding = ResourceBinding.objects.create(
            resource_type=ResourceType.DRONE,
            resource_object_id=drone.id,
            owner_department=self.child,
            dji_connection=connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=self.child_admin,
        )
        self.authenticate(self.child_admin)

        create_response = self.client.post(
            "/api/v2/resource/share-groups",
            {"name": "保障共享"},
            format="json",
        )

        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        group_id = create_response.data["data"]["id"]
        self.assertEqual(create_response.data["data"]["ownerDepartmentId"], self.child.id)
        self.assertTrue(
            V2AuditLog.objects.filter(
                action="create_share_group",
                actor_department=self.child,
                resource_owner_department=self.child,
                target_type="resource_share_group",
                target_id=str(group_id),
            ).exists()
        )

        update_response = self.client.put(
            f"/api/v2/resource/share-groups/{group_id}",
            {"name": "保障共享组", "status": DirectoryStatus.ACTIVE},
            format="json",
        )
        self.assertEqual(update_response.status_code, 200, getattr(update_response, "data", update_response.content))
        self.assertEqual(update_response.data["data"]["name"], "保障共享组")

        target_response = self.client.post(
            f"/api/v2/resource/share-groups/{group_id}/departments",
            {"departmentId": self.other.id},
            format="json",
        )
        self.assertEqual(target_response.status_code, 201, getattr(target_response, "data", target_response.content))
        self.assertEqual(target_response.data["data"]["departmentId"], self.other.id)

        duplicate_target_response = self.client.post(
            f"/api/v2/resource/share-groups/{group_id}/departments",
            {"departmentId": self.other.id},
            format="json",
        )
        self.assertEqual(
            duplicate_target_response.status_code,
            409,
            getattr(duplicate_target_response, "data", duplicate_target_response.content),
        )

        share_response = self.client.post(
            f"/api/v2/resource/share-groups/{group_id}/resources",
            {"resourceType": ResourceType.DRONE, "resourceId": drone.id, "permissions": ["monitor", "view", "view"]},
            format="json",
        )
        self.assertEqual(share_response.status_code, 201, getattr(share_response, "data", share_response.content))
        resource_share_id = share_response.data["data"]["id"]
        self.assertEqual(share_response.data["data"]["resourceType"], ResourceType.DRONE)
        self.assertEqual(share_response.data["data"]["resourceId"], drone.id)
        self.assertEqual(share_response.data["data"]["permissions"], ["view", "monitor"])

        self.authenticate(self.other_dispatcher)
        visible_response = self.client.get("/api/v2/resource/drones")
        self.assertEqual(visible_response.status_code, 200, getattr(visible_response, "data", visible_response.content))
        self.assertEqual(visible_response.data["data"]["total"], 1)
        self.assertEqual(visible_response.data["data"]["list"][0]["deviceSn"], "SHARED-DRONE-001")
        self.assertEqual(visible_response.data["data"]["list"][0]["effectivePermissions"], ["view", "monitor"])

        self.authenticate(self.child_admin)
        replace_permissions_response = self.client.put(
            f"/api/v2/resource/share-groups/{group_id}/resources/{resource_share_id}",
            {"permissions": ["dispatch_task", "view", "monitor"]},
            format="json",
        )
        self.assertEqual(
            replace_permissions_response.status_code,
            200,
            getattr(replace_permissions_response, "data", replace_permissions_response.content),
        )
        self.assertEqual(replace_permissions_response.data["data"]["permissions"], ["view", "monitor", "dispatch_task"])

        self.authenticate(self.other_dispatcher)
        narrowed_response = self.client.get("/api/v2/resource/drones")
        self.assertEqual(narrowed_response.status_code, 200, getattr(narrowed_response, "data", narrowed_response.content))
        self.assertEqual(
            narrowed_response.data["data"]["list"][0]["effectivePermissions"],
            ["view", "monitor", "dispatch_task"],
        )

        self.authenticate(self.child_admin)
        delete_share_response = self.client.delete(
            f"/api/v2/resource/share-groups/{group_id}/resources/{resource_share_id}"
        )
        self.assertEqual(delete_share_response.status_code, 200, getattr(delete_share_response, "data", delete_share_response.content))
        self.assertEqual(delete_share_response.data["data"]["id"], resource_share_id)

        self.authenticate(self.other_dispatcher)
        no_visible_response = self.client.get("/api/v2/resource/drones")
        self.assertEqual(no_visible_response.status_code, 200, getattr(no_visible_response, "data", no_visible_response.content))
        self.assertEqual(no_visible_response.data["data"]["total"], 0)

        self.authenticate(self.child_admin)
        delete_target_response = self.client.delete(
            f"/api/v2/resource/share-groups/{group_id}/departments/{self.other.id}"
        )
        self.assertEqual(delete_target_response.status_code, 200, getattr(delete_target_response, "data", delete_target_response.content))
        self.assertEqual(delete_target_response.data["data"]["departmentId"], self.other.id)

        expected_actions = {
            "create_share_group",
            "update_share_group",
            "add_share_group_target_department",
            "remove_share_group_target_department",
            "share_resource_to_group",
            "update_resource_share_permissions",
            "remove_resource_sharing",
        }
        logged_actions = set(
            V2AuditLog.objects.filter(actor_department=self.child, resource_owner_department=self.child).values_list(
                "action",
                flat=True,
            )
        )
        self.assertTrue(expected_actions.issubset(logged_actions))
        self.assertFalse(ResourceSharePermission.objects.filter(pk=resource_share_id).exists())
        self.assertTrue(ResourceBinding.objects.filter(pk=binding.id, status=BindingStatus.ACTIVE).exists())

    def test_platform_super_admin_should_manage_all_share_groups(self):
        child_drone = self.bind_v2_drone(
            department=self.child,
            actor=self.child_admin,
            device_sn="SUPER-SHARE-CHILD-DRONE",
        )
        other_drone = self.bind_v2_drone(
            department=self.other,
            actor=self.other_admin,
            device_sn="SUPER-SHARE-OTHER-DRONE",
        )
        self.authenticate(self.platform_super)

        create_response = self.client.post(
            "/api/v2/resource/share-groups",
            {"ownerDepartmentId": self.child.id, "name": "超管代建共享组"},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        group_id = create_response.data["data"]["id"]
        self.assertEqual(create_response.data["data"]["ownerDepartmentId"], self.child.id)

        update_response = self.client.put(
            f"/api/v2/resource/share-groups/{group_id}",
            {"name": "超管维护共享组", "status": DirectoryStatus.ACTIVE},
            format="json",
        )
        self.assertEqual(update_response.status_code, 200, getattr(update_response, "data", update_response.content))
        self.assertEqual(update_response.data["data"]["name"], "超管维护共享组")

        target_response = self.client.post(
            f"/api/v2/resource/share-groups/{group_id}/departments",
            {"departmentId": self.other.id},
            format="json",
        )
        self.assertEqual(target_response.status_code, 201, getattr(target_response, "data", target_response.content))

        share_response = self.client.post(
            f"/api/v2/resource/share-groups/{group_id}/resources",
            {"resourceType": ResourceType.DRONE, "resourceId": child_drone.id, "permissions": ["view", "monitor"]},
            format="json",
        )
        self.assertEqual(share_response.status_code, 201, getattr(share_response, "data", share_response.content))
        resource_share_id = share_response.data["data"]["id"]

        other_owner_resource_response = self.client.post(
            f"/api/v2/resource/share-groups/{group_id}/resources",
            {"resourceType": ResourceType.DRONE, "resourceId": other_drone.id, "permissions": ["view"]},
            format="json",
        )
        self.assertEqual(
            other_owner_resource_response.status_code,
            403,
            getattr(other_owner_resource_response, "data", other_owner_resource_response.content),
        )

        update_permissions_response = self.client.put(
            f"/api/v2/resource/share-groups/{group_id}/resources/{resource_share_id}",
            {"permissions": ["dispatch_task", "view"]},
            format="json",
        )
        self.assertEqual(
            update_permissions_response.status_code,
            200,
            getattr(update_permissions_response, "data", update_permissions_response.content),
        )
        self.assertEqual(update_permissions_response.data["data"]["permissions"], ["view", "dispatch_task"])

        delete_resource_response = self.client.delete(
            f"/api/v2/resource/share-groups/{group_id}/resources/{resource_share_id}"
        )
        self.assertEqual(
            delete_resource_response.status_code,
            200,
            getattr(delete_resource_response, "data", delete_resource_response.content),
        )

        delete_target_response = self.client.delete(
            f"/api/v2/resource/share-groups/{group_id}/departments/{self.other.id}"
        )
        self.assertEqual(
            delete_target_response.status_code,
            200,
            getattr(delete_target_response, "data", delete_target_response.content),
        )

        expected_actions = {
            "create_share_group",
            "update_share_group",
            "add_share_group_target_department",
            "remove_share_group_target_department",
            "share_resource_to_group",
            "update_resource_share_permissions",
            "remove_resource_sharing",
        }
        logged_actions = set(
            V2AuditLog.objects.filter(actor_department=self.root, resource_owner_department=self.child).values_list(
                "action",
                flat=True,
            )
        )
        self.assertTrue(expected_actions.issubset(logged_actions))

    def test_share_group_api_should_enforce_admin_ownership_and_validation(self):
        self.authenticate(self.child_admin)
        group = ResourceShareGroup.objects.create(owner_department=self.child, name="飞行队共享")
        child_connection = DjiConnection.objects.create(
            owner_department=self.child,
            name="飞行队 DJI",
            base_url="https://dji.example.test",
            username="adminPC1",
            password="secret",
            created_by_user=self.child_admin,
        )
        child_drone = DroneResource.objects.create(device_sn="CHILD-SHARE-DRONE", name="飞行队无人机", model="M30")
        ResourceBinding.objects.create(
            resource_type=ResourceType.DRONE,
            resource_object_id=child_drone.id,
            owner_department=self.child,
            dji_connection=child_connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=self.child_admin,
        )
        other_connection = DjiConnection.objects.create(
            owner_department=self.other,
            name="保障队 DJI",
            base_url="https://other.example.test",
            username="other",
            password="secret",
            created_by_user=self.other_admin,
        )
        other_drone = DroneResource.objects.create(device_sn="OTHER-SHARE-DRONE", name="保障队无人机", model="M30")
        ResourceBinding.objects.create(
            resource_type=ResourceType.DRONE,
            resource_object_id=other_drone.id,
            owner_department=self.other,
            dji_connection=other_connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=self.other_admin,
        )
        unbound_drone = DroneResource.objects.create(device_sn="UNBOUND-SHARE-DRONE", name="未绑定无人机", model="M30")
        disabled_department = Department.objects.create(
            name="停用部门",
            parent=self.root,
            status=DirectoryStatus.DISABLED,
        )

        self.authenticate(self.other_admin)
        denied_update_response = self.client.put(
            f"/api/v2/resource/share-groups/{group.id}",
            {"name": "越权修改", "status": DirectoryStatus.ACTIVE},
            format="json",
        )
        self.assertEqual(denied_update_response.status_code, 403, getattr(denied_update_response, "data", denied_update_response.content))

        self.authenticate(self.other_dispatcher)
        denied_create_response = self.client.post("/api/v2/resource/share-groups", {"name": "非法共享"}, format="json")
        self.assertEqual(denied_create_response.status_code, 403, getattr(denied_create_response, "data", denied_create_response.content))

        self.authenticate(self.child_admin)
        denied_other_owner_response = self.client.post(
            "/api/v2/resource/share-groups",
            {"ownerDepartmentId": self.other.id, "name": "越权代建"},
            format="json",
        )
        self.assertEqual(
            denied_other_owner_response.status_code,
            403,
            getattr(denied_other_owner_response, "data", denied_other_owner_response.content),
        )

        self.authenticate(self.platform_super)
        platform_list_response = self.client.get("/api/v2/resource/share-groups")
        self.assertEqual(platform_list_response.status_code, 200, getattr(platform_list_response, "data", platform_list_response.content))
        self.assertEqual(platform_list_response.data["data"]["total"], 1)
        platform_create_response = self.client.post(
            "/api/v2/resource/share-groups",
            {"ownerDepartmentId": self.child.id, "name": "超管代建"},
            format="json",
        )
        self.assertEqual(platform_create_response.status_code, 201, getattr(platform_create_response, "data", platform_create_response.content))
        self.assertEqual(platform_create_response.data["data"]["ownerDepartmentId"], self.child.id)

        self.authenticate(self.child_admin)
        disabled_target_response = self.client.post(
            f"/api/v2/resource/share-groups/{group.id}/departments",
            {"departmentId": disabled_department.id},
            format="json",
        )
        self.assertEqual(disabled_target_response.status_code, 400, getattr(disabled_target_response, "data", disabled_target_response.content))

        active_target_response = self.client.post(
            f"/api/v2/resource/share-groups/{group.id}/departments",
            {"departmentId": self.other.id},
            format="json",
        )
        self.assertEqual(
            active_target_response.status_code,
            201,
            getattr(active_target_response, "data", active_target_response.content),
        )

        for permissions in ([], ["unbind"], ["unknown"]):
            invalid_permissions_response = self.client.post(
                f"/api/v2/resource/share-groups/{group.id}/resources",
                {"resourceType": ResourceType.DRONE, "resourceId": child_drone.id, "permissions": permissions},
                format="json",
            )
            self.assertEqual(
                invalid_permissions_response.status_code,
                400,
                getattr(invalid_permissions_response, "data", invalid_permissions_response.content),
            )

        other_resource_response = self.client.post(
            f"/api/v2/resource/share-groups/{group.id}/resources",
            {"resourceType": ResourceType.DRONE, "resourceId": other_drone.id, "permissions": ["view"]},
            format="json",
        )
        self.assertEqual(other_resource_response.status_code, 403, getattr(other_resource_response, "data", other_resource_response.content))

        unbound_resource_response = self.client.post(
            f"/api/v2/resource/share-groups/{group.id}/resources",
            {"resourceType": ResourceType.DRONE, "resourceId": unbound_drone.id, "permissions": ["view"]},
            format="json",
        )
        self.assertEqual(
            unbound_resource_response.status_code,
            400,
            getattr(unbound_resource_response, "data", unbound_resource_response.content),
        )

        share_response = self.client.post(
            f"/api/v2/resource/share-groups/{group.id}/resources",
            {"resourceType": ResourceType.DRONE, "resourceId": child_drone.id, "permissions": ["view"]},
            format="json",
        )
        self.assertEqual(share_response.status_code, 201, getattr(share_response, "data", share_response.content))
        duplicate_share_response = self.client.post(
            f"/api/v2/resource/share-groups/{group.id}/resources",
            {"resourceType": ResourceType.DRONE, "resourceId": child_drone.id, "permissions": ["view"]},
            format="json",
        )
        self.assertEqual(duplicate_share_response.status_code, 409, getattr(duplicate_share_response, "data", duplicate_share_response.content))
