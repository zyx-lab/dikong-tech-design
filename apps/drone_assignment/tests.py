from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AuditLog, GroupPermissionScope, ScopeStatus, ScopeType, StaffProfile, StaffType, StaffTypeGroup
from apps.drone.models import Drone, DroneStatus
from apps.drone_assignment.models import DroneAssignment, DroneAssignmentStatus

User = get_user_model()


class DroneAssignmentApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.dispatcher_type = StaffType.objects.create(code="dispatcher", name="任务调度员", status=1)
        self.dispatcher_user = User.objects.create_user(username="dispatcher_a", password="pass1234", status=1)
        self.dispatcher_staff = StaffProfile.objects.create(
            user=self.dispatcher_user,
            staff_no="D-200",
            name="调度员A",
            employment_status=1,
            staff_type=self.dispatcher_type,
        )

        self.pilot_type = StaffType.objects.create(code="pilot_operator", name="飞手操作员", status=1)
        self.pilot_user = User.objects.create_user(username="pilot_b", password="pass1234", status=1)
        self.pilot_staff = StaffProfile.objects.create(
            user=self.pilot_user,
            staff_no="P-200",
            name="飞手B",
            employment_status=1,
            staff_type=self.pilot_type,
        )

        self.route_type = StaffType.objects.create(code="route_planner", name="航线规划员", status=1)
        self.route_user = User.objects.create_user(username="planner_a", password="pass1234", status=1)
        self.route_staff = StaffProfile.objects.create(
            user=self.route_user,
            staff_no="R-200",
            name="规划员A",
            employment_status=1,
            staff_type=self.route_type,
        )

        self.drone = Drone.objects.create(
            code="DJ-A-01",
            name="调度分配测试机",
            model="Matrice 300",
            serial_no="A-SN-1",
            status=DroneStatus.ENABLED,
        )

        group = Group.objects.create(name="无人机分配管理组")
        perm = Permission.objects.get(content_type__app_label="drone_assignment", codename="manage_drone_assignment")
        group.permissions.add(perm)
        StaffTypeGroup.objects.create(staff_type=self.dispatcher_type, group=group, status=ScopeStatus.ACTIVE)
        GroupPermissionScope.objects.create(
            group=group,
            permission=perm,
            scope_type=ScopeType.ALL,
            status=ScopeStatus.ACTIVE,
        )

        self.client.force_authenticate(self.dispatcher_user)

    def test_create_and_cancel_assignment_should_write_audit_logs(self):
        create_response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "staff": self.pilot_staff.id},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        assignment_id = create_response.data["id"]

        assignment = DroneAssignment.objects.get(id=assignment_id)
        self.assertEqual(assignment.status, DroneAssignmentStatus.ACTIVE)

        cancel_response = self.client.post(f"/api/v1/drone-assignments/{assignment_id}/cancel")
        self.assertEqual(cancel_response.status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, DroneAssignmentStatus.INACTIVE)
        self.assertIsNotNone(assignment.end_at)

        self.assertTrue(
            AuditLog.objects.filter(
                action="DRONE_ASSIGNMENT_CREATE",
                target_type="drone_assignment",
                target_id=str(assignment_id),
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="DRONE_ASSIGNMENT_CANCEL",
                target_type="drone_assignment",
                target_id=str(assignment_id),
            ).exists()
        )

    def test_assignment_should_reject_non_pilot_staff(self):
        response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "staff": self.route_staff.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("staff", response.data)

    def test_reactivate_assignment_should_restore_active(self):
        create_response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "staff": self.pilot_staff.id},
            format="json",
        )
        assignment_id = create_response.data["id"]

        cancel_response = self.client.post(f"/api/v1/drone-assignments/{assignment_id}/cancel")
        self.assertEqual(cancel_response.status_code, 200)

        reactivate_response = self.client.post(f"/api/v1/drone-assignments/{assignment_id}/reactivate")
        self.assertEqual(reactivate_response.status_code, 200)
        self.assertEqual(reactivate_response.data["business_code"], "SUCCESS")
        self.assertEqual(reactivate_response.data["business_detail_code"], "OK")

        assignment = DroneAssignment.objects.get(id=assignment_id)
        self.assertEqual(assignment.status, DroneAssignmentStatus.ACTIVE)
        self.assertIsNone(assignment.end_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action="DRONE_ASSIGNMENT_REACTIVATE",
                target_type="drone_assignment",
                target_id=str(assignment_id),
            ).exists()
        )

    def test_reactivate_assignment_with_body_should_return_invalid_params(self):
        create_response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "staff": self.pilot_staff.id},
            format="json",
        )
        assignment_id = create_response.data["id"]

        response = self.client.post(
            f"/api/v1/drone-assignments/{assignment_id}/reactivate",
            {"unexpected": True},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")
