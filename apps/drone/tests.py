from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import AuditLog, GroupPermissionScope, ScopeStatus, ScopeType, StaffProfile, StaffType, StaffTypeGroup
from apps.drone.models import Drone, DroneStatus
from apps.drone_assignment.models import DroneAssignment, DroneAssignmentStatus

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


class SuperuserRootPermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.superuser = User.objects.create_superuser(username="root_business", password="pass1234")
        self.client.force_authenticate(self.superuser)

    def test_superuser_should_access_business_list_without_staff_or_matrix(self):
        Drone.objects.create(code="DJ-ROOT-01", name="Root 机型", model="Matrice 4", serial_no="ROOT-SN-01")
        response = self.client.get("/api/v1/drones")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)

    def test_superuser_should_create_drone_without_staff_binding(self):
        payload = {
            "code": "DJ-ROOT-02",
            "name": "Root 新建",
            "model": "Matrice 4T",
            "serial_no": "ROOT-SN-02",
        }
        response = self.client.post("/api/v1/drones", payload, format="json")
        self.assertEqual(response.status_code, 201)
        drone = Drone.objects.get(code="DJ-ROOT-02")
        self.assertIsNone(drone.created_by_staff_id)


class DroneApiWriteTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff_type = StaffType.objects.create(code="ops_admin_test", name="Admin管理员", status=1)
        self.user = User.objects.create_user(username="drone_admin", password="pass1234", status=1)
        self.staff = StaffProfile.objects.create(
            user=self.user,
            staff_no="D-100",
            name="Admin管理员A",
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

    def _create_drone(self, *, code: str, status: str) -> Drone:
        return Drone.objects.create(
            code=code,
            name=f"{code}-name",
            model="Matrice 30",
            serial_no=f"{code}-sn",
            status=status,
            created_by_staff_id=self.staff.id,
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

    def test_put_should_not_be_exposed(self):
        self._grant_permissions(["drone.manage_drone"])
        drone = Drone.objects.create(
            code="DJ-0101-PUT",
            name="无人机-PUT",
            model="Matrice 30",
            serial_no="SN-101-PUT",
            status=DroneStatus.DISABLED,
            created_by_staff_id=self.staff.id,
        )

        response = self.client.put(
            f"/api/v1/drones/{drone.id}",
            {
                "code": "DJ-0101-PUT",
                "name": "无人机-PUT-更新",
                "model": "Matrice 30",
                "serial_no": "SN-101-PUT",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.data["business_detail_code"], "METHOD_NOT_ALLOWED")
        drone.refresh_from_db()
        self.assertEqual(drone.name, "无人机-PUT")

    def test_enable_should_transition_disabled_drone_and_write_audit_log(self):
        self._grant_permissions(["drone.change_drone_status"])
        drone = self._create_drone(code="DJ-ENABLE-01", status=DroneStatus.DISABLED)

        response = self.client.post(f"/api/v1/drones/{drone.id}/enable")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], DroneStatus.ENABLED)
        drone.refresh_from_db()
        self.assertEqual(drone.status, DroneStatus.ENABLED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="DRONE_STATUS_CHANGE",
                target_type="drone",
                target_id=str(drone.id),
            ).exists()
        )

    def test_enable_enabled_drone_should_be_idempotent_success(self):
        self._grant_permissions(["drone.change_drone_status"])
        drone = self._create_drone(code="DJ-ENABLE-02", status=DroneStatus.ENABLED)

        response = self.client.post(f"/api/v1/drones/{drone.id}/enable")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        drone.refresh_from_db()
        self.assertEqual(drone.status, DroneStatus.ENABLED)

    def test_disable_should_transition_enabled_drone_and_write_audit_log(self):
        self._grant_permissions(["drone.change_drone_status"])
        drone = self._create_drone(code="DJ-DISABLE-01", status=DroneStatus.ENABLED)

        response = self.client.post(f"/api/v1/drones/{drone.id}/disable")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], DroneStatus.DISABLED)
        drone.refresh_from_db()
        self.assertEqual(drone.status, DroneStatus.DISABLED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="DRONE_STATUS_CHANGE",
                target_type="drone",
                target_id=str(drone.id),
            ).exists()
        )

    def test_disable_disabled_drone_should_be_idempotent_success(self):
        self._grant_permissions(["drone.change_drone_status"])
        drone = self._create_drone(code="DJ-DISABLE-02", status=DroneStatus.DISABLED)

        response = self.client.post(f"/api/v1/drones/{drone.id}/disable")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        drone.refresh_from_db()
        self.assertEqual(drone.status, DroneStatus.DISABLED)

    def test_maintenance_should_transition_enabled_drone_and_write_audit_log(self):
        self._grant_permissions(["drone.change_drone_status"])
        drone = self._create_drone(code="DJ-MAINT-01", status=DroneStatus.ENABLED)

        response = self.client.post(f"/api/v1/drones/{drone.id}/maintenance")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], DroneStatus.MAINTENANCE)
        drone.refresh_from_db()
        self.assertEqual(drone.status, DroneStatus.MAINTENANCE)
        self.assertTrue(
            AuditLog.objects.filter(
                action="DRONE_STATUS_CHANGE",
                target_type="drone",
                target_id=str(drone.id),
            ).exists()
        )

    def test_maintenance_maintenance_drone_should_be_idempotent_success(self):
        self._grant_permissions(["drone.change_drone_status"])
        drone = self._create_drone(code="DJ-MAINT-02", status=DroneStatus.MAINTENANCE)

        response = self.client.post(f"/api/v1/drones/{drone.id}/maintenance")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        drone.refresh_from_db()
        self.assertEqual(drone.status, DroneStatus.MAINTENANCE)

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
        self.assertEqual(enable_response.status_code, 409)
        self.assertEqual(enable_response.data["business_code"], "STATE_CONFLICT")
        self.assertIn("RETIRED", enable_response.data["detail"])

        retire_again_response = self.client.post(f"/api/v1/drones/{drone.id}/retire")
        self.assertEqual(retire_again_response.status_code, 200)
        drone.refresh_from_db()
        self.assertEqual(drone.status, DroneStatus.RETIRED)

        self.assertEqual(
            AuditLog.objects.filter(action="DRONE_STATUS_CHANGE", target_type="drone", target_id=str(drone.id)).count(),
            2,
        )

    def test_delete_should_remove_drone_and_write_audit_log(self):
        self._grant_permissions(["drone.manage_drone"])
        drone = Drone.objects.create(
            code="DJ-0103",
            name="无人机-删除",
            model="Matrice 300",
            serial_no="SN-103",
            status=DroneStatus.DISABLED,
            created_by_staff_id=self.staff.id,
        )

        response = self.client.delete(f"/api/v1/drones/{drone.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertFalse(Drone.objects.filter(id=drone.id).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action="DRONE_DELETE",
                target_type="drone",
                target_id=str(drone.id),
            ).exists()
        )

    def test_delete_with_body_should_return_invalid_params(self):
        self._grant_permissions(["drone.manage_drone"])
        drone = Drone.objects.create(
            code="DJ-0104",
            name="无人机-删除参数",
            model="Matrice 300",
            serial_no="SN-104",
            status=DroneStatus.DISABLED,
            created_by_staff_id=self.staff.id,
        )

        response = self.client.delete(f"/api/v1/drones/{drone.id}", {"unexpected": True}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertTrue(Drone.objects.filter(id=drone.id).exists())

    def test_delete_with_active_assignment_should_return_state_conflict(self):
        self._grant_permissions(["drone.manage_drone"])
        drone = Drone.objects.create(
            code="DJ-0105",
            name="无人机-删除冲突",
            model="Matrice 300",
            serial_no="SN-105",
            status=DroneStatus.DISABLED,
            created_by_staff_id=self.staff.id,
        )

        pilot_type = StaffType.objects.create(code="pilot_operator", name="飞手操作员", status=1)
        pilot_user = User.objects.create_user(username="pilot_del_conflict", password="pass1234", status=1)
        pilot_staff = StaffProfile.objects.create(
            user=pilot_user,
            staff_no="P-DEL-01",
            name="飞手删除冲突",
            employment_status=1,
            staff_type=pilot_type,
        )
        DroneAssignment.objects.create(
            drone=drone,
            staff=pilot_staff,
            status=DroneAssignmentStatus.ACTIVE,
            created_by_staff_id=self.staff.id,
        )

        response = self.client.delete(f"/api/v1/drones/{drone.id}")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "STATE_CONFLICT")
        self.assertTrue(Drone.objects.filter(id=drone.id).exists())


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


class DroneHistoryApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.viewer_type = StaffType.objects.create(code="viewer_history", name="历史查看员", status=1)
        self.viewer_user = User.objects.create_user(username="history_viewer", password="pass1234", status=1)
        self.viewer_staff = StaffProfile.objects.create(
            user=self.viewer_user,
            staff_no="H-001",
            name="历史查看员A",
            employment_status=1,
            staff_type=self.viewer_type,
        )

        self.pilot_type = StaffType.objects.create(code="pilot_operator", name="飞手操作员", status=1)
        self.pilot_user = User.objects.create_user(username="history_pilot", password="pass1234", status=1)
        self.pilot_staff = StaffProfile.objects.create(
            user=self.pilot_user,
            staff_no="HP-001",
            name="历史飞手A",
            employment_status=1,
            staff_type=self.pilot_type,
        )

    def _grant_view_permission(self):
        group = Group.objects.create(name="无人机历史查看组")
        perm = Permission.objects.get(content_type__app_label="drone", codename="view_drone")
        group.permissions.add(perm)
        StaffTypeGroup.objects.create(staff_type=self.viewer_type, group=group, status=ScopeStatus.ACTIVE)
        GroupPermissionScope.objects.create(
            group=group,
            permission=perm,
            scope_type=ScopeType.ALL,
            status=ScopeStatus.ACTIVE,
        )

    def test_history_should_return_assignments(self):
        self._grant_view_permission()
        self.client.force_authenticate(self.viewer_user)

        drone = Drone.objects.create(
            code="DJ-HIS-01",
            name="历史测试机",
            model="Matrice 4T",
            serial_no="HIS-SN-01",
            status=DroneStatus.ENABLED,
        )
        DroneAssignment.objects.create(
            drone=drone,
            staff=self.pilot_staff,
            status=DroneAssignmentStatus.ACTIVE,
            created_by_staff_id=self.viewer_staff.id,
        )
        DroneAssignment.objects.create(
            drone=drone,
            staff=self.pilot_staff,
            status=DroneAssignmentStatus.INACTIVE,
            created_by_staff_id=self.viewer_staff.id,
        )

        response = self.client.get(f"/api/v1/drones/{drone.id}/assignments/history")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["drone_id"], drone.id)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(len(response.data["results"]), 2)
        self.assertEqual({item["status"] for item in response.data["results"]}, {"ACTIVE", "INACTIVE"})

    def test_history_without_auth_should_return_permission_denied(self):
        drone = Drone.objects.create(
            code="DJ-HIS-02",
            name="历史测试机2",
            model="Matrice 4T",
            serial_no="HIS-SN-02",
            status=DroneStatus.ENABLED,
        )
        response = self.client.get(f"/api/v1/drones/{drone.id}/assignments/history")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_history_not_found_should_return_resource_not_found(self):
        self._grant_view_permission()
        self.client.force_authenticate(self.viewer_user)
        response = self.client.get("/api/v1/drones/999999/assignments/history")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")


class DroneActiveAssignmentsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.viewer_type = StaffType.objects.create(code="viewer_active", name="当前分配查看员", status=1)
        self.viewer_user = User.objects.create_user(username="active_viewer", password="pass1234", status=1)
        self.viewer_staff = StaffProfile.objects.create(
            user=self.viewer_user,
            staff_no="A-001",
            name="当前分配查看员A",
            employment_status=1,
            staff_type=self.viewer_type,
        )

        self.pilot_type = StaffType.objects.create(code="pilot_operator", name="飞手操作员", status=1)
        self.pilot_user = User.objects.create_user(username="active_pilot", password="pass1234", status=1)
        self.pilot_staff = StaffProfile.objects.create(
            user=self.pilot_user,
            staff_no="AP-001",
            name="当前分配飞手A",
            employment_status=1,
            staff_type=self.pilot_type,
        )

    def _grant_view_permission(self):
        group = Group.objects.create(name="无人机当前分配查看组")
        perm = Permission.objects.get(content_type__app_label="drone", codename="view_drone")
        group.permissions.add(perm)
        StaffTypeGroup.objects.create(staff_type=self.viewer_type, group=group, status=ScopeStatus.ACTIVE)
        GroupPermissionScope.objects.create(
            group=group,
            permission=perm,
            scope_type=ScopeType.ALL,
            status=ScopeStatus.ACTIVE,
        )

    def test_active_assignments_should_only_return_active_records(self):
        self._grant_view_permission()
        self.client.force_authenticate(self.viewer_user)

        drone = Drone.objects.create(
            code="DJ-ACT-01",
            name="当前分配测试机",
            model="Matrice 4T",
            serial_no="ACT-SN-01",
            status=DroneStatus.ENABLED,
        )
        DroneAssignment.objects.create(
            drone=drone,
            staff=self.pilot_staff,
            status=DroneAssignmentStatus.ACTIVE,
            created_by_staff_id=self.viewer_staff.id,
        )
        DroneAssignment.objects.create(
            drone=drone,
            staff=self.pilot_staff,
            status=DroneAssignmentStatus.INACTIVE,
            created_by_staff_id=self.viewer_staff.id,
        )

        response = self.client.get(f"/api/v1/drones/{drone.id}/assignments/active")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["drone_id"], drone.id)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["status"], "ACTIVE")

    def test_active_assignments_without_auth_should_return_permission_denied(self):
        drone = Drone.objects.create(
            code="DJ-ACT-02",
            name="当前分配测试机2",
            model="Matrice 4T",
            serial_no="ACT-SN-02",
            status=DroneStatus.ENABLED,
        )
        response = self.client.get(f"/api/v1/drones/{drone.id}/assignments/active")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_active_assignments_not_found_should_return_resource_not_found(self):
        self._grant_view_permission()
        self.client.force_authenticate(self.viewer_user)
        response = self.client.get("/api/v1/drones/999999/assignments/active")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")


class DroneLatestAssignmentApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.viewer_type = StaffType.objects.create(code="viewer_latest", name="最近分配查看员", status=1)
        self.viewer_user = User.objects.create_user(username="latest_viewer", password="pass1234", status=1)
        self.viewer_staff = StaffProfile.objects.create(
            user=self.viewer_user,
            staff_no="L-001",
            name="最近分配查看员A",
            employment_status=1,
            staff_type=self.viewer_type,
        )

        self.pilot_type = StaffType.objects.create(code="pilot_latest", name="最近分配飞手", status=1)
        self.pilot_user = User.objects.create_user(username="latest_pilot", password="pass1234", status=1)
        self.pilot_staff = StaffProfile.objects.create(
            user=self.pilot_user,
            staff_no="LP-001",
            name="最近分配飞手A",
            employment_status=1,
            staff_type=self.pilot_type,
        )

    def _grant_view_permission(self):
        group = Group.objects.create(name="无人机最近分配查看组")
        perm = Permission.objects.get(content_type__app_label="drone", codename="view_drone")
        group.permissions.add(perm)
        StaffTypeGroup.objects.create(staff_type=self.viewer_type, group=group, status=ScopeStatus.ACTIVE)
        GroupPermissionScope.objects.create(
            group=group,
            permission=perm,
            scope_type=ScopeType.ALL,
            status=ScopeStatus.ACTIVE,
        )

    def test_latest_assignment_should_return_latest_record(self):
        self._grant_view_permission()
        self.client.force_authenticate(self.viewer_user)

        drone = Drone.objects.create(
            code="DJ-LATEST-01",
            name="最近分配测试机",
            model="Matrice 4T",
            serial_no="LATEST-SN-01",
            status=DroneStatus.ENABLED,
        )
        DroneAssignment.objects.create(
            drone=drone,
            staff=self.pilot_staff,
            status=DroneAssignmentStatus.ACTIVE,
            created_by_staff_id=self.viewer_staff.id,
        )
        latest_assignment = DroneAssignment.objects.create(
            drone=drone,
            staff=self.pilot_staff,
            status=DroneAssignmentStatus.INACTIVE,
            created_by_staff_id=self.viewer_staff.id,
        )

        response = self.client.get(f"/api/v1/drones/{drone.id}/assignments/latest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["drone_id"], drone.id)
        self.assertTrue(response.data["has_record"])
        self.assertIsNotNone(response.data["result"])
        self.assertEqual(response.data["result"]["id"], latest_assignment.id)
        self.assertEqual(response.data["result"]["status"], "INACTIVE")

    def test_latest_assignment_should_return_null_when_no_record(self):
        self._grant_view_permission()
        self.client.force_authenticate(self.viewer_user)
        drone = Drone.objects.create(
            code="DJ-LATEST-02",
            name="最近分配测试机2",
            model="Matrice 4T",
            serial_no="LATEST-SN-02",
            status=DroneStatus.ENABLED,
        )
        response = self.client.get(f"/api/v1/drones/{drone.id}/assignments/latest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["drone_id"], drone.id)
        self.assertFalse(response.data["has_record"])
        self.assertIsNone(response.data["result"])

    def test_latest_assignment_without_auth_should_return_permission_denied(self):
        drone = Drone.objects.create(
            code="DJ-LATEST-03",
            name="最近分配测试机3",
            model="Matrice 4T",
            serial_no="LATEST-SN-03",
            status=DroneStatus.ENABLED,
        )
        response = self.client.get(f"/api/v1/drones/{drone.id}/assignments/latest")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertIn(response.data["business_detail_code"], {"NOT_AUTHENTICATED", "FORBIDDEN"})

    def test_latest_assignment_not_found_should_return_resource_not_found(self):
        self._grant_view_permission()
        self.client.force_authenticate(self.viewer_user)
        response = self.client.get("/api/v1/drones/999999/assignments/latest")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "NOT_FOUND")


class BusinessAdminApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff_type = StaffType.objects.create(code="business_admin", name="业务管理员", status=1)
        self.user = User.objects.create_user(username="biz_super_u", password="pass1234", status=1)
        self.staff = StaffProfile.objects.create(
            user=self.user,
            staff_no="BS-900",
            name="业务管理员U",
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

        group = Group.objects.create(name="业务管理员权限组")
        permission_specs = [
            ("drone", "view_drone"),
            ("drone", "manage_drone"),
            ("drone", "change_drone_status"),
            ("drone_assignment", "manage_drone_assignment"),
        ]
        for app_label, codename in permission_specs:
            perm = Permission.objects.get(content_type__app_label=app_label, codename=codename)
            group.permissions.add(perm)
            GroupPermissionScope.objects.create(
                group=group,
                permission=perm,
                scope_type=ScopeType.ALL,
                status=ScopeStatus.ACTIVE,
            )
        StaffTypeGroup.objects.create(staff_type=self.staff_type, group=group, status=ScopeStatus.ACTIVE)
        self.client.force_authenticate(self.user)

    def test_business_admin_can_access_business_apis(self):
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
