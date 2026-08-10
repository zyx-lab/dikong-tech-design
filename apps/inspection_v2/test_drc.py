import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.session_services import create_auth_session
from apps.dji_cloud.gateway import DjiGatewayUpstreamError
from apps.iam_v2.models import Department, FixedRole, V2AccountProfile, V2AccountRoleAssignment
from apps.inspection_v2.consumers import validate_control_frame
from apps.inspection_v2.services import apply_osd_telemetry
from apps.resource_v2.models import (
    BindingStatus,
    DjiConnection,
    DockResource,
    DroneResource,
    ResourceBinding,
    ResourceType,
)
from apps.resource_v2.serializers import upsert_resource_from_payload
from config.asgi import application


class FakeDrcMqttClient:
    def __init__(self, *args, **kwargs):
        del args, kwargs
        self.published = []
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None

    def username_pw_set(self, username, password):
        self.credentials = (username, password)

    def connect_async(self, host, port, keepalive):
        self.endpoint = (host, port, keepalive)

    def loop_start(self):
        self.on_connect(self, None, None, SimpleNamespace(is_failure=False), None)

    def loop_stop(self):
        pass

    def disconnect(self):
        self.on_disconnect(self, None, None, 0, None)

    def subscribe(self, topic, qos):
        self.subscription = (topic, qos)

    def publish(self, topic, payload, qos):
        self.published.append((topic, json.loads(payload), qos))
        return SimpleNamespace(rc=0)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
