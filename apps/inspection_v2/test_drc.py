from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.iam_v2.models import Department, FixedRole, V2AccountProfile, V2AccountRoleAssignment
from apps.inspection_v2.consumers import validate_control_frame
from apps.resource_v2.models import (
    BindingStatus,
    DjiConnection,
    DockResource,
    DroneResource,
    ResourceBinding,
    ResourceType,
)


class DrcProxyApiTests(TestCase):
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

    def test_capabilities_are_local_and_do_not_call_upstream(self):
        response = self.client.get(
            "/api/v2/inspection/drc/capabilities", {"dockId": self.dock.id}
        )

        self.assertEqual(response.status_code, 200)
        payload = response.data["data"]
        self.assertTrue(payload["supported"])
        self.assertTrue(payload["available"])
        self.assertEqual(payload["control"]["protocol"], "stick_control")

    def test_connect_rejects_unknown_fields(self):
        response = self.client.post(
            "/api/v2/inspection/drc/connect",
            {"dockId": self.dock.id, "mqttPassword": "not-accepted"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

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
