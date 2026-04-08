"""Mission live HTTP smoke tests."""

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_live_base import LiveDjiGatewayApiTestCase, User
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.dji_bff.models import TenantRouteIndex
from apps.drone.models import Drone
from apps.route.models import Route


class LiveMissionApiTests(LiveDjiGatewayApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="mission_live_dispatcher", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="实时任务调度员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="mission_live_tenant",
            role_code="mission_live_role",
            role_name="实时任务角色",
        )
        grant_role_permissions(
            self.role,
            {
                "mission.view_mission": ScopeType.ALL,
                "mission.manage_mission": ScopeType.ALL,
            },
        )

        self.pilot_user = User.objects.create_user(username="mission_live_pilot", password="pass1234", status=1)
        ensure_staff_profile(self.pilot_user, name="飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, self.pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")

        self.route = Route.objects.create(tenant=self.tenant, name="实时任务航线")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=self.route,
            dji_wayline_id="mission-wayline",
            is_published=True,
        )
        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="MISSION-LIVE-DRONE-001",
            name="实时任务无人机",
            model="M30",
            device_sn="MISSION-LIVE-SN-001",
        )

        self.login(username="mission_live_dispatcher", password="pass1234", tenant_code=self.tenant.code)

    def test_create_and_delete_should_follow_live_http_contract(self):
        create_response = self.client.post(
            "/api/v1/missions",
            {
                "name": "实时巡检任务",
                "route": self.route.id,
                "drone": self.drone.id,
                "pilot": self.pilot_member.id,
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        mission_id = create_response.json()["data"]["id"]
        self.assertEqual(create_response.json()["data"]["status"], 1)

        cancel_response = self.client.post(f"/api/v1/missions/{mission_id}/cancel")
        self.assertEqual(cancel_response.status_code, 404)

        delete_response = self.client.delete(f"/api/v1/missions/{mission_id}")
        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.json()["data"]["deleted"])
