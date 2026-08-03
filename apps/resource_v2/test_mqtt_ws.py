import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.access.session_services import create_auth_session
from apps.iam_v2.models import Department, FixedRole, V2AccountProfile, V2AccountRoleAssignment
from apps.resource_v2.models import (
    BindingStatus,
    CameraResource,
    DjiConnection,
    DroneResource,
    MqttLatestMessage,
    ResourceBinding,
    ResourceType,
)
from apps.resource_v2.mqtt import MQTT_BROADCAST_GROUP
from config.asgi import application


User = get_user_model()


def create_v2_actor(*, username: str, role_code: str | None, department: Department):
    user = User.objects.create_user(username=username, password="pass1234", status=1)
    profile = V2AccountProfile.objects.create(
        user=user,
        department=department,
        name=username,
        phone=f"139{user.id:08d}",
        email=f"{username}@example.test",
    )
    if role_code is not None:
        V2AccountRoleAssignment.objects.create(account_profile=profile, role_code=role_code, assigned_by_user=user)
    return user


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
class ResourceV2MqttWebSocketTests(TransactionTestCase):
    def setUp(self):
        self.root = Department.objects.create(name="总部")
        self.owner_department = Department.objects.create(name="资源队", parent=self.root)
        self.dispatcher = create_v2_actor(
            username="mqtt_ws_dispatcher",
            role_code=FixedRole.TASK_MONITOR_DISPATCHER,
            department=self.owner_department,
        )
        self.connection = DjiConnection.objects.create(
            owner_department=self.owner_department,
            name="mqtt ws connection",
            base_url="https://dji.example.test",
            username="admin",
            password="secret",
            created_by_user=self.dispatcher,
        )
        self.drone = DroneResource.objects.create(device_sn="DRONE-WS-001", name="WS 无人机", model="M30")
        ResourceBinding.objects.create(
            resource_type=ResourceType.DRONE,
            resource_object_id=self.drone.id,
            owner_department=self.owner_department,
            dji_connection=self.connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=self.dispatcher,
        )
        MqttLatestMessage.objects.create(
            dji_connection=self.connection,
            topic="thing/product/DRONE-WS-001/osd",
            topic_kind="osd",
            device_sn=self.drone.device_sn,
            received_at=timezone.now(),
            sequence=1,
            raw_payload={"data": {"latitude": 31.23}},
        )

    def access_token(self) -> str:
        return create_auth_session(user=self.dispatcher, request=SimpleNamespace(META={}))["accessToken"]

    def test_websocket_should_authenticate_subscribe_and_stream_realtime_messages_only(self):
        async_to_sync(self._run_subscribe_realtime_only)(self.access_token())

    def test_websocket_should_not_receive_mqtt_messages_before_subscribe(self):
        async_to_sync(self._run_no_push_before_subscribe)(self.access_token())

    async def _run_no_push_before_subscribe(self, token: str):
        communicator = WebsocketCommunicator(application, f"/ws/v2/dji/mqtt?token={token}")
        connected, _subprotocol = await communicator.connect()
        self.assertTrue(connected)

        await get_channel_layer().group_send(
            MQTT_BROADCAST_GROUP,
            {
                "type": "mqtt.message",
                "message": {
                    "type": "mqtt.message",
                    "connectionId": self.connection.id,
                    "topic": "thing/product/DRONE-WS-001/osd",
                    "topicKind": "osd",
                    "deviceSn": self.drone.device_sn,
                    "receivedAt": timezone.now().isoformat(),
                    "sequence": 99,
                    "rawPayload": {"data": {"latitude": 31.99}},
                },
            },
        )

        self.assertTrue(await communicator.receive_nothing(timeout=0.1, interval=0.01))
        await communicator.disconnect()

    async def _run_subscribe_realtime_only(self, token: str):
        communicator = WebsocketCommunicator(application, f"/ws/v2/dji/mqtt?token={token}")
        connected, _subprotocol = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to(
            {
                "type": "subscribe",
                "deviceSns": [self.drone.device_sn],
                "topicKinds": ["osd"],
            }
        )

        accepted = await communicator.receive_json_from(timeout=1)
        self.assertEqual(accepted["type"], "subscription.accepted")
        self.assertEqual(accepted["deviceSns"], [self.drone.device_sn])
        self.assertTrue(await communicator.receive_nothing(timeout=0.1, interval=0.01))

        await get_channel_layer().group_send(
            MQTT_BROADCAST_GROUP,
            {
                "type": "mqtt.message",
                "message": {
                    "type": "mqtt.message",
                    "connectionId": self.connection.id,
                    "topic": "thing/product/DRONE-WS-001/osd",
                    "topicKind": "osd",
                    "deviceSn": self.drone.device_sn,
                    "receivedAt": timezone.now().isoformat(),
                    "sequence": 2,
                    "rawPayload": {"data": {"latitude": 31.24}},
                },
            },
        )
        pushed = await communicator.receive_json_from(timeout=1)
        self.assertEqual(pushed["sequence"], 2)
        self.assertEqual(pushed["rawPayload"]["data"]["latitude"], 31.24)

        await communicator.disconnect()


class FakeCameraResultsUpstream:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.messages.get()

    async def close(self):
        self.closed = True


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
class CameraResultsWebSocketTests(TransactionTestCase):
    def setUp(self):
        self.root = Department.objects.create(name="总部")
        self.owner_department = Department.objects.create(name="资源队", parent=self.root)
        self.viewer = create_v2_actor(username="camera_viewer", role_code=None, department=self.owner_department)
        self.camera = CameraResource.objects.create(
            device_sn="CAMERA-WS-001",
            name="固定摄像头",
            webrtc_url="https://video.example.test/camera/whep",
            results_ws_url="wss://video.example.test/target.results",
            api_username="camera-user",
            api_key="camera-secret",
        )
        ResourceBinding.objects.create(
            resource_type=ResourceType.CAMERA,
            resource_object_id=self.camera.id,
            owner_department=self.owner_department,
            status=BindingStatus.ACTIVE,
            bound_by_user=self.viewer,
        )

    def test_camera_results_should_proxy_for_tree_visible_user_without_role(self):
        token = create_auth_session(user=self.viewer, request=SimpleNamespace(META={}))["accessToken"]
        async_to_sync(self._run_proxy)(token)

    async def _run_proxy(self, token: str):
        upstream = FakeCameraResultsUpstream()
        connect = AsyncMock(return_value=upstream)
        with patch("apps.resource_v2.consumers.websockets.connect", connect):
            communicator = WebsocketCommunicator(application, f"/ws/v2/cameras/{self.camera.id}/results?token={token}")
            connected, _subprotocol = await communicator.connect()
            self.assertTrue(connected)
            connect.assert_awaited_once_with(
                self.camera.results_ws_url,
                ping_interval=20,
                ping_timeout=20,
            )

            await upstream.messages.put('{"tracking_state":"TRACKING","class_id":1}')
            pushed = await communicator.receive_json_from(timeout=1)
            self.assertEqual(pushed["tracking_state"], "TRACKING")
            self.assertEqual(pushed["class_id"], 1)

            await communicator.disconnect()
            self.assertTrue(upstream.closed)
