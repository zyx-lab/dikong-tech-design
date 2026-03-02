from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AuditLog, GroupPermissionScope, ScopeStatus, ScopeType, StaffProfile, StaffType, StaffTypeGroup
from apps.drone.models import Drone, DroneAssignment, DroneAssignmentStatus, DroneStatus

User = get_user_model()


class DroneApiAuthzTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff_type = StaffType.objects.create(code="dispatcher_test", name="任务调度员", status=1)
        self.user = User.objects.create_user(username="drone_user", password="pass1234", status=1)
        StaffProfile.objects.create(
            user=self.user,
            staff_no="D-001",
            name="调度员A",
            employment_status=1,
            staff_type=self.staff_type,
        )

    def _grant_permission(self, permission_code: str, with_scope: bool = True):
        app_label, codename = permission_code.split(".", 1)
        perm = Permission.objects.get(content_type__app_label=app_label, codename=codename)
        group = Group.objects.create(name=f"{permission_code}-group")
        group.permissions.add(perm)
        StaffTypeGroup.objects.create(staff_type=self.staff_type, group=group, status=ScopeStatus.ACTIVE)
        if with_scope:
            GroupPermissionScope.objects.create(
                group=group,
                permission=perm,
                scope_type=ScopeType.ALL,
                status=ScopeStatus.ACTIVE,
            )

    def test_unauthenticated_should_be_rejected(self):
        response = self.client.get("/api/v1/drones")
        self.assertIn(response.status_code, (401, 403))

    def test_authenticated_without_permission_should_be_denied(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/drones")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["detail"], "PERMISSION_DENIED")

    def test_permission_without_scope_should_be_denied(self):
        self._grant_permission("drone.view_drone", with_scope=False)
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/drones")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["detail"], "SCOPE_NOT_CONFIGURED")

    def test_view_permission_with_all_scope_should_allow_list(self):
        self._grant_permission("drone.view_drone", with_scope=True)
        Drone.objects.create(code="DJ-0001", name="无人机1", model="Mavic 3E", serial_no="SN-1", status=DroneStatus.ENABLED)
        Drone.objects.create(code="DJ-0002", name="无人机2", model="Matrice 30", serial_no="SN-2", status=DroneStatus.DISABLED)

        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/drones")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)


class DroneApiWriteTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff_type = StaffType.objects.create(code="ops_admin_test", name="运营管理员", status=1)
        self.user = User.objects.create_user(username="drone_admin", password="pass1234", status=1)
        self.staff = StaffProfile.objects.create(
            user=self.user,
            staff_no="D-100",
            name="运营管理员A",
            employment_status=1,
            staff_type=self.staff_type,
        )
        self.client.force_authenticate(self.user)

    def _grant_permissions(self, permission_codes: list[str]):
        group = Group.objects.create(name="drone-admin-group")
        StaffTypeGroup.objects.create(staff_type=self.staff_type, group=group, status=ScopeStatus.ACTIVE)

        for permission_code in permission_codes:
            app_label, codename = permission_code.split(".", 1)
            perm = Permission.objects.get(content_type__app_label=app_label, codename=codename)
            group.permissions.add(perm)
            GroupPermissionScope.objects.create(
                group=group,
                permission=perm,
                scope_type=ScopeType.ALL,
                status=ScopeStatus.ACTIVE,
            )

    def test_create_should_record_creator_staff_and_audit_log(self):
        self._grant_permissions(["drone.manage_drone"])
        payload = {
            "code": "DJ-0100",
            "name": "新无人机",
            "model": "Mavic 3E",
            "serial_no": "SN-100",
            "org_id": 1001,
        }
        response = self.client.post("/api/v1/drones", payload, format="json")
        self.assertEqual(response.status_code, 201)

        drone = Drone.objects.get(code="DJ-0100")
        self.assertEqual(drone.created_by_staff_id, self.staff.id)
        self.assertTrue(
            AuditLog.objects.filter(
                action="DRONE_CREATE",
                target_type="drone",
                target_id=str(drone.id),
            ).exists()
        )

    def test_patch_should_not_allow_status_field(self):
        self._grant_permissions(["drone.manage_drone"])
        drone = Drone.objects.create(
            code="DJ-0101",
            name="无人机-编辑",
            model="Matrice 30",
            serial_no="SN-101",
            status=DroneStatus.DISABLED,
            created_by_staff_id=self.staff.id,
        )
        response = self.client.patch(
            f"/api/v1/drones/{drone.id}",
            {"status": DroneStatus.ENABLED},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("status", response.data)

    def test_retired_status_is_irreversible_and_idempotent(self):
        self._grant_permissions(["drone.view_drone", "drone.change_drone_status"])
        drone = Drone.objects.create(
            code="DJ-0102",
            name="无人机-状态",
            model="Matrice 30T",
            serial_no="SN-102",
            status=DroneStatus.ENABLED,
            created_by_staff_id=self.staff.id,
        )

        retire_response = self.client.post(f"/api/v1/drones/{drone.id}/retire")
        self.assertEqual(retire_response.status_code, 200)
        drone.refresh_from_db()
        self.assertEqual(drone.status, DroneStatus.RETIRED)

        enable_response = self.client.post(f"/api/v1/drones/{drone.id}/enable")
        self.assertEqual(enable_response.status_code, 400)
        self.assertIn("RETIRED", enable_response.data["detail"])

        retire_again_response = self.client.post(f"/api/v1/drones/{drone.id}/retire")
        self.assertEqual(retire_again_response.status_code, 200)
        drone.refresh_from_db()
        self.assertEqual(drone.status, DroneStatus.RETIRED)

        self.assertEqual(
            AuditLog.objects.filter(action="DRONE_STATUS_CHANGE", target_type="drone", target_id=str(drone.id)).count(),
            2,
        )


class PilotAssignedScopeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.pilot_staff_type = StaffType.objects.create(code="pilot_operator", name="飞手操作员", status=1)
        self.pilot_user = User.objects.create_user(username="pilot_a", password="pass1234", status=1)
        self.pilot_staff = StaffProfile.objects.create(
            user=self.pilot_user,
            staff_no="P-001",
            name="飞手A",
            employment_status=1,
            staff_type=self.pilot_staff_type,
        )

        group = Group.objects.create(name="无人机按分配查看组")
        perm = Permission.objects.get(content_type__app_label="drone", codename="view_drone")
        group.permissions.add(perm)
        StaffTypeGroup.objects.create(staff_type=self.pilot_staff_type, group=group, status=ScopeStatus.ACTIVE)
        GroupPermissionScope.objects.create(
            group=group,
            permission=perm,
            scope_type=ScopeType.ASSIGNED,
            status=ScopeStatus.ACTIVE,
        )

        self.drone_assigned = Drone.objects.create(
            code="DJ-P-01",
            name="飞手可见-1",
            model="Mavic 3E",
            serial_no="P-SN-1",
            status=DroneStatus.ENABLED,
        )
        self.drone_unassigned = Drone.objects.create(
            code="DJ-P-02",
            name="飞手不可见-2",
            model="Matrice 30",
            serial_no="P-SN-2",
            status=DroneStatus.ENABLED,
        )
        DroneAssignment.objects.create(
            drone=self.drone_assigned,
            staff=self.pilot_staff,
            status=DroneAssignmentStatus.ACTIVE,
        )
        self.client.force_authenticate(self.pilot_user)

    def test_pilot_should_only_list_assigned_drones(self):
        response = self.client.get("/api/v1/drones")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], self.drone_assigned.id)

    def test_pilot_retrieve_unassigned_drone_should_be_404(self):
        response = self.client.get(f"/api/v1/drones/{self.drone_unassigned.id}")
        self.assertEqual(response.status_code, 404)


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
        perm = Permission.objects.get(content_type__app_label="drone", codename="manage_drone_assignment")
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


class BusinessSuperAdminApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff_type = StaffType.objects.create(code="business_super_admin", name="业务超级管理员", status=1)
        self.user = User.objects.create_user(username="biz_super_u", password="pass1234", status=1)
        self.staff = StaffProfile.objects.create(
            user=self.user,
            staff_no="BS-900",
            name="业务超级U",
            employment_status=1,
            staff_type=self.staff_type,
        )

        self.pilot_type = StaffType.objects.create(code="pilot_operator", name="飞手操作员", status=1)
        self.pilot_user = User.objects.create_user(username="pilot_for_biz_super", password="pass1234", status=1)
        self.pilot_staff = StaffProfile.objects.create(
            user=self.pilot_user,
            staff_no="P-900",
            name="飞手900",
            employment_status=1,
            staff_type=self.pilot_type,
        )

        group = Group.objects.create(name="业务超级权限组")
        for codename in ("view_drone", "manage_drone", "change_drone_status", "manage_drone_assignment"):
            perm = Permission.objects.get(content_type__app_label="drone", codename=codename)
            group.permissions.add(perm)
            GroupPermissionScope.objects.create(
                group=group,
                permission=perm,
                scope_type=ScopeType.ALL,
                status=ScopeStatus.ACTIVE,
            )
        StaffTypeGroup.objects.create(staff_type=self.staff_type, group=group, status=ScopeStatus.ACTIVE)
        self.client.force_authenticate(self.user)

    def test_business_super_admin_can_access_business_apis(self):
        create_drone_resp = self.client.post(
            "/api/v1/drones",
            {
                "code": "BS-DJ-001",
                "name": "超级业务无人机",
                "model": "Matrice 350",
                "serial_no": "BS-SN-001",
            },
            format="json",
        )
        self.assertEqual(create_drone_resp.status_code, 201)
        drone_id = Drone.objects.get(code="BS-DJ-001").id

        list_resp = self.client.get("/api/v1/drones")
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(list_resp.data["count"], 1)

        assign_resp = self.client.post(
            "/api/v1/drone-assignments",
            {
                "drone": drone_id,
                "staff": self.pilot_staff.id,
            },
            format="json",
        )
        self.assertEqual(assign_resp.status_code, 201)
