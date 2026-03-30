"""Drone assignment live HTTP smoke tests."""

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_live_base import LiveIamApiTestCase, User
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.drone.models import Drone


class LiveDroneAssignmentApiTests(LiveIamApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="assignment_live_dispatcher", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="实时分配调度员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="assignment_live_tenant",
            role_code="assignment_live_role",
            role_name="实时分配角色",
        )
        grant_role_permissions(self.role, {"drone_assignment.manage_drone_assignment": ScopeType.ALL})

        self.pilot_user = User.objects.create_user(username="assignment_live_pilot", password="pass1234", status=1)
        ensure_staff_profile(self.pilot_user, name="飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, self.pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手")

        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="ASSIGN-LIVE-DRONE-001",
            name="实时分配无人机",
            model="M30",
            device_sn="ASSIGN-LIVE-SN-001",
        )

        self.login(username="assignment_live_dispatcher", password="pass1234", tenant_code=self.tenant.code)

    def test_create_and_cancel_should_follow_live_http_contract(self):
        create_response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "tenant_member": self.pilot_member.id},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        assignment_id = create_response.json()["data"]["id"]

        cancel_response = self.client.post(f"/api/v1/drone-assignments/{assignment_id}/cancel")
        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.json()["data"]["status"], "INACTIVE")

        removed_response = self.client.post(f"/api/v1/drone-assignments/{assignment_id}/reactivate")
        self.assertEqual(removed_response.status_code, 404)
