"""Drone live HTTP test events.

- 调度员通过真实 HTTP 完成无人机创建/查询/更新/状态流转/删除闭环
- 删除冲突、非法 body、重复提交按合同拒绝
- assignments/history、assignments/active、assignments/latest 返回符合业务约定
- ASSIGNED scope 飞手只能查看自己被分配的无人机
- business drone API 强制要求有效 Bearer + X-TENANT-CODE
- platform_admin 即使权限矩阵放开也禁止访问租户业务接口
"""

from django.utils import timezone

from apps.access.models import AuditLog, DirectoryStatus, EmploymentStatus, Role, ScopeType
from apps.access.test_live_base import LiveIamApiTestCase, User
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.drone.models import Drone, DroneStatus
from apps.drone_assignment.models import DroneAssignment, DroneAssignmentStatus


class LiveDroneApiTestCase(LiveIamApiTestCase):
    def setUp(self):
        super().setUp()
        self.admin_user = User.objects.create_user(username="drone_live_admin", password="pass1234", status=1)
        self.admin_staff = ensure_staff_profile(
            self.admin_user,
            staff_no="DL-001",
            name="实时无人机管理员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.tenant, self.admin_member, self.admin_role = ensure_tenant_role_binding(
            self.admin_user,
            tenant_code="drone_live_tenant",
            role_code="drone_live_admin_role",
            role_name="实时无人机管理角色",
        )
        grant_role_permissions(
            self.admin_role,
            {
                "drone.view_drone": ScopeType.ALL,
                "drone.manage_drone": ScopeType.ALL,
                "drone.change_drone_status": ScopeType.ALL,
            },
            group_name="drone-live-admin-group",
        )

        self.pilot_user = User.objects.create_user(username="drone_live_pilot", password="pass1234", status=1)
        self.pilot_staff = ensure_staff_profile(
            self.pilot_user,
            staff_no="DL-P-001",
            name="实时无人机飞手",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, self.pilot_member, self.pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手操作员",
            member_no="DL-P-001",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手操作员")
        grant_role_permissions(
            self.pilot_role,
            {"drone.view_drone": ScopeType.ASSIGNED},
            group_name="drone-live-pilot-group",
        )

        self.other_admin_user = User.objects.create_user(username="drone_live_other_admin", password="pass1234", status=1)
        ensure_staff_profile(
            self.other_admin_user,
            staff_no="DL-OTHER-001",
            name="其他租户无人机管理员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.other_tenant, self.other_admin_member, self.other_admin_role = ensure_tenant_role_binding(
            self.other_admin_user,
            tenant_code="drone_live_other_tenant",
            role_code="drone_live_other_admin_role",
            role_name="其他租户无人机管理角色",
        )
        grant_role_permissions(
            self.other_admin_role,
            {
                "drone.view_drone": ScopeType.ALL,
                "drone.manage_drone": ScopeType.ALL,
                "drone.change_drone_status": ScopeType.ALL,
            },
            group_name="drone-live-other-admin-group",
        )

        self.login(username="drone_live_admin", password="pass1234", tenant_code=self.tenant.code)
        self.pilot_client = self.new_client()
        self.authenticate_client(
            self.pilot_client,
            username="drone_live_pilot",
            password="pass1234",
            tenant_code=self.tenant.code,
        )

    def _create_drone(
        self,
        *,
        code: str,
        name: str | None = None,
        model: str = "Matrice 30",
        serial_no: str | None = None,
        status: str = DroneStatus.DISABLED,
        org_id: int | None = None,
        tenant=None,
        created_by_tenant_member_id: int | None = None,
    ) -> Drone:
        tenant = tenant or self.tenant
        if created_by_tenant_member_id is None and tenant == self.tenant:
            created_by_tenant_member_id = self.admin_member.id
        return Drone.objects.create(
            tenant=tenant,
            code=code,
            name=name or f"{code}-name",
            model=model,
            serial_no=serial_no or f"{code}-sn",
            status=status,
            org_id=org_id,
            created_by_tenant_member_id=created_by_tenant_member_id,
        )

    def _create_assignment(
        self,
        *,
        drone: Drone,
        tenant_member=None,
        status: str = DroneAssignmentStatus.ACTIVE,
    ) -> DroneAssignment:
        return DroneAssignment.objects.create(
            tenant=self.tenant,
            drone=drone,
            tenant_member=tenant_member or self.pilot_member,
            status=status,
            end_at=timezone.now() if status == DroneAssignmentStatus.INACTIVE else None,
            created_by_tenant_member_id=self.admin_member.id,
        )


class LiveDroneApiTests(LiveDroneApiTestCase):
    def test_drone_lifecycle_should_follow_live_http_contract(self):
        create_response = self.client.post(
            "/api/v1/drones",
            {
                "code": "DL-DRONE-001",
                "name": "实时巡检无人机",
                "model": "Mavic 3E",
                "serial_no": "DL-DRONE-SN-001",
                "org_id": 2001,
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.json()["code"], "00000")
        create_data = create_response.json()["data"]
        self.assertEqual(create_data["code"], "DL-DRONE-001")
        self.assertEqual(create_data["status"], DroneStatus.DISABLED)
        self.assertEqual(create_data["created_by_tenant_member_id"], self.admin_member.id)
        drone_id = create_data["id"]

        list_response = self.client.get("/api/v1/drones", {"code": "DL-DRONE-001"})
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["data"]["total"], 1)
        self.assertEqual(list_response.json()["data"]["list"][0]["id"], drone_id)

        retrieve_response = self.client.get(f"/api/v1/drones/{drone_id}")
        self.assertEqual(retrieve_response.status_code, 200)
        self.assertEqual(retrieve_response.json()["data"]["name"], "实时巡检无人机")

        put_response = self.client.put(
            f"/api/v1/drones/{drone_id}",
            {
                "code": "DL-DRONE-001",
                "name": "实时巡检无人机-全量更新",
                "model": "Matrice 30T",
                "serial_no": "DL-DRONE-SN-001",
                "org_id": 2002,
            },
            format="json",
        )
        self.assertEqual(put_response.status_code, 200)
        self.assertEqual(put_response.json()["code"], "00000")
        self.assertEqual(put_response.json()["data"]["name"], "实时巡检无人机-全量更新")
        self.assertEqual(put_response.json()["data"]["model"], "Matrice 30T")
        self.assertEqual(put_response.json()["data"]["org_id"], 2002)

        patch_response = self.client.patch(
            f"/api/v1/drones/{drone_id}",
            {"name": "实时巡检无人机-局部更新", "org_id": 2003},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["data"]["name"], "实时巡检无人机-局部更新")
        self.assertEqual(patch_response.json()["data"]["org_id"], 2003)

        enable_response = self.client.post(f"/api/v1/drones/{drone_id}/enable")
        self.assertEqual(enable_response.status_code, 200)
        self.assertEqual(enable_response.json()["data"]["status"], DroneStatus.ENABLED)

        maintenance_response = self.client.post(f"/api/v1/drones/{drone_id}/maintenance")
        self.assertEqual(maintenance_response.status_code, 200)
        self.assertEqual(maintenance_response.json()["data"]["status"], DroneStatus.MAINTENANCE)

        disable_response = self.client.post(f"/api/v1/drones/{drone_id}/disable")
        self.assertEqual(disable_response.status_code, 200)
        self.assertEqual(disable_response.json()["data"]["status"], DroneStatus.DISABLED)

        retire_response = self.client.post(f"/api/v1/drones/{drone_id}/retire")
        self.assertEqual(retire_response.status_code, 200)
        self.assertEqual(retire_response.json()["data"]["status"], DroneStatus.RETIRED)

        enable_after_retire_response = self.client.post(f"/api/v1/drones/{drone_id}/enable")
        self.assertEqual(enable_after_retire_response.status_code, 409)
        self.assertEqual(enable_after_retire_response.json()["code"], "C0201")
        self.assertIn("RETIRED", enable_after_retire_response.json()["msg"])

        retire_again_response = self.client.post(f"/api/v1/drones/{drone_id}/retire")
        self.assertEqual(retire_again_response.status_code, 200)
        self.assertEqual(retire_again_response.json()["data"]["status"], DroneStatus.RETIRED)

        delete_response = self.client.delete(f"/api/v1/drones/{drone_id}")
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json()["code"], "00000")
        self.assertEqual(delete_response.json()["data"]["id"], drone_id)
        self.assertTrue(delete_response.json()["data"]["deleted"])
        self.assertFalse(Drone.objects.filter(id=drone_id).exists())

        self.assertTrue(
            AuditLog.objects.filter(
                tenant=self.tenant,
                action="DRONE_CREATE",
                target_type="drone",
                target_id=str(drone_id),
            ).exists()
        )
        self.assertEqual(
            AuditLog.objects.filter(
                tenant=self.tenant,
                action="DRONE_UPDATE",
                target_type="drone",
                target_id=str(drone_id),
            ).count(),
            2,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                tenant=self.tenant,
                action="DRONE_STATUS_CHANGE",
                target_type="drone",
                target_id=str(drone_id),
            ).count(),
            5,
        )
        self.assertTrue(
            AuditLog.objects.filter(
                tenant=self.tenant,
                action="DRONE_DELETE",
                target_type="drone",
                target_id=str(drone_id),
            ).exists()
        )

    def test_assignment_queries_should_follow_live_http_contract(self):
        drone = self._create_drone(code="DL-HISTORY-001", status=DroneStatus.ENABLED)
        empty_drone = self._create_drone(code="DL-HISTORY-002", status=DroneStatus.ENABLED)
        self._create_assignment(drone=drone, status=DroneAssignmentStatus.ACTIVE)
        latest_assignment = self._create_assignment(drone=drone, status=DroneAssignmentStatus.INACTIVE)

        history_response = self.client.get(f"/api/v1/drones/{drone.id}/assignments/history")
        self.assertEqual(history_response.status_code, 200)
        self.assertEqual(history_response.json()["code"], "00000")
        self.assertEqual(history_response.json()["data"]["drone_id"], drone.id)
        self.assertEqual(history_response.json()["data"]["total"], 2)
        self.assertEqual(
            {item["status"] for item in history_response.json()["data"]["list"]},
            {DroneAssignmentStatus.ACTIVE, DroneAssignmentStatus.INACTIVE},
        )

        active_response = self.client.get(f"/api/v1/drones/{drone.id}/assignments/active")
        self.assertEqual(active_response.status_code, 200)
        self.assertEqual(active_response.json()["data"]["drone_id"], drone.id)
        self.assertEqual(active_response.json()["data"]["total"], 1)
        self.assertEqual(active_response.json()["data"]["list"][0]["status"], DroneAssignmentStatus.ACTIVE)
        self.assertEqual(active_response.json()["data"]["list"][0]["staff_name"], self.pilot_staff.name)

        latest_response = self.client.get(f"/api/v1/drones/{drone.id}/assignments/latest")
        self.assertEqual(latest_response.status_code, 200)
        self.assertTrue(latest_response.json()["data"]["has_record"])
        self.assertEqual(latest_response.json()["data"]["result"]["id"], latest_assignment.id)
        self.assertEqual(latest_response.json()["data"]["result"]["status"], DroneAssignmentStatus.INACTIVE)

        empty_latest_response = self.client.get(f"/api/v1/drones/{empty_drone.id}/assignments/latest")
        self.assertEqual(empty_latest_response.status_code, 200)
        self.assertEqual(empty_latest_response.json()["code"], "00000")
        self.assertFalse(empty_latest_response.json()["data"]["has_record"])
        self.assertIsNone(empty_latest_response.json()["data"]["result"])

    def test_duplicate_and_invalid_mutations_should_be_rejected_over_live_http(self):
        existing_drone = self._create_drone(
            code="DL-CONFLICT-001",
            name="冲突无人机",
            serial_no="DL-CONFLICT-SN-001",
            status=DroneStatus.DISABLED,
        )

        duplicate_response = self.client.post(
            "/api/v1/drones",
            {
                "code": "DL-CONFLICT-001",
                "name": "重复编码无人机",
                "model": "Mavic 3E",
                "serial_no": "DL-CONFLICT-SN-002",
            },
            format="json",
        )
        self.assertEqual(duplicate_response.status_code, 409)
        self.assertEqual(duplicate_response.json()["code"], "C0101")
        self.assertIn("code", duplicate_response.json()["data"])

        patch_response = self.client.patch(
            f"/api/v1/drones/{existing_drone.id}",
            {"status": DroneStatus.ENABLED},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 400)
        self.assertEqual(patch_response.json()["code"], "B0001")
        self.assertIn("status", patch_response.json()["data"])
        existing_drone.refresh_from_db()
        self.assertEqual(existing_drone.status, DroneStatus.DISABLED)

        delete_with_body_response = self.client.delete(
            f"/api/v1/drones/{existing_drone.id}",
            {"unexpected": True},
            format="json",
        )
        self.assertEqual(delete_with_body_response.status_code, 400)
        self.assertEqual(delete_with_body_response.json()["code"], "B0001")
        self.assertIn("body", delete_with_body_response.json()["data"])
        self.assertTrue(Drone.objects.filter(id=existing_drone.id).exists())

    def test_delete_with_active_assignment_should_return_state_conflict_over_live_http(self):
        drone = self._create_drone(code="DL-CONFLICT-DELETE-001", status=DroneStatus.ENABLED)
        self._create_assignment(drone=drone, status=DroneAssignmentStatus.ACTIVE)

        response = self.client.delete(f"/api/v1/drones/{drone.id}")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "C0201")
        self.assertEqual(response.json()["data"]["drone_id"], drone.id)
        self.assertTrue(Drone.objects.filter(id=drone.id).exists())

    def test_business_drone_api_should_require_tenant_context(self):
        tenantless_client = self.new_client()
        self.authenticate_client(
            tenantless_client,
            username="drone_live_admin",
            password="pass1234",
        )

        list_response = tenantless_client.get("/api/v1/drones")
        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(list_response.json()["code"], "A0403")

        create_response = tenantless_client.post(
            "/api/v1/drones",
            {
                "code": "DL-NO-TENANT-001",
                "name": "缺少租户上下文无人机",
                "model": "Mavic 3E",
                "serial_no": "DL-NO-TENANT-SN-001",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 403)
        self.assertEqual(create_response.json()["code"], "A0403")

    def test_platform_admin_should_be_blocked_from_business_drone_api_even_with_permission(self):
        platform_role, _ = Role.objects.update_or_create(
            code="platform_admin",
            defaults={"name": "平台管理员", "status": DirectoryStatus.ACTIVE},
        )
        grant_role_permissions(
            platform_role,
            {"drone.view_drone": ScopeType.ALL},
            group_name="drone-live-platform-group",
        )
        platform_user = User.objects.create_user(
            username="drone_live_platform_admin",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        ensure_staff_profile(platform_user, name="平台无人机管理员")
        platform_client = self.new_client()
        self.authenticate_client(
            platform_client,
            username="drone_live_platform_admin",
            password="pass1234",
            tenant_code=self.tenant.code,
        )

        response = platform_client.get("/api/v1/drones")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "A0403")

    def test_cross_tenant_drones_should_be_invisible_and_immutable_over_live_http(self):
        foreign_drone = self._create_drone(
            code="DL-FOREIGN-001",
            status=DroneStatus.ENABLED,
            tenant=self.other_tenant,
            created_by_tenant_member_id=self.other_admin_member.id,
        )

        list_response = self.client.get("/api/v1/drones")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["code"], "00000")
        self.assertEqual(list_response.json()["data"]["total"], 0)

        retrieve_response = self.client.get(f"/api/v1/drones/{foreign_drone.id}")
        self.assertEqual(retrieve_response.status_code, 404)
        self.assertEqual(retrieve_response.json()["code"], "C0404")

        patch_response = self.client.patch(
            f"/api/v1/drones/{foreign_drone.id}",
            {"name": "越权更新"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 404)
        self.assertEqual(patch_response.json()["code"], "C0404")

        status_response = self.client.post(f"/api/v1/drones/{foreign_drone.id}/enable")
        self.assertEqual(status_response.status_code, 404)
        self.assertEqual(status_response.json()["code"], "C0404")

        delete_response = self.client.delete(f"/api/v1/drones/{foreign_drone.id}")
        self.assertEqual(delete_response.status_code, 404)
        self.assertEqual(delete_response.json()["code"], "C0404")
        self.assertTrue(Drone.objects.filter(id=foreign_drone.id).exists())

    def test_cross_tenant_assignment_actions_should_return_not_found_over_live_http(self):
        foreign_drone = self._create_drone(
            code="DL-FOREIGN-ASSIGN-001",
            status=DroneStatus.ENABLED,
            tenant=self.other_tenant,
            created_by_tenant_member_id=self.other_admin_member.id,
        )

        history_response = self.client.get(f"/api/v1/drones/{foreign_drone.id}/assignments/history")
        self.assertEqual(history_response.status_code, 404)
        self.assertEqual(history_response.json()["code"], "C0404")

        active_response = self.client.get(f"/api/v1/drones/{foreign_drone.id}/assignments/active")
        self.assertEqual(active_response.status_code, 404)
        self.assertEqual(active_response.json()["code"], "C0404")

        latest_response = self.client.get(f"/api/v1/drones/{foreign_drone.id}/assignments/latest")
        self.assertEqual(latest_response.status_code, 404)
        self.assertEqual(latest_response.json()["code"], "C0404")

    def test_cross_tenant_duplicate_code_and_serial_should_be_allowed(self):
        self._create_drone(
            code="DL-CROSS-TENANT-CODE",
            serial_no="DL-CROSS-TENANT-SN",
            status=DroneStatus.ENABLED,
            tenant=self.other_tenant,
            created_by_tenant_member_id=self.other_admin_member.id,
        )

        response = self.client.post(
            "/api/v1/drones",
            {
                "code": "DL-CROSS-TENANT-CODE",
                "name": "当前租户同编码无人机",
                "model": "Matrice 4T",
                "serial_no": "DL-CROSS-TENANT-SN",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["code"], "00000")
        self.assertEqual(response.json()["data"]["code"], "DL-CROSS-TENANT-CODE")
        self.assertEqual(response.json()["data"]["serial_no"], "DL-CROSS-TENANT-SN")
        self.assertEqual(Drone.objects.filter(code="DL-CROSS-TENANT-CODE").count(), 2)
        self.assertEqual(Drone.objects.filter(serial_no="DL-CROSS-TENANT-SN").count(), 2)


class LiveDronePilotScopeTests(LiveDroneApiTestCase):
    def test_assigned_scope_pilot_should_only_list_and_retrieve_own_drones(self):
        assigned_drone = self._create_drone(code="DL-ASSIGNED-001", status=DroneStatus.ENABLED)
        unassigned_drone = self._create_drone(code="DL-ASSIGNED-002", status=DroneStatus.ENABLED)
        self._create_assignment(drone=assigned_drone, tenant_member=self.pilot_member, status=DroneAssignmentStatus.ACTIVE)

        list_response = self.pilot_client.get("/api/v1/drones")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["code"], "00000")
        self.assertEqual(list_response.json()["data"]["total"], 1)
        self.assertEqual(list_response.json()["data"]["list"][0]["id"], assigned_drone.id)

        retrieve_response = self.pilot_client.get(f"/api/v1/drones/{assigned_drone.id}")
        self.assertEqual(retrieve_response.status_code, 200)
        self.assertEqual(retrieve_response.json()["data"]["id"], assigned_drone.id)

        other_detail_response = self.pilot_client.get(f"/api/v1/drones/{unassigned_drone.id}")
        self.assertEqual(other_detail_response.status_code, 404)
        self.assertEqual(other_detail_response.json()["code"], "C0404")

    def test_assigned_scope_pilot_should_only_query_assignment_views_for_own_drones(self):
        assigned_drone = self._create_drone(code="DL-ASSIGNED-VIEW-001", status=DroneStatus.ENABLED)
        unassigned_drone = self._create_drone(code="DL-ASSIGNED-VIEW-002", status=DroneStatus.ENABLED)
        active_assignment = self._create_assignment(
            drone=assigned_drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.ACTIVE,
        )
        latest_assignment = self._create_assignment(
            drone=assigned_drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.INACTIVE,
        )

        history_response = self.pilot_client.get(f"/api/v1/drones/{assigned_drone.id}/assignments/history")
        self.assertEqual(history_response.status_code, 200)
        self.assertEqual(history_response.json()["code"], "00000")
        self.assertEqual(history_response.json()["data"]["drone_id"], assigned_drone.id)
        self.assertEqual(history_response.json()["data"]["total"], 2)

        active_response = self.pilot_client.get(f"/api/v1/drones/{assigned_drone.id}/assignments/active")
        self.assertEqual(active_response.status_code, 200)
        self.assertEqual(active_response.json()["code"], "00000")
        self.assertEqual(active_response.json()["data"]["drone_id"], assigned_drone.id)
        self.assertEqual(active_response.json()["data"]["total"], 1)
        self.assertEqual(active_response.json()["data"]["list"][0]["id"], active_assignment.id)

        latest_response = self.pilot_client.get(f"/api/v1/drones/{assigned_drone.id}/assignments/latest")
        self.assertEqual(latest_response.status_code, 200)
        self.assertEqual(latest_response.json()["code"], "00000")
        self.assertTrue(latest_response.json()["data"]["has_record"])
        self.assertEqual(latest_response.json()["data"]["result"]["id"], latest_assignment.id)

        other_history_response = self.pilot_client.get(f"/api/v1/drones/{unassigned_drone.id}/assignments/history")
        self.assertEqual(other_history_response.status_code, 404)
        self.assertEqual(other_history_response.json()["code"], "C0404")

        other_active_response = self.pilot_client.get(f"/api/v1/drones/{unassigned_drone.id}/assignments/active")
        self.assertEqual(other_active_response.status_code, 404)
        self.assertEqual(other_active_response.json()["code"], "C0404")

        other_latest_response = self.pilot_client.get(f"/api/v1/drones/{unassigned_drone.id}/assignments/latest")
        self.assertEqual(other_latest_response.status_code, 404)
        self.assertEqual(other_latest_response.json()["code"], "C0404")


class LiveDroneOwnScopeTests(LiveDroneApiTestCase):
    def setUp(self):
        super().setUp()
        grant_role_permissions(
            self.admin_role,
            {
                "drone.view_drone": ScopeType.OWN,
                "drone.manage_drone": ScopeType.OWN,
                "drone.change_drone_status": ScopeType.OWN,
            },
            group_name="drone-live-own-scope-group",
        )
        self.own_drone = self._create_drone(
            code="DL-OWN-001",
            status=DroneStatus.ENABLED,
            created_by_tenant_member_id=self.admin_member.id,
        )
        self.other_drone = self._create_drone(
            code="DL-OWN-002",
            status=DroneStatus.ENABLED,
            created_by_tenant_member_id=self.pilot_member.id,
        )
        self.own_active_assignment = self._create_assignment(
            drone=self.own_drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.ACTIVE,
        )
        self.own_latest_assignment = self._create_assignment(
            drone=self.own_drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.INACTIVE,
        )

    def test_own_scope_operator_should_only_list_retrieve_and_mutate_owned_drones(self):
        list_response = self.client.get("/api/v1/drones")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["code"], "00000")
        self.assertEqual(list_response.json()["data"]["total"], 1)
        self.assertEqual(list_response.json()["data"]["list"][0]["id"], self.own_drone.id)

        retrieve_response = self.client.get(f"/api/v1/drones/{self.own_drone.id}")
        self.assertEqual(retrieve_response.status_code, 200)
        self.assertEqual(retrieve_response.json()["data"]["id"], self.own_drone.id)

        other_detail_response = self.client.get(f"/api/v1/drones/{self.other_drone.id}")
        self.assertEqual(other_detail_response.status_code, 404)
        self.assertEqual(other_detail_response.json()["code"], "C0404")

        patch_response = self.client.patch(
            f"/api/v1/drones/{self.own_drone.id}",
            {"name": "OWN 范围更新后的无人机"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["code"], "00000")
        self.assertEqual(patch_response.json()["data"]["name"], "OWN 范围更新后的无人机")

        other_patch_response = self.client.patch(
            f"/api/v1/drones/{self.other_drone.id}",
            {"name": "越权更新无人机"},
            format="json",
        )
        self.assertEqual(other_patch_response.status_code, 404)
        self.assertEqual(other_patch_response.json()["code"], "C0404")

        disable_response = self.client.post(f"/api/v1/drones/{self.own_drone.id}/disable")
        self.assertEqual(disable_response.status_code, 200)
        self.assertEqual(disable_response.json()["code"], "00000")
        self.assertEqual(disable_response.json()["data"]["status"], DroneStatus.DISABLED)

        other_disable_response = self.client.post(f"/api/v1/drones/{self.other_drone.id}/disable")
        self.assertEqual(other_disable_response.status_code, 404)
        self.assertEqual(other_disable_response.json()["code"], "C0404")

    def test_own_scope_operator_should_only_query_assignment_views_for_owned_drones(self):
        history_response = self.client.get(f"/api/v1/drones/{self.own_drone.id}/assignments/history")
        self.assertEqual(history_response.status_code, 200)
        self.assertEqual(history_response.json()["code"], "00000")
        self.assertEqual(history_response.json()["data"]["drone_id"], self.own_drone.id)
        self.assertEqual(history_response.json()["data"]["total"], 2)

        active_response = self.client.get(f"/api/v1/drones/{self.own_drone.id}/assignments/active")
        self.assertEqual(active_response.status_code, 200)
        self.assertEqual(active_response.json()["code"], "00000")
        self.assertEqual(active_response.json()["data"]["drone_id"], self.own_drone.id)
        self.assertEqual(active_response.json()["data"]["total"], 1)
        self.assertEqual(active_response.json()["data"]["list"][0]["id"], self.own_active_assignment.id)

        latest_response = self.client.get(f"/api/v1/drones/{self.own_drone.id}/assignments/latest")
        self.assertEqual(latest_response.status_code, 200)
        self.assertEqual(latest_response.json()["code"], "00000")
        self.assertTrue(latest_response.json()["data"]["has_record"])
        self.assertEqual(latest_response.json()["data"]["result"]["id"], self.own_latest_assignment.id)

        other_history_response = self.client.get(f"/api/v1/drones/{self.other_drone.id}/assignments/history")
        self.assertEqual(other_history_response.status_code, 404)
        self.assertEqual(other_history_response.json()["code"], "C0404")

        other_active_response = self.client.get(f"/api/v1/drones/{self.other_drone.id}/assignments/active")
        self.assertEqual(other_active_response.status_code, 404)
        self.assertEqual(other_active_response.json()["code"], "C0404")

        other_latest_response = self.client.get(f"/api/v1/drones/{self.other_drone.id}/assignments/latest")
        self.assertEqual(other_latest_response.status_code, 404)
        self.assertEqual(other_latest_response.json()["code"], "C0404")
