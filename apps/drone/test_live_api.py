"""Drone live HTTP smoke tests."""

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_live_base import LiveDjiGatewayApiTestCase, User
from apps.access.test_support import ensure_staff_profile, ensure_tenant_role_binding, grant_role_permissions
from apps.dji_bff.models import DjiDeviceIndex
from apps.dji_mock.state import mock_dji_state


class LiveDroneApiTests(LiveDjiGatewayApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="drone_live_admin", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="实时无人机管理员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="drone_live_tenant",
            role_code="drone_live_role",
            role_name="实时无人机角色",
        )
        grant_role_permissions(
            self.role,
            {
                "drone.view_drone": ScopeType.ALL,
                "drone.manage_drone": ScopeType.ALL,
            },
        )
        self.login(username="drone_live_admin", password="pass1234", tenant_code=self.tenant.code)

    def test_claim_available_and_live_should_follow_http_contract(self):
        mock_dji_state.seed_device(device_sn="SN-LIVE-001", name="实时设备1", model="M30")
        mock_dji_state.seed_device(device_sn="SN-LIVE-002", name="实时设备2", model="M3D")
        DjiDeviceIndex.objects.create(device_sn="SN-LIVE-001", last_payload={"name": "实时设备1"})
        DjiDeviceIndex.objects.create(device_sn="SN-LIVE-002", last_payload={"name": "实时设备2"})

        create_response = self.client.post(
            "/api/v1/drones",
            {"code": "DJ-LIVE-001", "device_sn": "SN-LIVE-001"},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        drone_id = create_response.json()["data"]["id"]

        update_response = self.client.put(
            f"/api/v1/drones/{drone_id}",
            {"name": "实时设备1-更新"},
            format="json",
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()["data"]["name"], "实时设备1-更新")
        self.assertEqual(update_response.json()["data"]["code"], "DJ-LIVE-001")

        available_response = self.client.get("/api/v1/drones/available")
        self.assertEqual(available_response.status_code, 200)
        self.assertEqual(available_response.json()["data"]["total"], 1)
        self.assertEqual(available_response.json()["data"]["list"][0]["device_sn"], "SN-LIVE-002")

        capacity_response = self.client.get(f"/api/v1/drones/{drone_id}/live/capacity")
        self.assertEqual(capacity_response.status_code, 200)
        self.assertEqual(capacity_response.json()["data"]["sn"], "SN-LIVE-001")
        self.assertIn("cameras_list", capacity_response.json()["data"])

        start_response = self.client.post(
            f"/api/v1/drones/{drone_id}/live/start",
            {"camera_index": "88-0-0", "video_index": "normal-0"},
            format="json",
        )
        self.assertEqual(start_response.status_code, 200)
        self.assertIn("rtmp_url", start_response.json()["data"])
        self.assertIn("whep_url", start_response.json()["data"])
        self.assertIn("SN-LIVE-001-88-0-0", start_response.json()["data"]["url"])

        removed_response = self.client.post(f"/api/v1/drones/{drone_id}/enable")
        self.assertEqual(removed_response.status_code, 404)
