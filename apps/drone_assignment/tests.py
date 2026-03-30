from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.drone.models import Drone
from apps.drone_assignment.models import DroneAssignment, DroneAssignmentStatus

User = get_user_model()


class DroneAssignmentApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="assignment_dispatcher", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="分配调度员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="assignment_test_tenant",
            role_code="assignment_test_role",
            role_name="分配测试角色",
        )
        grant_role_permissions(self.role, {"drone_assignment.manage_drone_assignment": ScopeType.ALL})
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

        self.pilot_user = User.objects.create_user(username="assignment_pilot", password="pass1234", status=1)
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
            code="ASSIGN-DRONE-001",
            name="分配无人机",
            model="M30",
            device_sn="ASSIGN-SN-001",
        )

    def test_create_should_succeed(self):
        response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "tenant_member": self.pilot_member.id},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        assignment = DroneAssignment.objects.get(drone=self.drone, tenant_member=self.pilot_member)
        self.assertEqual(assignment.created_by_tenant_member_id, self.member.id)
        self.assertEqual(assignment.status, DroneAssignmentStatus.ACTIVE)

    def test_duplicate_active_assignment_should_return_duplicate_code(self):
        DroneAssignment.objects.create(
            tenant=self.tenant,
            drone=self.drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.ACTIVE,
            created_by_tenant_member_id=self.member.id,
        )

        response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "tenant_member": self.pilot_member.id},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "C0101")

    def test_cancel_should_mark_assignment_inactive(self):
        assignment = DroneAssignment.objects.create(
            tenant=self.tenant,
            drone=self.drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.ACTIVE,
            created_by_tenant_member_id=self.member.id,
        )

        response = self.client.post(f"/api/v1/drone-assignments/{assignment.id}/cancel")

        self.assertEqual(response.status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, DroneAssignmentStatus.INACTIVE)
        self.assertIsNotNone(assignment.end_at)

    def test_reactivate_endpoint_should_be_removed(self):
        assignment = DroneAssignment.objects.create(
            tenant=self.tenant,
            drone=self.drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.INACTIVE,
            created_by_tenant_member_id=self.member.id,
            end_at=timezone.now(),
        )

        response = self.client.post(f"/api/v1/drone-assignments/{assignment.id}/reactivate")

        self.assertEqual(response.status_code, 404)
