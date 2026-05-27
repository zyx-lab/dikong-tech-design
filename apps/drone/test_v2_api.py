from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from unittest.mock import patch

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_support import ensure_staff_profile, ensure_tenant_role_binding, grant_role_permissions
from apps.dji_bff.models import DjiDeviceIndex
from apps.drone.models import Drone, DroneStatus

User = get_user_model()


def DjiCloudPlatform():
    return apps.get_model("dji_bff", "DjiCloudPlatform")


class FakeV2Gateway:
    devices_by_platform_id = {}
    calls = []

    def __init__(self, *, platform=None, **kwargs):
        self.platform = platform
        self.calls.append(platform.id if platform is not None else None)

    def list_devices(self):
        return list(self.devices_by_platform_id.get(self.platform.id, []))


class V2DroneApiTests(TestCase):
    def setUp(self):
        super().setUp()
        FakeV2Gateway.devices_by_platform_id = {}
        FakeV2Gateway.calls = []
        self.client = APIClient()
        self.user = User.objects.create_user(username="v2_drone_admin", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="V2 无人机管理员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="v2_drone_tenant",
            role_code="v2_drone_role",
            role_name="V2 无人机角色",
        )
        grant_role_permissions(
            self.role,
            {
                "drone.view_drone": ScopeType.ALL,
                "drone.manage_drone": ScopeType.ALL,
            },
        )
        self.platform_a = DjiCloudPlatform().objects.create(
            tenant=self.tenant,
            name="平台 A",
            base_url="https://a.example.test",
            username="admin-a",
            password="secret",
            is_default=True,
        )
        self.platform_b = DjiCloudPlatform().objects.create(
            tenant=self.tenant,
            name="平台 B",
            base_url="https://b.example.test",
            username="admin-b",
            password="secret",
        )
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    @patch("apps.api_v2.views.DjiGateway", FakeV2Gateway)
    def test_available_should_live_fetch_selected_platform_and_exclude_claims_on_that_platform_only(self):
        Drone.objects.create(
            tenant=self.tenant,
            dji_platform=self.platform_a,
            code="CLAIMED-A",
            name="已认领 A",
            model="M30",
            device_sn="SN-CLAIMED",
            status=DroneStatus.CLAIMED,
        )
        Drone.objects.create(
            tenant=self.tenant,
            dji_platform=self.platform_b,
            code="CLAIMED-B",
            name="已认领 B",
            model="M30",
            device_sn="SN-ON-B",
            status=DroneStatus.CLAIMED,
        )
        FakeV2Gateway.devices_by_platform_id = {
            self.platform_a.id: [
                {"device_sn": "SN-CLAIMED", "name": "已认领 A", "model": "M30", "status": True},
                {"device_sn": "SN-AVAILABLE", "name": "可认领 A", "model": "M3D", "status": True},
                {"device_sn": "SN-ON-B", "name": "同 SN 另一平台已认领", "model": "M30", "status": True},
            ],
            self.platform_b.id: [{"device_sn": "SN-ONLY-B", "name": "B", "model": "M30", "status": True}],
        }

        response = self.client.get(f"/api/v2/drones/available?platform_id={self.platform_a.id}")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(FakeV2Gateway.calls, [self.platform_a.id])
        returned_sns = {item["device_sn"] for item in response.data["data"]["list"]}
        self.assertEqual(returned_sns, {"SN-AVAILABLE", "SN-ON-B"})
        self.assertTrue(DjiDeviceIndex.objects.filter(dji_platform=self.platform_a, device_sn="SN-AVAILABLE").exists())
        self.assertFalse(DjiDeviceIndex.objects.filter(dji_platform=self.platform_b, device_sn="SN-ONLY-B").exists())

    def test_claim_should_allow_same_device_sn_on_different_platform_but_reject_duplicate_same_platform(self):
        DjiDeviceIndex.objects.create(
            dji_platform=self.platform_a,
            device_sn="SAME-SN",
            last_payload={"name": "平台 A 设备", "model": "M30"},
        )
        DjiDeviceIndex.objects.create(
            dji_platform=self.platform_b,
            device_sn="SAME-SN",
            last_payload={"name": "平台 B 设备", "model": "M30"},
        )
        Drone.objects.create(
            tenant=self.tenant,
            dji_platform=self.platform_a,
            code="DRONE-A",
            name="平台 A 设备",
            model="M30",
            device_sn="SAME-SN",
        )

        allowed_response = self.client.post(
            "/api/v2/drones",
            {"platform_id": self.platform_b.id, "code": "DRONE-B", "device_sn": "SAME-SN"},
            format="json",
        )
        duplicate_response = self.client.post(
            "/api/v2/drones",
            {"platform_id": self.platform_a.id, "code": "DRONE-A-DUP", "device_sn": "SAME-SN"},
            format="json",
        )

        self.assertEqual(allowed_response.status_code, 201, getattr(allowed_response, "data", allowed_response.content))
        self.assertEqual(allowed_response.data["data"]["dji_platform"], self.platform_b.id)
        self.assertEqual(
            duplicate_response.status_code,
            409,
            getattr(duplicate_response, "data", duplicate_response.content),
        )
        self.assertEqual(Drone.objects.filter(tenant=self.tenant, device_sn="SAME-SN").count(), 2)

    @patch("apps.api_v2.views.DjiGateway", FakeV2Gateway)
    def test_list_should_overlay_online_state_from_involved_platforms_on_request(self):
        drone_a = Drone.objects.create(
            tenant=self.tenant,
            dji_platform=self.platform_a,
            code="DRONE-A",
            name="A",
            model="M30",
            device_sn="SN-A",
            dji_online=True,
        )
        drone_b = Drone.objects.create(
            tenant=self.tenant,
            dji_platform=self.platform_b,
            code="DRONE-B",
            name="B",
            model="M30",
            device_sn="SN-B",
            dji_online=False,
        )
        FakeV2Gateway.devices_by_platform_id = {
            self.platform_a.id: [{"device_sn": "SN-A", "status": False}],
            self.platform_b.id: [{"device_sn": "SN-B", "status": True}],
        }

        response = self.client.get("/api/v2/drones")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        rows = {item["device_sn"]: item for item in response.data["data"]["list"]}
        self.assertFalse(rows["SN-A"]["dji_online"])
        self.assertTrue(rows["SN-B"]["dji_online"])
        drone_a.refresh_from_db()
        drone_b.refresh_from_db()
        self.assertFalse(drone_a.dji_online)
        self.assertTrue(drone_b.dji_online)
        self.assertEqual(set(FakeV2Gateway.calls), {self.platform_a.id, self.platform_b.id})
