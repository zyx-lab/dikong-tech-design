from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import AuditLog, Permission, ScopeType, StaffProfile, Tenant, TenantStatus
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.drone.models import Drone, DroneStatus
from apps.drone_assignment.models import DroneAssignment, DroneAssignmentStatus

User = get_user_model()


class DroneApiAuthzTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="drone_user", password="pass1234", status=1)
        self.staff = ensure_staff_profile(
            self.user,
            staff_no="D-001",
            name="调度员A",
        )
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            role_code="drone_viewer_test_role",
            role_name="无人机查看测试角色",
            tenant_code="drone_authz_tenant",
        )
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    def _grant_permission(self, permission_code: str, with_scope: bool = True):
        if with_scope:
            grant_role_permissions(self.role, {permission_code: ScopeType.ALL})
            return
        Permission.objects.update_or_create(
            code=permission_code,
            defaults={"name": permission_code, "module": permission_code.split(".", 1)[0], "status": 1},
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
        self.assertEqual(response.data["detail"], "PERMISSION_DENIED")

    def test_view_permission_with_all_scope_should_allow_list(self):
        self._grant_permission("drone.view_drone", with_scope=True)
        Drone.objects.create(
            tenant=self.tenant,
            code="DJ-0001",
            name="无人机1",
            model="Mavic 3E",
            serial_no="SN-1",
            status=DroneStatus.ENABLED,
        )
        Drone.objects.create(
            tenant=self.tenant,
            code="DJ-0002",
            name="无人机2",
            model="Matrice 30",
            serial_no="SN-2",
            status=DroneStatus.DISABLED,
        )

        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/drones")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)

    def test_list_should_only_return_current_tenant_drones(self):
        self._grant_permission("drone.view_drone", with_scope=True)
        other_tenant = Tenant.objects.create(
            code="drone_authz_other_tenant",
            name="无人机其他租户",
            status=TenantStatus.ACTIVE,
        )
        Drone.objects.create(
            tenant=self.tenant,
            code="DJ-CURRENT-01",
            name="当前租户无人机",
            model="Mavic 3E",
            serial_no="CURRENT-SN-01",
            status=DroneStatus.ENABLED,
        )
        Drone.objects.create(
            tenant=other_tenant,
            code="DJ-OTHER-01",
            name="其他租户无人机",
            model="Matrice 30",
            serial_no="OTHER-SN-01",
            status=DroneStatus.ENABLED,
        )

        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/drones")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["code"], "DJ-CURRENT-01")


class SuperuserRootPermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.superuser = User.objects.create_superuser(username="root_business", password="pass1234")
        self.tenant = Tenant.objects.create(
            code="root_business_tenant",
            name="超级管理员业务租户",
            status=TenantStatus.ACTIVE,
        )
        self.client.force_authenticate(self.superuser)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    def test_superuser_should_access_business_list_without_staff_or_matrix(self):
        Drone.objects.create(
            tenant=self.tenant,
            code="DJ-ROOT-01",
            name="Root 机型",
            model="Matrice 4",
            serial_no="ROOT-SN-01",
        )
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
        self.assertIsNone(drone.created_by_tenant_member_id)


class DroneApiWriteTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="drone_admin", password="pass1234", status=1)
        self.staff = ensure_staff_profile(
            self.user,
            staff_no="D-100",
            name="Admin管理员A",
        )
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            role_code="drone_admin_test_role",
            role_name="无人机管理测试角色",
            tenant_code="drone_write_tenant",
        )
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    def _grant_permissions(self, permission_codes: list[str]):
        grant_role_permissions(
            self.role,
            {permission_code: ScopeType.ALL for permission_code in permission_codes},
            group_name="drone-admin-group",
        )

    def _create_drone(self, *, code: str, status: str) -> Drone:
        return Drone.objects.create(
            tenant=self.tenant,
            code=code,
            name=f"{code}-name",
            model="Matrice 30",
            serial_no=f"{code}-sn",
            status=status,
            created_by_tenant_member_id=self.member.id,
        )

    def test_create_should_record_creator_member_and_audit_log(self):
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
        self.assertEqual(drone.created_by_tenant_member_id, self.member.id)
        self.assertTrue(
            AuditLog.objects.filter(
                action="DRONE_CREATE",
                target_type="drone",
                target_id=str(drone.id),
            ).exists()
        )
        self.assertEqual(
            AuditLog.objects.get(
                action="DRONE_CREATE",
                target_type="drone",
                target_id=str(drone.id),
            ).tenant,
            self.tenant,
        )

    def test_patch_should_not_allow_status_field(self):
        self._grant_permissions(["drone.manage_drone"])
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-0101",
            name="无人机-编辑",
            model="Matrice 30",
            serial_no="SN-101",
            status=DroneStatus.DISABLED,
            created_by_tenant_member_id=self.member.id,
        )
        original_status = drone.status
        response = self.client.patch(
            f"/api/v1/drones/{drone.id}",
            {"status": DroneStatus.ENABLED},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("status", response.data)
        # 验证状态实际未改变
        drone.refresh_from_db()
        self.assertEqual(drone.status, original_status)

    def test_put_should_not_be_exposed(self):
        self._grant_permissions(["drone.manage_drone"])
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-0101-PUT",
            name="无人机-PUT",
            model="Matrice 30",
            serial_no="SN-101-PUT",
            status=DroneStatus.DISABLED,
            created_by_tenant_member_id=self.member.id,
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
            tenant=self.tenant,
            code="DJ-0102",
            name="无人机-状态",
            model="Matrice 30T",
            serial_no="SN-102",
            status=DroneStatus.ENABLED,
            created_by_tenant_member_id=self.member.id,
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

    def test_model_should_reject_revert_from_retired(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-RET-MODEL-01",
            name="模型退役无人机",
            model="Matrice 4T",
            serial_no="RET-MODEL-SN-01",
            status=DroneStatus.RETIRED,
            created_by_tenant_member_id=self.member.id,
        )

        drone.status = DroneStatus.ENABLED

        with self.assertRaises(ValidationError):
            drone.save()

    def test_delete_should_remove_drone_and_write_audit_log(self):
        self._grant_permissions(["drone.manage_drone"])
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-0103",
            name="无人机-删除",
            model="Matrice 300",
            serial_no="SN-103",
            status=DroneStatus.DISABLED,
            created_by_tenant_member_id=self.member.id,
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
            tenant=self.tenant,
            code="DJ-0104",
            name="无人机-删除参数",
            model="Matrice 300",
            serial_no="SN-104",
            status=DroneStatus.DISABLED,
            created_by_tenant_member_id=self.member.id,
        )

        response = self.client.delete(f"/api/v1/drones/{drone.id}", {"unexpected": True}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertTrue(Drone.objects.filter(id=drone.id).exists())

    def test_delete_with_active_assignment_should_return_state_conflict(self):
        self._grant_permissions(["drone.manage_drone"])
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-0105",
            name="无人机-删除冲突",
            model="Matrice 300",
            serial_no="SN-105",
            status=DroneStatus.DISABLED,
            created_by_tenant_member_id=self.member.id,
        )

        pilot_user = User.objects.create_user(username="pilot_del_conflict", password="pass1234", status=1)
        pilot_staff = ensure_staff_profile(
            pilot_user,
            staff_no="P-DEL-01",
            name="飞手删除冲突",
            employment_status=1,
        )
        _pilot_tenant, pilot_member, _pilot_role = ensure_tenant_role_binding(
            pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手操作员",
        )
        ensure_tenant_member_position(pilot_member, code="pilot_operator", name="飞手操作员")
        DroneAssignment.objects.create(
            tenant=self.tenant,
            drone=drone,
            tenant_member=pilot_member,
            status=DroneAssignmentStatus.ACTIVE,
            created_by_tenant_member_id=self.member.id,
        )

        response = self.client.delete(f"/api/v1/drones/{drone.id}")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "STATE_CONFLICT")
        self.assertTrue(Drone.objects.filter(id=drone.id).exists())


class PilotAssignedScopeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.pilot_user = User.objects.create_user(username="pilot_a", password="pass1234", status=1)
        self.pilot_staff = ensure_staff_profile(self.pilot_user, staff_no="P-001", name="飞手A", employment_status=1)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant_code="drone_assigned_scope_tenant",
            role_code="pilot_assigned_scope_role",
            role_name="飞手按分配查看角色",
        )
        ensure_tenant_member_position(self.member, code="pilot_operator", name="飞手操作员")
        grant_role_permissions(
            self.role,
            {"drone.view_drone": ScopeType.ASSIGNED},
            group_name="无人机按分配查看组",
        )

        self.drone_assigned = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-P-01",
            name="飞手可见-1",
            model="Mavic 3E",
            serial_no="P-SN-1",
            status=DroneStatus.ENABLED,
        )
        self.drone_unassigned = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-P-02",
            name="飞手不可见-2",
            model="Matrice 30",
            serial_no="P-SN-2",
            status=DroneStatus.ENABLED,
        )
        DroneAssignment.objects.create(
            tenant=self.tenant,
            drone=self.drone_assigned,
            tenant_member=self.member,
            status=DroneAssignmentStatus.ACTIVE,
        )
        self.client.force_authenticate(self.pilot_user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

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
        self.viewer_user = User.objects.create_user(username="history_viewer", password="pass1234", status=1)
        self.viewer_staff = ensure_staff_profile(
            self.viewer_user,
            staff_no="H-001",
            name="历史查看员A",
            employment_status=1,
        )
        self.pilot_user = User.objects.create_user(username="history_pilot", password="pass1234", status=1)
        self.pilot_staff = ensure_staff_profile(
            self.pilot_user,
            staff_no="HP-001",
            name="历史飞手A",
            employment_status=1,
        )
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.viewer_user,
            tenant_code="drone_history_tenant",
            role_code="drone_history_viewer_role",
            role_name="无人机历史查看角色",
        )
        _pilot_tenant, self.pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手操作员",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手操作员")
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    def _grant_view_permission(self):
        grant_role_permissions(
            self.role,
            {"drone.view_drone": ScopeType.ALL},
            group_name="无人机历史查看组",
        )

    def test_history_should_return_assignments(self):
        self._grant_view_permission()
        self.client.force_authenticate(self.viewer_user)

        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-HIS-01",
            name="历史测试机",
            model="Matrice 4T",
            serial_no="HIS-SN-01",
            status=DroneStatus.ENABLED,
        )
        DroneAssignment.objects.create(
            tenant=self.tenant,
            drone=drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.ACTIVE,
            created_by_tenant_member_id=self.member.id,
        )
        DroneAssignment.objects.create(
            tenant=self.tenant,
            drone=drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.INACTIVE,
            end_at=timezone.now(),
            created_by_tenant_member_id=self.member.id,
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
            tenant=self.tenant,
            code="DJ-HIS-02",
            name="历史测试机2",
            model="Matrice 4T",
            serial_no="HIS-SN-02",
            status=DroneStatus.ENABLED,
        )
        response = self.client.get(f"/api/v1/drones/{drone.id}/assignments/history")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

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
        self.viewer_user = User.objects.create_user(username="active_viewer", password="pass1234", status=1)
        self.viewer_staff = ensure_staff_profile(
            self.viewer_user,
            staff_no="A-001",
            name="当前分配查看员A",
            employment_status=1,
        )
        self.pilot_user = User.objects.create_user(username="active_pilot", password="pass1234", status=1)
        self.pilot_staff = ensure_staff_profile(
            self.pilot_user,
            staff_no="AP-001",
            name="当前分配飞手A",
            employment_status=1,
        )
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.viewer_user,
            tenant_code="drone_active_tenant",
            role_code="drone_active_viewer_role",
            role_name="无人机当前分配查看角色",
        )
        _pilot_tenant, self.pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手操作员",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手操作员")
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    def _grant_view_permission(self):
        grant_role_permissions(
            self.role,
            {"drone.view_drone": ScopeType.ALL},
            group_name="无人机当前分配查看组",
        )

    def test_active_assignments_should_only_return_active_records(self):
        self._grant_view_permission()
        self.client.force_authenticate(self.viewer_user)

        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-ACT-01",
            name="当前分配测试机",
            model="Matrice 4T",
            serial_no="ACT-SN-01",
            status=DroneStatus.ENABLED,
        )
        DroneAssignment.objects.create(
            tenant=self.tenant,
            drone=drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.ACTIVE,
            created_by_tenant_member_id=self.member.id,
        )
        DroneAssignment.objects.create(
            tenant=self.tenant,
            drone=drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.INACTIVE,
            end_at=timezone.now(),
            created_by_tenant_member_id=self.member.id,
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
            tenant=self.tenant,
            code="DJ-ACT-02",
            name="当前分配测试机2",
            model="Matrice 4T",
            serial_no="ACT-SN-02",
            status=DroneStatus.ENABLED,
        )
        response = self.client.get(f"/api/v1/drones/{drone.id}/assignments/active")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

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
        self.viewer_user = User.objects.create_user(username="latest_viewer", password="pass1234", status=1)
        self.viewer_staff = ensure_staff_profile(
            self.viewer_user,
            staff_no="L-001",
            name="最近分配查看员A",
            employment_status=1,
        )
        self.pilot_user = User.objects.create_user(username="latest_pilot", password="pass1234", status=1)
        self.pilot_staff = ensure_staff_profile(
            self.pilot_user,
            staff_no="LP-001",
            name="最近分配飞手A",
            employment_status=1,
        )
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.viewer_user,
            tenant_code="drone_latest_tenant",
            role_code="drone_latest_viewer_role",
            role_name="无人机最近分配查看角色",
        )
        _pilot_tenant, self.pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手操作员",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手操作员")
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    def _grant_view_permission(self):
        grant_role_permissions(
            self.role,
            {"drone.view_drone": ScopeType.ALL},
            group_name="无人机最近分配查看组",
        )

    def test_latest_assignment_should_return_latest_record(self):
        self._grant_view_permission()
        self.client.force_authenticate(self.viewer_user)

        drone = Drone.objects.create(
            tenant=self.tenant,
            code="DJ-LATEST-01",
            name="最近分配测试机",
            model="Matrice 4T",
            serial_no="LATEST-SN-01",
            status=DroneStatus.ENABLED,
        )
        DroneAssignment.objects.create(
            tenant=self.tenant,
            drone=drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.ACTIVE,
            created_by_tenant_member_id=self.member.id,
        )
        latest_assignment = DroneAssignment.objects.create(
            tenant=self.tenant,
            drone=drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.INACTIVE,
            end_at=timezone.now(),
            created_by_tenant_member_id=self.member.id,
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
            tenant=self.tenant,
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
            tenant=self.tenant,
            code="DJ-LATEST-03",
            name="最近分配测试机3",
            model="Matrice 4T",
            serial_no="LATEST-SN-03",
            status=DroneStatus.ENABLED,
        )
        response = self.client.get(f"/api/v1/drones/{drone.id}/assignments/latest")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

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
        self.user = User.objects.create_user(username="biz_super_u", password="pass1234", status=1)
        self.staff = ensure_staff_profile(self.user, staff_no="BS-900", name="业务管理员U", employment_status=1)
        self.pilot_user = User.objects.create_user(username="pilot_for_biz_super", password="pass1234", status=1)
        self.pilot_staff = ensure_staff_profile(
            self.pilot_user,
            staff_no="P-900",
            name="飞手900",
            employment_status=1,
        )
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="drone_business_admin_tenant",
            role_code="business_admin_test_role",
            role_name="业务管理员测试角色",
        )
        _pilot_tenant, self.pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手操作员",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手操作员")
        grant_role_permissions(
            self.role,
            {
                "drone.view_drone": ScopeType.ALL,
                "drone.manage_drone": ScopeType.ALL,
                "drone.change_drone_status": ScopeType.ALL,
                "drone_assignment.manage_drone_assignment": ScopeType.ALL,
            },
            group_name="业务管理员权限组",
        )
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

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
                "tenant_member": self.pilot_member.id,
            },
            format="json",
        )
        self.assertEqual(assign_resp.status_code, 201)


