from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.iam_v2.models import Department, FixedRole, V2AccountProfile, V2AccountRoleAssignment
from apps.resource_v2.models import (
    BindingStatus,
    DjiConnection,
    DockResource,
    DroneResource,
    ResourceBinding,
    ResourceType,
)


class DrcDirectMqttApiTests(TestCase):
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
            "expireTime": 1_800_000_000,
            "enableTls": False,
        }
        self.gateway.enter_drc.return_value = {
            "pub": ["thing/product/DOCK-1/drc/down"],
            "sub": ["thing/product/DOCK-1/drc/up"],
        }

    def test_connect_returns_short_lived_mqtt_credentials_and_topics(self):
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
        self.assertEqual(payload["mqtt"]["password"], "short-lived-password")
        self.assertEqual(payload["mqtt"]["clientId"], "drc-client-1")
        self.assertEqual(payload["publishTopic"], "thing/product/DOCK-1/drc/down")
        self.assertEqual(payload["subscribeTopic"], "thing/product/DOCK-1/drc/up")
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

    def test_connect_reuses_client_id_for_credential_refresh(self):
        with patch(
            "apps.inspection_v2.drc_services.dji_connection_gateway",
            return_value=self.gateway,
        ):
            response = self.client.post(
                "/api/v2/inspection/drc/connect",
                {"dockId": self.dock.id, "clientId": "drc-client-1"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.gateway.connect_drc.assert_called_once_with(
            dock_sn="DOCK-1", expire_sec=3600, client_id="drc-client-1"
        )

    def test_exit_uses_the_same_dock_and_client(self):
        with patch(
            "apps.inspection_v2.drc_services.dji_connection_gateway",
            return_value=self.gateway,
        ):
            response = self.client.post(
                "/api/v2/inspection/drc/exit",
                {"dockId": self.dock.id, "clientId": "drc-client-1"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"], {"status": "CLOSED"})
        self.gateway.exit_drc.assert_called_once_with(
            dock_sn="DOCK-1", client_id="drc-client-1"
        )

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
