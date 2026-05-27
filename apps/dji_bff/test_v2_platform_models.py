from django.apps import apps
from django.db import IntegrityError
from django.test import TestCase

from apps.access.models import Tenant, TenantStatus
from apps.dji_bff.gateway import DjiGateway
from apps.dji_bff.models import DjiDeviceIndex, DjiWorkspaceConfig
from apps.drone.models import Drone, DroneStatus


def DjiCloudPlatform():
    return apps.get_model("dji_bff", "DjiCloudPlatform")


class DjiCloudPlatformModelTests(TestCase):
    def setUp(self):
        super().setUp()
        self.tenant_a = Tenant.objects.create(code="v2_tenant_a", name="租户 A", status=TenantStatus.ACTIVE)
        self.tenant_b = Tenant.objects.create(code="v2_tenant_b", name="租户 B", status=TenantStatus.ACTIVE)

    def _platform(self, tenant, *, name: str, workspace_id: str):
        return DjiCloudPlatform().objects.create(
            tenant=tenant,
            name=name,
            base_url=f"https://{workspace_id}.example.test",
            username=f"{workspace_id}-user",
            password="secret",
            login_flag=1,
            workspace_id=workspace_id,
            access_token=f"{workspace_id}-token",
            is_default=False,
        )

    def test_device_index_unique_key_is_scoped_by_dji_platform(self):
        platform_a = self._platform(self.tenant_a, name="平台 A", workspace_id="workspace-a")
        platform_b = self._platform(self.tenant_b, name="平台 B", workspace_id="workspace-b")

        DjiDeviceIndex.objects.create(dji_platform=platform_a, device_sn="SAME-SN", last_payload={"name": "A"})
        DjiDeviceIndex.objects.create(dji_platform=platform_b, device_sn="SAME-SN", last_payload={"name": "B"})

        with self.assertRaises(IntegrityError):
            DjiDeviceIndex.objects.create(dji_platform=platform_a, device_sn="SAME-SN", last_payload={})

    def test_active_drone_device_sn_unique_key_is_scoped_by_tenant_and_dji_platform(self):
        platform_a = self._platform(self.tenant_a, name="平台 A", workspace_id="workspace-a")
        platform_b = self._platform(self.tenant_a, name="平台 B", workspace_id="workspace-b")
        platform_c = self._platform(self.tenant_b, name="平台 C", workspace_id="workspace-c")

        Drone.objects.create(
            tenant=self.tenant_a,
            dji_platform=platform_a,
            code="DRONE-A",
            name="A",
            model="M30",
            device_sn="SAME-SN",
            status=DroneStatus.CLAIMED,
        )
        Drone.objects.create(
            tenant=self.tenant_a,
            dji_platform=platform_b,
            code="DRONE-B",
            name="B",
            model="M30",
            device_sn="SAME-SN",
            status=DroneStatus.CLAIMED,
        )
        Drone.objects.create(
            tenant=self.tenant_b,
            dji_platform=platform_c,
            code="DRONE-C",
            name="C",
            model="M30",
            device_sn="SAME-SN",
            status=DroneStatus.CLAIMED,
        )

        with self.assertRaises(IntegrityError):
            Drone.objects.create(
                tenant=self.tenant_a,
                dji_platform=platform_a,
                code="DRONE-DUP",
                name="duplicate",
                model="M30",
                device_sn="SAME-SN",
                status=DroneStatus.CLAIMED,
            )


class DjiGatewayPlatformBindingTests(TestCase):
    def setUp(self):
        super().setUp()
        self.tenant = Tenant.objects.create(code="gateway_tenant", name="网关租户", status=TenantStatus.ACTIVE)

    def _platform(self, *, name: str, workspace_id: str, access_token: str):
        return DjiCloudPlatform().objects.create(
            tenant=self.tenant,
            name=name,
            base_url=f"https://{workspace_id}.example.test",
            username=f"{workspace_id}-user",
            password="secret",
            login_flag=1,
            workspace_id=workspace_id,
            access_token=access_token,
            is_default=False,
        )

    def test_save_session_for_platform_does_not_delete_or_mutate_other_platforms(self):
        platform_a = self._platform(name="平台 A", workspace_id="workspace-a", access_token="old-a")
        platform_b = self._platform(name="平台 B", workspace_id="workspace-b", access_token="old-b")
        legacy_config = DjiWorkspaceConfig.objects.create(workspace_id="legacy-workspace", access_token="legacy-token")

        saved = DjiGateway(platform=platform_a)._save_session(
            {
                "workspace_id": "workspace-a-new",
                "user_id": "user-a",
                "username": "admin-a",
                "user_type": "tenant_admin",
                "access_token": "new-token-a",
                "mqtt_username": "mqtt-a",
                "mqtt_password": "mqtt-secret-a",
                "mqtt_addr": "mqtt://a.example.test",
            },
            config=platform_a,
        )

        platform_a.refresh_from_db()
        platform_b.refresh_from_db()
        legacy_config.refresh_from_db()

        self.assertEqual(saved.id, platform_a.id)
        self.assertEqual(platform_a.workspace_id, "workspace-a-new")
        self.assertEqual(platform_a.access_token, "new-token-a")
        self.assertEqual(platform_a.mqtt_password, "mqtt-secret-a")
        self.assertEqual(platform_b.workspace_id, "workspace-b")
        self.assertEqual(platform_b.access_token, "old-b")
        self.assertEqual(legacy_config.workspace_id, "legacy-workspace")
        self.assertEqual(DjiCloudPlatform().objects.count(), 2)
        self.assertEqual(DjiWorkspaceConfig.objects.count(), 1)

    def test_gateway_uses_platform_credentials_and_base_url_when_platform_is_bound(self):
        platform = self._platform(name="平台 A", workspace_id="workspace-a", access_token="token-a")
        platform.password = "platform-password"
        platform.login_flag = 7
        platform.save(update_fields=["password", "login_flag", "updated_at"])

        gateway = DjiGateway(platform=platform)

        self.assertEqual(gateway.base_url, "https://workspace-a.example.test")
        self.assertEqual(gateway._configured_credentials(), ("workspace-a-user", "platform-password"))
        self.assertEqual(gateway._configured_login_flag(), 7)
