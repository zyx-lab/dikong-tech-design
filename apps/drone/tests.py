from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import EmploymentStatus, ScopeType, Tenant, TenantStatus
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.dji_bff.models import DjiDeviceIndex
from apps.dji_mock.state import mock_dji_state
from apps.dji_mock.test_support import MockDjiUpstreamTestMixin
from apps.drone.models import Drone
from apps.drone_assignment.models import DroneAssignment, DroneAssignmentStatus

User = get_user_model()


class DroneApiTests(MockDjiUpstreamTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.user = User.objects.create_user(username="drone_admin", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="无人机管理员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="drone_test_tenant",
            role_code="drone_test_role",
            role_name="无人机测试角色",
        )
        grant_role_permissions(
            self.role,
            {
                "drone.view_drone": ScopeType.ALL,
                "drone.manage_drone": ScopeType.ALL,
            },
        )
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    def test_available_should_only_return_unclaimed_devices(self):
        DjiDeviceIndex.objects.create(device_sn="SN-001", last_payload={"name": "共享设备1"})
        DjiDeviceIndex.objects.create(device_sn="SN-002", last_payload={"name": "共享设备2"})
        Drone.objects.create(
            tenant=self.tenant,
            code="DJ-001",
            name="已认领设备",
            model="M30",
            device_sn="SN-001",
            created_by_tenant_member_id=self.member.id,
        )

        response = self.client.get("/api/v1/drones/available")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["total"], 1)
        self.assertEqual(response.data["data"]["list"][0]["device_sn"], "SN-002")

    def test_claim_should_fill_name_and_model_from_device_index(self):
        DjiDeviceIndex.objects.create(
            device_sn="SN-CLAIM-001",
            last_payload={"name": "共享巡检机", "model": "Matrice 30"},
        )

        response = self.client.post(
            "/api/v1/drones",
            {"code": "DJ-CLAIM-001", "device_sn": "SN-CLAIM-001"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        drone = Drone.objects.get(device_sn="SN-CLAIM-001")
        self.assertEqual(drone.name, "共享巡检机")
        self.assertEqual(drone.model, "Matrice 30")
        self.assertEqual(drone.created_by_tenant_member_id, self.member.id)

    def test_claim_should_reject_device_claimed_by_other_tenant(self):
        other_tenant = Tenant.objects.create(code="drone_other_tenant", name="其他租户", status=TenantStatus.ACTIVE)
        DjiDeviceIndex.objects.create(device_sn="SN-CONFLICT-001", last_payload={})
        Drone.objects.create(
            tenant=other_tenant,
            code="DJ-OTHER-001",
            name="其他租户已认领设备",
            model="M30",
            device_sn="SN-CONFLICT-001",
        )

        response = self.client.post(
            "/api/v1/drones",
            {"code": "DJ-CLAIM-002", "device_sn": "SN-CONFLICT-001"},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "C0101")
        self.assertIn("device_sn", response.data["data"])

    def test_live_start_should_proxy_with_composed_video_id(self):
        mock_dji_state.seed_device(device_sn="SN-LIVE-001", name="直播设备", model="M30")
        DjiDeviceIndex.objects.create(device_sn="SN-LIVE-001", last_payload={})
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-LIVE-001",
            name="直播设备",
            model="M30",
            device_sn="SN-LIVE-001",
        )

        response = self.client.post(
            f"/api/v1/drones/{drone.id}/live/start",
            {"camera_index": "88-0-0", "video_index": "normal-0"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["data"]["accepted"])
        self.assertEqual(
            response.data["data"]["payload"]["video_id"],
            "SN-LIVE-001/88-0-0/normal-0",
        )

    def test_assigned_scope_member_should_only_see_assigned_drones(self):
        pilot_user = User.objects.create_user(username="drone_pilot", password="pass1234", status=1)
        ensure_staff_profile(pilot_user, name="飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, pilot_member, pilot_role = ensure_tenant_role_binding(
            pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(pilot_member, code="pilot_operator", name="飞手")
        grant_role_permissions(pilot_role, {"drone.view_drone": ScopeType.ASSIGNED})

        visible_drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-SCOPE-001",
            name="可见设备",
            model="M30",
            device_sn="SN-SCOPE-001",
        )
        hidden_drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-SCOPE-002",
            name="不可见设备",
            model="M30",
            device_sn="SN-SCOPE-002",
        )
        DroneAssignment.objects.create(
            tenant=self.tenant,
            drone=visible_drone,
            tenant_member=pilot_member,
            status=DroneAssignmentStatus.ACTIVE,
        )

        pilot_client = APIClient()
        pilot_client.force_authenticate(pilot_user)
        pilot_client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

        response = pilot_client.get("/api/v1/drones")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["total"], 1)
        self.assertEqual(response.data["data"]["list"][0]["id"], visible_drone.id)
        self.assertNotEqual(response.data["data"]["list"][0]["id"], hidden_drone.id)
