from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.drone.models import Drone
from apps.route.models import Route

User = get_user_model()


def DjiCloudPlatform():
    return apps.get_model("dji_bff", "DjiCloudPlatform")


class V2MissionApiTests(TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.user = User.objects.create_user(username="v2_mission_admin", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="V2 任务调度员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="v2_mission_tenant",
            role_code="v2_mission_role",
            role_name="V2 任务角色",
        )
        grant_role_permissions(
            self.role,
            {
                "mission.view_mission": ScopeType.ALL,
                "mission.manage_mission": ScopeType.ALL,
            },
        )
        self.pilot_user = User.objects.create_user(username="v2_mission_pilot", password="pass1234", status=1)
        ensure_staff_profile(self.pilot_user, name="飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, self.pilot_member, _role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")
        self.platform_a = DjiCloudPlatform().objects.create(
            tenant=self.tenant,
            name="平台 A",
            base_url="https://a.example.test",
            username="admin-a",
            password="secret",
        )
        self.platform_b = DjiCloudPlatform().objects.create(
            tenant=self.tenant,
            name="平台 B",
            base_url="https://b.example.test",
            username="admin-b",
            password="secret",
        )
        self.route_a = Route.objects.create(tenant=self.tenant, dji_platform=self.platform_a, name="平台 A 航线")
        self.drone_a = Drone.objects.create(
            tenant=self.tenant,
            dji_platform=self.platform_a,
            code="DRONE-A",
            name="平台 A 无人机",
            model="M30",
            device_sn="SN-A",
        )
        self.drone_b = Drone.objects.create(
            tenant=self.tenant,
            dji_platform=self.platform_b,
            code="DRONE-B",
            name="平台 B 无人机",
            model="M30",
            device_sn="SN-B",
        )
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    def test_create_should_reject_route_and_drone_from_different_dji_platforms(self):
        response = self.client.post(
            "/api/v2/missions",
            {
                "name": "平台不一致任务",
                "route": self.route_a.id,
                "drone": self.drone_b.id,
                "pilot": self.pilot_member.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, getattr(response, "data", response.content))
        self.assertIn("dji_platform", response.data["data"])

    def test_create_should_bind_mission_to_route_and_drone_platform(self):
        response = self.client.post(
            "/api/v2/missions",
            {
                "name": "平台一致任务",
                "route": self.route_a.id,
                "drone": self.drone_a.id,
                "pilot": self.pilot_member.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["dji_platform"], self.platform_a.id)