class DrcProxyApiTests(TransactionTestCase):
    def setUp(self):
        self.department = Department.objects.create(name="DRC")
        self.user = get_user_model().objects.create_user(
            username="drc-operator", password="pass1234", status=1
        )
        profile = V2AccountProfile.objects.create(
            user=self.user,
            department=self.department,
            name="DRC operator",
            phone="13800000001",
            email="drc@example.test",
        )
        V2AccountRoleAssignment.objects.create(
            account_profile=profile,
            role_code=FixedRole.TASK_MONITOR_DISPATCHER,
            assigned_by_user=self.user,
        )
        self.connection = DjiConnection.objects.create(
            owner_department=self.department,
            name="DJI",
            base_url="https://dji.example.test",
            username="admin",
            password="secret",
            workspace_id="workspace-1",
            access_token="token",
        )
        self.drone = DroneResource.objects.create(
            device_sn="DRONE-1", model="Matrice 4D", online_status=True
        )
        self.dock = DockResource.objects.create(
            device_sn="DOCK-1",
            model="Dock 3",
            online_status=True,
            last_payload={"child_device_sn": self.drone.device_sn},
        )
        for resource_type, resource in (
            (ResourceType.DOCK, self.dock),
            (ResourceType.DRONE, self.drone),
        ):
            ResourceBinding.objects.create(
                resource_type=resource_type,
                resource_object_id=resource.id,
                owner_department=self.department,
                dji_connection=self.connection,
                status=BindingStatus.ACTIVE,
                bound_by_user=self.user,
            )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.gateway = MagicMock()
        self.gateway.connect_drc.return_value = {
            "address": "mqtt://broker.example.test:1883",
            "username": "operator",
            "password": "short-lived-password",
            "clientId": "drc-client-1",
            "expireTime": int(timezone.now().timestamp()) + 1800,
            "enableTls": False,
        }
        self.gateway.enter_drc.return_value = {
            "pub": ["thing/product/DOCK-1/drc/down"],
            "sub": ["thing/product/DOCK-1/drc/up"],
        }

    def test_connect_keeps_mqtt_credentials_inside_the_project(self):
        with patch(
            "apps.inspection_v2.drc_services.dji_connection_gateway",
            return_value=self.gateway,
        ):
            response = self.client.post(
                "/api/v2/inspection/drc/connect",
                {
                    "dockId": self.dock.id,
                    "expireSec": 1800,
                    "osdFrequency": 10,
                    "hsiFrequency": 5,
                },
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.data["data"]
        self.assertEqual(payload["dockId"], self.dock.id)
        self.assertEqual(payload["droneId"], self.drone.id)
        self.assertEqual(
            payload["webSocketPath"], f"/ws/v2/drc/sessions/{payload['sessionId']}"
        )
        self.assertFalse(
            {"mqtt", "address", "username", "password", "clientId", "publishTopic", "subscribeTopic"}
            .intersection(payload)
        )
        config = cache.get(f"drc:mqtt:{payload['sessionId']}")
        self.assertEqual(config["password"], "short-lived-password")
        self.assertEqual(config["publishTopic"], "thing/product/DOCK-1/drc/down")
        self.gateway.connect_drc.assert_called_once_with(
            dock_sn="DOCK-1", expire_sec=1800, client_id=None
        )
        self.gateway.enter_drc.assert_called_once_with(
            dock_sn="DOCK-1",
            client_id="drc-client-1",
            expire_sec=1800,
            osd_frequency=10,
            hsi_frequency=5,
        )

    def test_exit_uses_the_server_side_session(self):
        with patch(
            "apps.inspection_v2.drc_services.dji_connection_gateway",
            return_value=self.gateway,
        ):
            connected = self.client.post(
                "/api/v2/inspection/drc/connect",
                {"dockId": self.dock.id},
                format="json",
            ).data["data"]
            response = self.client.post(
                "/api/v2/inspection/drc/exit",
                {"sessionId": connected["sessionId"]},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"], {"status": "CLOSED"})
        self.assertIsNone(cache.get(f"drc:mqtt:{connected['sessionId']}"))
        self.gateway.exit_drc.assert_called_once_with(
            dock_sn="DOCK-1", client_id="drc-client-1"
        )

    def test_control_frame_validation_enforces_range_and_sequence(self):
        frame = {
            "type": "control.frame",
            "clientSeq": 2,
            "sentAt": 1,
            "roll": 364,
            "pitch": 1024,
            "throttle": 1684,
            "yaw": 1024,
        }
        client_seq, values = validate_control_frame(frame, last_client_seq=1)
        self.assertEqual(client_seq, 2)
        self.assertEqual(values["throttle"], 1684)
        with self.assertRaises(ValueError):
            validate_control_frame({**frame, "roll": 363}, last_client_seq=1)
        with self.assertRaises(ValueError):
            validate_control_frame({**frame, "roll": True}, last_client_seq=1)
        with self.assertRaises(ValueError):
            validate_control_frame(frame, last_client_seq=2)

    def test_websocket_control_emergency_stop_and_single_connection(self):
        mqtt_client = FakeDrcMqttClient()
        token = create_auth_session(
            user=self.user, request=SimpleNamespace(META={})
        )["accessToken"]
        with patch(
            "apps.inspection_v2.drc_services.dji_connection_gateway",
            return_value=self.gateway,
        ), patch(
            "apps.inspection_v2.consumers.mqtt.Client",
            return_value=mqtt_client,
        ):
            session = self.client.post(
                "/api/v2/inspection/drc/connect",
                {"dockId": self.dock.id},
                format="json",
            ).data["data"]
            async_to_sync(self._run_websocket_control)(
                token, str(session["sessionId"]), mqtt_client
            )

        self.assertIsNone(cache.get(f"drc:mqtt:{session['sessionId']}"))
        self.assertIsNone(cache.get(f"drc:websocket:{session['sessionId']}"))
        self.gateway.exit_drc.assert_called_once_with(
            dock_sn="DOCK-1", client_id="drc-client-1"
        )

    async def _run_websocket_control(self, token, session_id, mqtt_client):
        path = f"/ws/v2/drc/sessions/{session_id}?token={token}"
        communicator = WebsocketCommunicator(application, path)
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        self.assertEqual((await communicator.receive_json_from())["type"], "session.connecting")
        self.assertEqual((await communicator.receive_json_from())["type"], "session.connected")

        duplicate = WebsocketCommunicator(application, path)
        self.assertEqual(await duplicate.connect(), (False, 4409))

        await communicator.send_json_to({"type": "control.arm"})
        self.assertEqual(
            await communicator.receive_json_from(),
            {"type": "control.state", "armed": True},
        )
        await communicator.send_json_to({
            "type": "control.frame",
            "clientSeq": 1,
            "sentAt": 1,
            "roll": 1024,
            "pitch": 1024,
            "throttle": 1024,
            "yaw": 1024,
        })
        self.assertEqual((await communicator.receive_json_from())["type"], "control.ack")

        await communicator.send_json_to({"type": "control.emergencyStop"})
        state = await communicator.receive_json_from()
        ack = await communicator.receive_json_from()
        self.assertEqual(state["reason"], "EMERGENCY_STOP_LATCHED")
        self.assertEqual(ack["type"], "control.emergencyStop.ack")
        self.assertEqual(
            [message[1]["method"] for message in mqtt_client.published[-2:]],
            ["stick_control", "drone_emergency_stop"],
        )

        await communicator.send_json_to({"type": "control.arm"})
        self.assertEqual(
            await communicator.receive_json_from(),
            {"type": "error", "code": "EMERGENCY_STOP_LATCHED"},
        )
        await communicator.disconnect()

    def test_capabilities_are_local_and_do_not_call_upstream(self):
        response = self.client.get(
            "/api/v2/inspection/drc/capabilities", {"dockId": self.dock.id}
        )

        self.assertEqual(response.status_code, 200)
        payload = response.data["data"]
        self.assertTrue(payload["supported"])
        self.assertTrue(payload["available"])
        self.assertEqual(payload["control"]["protocol"], "stick_control")

    def test_capabilities_accepts_numeric_dock3_model(self):
        self.dock.model = "3"
        self.dock.save(update_fields=["model", "updated_at"])

        response = self.client.get(
            "/api/v2/inspection/drc/capabilities", {"dockId": self.dock.id}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["data"]["supported"])

    def test_dock_osd_sub_device_updates_child_and_capability(self):
        self.dock.model = "3"
        self.dock.last_payload = {}
        self.dock.save(update_fields=["model", "last_payload", "updated_at"])

        apply_osd_telemetry(
            device_sn=self.dock.device_sn,
            dji_connection=self.connection,
            payload={
                "data": {
                    "sub_device": {
                        "device_sn": self.drone.device_sn,
                        "device_online_status": 0,
                    }
                }
            },
        )

        self.drone.refresh_from_db()
        self.assertFalse(self.drone.online_status)
        self.assertIsNone(self.drone.last_seen_at)
        response = self.client.get(
            "/api/v2/inspection/drc/capabilities", {"dockId": self.dock.id}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["droneId"], self.drone.id)
        self.assertEqual(
            response.data["data"]["blockers"],
            [{"code": "DRONE_OFFLINE", "message": "子无人机不在线"}],
        )

    def test_sparse_dock_osd_and_resource_sync_preserve_camel_case_sub_device(self):
        apply_osd_telemetry(
            device_sn=self.dock.device_sn,
            dji_connection=self.connection,
            payload={
                "data": {
                    "cover_state": 0,
                    "subDevice": {
                        "deviceSn": self.drone.device_sn,
                        "deviceOnlineStatus": 1,
                    },
                }
            },
        )
        apply_osd_telemetry(
            device_sn=self.dock.device_sn,
            dji_connection=self.connection,
            payload={"data": {"environment_temperature": 25}},
        )
        upsert_resource_from_payload(
            ResourceType.DOCK,
            {"device_sn": self.dock.device_sn, "type": 3, "status": False},
        )

        self.dock.refresh_from_db()
        self.drone.refresh_from_db()
        self.assertEqual(
            self.dock.last_payload["data"]["subDevice"]["deviceSn"],
            self.drone.device_sn,
        )
        self.assertEqual(self.dock.last_payload["data"]["cover_state"], 0)
        self.assertEqual(self.dock.last_payload["data"]["environment_temperature"], 25)
        self.assertTrue(self.drone.online_status)
        self.assertIsNotNone(self.drone.last_seen_at)

    def test_dock_debug_actions_use_existing_java_remote_debug_endpoint(self):
        self.dock.model = "3"
        self.dock.last_payload = {}
        self.dock.save(update_fields=["model", "last_payload", "updated_at"])
        self.drone.online_status = False
        self.drone.save(update_fields=["online_status", "updated_at"])
        self.gateway.control_dock_debug.return_value = {"accepted": True}

        with patch(
            "apps.inspection_v2.drc_services.dji_connection_gateway",
            return_value=self.gateway,
        ):
            for action in (
                "debug_mode_open",
                "cover_open",
                "cover_close",
                "debug_mode_close",
            ):
                with self.subTest(action=action):
                    response = self.client.post(
                        "/api/v2/inspection/drc/dock-actions",
                        {"dockId": self.dock.id, "action": action},
                        format="json",
                    )

                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.data["data"]["action"], action)
                    self.gateway.control_dock_debug.assert_called_once_with("DOCK-1", action)
                    self.gateway.control_dock_debug.reset_mock()

    def test_dock_debug_actions_reject_unknown_action(self):
        response = self.client.post(
            "/api/v2/inspection/drc/dock-actions",
            {"dockId": self.dock.id, "action": "device_reboot"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_dock_debug_reply_timeout_warns_that_outcome_is_unknown(self):
        self.gateway.control_dock_debug.side_effect = DjiGatewayUpstreamError(
            "DJI upstream business error",
            status_code=200,
            data={
                "code": "E0001",
                "msg": "CloudSDKException: Error Code: 211001, No message reply received.",
            },
        )

        with patch(
            "apps.inspection_v2.drc_services.dji_connection_gateway",
            return_value=self.gateway,
        ):
            response = self.client.post(
                "/api/v2/inspection/drc/dock-actions",
                {"dockId": self.dock.id, "action": "cover_open"},
                format="json",
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.data["msg"],
            "机场未及时确认指令，动作可能已经执行，请先检查机场状态，勿重复下发",
        )
        self.assertEqual(
            response.data["data"]["reasonCode"],
            "DJI_COMMAND_OUTCOME_UNKNOWN",
        )

    def test_connect_rejects_unknown_fields(self):
        response = self.client.post(
            "/api/v2/inspection/drc/connect",
            {"dockId": self.dock.id, "mqttPassword": "not-accepted"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_flight_actions_use_existing_java_services_endpoints(self):
        self.gateway.takeoff_to_point.return_value = {"accepted": True}
        self.gateway.fly_to_point.return_value = {"accepted": True}
        self.gateway.update_fly_to_point.return_value = {"accepted": True}
        self.gateway.stop_fly_to_point.return_value = {"accepted": True}
        point = {"latitude": 22.5, "longitude": 113.9, "height": 120.0}
        cases = [
            (
                {
                    "dockId": self.dock.id,
                    "action": "takeoff_to_point",
                    "targetLatitude": 22.5,
                    "targetLongitude": 113.9,
                    "targetHeight": 120.0,
                    "securityTakeoffHeight": 30.0,
                    "rthMode": 1,
                    "rthAltitude": 100.0,
                    "rcLostAction": 2,
                    "commanderModeLostAction": 1,
                    "commanderFlightMode": 1,
                    "commanderFlightHeight": 80.0,
                    "flightSafetyAdvanceCheck": True,
                    "maxSpeed": 10,
                },
                "takeoff_to_point",
            ),
            ({"dockId": self.dock.id, "action": "fly_to_point", "maxSpeed": 10, "points": [point]}, "fly_to_point"),
            (
                {"dockId": self.dock.id, "action": "fly_to_point_update", "maxSpeed": 8, "points": [point]},
                "update_fly_to_point",
            ),
            ({"dockId": self.dock.id, "action": "fly_to_point_stop"}, "stop_fly_to_point"),
        ]

        with patch(
            "apps.inspection_v2.drc_services.dji_connection_gateway",
            return_value=self.gateway,
        ):
            for request_data, gateway_method in cases:
                with self.subTest(action=request_data["action"]):
                    response = self.client.post(
                        "/api/v2/inspection/drc/actions", request_data, format="json"
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.data["data"]["action"], request_data["action"])
                    getattr(self.gateway, gateway_method).assert_called_once()

        takeoff_data = self.gateway.takeoff_to_point.call_args.args[1]
        self.assertEqual(takeoff_data["exit_wayline_when_rc_lost"], 0)
        self.assertTrue(takeoff_data["flight_safety_advance_check"])
        self.gateway.fly_to_point.assert_called_once_with(
            "DOCK-1", {"max_speed": 10, "points": [point]}
        )
        self.gateway.update_fly_to_point.assert_called_once_with(
            "DOCK-1", {"max_speed": 8, "points": [point]}
        )
        self.gateway.stop_fly_to_point.assert_called_once_with("DOCK-1")

    def test_connect_requires_an_operator_role(self):
        user = get_user_model().objects.create_user(
            username="viewer", password="pass1234", status=1
        )
        V2AccountProfile.objects.create(
            user=user,
            department=self.department,
            name="viewer",
            phone="13800000002",
            email="viewer@example.test",
        )
        self.client.force_authenticate(user)

        response = self.client.post(
            "/api/v2/inspection/drc/connect", {"dockId": self.dock.id}, format="json"
        )

        self.assertEqual(response.status_code, 403)