# ============================================================================
# 边界值测试
# ============================================================================

class DroneBoundaryTests(TestCase):
    """无人机边界值测试"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="drone_bound", password="pass1234", status=1)
        self.staff = ensure_staff_profile(self.user, staff_no="DB-001", name="边界测试员", employment_status=1)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="drone_boundary_tenant",
            role_code="drone_boundary_role",
            role_name="无人机边界测试角色",
        )
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)
        self._grant_permission("drone.manage_drone")

    def _grant_permission(self, permission_code: str):
        grant_role_permissions(
            self.role,
            {permission_code: ScopeType.ALL},
            group_name=f"{permission_code}-group-bound",
        )

    def test_create_drone_max_name_length(self):
        """测试最大名称长度（128字符）"""
        self.client.force_authenticate(self.user)
        max_length_name = "A" * 128

        response = self.client.post("/api/v1/drones", {
            "code": "DJ-MAX-NAME",
            "name": max_length_name,
            "model": "TestModel",
            "serial_no": "SN-MAX-001",
        })

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")

    def test_create_drone_exceed_name_length(self):
        """测试名称超过最大长度"""
        self.client.force_authenticate(self.user)
        long_name = "A" * 129

        response = self.client.post("/api/v1/drones", {
            "code": "DJ-LONG-NAME",
            "name": long_name,
            "model": "TestModel",
            "serial_no": "SN-LONG-001",
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")

    def test_create_drone_duplicate_code(self):
        """测试重复业务编码"""
        self.client.force_authenticate(self.user)
        code = "DJ-DUP-TEST"

        # 创建第一个
        response1 = self.client.post("/api/v1/drones", {
            "code": code,
            "name": "测试无人机1",
            "model": "Model1",
            "serial_no": "SN-DUP-001",
        })
        self.assertEqual(response1.status_code, 201)

        # 尝试重复 - 系统可能返回 IDEMPOTENT_DUPLICATE（幂等重复）
        response2 = self.client.post("/api/v1/drones", {
            "code": code,
            "name": "测试无人机2",
            "model": "Model2",
            "serial_no": "SN-DUP-002",
        })
        self.assertEqual(response2.status_code, 409)
        self.assertEqual(response2.data["business_code"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(response2.data["business_detail_code"], "DUPLICATE_REQUEST")
        self.assertIn("code", response2.data["errors"])

    def test_create_drone_duplicate_code_in_other_tenant_should_be_allowed(self):
        """测试跨租户重复业务编码允许创建"""
        self.client.force_authenticate(self.user)
        other_tenant = Tenant.objects.create(
            code="drone_boundary_other_tenant_code",
            name="无人机边界其他租户",
            status=TenantStatus.ACTIVE,
        )
        Drone.objects.create(
            tenant=other_tenant,
            code="DJ-CROSS-TENANT-CODE",
            name="其他租户无人机",
            model="Model-Other",
            serial_no="SN-CROSS-TENANT-001",
            status=DroneStatus.ENABLED,
        )

        response = self.client.post("/api/v1/drones", {
            "code": "DJ-CROSS-TENANT-CODE",
            "name": "当前租户无人机",
            "model": "Model-Current",
            "serial_no": "SN-CROSS-TENANT-002",
        })

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["code"], "DJ-CROSS-TENANT-CODE")

    def test_create_drone_duplicate_serial_no(self):
        """测试重复序列号"""
        self.client.force_authenticate(self.user)
        serial = "SN-SAME-SERIAL"

        response1 = self.client.post("/api/v1/drones", {
            "code": "DJ-SERIAL-1",
            "name": "测试1",
            "model": "Model1",
            "serial_no": serial,
        })
        self.assertEqual(response1.status_code, 201)

        response2 = self.client.post("/api/v1/drones", {
            "code": "DJ-SERIAL-2",
            "name": "测试2",
            "model": "Model2",
            "serial_no": serial,
        })
        self.assertEqual(response2.status_code, 409)
        self.assertEqual(response2.data["business_code"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(response2.data["business_detail_code"], "DUPLICATE_REQUEST")
        self.assertIn("serial_no", response2.data["errors"])

    def test_create_drone_duplicate_serial_no_in_other_tenant_should_be_allowed(self):
        """测试跨租户重复序列号允许创建"""
        self.client.force_authenticate(self.user)
        other_tenant = Tenant.objects.create(
            code="drone_boundary_other_tenant_serial",
            name="无人机边界其他租户序列号",
            status=TenantStatus.ACTIVE,
        )
        Drone.objects.create(
            tenant=other_tenant,
            code="DJ-CROSS-TENANT-SERIAL-1",
            name="其他租户无人机",
            model="Model-Other",
            serial_no="SN-CROSS-TENANT-SHARED",
            status=DroneStatus.ENABLED,
        )

        response = self.client.post("/api/v1/drones", {
            "code": "DJ-CROSS-TENANT-SERIAL-2",
            "name": "当前租户无人机",
            "model": "Model-Current",
            "serial_no": "SN-CROSS-TENANT-SHARED",
        })

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["serial_no"], "SN-CROSS-TENANT-SHARED")

    def test_create_drone_missing_required_field(self):
        """测试缺少必填字段"""
        self.client.force_authenticate(self.user)

        # 缺少 code
        response = self.client.post("/api/v1/drones", {
            "name": "测试无人机",
            "model": "ModelX",
            "serial_no": "SN-MISSING-001",
        })
        self.assertEqual(response.status_code, 400)

        # 缺少 serial_no
        response2 = self.client.post("/api/v1/drones", {
            "code": "DJ-MISSING-002",
            "name": "测试无人机",
            "model": "ModelX",
        })
        self.assertEqual(response2.status_code, 400)

    def test_create_drone_empty_field(self):
        """测试空字段"""
        self.client.force_authenticate(self.user)

        response = self.client.post("/api/v1/drones", {
            "code": "",
            "name": "",
            "model": "",
            "serial_no": "",
        })
        self.assertEqual(response.status_code, 400)
