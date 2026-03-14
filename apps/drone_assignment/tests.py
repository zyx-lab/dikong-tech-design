from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import (
    AuditLog,
    EmploymentStatus,
    Permission,
    ScopeType,
    StaffProfile,
)
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.drone.models import Drone, DroneStatus
from apps.drone_assignment.models import DroneAssignment, DroneAssignmentStatus

User = get_user_model()


class DroneAssignmentApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.dispatcher_user = User.objects.create_user(username="dispatcher_a", password="pass1234", status=1)
        self.dispatcher_staff = ensure_staff_profile(
            self.dispatcher_user,
            staff_no="D-200",
            name="调度员A",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.dispatcher_user,
            tenant_code="drone_assignment_test_tenant",
            role_code="drone_assignment_test_role",
            role_name="无人机分配测试角色",
        )
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

        self.pilot_user = User.objects.create_user(username="pilot_b", password="pass1234", status=1)
        self.pilot_staff = ensure_staff_profile(
            self.pilot_user,
            staff_no="P-200",
            name="飞手B",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _pilot_tenant, pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手操作员",
        )
        ensure_tenant_member_position(pilot_member, code="pilot_operator", name="飞手操作员")

        self.inactive_pilot_user = User.objects.create_user(username="pilot_inactive", password="pass1234", status=1)
        self.inactive_pilot_staff = ensure_staff_profile(
            self.inactive_pilot_user,
            staff_no="P-201",
            name="离职飞手",
            employment_status=EmploymentStatus.INACTIVE,
        )
        _inactive_tenant, inactive_member, _inactive_role = ensure_tenant_role_binding(
            self.inactive_pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手操作员",
        )
        ensure_tenant_member_position(inactive_member, code="pilot_operator", name="飞手操作员")

        self.observer_user = User.objects.create_user(username="planner_a", password="pass1234", status=1)
        self.observer_staff = ensure_staff_profile(
            self.observer_user,
            staff_no="R-200",
            name="规划员A",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _observer_tenant, observer_member, _observer_role = ensure_tenant_role_binding(
            self.observer_user,
            tenant=self.tenant,
            role_code="route_planner",
            role_name="航线规划员",
        )
        ensure_tenant_member_position(observer_member, code="route_planner", name="航线规划员")

        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-A-01",
            name="调度分配测试机",
            model="Matrice 300",
            serial_no="A-SN-1",
            status=DroneStatus.ENABLED,
        )
        self.secondary_drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-A-02",
            name="调度分配测试机2",
            model="Matrice 30",
            serial_no="A-SN-2",
            status=DroneStatus.ENABLED,
        )
        self.retired_drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-A-03",
            name="已退役无人机",
            model="Matrice 4",
            serial_no="A-SN-3",
            status=DroneStatus.RETIRED,
        )

    def _grant_manage_permission(self, *, with_scope: bool = True):
        if with_scope:
            grant_role_permissions(
                self.role,
                {"drone_assignment.manage_drone_assignment": ScopeType.ALL},
                group_name="无人机分配管理组",
            )
            return

        Permission.objects.update_or_create(
            code="drone_assignment.manage_drone_assignment",
            defaults={
                "name": "drone_assignment.manage_drone_assignment",
                "module": "drone_assignment",
                "status": 1,
            },
        )

    def _authenticate_dispatcher(self):
        self.client.force_authenticate(self.dispatcher_user)

    def _create_assignment(
        self,
        *,
        drone: Drone | None = None,
        staff: StaffProfile | None = None,
        status: str = DroneAssignmentStatus.ACTIVE,
    ) -> DroneAssignment:
        return DroneAssignment.objects.create(
            drone=drone or self.drone,
            staff=staff or self.pilot_staff,
            status=status,
            created_by_staff_id=self.dispatcher_staff.id,
        )

    def test_list_assignments_should_return_success(self):
        self._grant_manage_permission()
        self._authenticate_dispatcher()
        self._create_assignment(drone=self.drone, status=DroneAssignmentStatus.ACTIVE)
        self._create_assignment(drone=self.secondary_drone, status=DroneAssignmentStatus.INACTIVE)

        response = self.client.get("/api/v1/drone-assignments")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(len(response.data["results"]), 2)

    def test_list_assignments_with_filters_should_return_filtered_results(self):
        self._grant_manage_permission()
        self._authenticate_dispatcher()
        self._create_assignment(drone=self.drone, status=DroneAssignmentStatus.ACTIVE)
        self._create_assignment(drone=self.secondary_drone, status=DroneAssignmentStatus.INACTIVE)

        response = self.client.get(
            "/api/v1/drone-assignments",
            {"drone_id": self.secondary_drone.id, "status": DroneAssignmentStatus.INACTIVE},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["drone"], self.secondary_drone.id)
        self.assertEqual(response.data["results"][0]["status"], DroneAssignmentStatus.INACTIVE)

    def test_list_assignments_without_auth_should_return_permission_denied(self):
        response = self.client.get("/api/v1/drone-assignments")

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_list_assignments_without_permission_should_return_permission_denied(self):
        self._authenticate_dispatcher()

        response = self.client.get("/api/v1/drone-assignments")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_list_assignments_without_scope_should_return_permission_denied(self):
        self._grant_manage_permission(with_scope=False)
        self._authenticate_dispatcher()

        response = self.client.get("/api/v1/drone-assignments")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["detail"], "PERMISSION_DENIED")

    def test_retrieve_assignment_should_return_success(self):
        self._grant_manage_permission()
        self._authenticate_dispatcher()
        assignment = self._create_assignment(status=DroneAssignmentStatus.ACTIVE)

        response = self.client.get(f"/api/v1/drone-assignments/{assignment.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["id"], assignment.id)
        self.assertEqual(response.data["drone"], self.drone.id)
        self.assertEqual(response.data["staff"], self.pilot_staff.id)

    def test_retrieve_assignment_not_found_should_return_resource_not_found(self):
        self._grant_manage_permission()
        self._authenticate_dispatcher()

        response = self.client.get("/api/v1/drone-assignments/999999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_retrieve_assignment_without_auth_should_return_permission_denied(self):
        assignment = self._create_assignment(status=DroneAssignmentStatus.ACTIVE)

        response = self.client.get(f"/api/v1/drone-assignments/{assignment.id}")

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_retrieve_assignment_without_permission_should_return_permission_denied(self):
        assignment = self._create_assignment(status=DroneAssignmentStatus.ACTIVE)
        self._authenticate_dispatcher()

        response = self.client.get(f"/api/v1/drone-assignments/{assignment.id}")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_create_assignment_should_return_success_and_write_audit_log(self):
        self._grant_manage_permission()
        self._authenticate_dispatcher()

        response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "staff": self.pilot_staff.id},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        assignment = DroneAssignment.objects.get(id=response.data["id"])
        self.assertEqual(assignment.status, DroneAssignmentStatus.ACTIVE)
        self.assertIsNone(assignment.end_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action="DRONE_ASSIGNMENT_CREATE",
                target_type="drone_assignment",
                target_id=str(assignment.id),
            ).exists()
        )

    def test_create_assignment_with_retired_drone_should_return_invalid_params(self):
        self._grant_manage_permission()
        self._authenticate_dispatcher()

        response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.retired_drone.id, "staff": self.pilot_staff.id},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("drone", response.data)

    def test_create_assignment_with_inactive_staff_should_return_invalid_params(self):
        self._grant_manage_permission()
        self._authenticate_dispatcher()

        response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "staff": self.inactive_pilot_staff.id},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("staff", response.data)

    def test_create_assignment_with_non_pilot_staff_should_return_invalid_params(self):
        self._grant_manage_permission()
        self._authenticate_dispatcher()

        response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "staff": self.observer_staff.id},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("staff", response.data)

    def test_create_assignment_with_nonexistent_drone_should_return_invalid_params(self):
        """测试 drone 不存在"""
        self._grant_manage_permission()
        self._authenticate_dispatcher()

        response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": 99999, "staff": self.pilot_staff.id},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")

    def test_create_assignment_with_nonexistent_staff_should_return_invalid_params(self):
        """测试 staff 不存在"""
        self._grant_manage_permission()
        self._authenticate_dispatcher()

        response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "staff": 99999},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")

    def test_create_assignment_with_duplicate_active_pair_should_return_duplicate_request(self):
        self._grant_manage_permission()
        self._authenticate_dispatcher()
        self._create_assignment(status=DroneAssignmentStatus.ACTIVE)

        response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "staff": self.pilot_staff.id},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(response.data["business_detail_code"], "DUPLICATE_REQUEST")
        self.assertIn("non_field_errors", response.data)

    def test_create_assignment_without_auth_should_return_permission_denied(self):
        response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "staff": self.pilot_staff.id},
            format="json",
        )

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_create_assignment_without_permission_should_return_permission_denied(self):
        self._authenticate_dispatcher()

        response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "staff": self.pilot_staff.id},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_cancel_assignment_should_return_success_and_write_audit_log(self):
        self._grant_manage_permission()
        self._authenticate_dispatcher()
        assignment = self._create_assignment(status=DroneAssignmentStatus.ACTIVE)

        response = self.client.post(f"/api/v1/drone-assignments/{assignment.id}/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, DroneAssignmentStatus.INACTIVE)
        self.assertIsNotNone(assignment.end_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action="DRONE_ASSIGNMENT_CANCEL",
                target_type="drone_assignment",
                target_id=str(assignment.id),
            ).exists()
        )

    def test_cancel_inactive_assignment_should_be_idempotent_success(self):
        self._grant_manage_permission()
        self._authenticate_dispatcher()
        assignment = self._create_assignment(status=DroneAssignmentStatus.INACTIVE)

        response = self.client.post(f"/api/v1/drone-assignments/{assignment.id}/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, DroneAssignmentStatus.INACTIVE)
        self.assertTrue(
            AuditLog.objects.filter(
                action="DRONE_ASSIGNMENT_CANCEL",
                target_type="drone_assignment",
                target_id=str(assignment.id),
            ).exists()
        )

    def test_cancel_assignment_not_found_should_return_resource_not_found(self):
        self._grant_manage_permission()
        self._authenticate_dispatcher()

        response = self.client.post("/api/v1/drone-assignments/999999/cancel")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_cancel_assignment_without_auth_should_return_permission_denied(self):
        assignment = self._create_assignment(status=DroneAssignmentStatus.ACTIVE)

        response = self.client.post(f"/api/v1/drone-assignments/{assignment.id}/cancel")

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_cancel_assignment_without_permission_should_return_permission_denied(self):
        assignment = self._create_assignment(status=DroneAssignmentStatus.ACTIVE)
        self._authenticate_dispatcher()

        response = self.client.post(f"/api/v1/drone-assignments/{assignment.id}/cancel")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})

    def test_reactivate_assignment_should_restore_active_and_write_audit_log(self):
        self._grant_manage_permission()
        self._authenticate_dispatcher()
        assignment = self._create_assignment(status=DroneAssignmentStatus.INACTIVE)

        response = self.client.post(f"/api/v1/drone-assignments/{assignment.id}/reactivate")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, DroneAssignmentStatus.ACTIVE)
        self.assertIsNone(assignment.end_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action="DRONE_ASSIGNMENT_REACTIVATE",
                target_type="drone_assignment",
                target_id=str(assignment.id),
            ).exists()
        )

    def test_reactivate_assignment_with_body_should_return_invalid_params(self):
        self._grant_manage_permission()
        self._authenticate_dispatcher()
        assignment = self._create_assignment(status=DroneAssignmentStatus.INACTIVE)

        response = self.client.post(
            f"/api/v1/drone-assignments/{assignment.id}/reactivate",
            {"unexpected": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
        self.assertIn("errors", response.data)

    def test_reactivate_active_assignment_should_be_idempotent_success(self):
        self._grant_manage_permission()
        self._authenticate_dispatcher()
        assignment = self._create_assignment(status=DroneAssignmentStatus.ACTIVE)

        response = self.client.post(f"/api/v1/drone-assignments/{assignment.id}/reactivate")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, DroneAssignmentStatus.ACTIVE)
        self.assertTrue(
            AuditLog.objects.filter(
                action="DRONE_ASSIGNMENT_REACTIVATE",
                target_type="drone_assignment",
                target_id=str(assignment.id),
            ).exists()
        )

    def test_reactivate_assignment_with_existing_active_pair_should_return_state_conflict(self):
        self._grant_manage_permission()
        self._authenticate_dispatcher()
        self._create_assignment(status=DroneAssignmentStatus.ACTIVE)
        assignment = self._create_assignment(status=DroneAssignmentStatus.INACTIVE)

        response = self.client.post(f"/api/v1/drone-assignments/{assignment.id}/reactivate")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "STATE_CONFLICT")
        self.assertEqual(response.data["business_detail_code"], "STATE_CONFLICT")
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, DroneAssignmentStatus.INACTIVE)

    def test_reactivate_assignment_not_found_should_return_resource_not_found(self):
        self._grant_manage_permission()
        self._authenticate_dispatcher()

        response = self.client.post("/api/v1/drone-assignments/999999/reactivate")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")

    def test_reactivate_assignment_without_auth_should_return_permission_denied(self):
        assignment = self._create_assignment(status=DroneAssignmentStatus.INACTIVE)

        response = self.client.post(f"/api/v1/drone-assignments/{assignment.id}/reactivate")

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_reactivate_assignment_without_permission_should_return_permission_denied(self):
        assignment = self._create_assignment(status=DroneAssignmentStatus.INACTIVE)
        self._authenticate_dispatcher()

        response = self.client.post(f"/api/v1/drone-assignments/{assignment.id}/reactivate")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"FORBIDDEN", "PERMISSION_DENIED"})
