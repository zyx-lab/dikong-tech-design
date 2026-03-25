"""Drone assignment live HTTP test events.

- 调度员通过真实 HTTP 完成分配创建/读取/取消/恢复
- business drone-assignment API 强制要求有效 Bearer + X-TENANT-CODE
- 恢复动作拒绝 body
- ACTIVE 分配对重复创建返回冲突
- platform_admin 即使权限矩阵放开也禁止访问租户业务接口
"""

from django.utils import timezone

from apps.access.models import AuditLog, DirectoryStatus, EmploymentStatus, Role, ScopeType, TenantMemberStatus
from apps.access.test_live_base import LiveIamApiTestCase, User
from apps.access.test_support import (
    ensure_staff_profile,
    ensure_tenant_member_position,
    ensure_tenant_role_binding,
    grant_role_permissions,
)
from apps.drone.models import Drone, DroneStatus
from apps.drone_assignment.models import DroneAssignment, DroneAssignmentStatus


class LiveDroneAssignmentApiTestCase(LiveIamApiTestCase):
    def setUp(self):
        super().setUp()
        self.dispatcher_user = User.objects.create_user(username="drone_assignment_live_dispatcher", password="pass1234", status=1)
        ensure_staff_profile(
            self.dispatcher_user,
            staff_no="DAL-001",
            name="实时分配调度员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.tenant, self.dispatcher_member, self.dispatcher_role = ensure_tenant_role_binding(
            self.dispatcher_user,
            tenant_code="drone_assignment_live_tenant",
            role_code="drone_assignment_live_dispatcher_role",
            role_name="实时分配调度角色",
        )
        grant_role_permissions(
            self.dispatcher_role,
            {"drone_assignment.manage_drone_assignment": ScopeType.ALL},
            group_name="drone-assignment-live-dispatcher-group",
        )

        self.other_dispatcher_user = User.objects.create_user(username="drone_assignment_live_other_dispatcher", password="pass1234", status=1)
        ensure_staff_profile(
            self.other_dispatcher_user,
            staff_no="DAL-OTHER-001",
            name="其他租户分配调度员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        self.other_tenant, self.other_dispatcher_member, self.other_dispatcher_role = ensure_tenant_role_binding(
            self.other_dispatcher_user,
            tenant_code="drone_assignment_live_other_tenant",
            role_code="drone_assignment_live_other_dispatcher_role",
            role_name="其他租户实时分配调度角色",
        )
        grant_role_permissions(
            self.other_dispatcher_role,
            {"drone_assignment.manage_drone_assignment": ScopeType.ALL},
            group_name="drone-assignment-live-other-dispatcher-group",
        )

        self.pilot_user = User.objects.create_user(username="drone_assignment_live_pilot", password="pass1234", status=1)
        ensure_staff_profile(
            self.pilot_user,
            staff_no="DAL-P-001",
            name="实时分配飞手",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, self.pilot_member, _pilot_role = ensure_tenant_role_binding(
            self.pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手操作员",
            member_no="DAL-P-001",
        )
        ensure_tenant_member_position(self.pilot_member, code="pilot_operator", name="飞手操作员")

        self.other_tenant_pilot_user = User.objects.create_user(username="drone_assignment_live_other_tenant_pilot", password="pass1234", status=1)
        ensure_staff_profile(
            self.other_tenant_pilot_user,
            staff_no="DAL-OTHER-P-001",
            name="其他租户分配飞手",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, self.other_tenant_pilot_member, _other_tenant_pilot_role = ensure_tenant_role_binding(
            self.other_tenant_pilot_user,
            tenant=self.other_tenant,
            role_code="pilot_operator",
            role_name="飞手操作员",
            member_no="DAL-OTHER-P-001",
        )
        ensure_tenant_member_position(self.other_tenant_pilot_member, code="pilot_operator", name="飞手操作员")

        self.drone = Drone.objects.create(
            tenant=self.tenant,
            code="DAL-DRONE-001",
            name="实时分配无人机",
            model="Matrice 300",
            serial_no="DAL-DRONE-SN-001",
            status=DroneStatus.ENABLED,
        )
        self.secondary_drone = Drone.objects.create(
            tenant=self.tenant,
            code="DAL-DRONE-002",
            name="实时分配无人机2",
            model="Matrice 30",
            serial_no="DAL-DRONE-SN-002",
            status=DroneStatus.ENABLED,
        )
        self.retired_drone = Drone.objects.create(
            tenant=self.tenant,
            code="DAL-DRONE-003",
            name="已退役实时无人机",
            model="Matrice 4",
            serial_no="DAL-DRONE-SN-003",
            status=DroneStatus.RETIRED,
        )
        self.other_tenant_drone = Drone.objects.create(
            tenant=self.other_tenant,
            code="DAL-OTHER-DRONE-001",
            name="其他租户实时分配无人机",
            model="Matrice 350",
            serial_no="DAL-OTHER-DRONE-SN-001",
            status=DroneStatus.ENABLED,
        )

        self.login(
            username="drone_assignment_live_dispatcher",
            password="pass1234",
            tenant_code=self.tenant.code,
        )

    def _create_assignment(
        self,
        *,
        drone: Drone | None = None,
        tenant_member=None,
        status: str = DroneAssignmentStatus.ACTIVE,
        tenant=None,
        created_by_tenant_member_id: int | None = None,
    ) -> DroneAssignment:
        tenant = tenant or self.tenant
        tenant_member = tenant_member or self.pilot_member
        if created_by_tenant_member_id is None:
            created_by_tenant_member_id = self.dispatcher_member.id if tenant == self.tenant else self.other_dispatcher_member.id
        return DroneAssignment.objects.create(
            tenant=tenant,
            drone=drone or self.drone,
            tenant_member=tenant_member,
            status=status,
            end_at=timezone.now() if status == DroneAssignmentStatus.INACTIVE else None,
            created_by_tenant_member_id=created_by_tenant_member_id,
        )


class LiveDroneAssignmentApiTests(LiveDroneAssignmentApiTestCase):
    def test_assignment_lifecycle_should_follow_live_http_contract(self):
        create_response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "tenant_member": self.pilot_member.id},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.json()["code"], "00000")
        create_data = create_response.json()["data"]
        self.assertEqual(create_data["drone"], self.drone.id)
        self.assertEqual(create_data["tenant_member"], self.pilot_member.id)
        self.assertEqual(create_data["member_no"], "DAL-P-001")
        self.assertEqual(create_data["status"], DroneAssignmentStatus.ACTIVE)
        self.assertEqual(create_data["created_by_tenant_member_id"], self.dispatcher_member.id)
        assignment_id = create_data["id"]

        list_response = self.client.get(
            "/api/v1/drone-assignments",
            {"drone_id": self.drone.id, "status": DroneAssignmentStatus.ACTIVE},
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["data"]["total"], 1)
        self.assertEqual(list_response.json()["data"]["list"][0]["id"], assignment_id)

        detail_response = self.client.get(f"/api/v1/drone-assignments/{assignment_id}")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()["data"]["staff_name"], "实时分配飞手")

        cancel_response = self.client.post(f"/api/v1/drone-assignments/{assignment_id}/cancel")
        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.json()["data"]["status"], DroneAssignmentStatus.INACTIVE)
        self.assertIsNotNone(cancel_response.json()["data"]["end_at"])

        reactivate_response = self.client.post(f"/api/v1/drone-assignments/{assignment_id}/reactivate")
        self.assertEqual(reactivate_response.status_code, 200)
        self.assertEqual(reactivate_response.json()["data"]["status"], DroneAssignmentStatus.ACTIVE)
        self.assertIsNone(reactivate_response.json()["data"]["end_at"])

        assignment = DroneAssignment.objects.get(id=assignment_id)
        self.assertEqual(assignment.status, DroneAssignmentStatus.ACTIVE)
        self.assertIsNone(assignment.end_at)

        for action in ["DRONE_ASSIGNMENT_CREATE", "DRONE_ASSIGNMENT_CANCEL", "DRONE_ASSIGNMENT_REACTIVATE"]:
            self.assertTrue(
                AuditLog.objects.filter(
                    tenant=self.tenant,
                    action=action,
                    target_type="drone_assignment",
                    target_id=str(assignment_id),
                ).exists(),
                action,
            )

    def test_duplicate_active_assignment_should_return_duplicate_code_over_live_http(self):
        self._create_assignment(status=DroneAssignmentStatus.ACTIVE)

        response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "tenant_member": self.pilot_member.id},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "C0101")
        self.assertIn("non_field_errors", response.json()["data"])

    def test_business_drone_assignment_api_should_require_tenant_context(self):
        tenantless_client = self.new_client()
        self.authenticate_client(
            tenantless_client,
            username="drone_assignment_live_dispatcher",
            password="pass1234",
        )

        list_response = tenantless_client.get("/api/v1/drone-assignments")
        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(list_response.json()["code"], "A0403")

        create_response = tenantless_client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "tenant_member": self.pilot_member.id},
            format="json",
        )
        self.assertEqual(create_response.status_code, 403)
        self.assertEqual(create_response.json()["code"], "A0403")

    def test_reactivate_should_reject_body_over_live_http(self):
        assignment = self._create_assignment(status=DroneAssignmentStatus.INACTIVE)

        response = self.client.post(
            f"/api/v1/drone-assignments/{assignment.id}/reactivate",
            {"unexpected": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "B0001")
        self.assertIn("body", response.json()["data"])
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, DroneAssignmentStatus.INACTIVE)

    def test_platform_admin_should_be_blocked_from_business_drone_assignment_api_even_with_permission(self):
        platform_role, _ = Role.objects.update_or_create(
            code="platform_admin",
            defaults={"name": "平台管理员", "status": DirectoryStatus.ACTIVE},
        )
        grant_role_permissions(
            platform_role,
            {"drone_assignment.manage_drone_assignment": ScopeType.ALL},
            group_name="drone-assignment-live-platform-group",
        )
        platform_user = User.objects.create_user(
            username="drone_assignment_live_platform_admin",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        ensure_staff_profile(platform_user, name="平台分配管理员")
        platform_client = self.new_client()
        self.authenticate_client(
            platform_client,
            username="drone_assignment_live_platform_admin",
            password="pass1234",
            tenant_code=self.tenant.code,
        )

        response = platform_client.get("/api/v1/drone-assignments")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "A0403")
        self.assertEqual(response.json()["msg"], "平台管理员不可访问租户业务接口")

    def test_cross_tenant_binding_should_be_rejected_over_live_http(self):
        foreign_drone_response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.other_tenant_drone.id, "tenant_member": self.pilot_member.id},
            format="json",
        )
        self.assertEqual(foreign_drone_response.status_code, 400)
        self.assertEqual(foreign_drone_response.json()["code"], "B0001")
        self.assertIn("drone", foreign_drone_response.json()["data"])

        foreign_member_response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "tenant_member": self.other_tenant_pilot_member.id},
            format="json",
        )
        self.assertEqual(foreign_member_response.status_code, 400)
        self.assertEqual(foreign_member_response.json()["code"], "B0001")
        self.assertIn("tenant_member", foreign_member_response.json()["data"])

    def test_retired_drone_should_not_allow_assignment_over_live_http(self):
        response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.retired_drone.id, "tenant_member": self.pilot_member.id},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "B0001")
        self.assertIn("drone", response.json()["data"])

    def test_cross_tenant_assignment_should_be_invisible_and_unmodifiable_over_live_http(self):
        foreign_assignment = self._create_assignment(
            drone=self.other_tenant_drone,
            tenant_member=self.other_tenant_pilot_member,
            tenant=self.other_tenant,
            status=DroneAssignmentStatus.ACTIVE,
        )

        list_response = self.client.get("/api/v1/drone-assignments")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["code"], "00000")
        self.assertEqual(list_response.json()["data"]["total"], 0)

        detail_response = self.client.get(f"/api/v1/drone-assignments/{foreign_assignment.id}")
        self.assertEqual(detail_response.status_code, 404)
        self.assertEqual(detail_response.json()["code"], "C0404")

        cancel_response = self.client.post(f"/api/v1/drone-assignments/{foreign_assignment.id}/cancel")
        self.assertEqual(cancel_response.status_code, 404)
        self.assertEqual(cancel_response.json()["code"], "C0404")

        reactivate_response = self.client.post(f"/api/v1/drone-assignments/{foreign_assignment.id}/reactivate")
        self.assertEqual(reactivate_response.status_code, 404)
        self.assertEqual(reactivate_response.json()["code"], "C0404")

    def test_reactivate_should_return_state_conflict_when_active_pair_exists_over_live_http(self):
        existing_active = self._create_assignment(
            drone=self.secondary_drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.ACTIVE,
        )
        inactive_assignment = self._create_assignment(
            drone=self.secondary_drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.INACTIVE,
        )

        response = self.client.post(f"/api/v1/drone-assignments/{inactive_assignment.id}/reactivate")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "C0201")
        self.assertEqual(response.json()["data"]["assignment_id"], inactive_assignment.id)
        existing_active.refresh_from_db()
        inactive_assignment.refresh_from_db()
        self.assertEqual(existing_active.status, DroneAssignmentStatus.ACTIVE)
        self.assertEqual(inactive_assignment.status, DroneAssignmentStatus.INACTIVE)

    def test_cancel_and_reactivate_should_be_idempotent_over_live_http(self):
        inactive_assignment = self._create_assignment(
            drone=self.secondary_drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.INACTIVE,
        )
        cancel_response = self.client.post(f"/api/v1/drone-assignments/{inactive_assignment.id}/cancel")
        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.json()["code"], "00000")
        self.assertEqual(cancel_response.json()["data"]["status"], DroneAssignmentStatus.INACTIVE)
        self.assertIsNotNone(cancel_response.json()["data"]["end_at"])

        active_assignment = self._create_assignment(
            drone=self.drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.ACTIVE,
        )
        reactivate_response = self.client.post(f"/api/v1/drone-assignments/{active_assignment.id}/reactivate")
        self.assertEqual(reactivate_response.status_code, 200)
        self.assertEqual(reactivate_response.json()["code"], "00000")
        self.assertEqual(reactivate_response.json()["data"]["status"], DroneAssignmentStatus.ACTIVE)
        self.assertIsNone(reactivate_response.json()["data"]["end_at"])

    def test_assignment_create_should_reject_invalid_member_states_over_live_http(self):
        inactive_user = User.objects.create_user(username="drone_assignment_live_inactive_member", password="pass1234", status=1)
        ensure_staff_profile(
            inactive_user,
            staff_no="DAL-I-001",
            name="非激活成员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, inactive_member, _inactive_role = ensure_tenant_role_binding(
            inactive_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手操作员",
            member_no="DAL-I-001",
        )
        ensure_tenant_member_position(inactive_member, code="pilot_operator", name="飞手操作员")
        inactive_member.status = TenantMemberStatus.DISABLED
        inactive_member.save(update_fields=["status", "responded_at", "joined_at", "updated_at"])

        inactive_response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "tenant_member": inactive_member.id},
            format="json",
        )
        self.assertEqual(inactive_response.status_code, 400)
        self.assertEqual(inactive_response.json()["code"], "B0001")
        self.assertIn("tenant_member", inactive_response.json()["data"])

        no_staff_user = User.objects.create_user(username="drone_assignment_live_no_staff", password="pass1234", status=1)
        _tenant, no_staff_member, _no_staff_role = ensure_tenant_role_binding(
            no_staff_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手操作员",
            member_no="DAL-NS-001",
        )
        ensure_tenant_member_position(no_staff_member, code="pilot_operator", name="飞手操作员")
        no_staff_user.staff_profile.delete()
        no_staff_response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "tenant_member": no_staff_member.id},
            format="json",
        )
        self.assertEqual(no_staff_response.status_code, 400)
        self.assertEqual(no_staff_response.json()["code"], "B0001")
        self.assertIn("tenant_member", no_staff_response.json()["data"])

        inactive_staff_user = User.objects.create_user(
            username="drone_assignment_live_inactive_staff",
            password="pass1234",
            status=1,
        )
        ensure_staff_profile(
            inactive_staff_user,
            staff_no="DAL-IS-001",
            name="离职飞手",
            employment_status=EmploymentStatus.INACTIVE,
        )
        _tenant, inactive_staff_member, _inactive_staff_role = ensure_tenant_role_binding(
            inactive_staff_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手操作员",
            member_no="DAL-IS-001",
        )
        ensure_tenant_member_position(inactive_staff_member, code="pilot_operator", name="飞手操作员")
        inactive_staff_response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "tenant_member": inactive_staff_member.id},
            format="json",
        )
        self.assertEqual(inactive_staff_response.status_code, 400)
        self.assertEqual(inactive_staff_response.json()["code"], "B0001")
        self.assertIn("tenant_member", inactive_staff_response.json()["data"])

        non_pilot_user = User.objects.create_user(username="drone_assignment_live_non_pilot", password="pass1234", status=1)
        ensure_staff_profile(
            non_pilot_user,
            staff_no="DAL-NP-001",
            name="非飞手成员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, non_pilot_member, _non_pilot_role = ensure_tenant_role_binding(
            non_pilot_user,
            tenant=self.tenant,
            role_code="drone_assignment_live_viewer_role",
            role_name="普通成员",
            member_no="DAL-NP-001",
        )
        non_pilot_response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": self.drone.id, "tenant_member": non_pilot_member.id},
            format="json",
        )
        self.assertEqual(non_pilot_response.status_code, 400)
        self.assertEqual(non_pilot_response.json()["code"], "B0001")
        self.assertIn("tenant_member", non_pilot_response.json()["data"])


class LiveDroneAssignmentOwnScopeTests(LiveDroneAssignmentApiTestCase):
    def setUp(self):
        super().setUp()
        grant_role_permissions(
            self.dispatcher_role,
            {"drone_assignment.manage_drone_assignment": ScopeType.OWN},
            group_name="drone-assignment-live-own-scope-group",
        )
        self.same_tenant_other_dispatcher_user = User.objects.create_user(
            username="drone_assignment_live_same_tenant_other_dispatcher",
            password="pass1234",
            status=1,
        )
        ensure_staff_profile(
            self.same_tenant_other_dispatcher_user,
            staff_no="DAL-ST-001",
            name="同租户其他调度员",
            employment_status=EmploymentStatus.ACTIVE,
        )
        _tenant, self.same_tenant_other_dispatcher_member, _same_tenant_other_dispatcher_role = ensure_tenant_role_binding(
            self.same_tenant_other_dispatcher_user,
            tenant=self.tenant,
            role_code="drone_assignment_live_same_tenant_other_dispatcher_role",
            role_name="同租户其他调度角色",
            member_no="DAL-ST-001",
        )

    def test_own_scope_dispatcher_should_list_retrieve_and_create_owned_assignments(self):
        own_assignment = self._create_assignment(
            drone=self.drone,
            tenant_member=self.pilot_member,
            created_by_tenant_member_id=self.dispatcher_member.id,
        )
        other_assignment = self._create_assignment(
            drone=self.secondary_drone,
            tenant_member=self.pilot_member,
            created_by_tenant_member_id=self.same_tenant_other_dispatcher_member.id,
        )

        list_response = self.client.get("/api/v1/drone-assignments")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["code"], "00000")
        self.assertEqual(list_response.json()["data"]["total"], 1)
        self.assertEqual(list_response.json()["data"]["list"][0]["id"], own_assignment.id)

        own_detail_response = self.client.get(f"/api/v1/drone-assignments/{own_assignment.id}")
        self.assertEqual(own_detail_response.status_code, 200)
        self.assertEqual(own_detail_response.json()["data"]["id"], own_assignment.id)

        other_detail_response = self.client.get(f"/api/v1/drone-assignments/{other_assignment.id}")
        self.assertEqual(other_detail_response.status_code, 404)
        self.assertEqual(other_detail_response.json()["code"], "C0404")

        create_drone = Drone.objects.create(
            tenant=self.tenant,
            code="DAL-OWN-CREATE-001",
            name="OWN 范围创建分配无人机",
            model="Matrice 350",
            serial_no="DAL-OWN-CREATE-SN-001",
            status=DroneStatus.ENABLED,
        )
        create_response = self.client.post(
            "/api/v1/drone-assignments",
            {"drone": create_drone.id, "tenant_member": self.pilot_member.id},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.json()["code"], "00000")
        self.assertEqual(create_response.json()["data"]["created_by_tenant_member_id"], self.dispatcher_member.id)

    def test_own_scope_dispatcher_should_only_cancel_owned_assignments(self):
        own_assignment = self._create_assignment(
            drone=self.drone,
            tenant_member=self.pilot_member,
            created_by_tenant_member_id=self.dispatcher_member.id,
        )
        other_assignment = self._create_assignment(
            drone=self.secondary_drone,
            tenant_member=self.pilot_member,
            created_by_tenant_member_id=self.same_tenant_other_dispatcher_member.id,
        )

        own_cancel_response = self.client.post(f"/api/v1/drone-assignments/{own_assignment.id}/cancel")
        self.assertEqual(own_cancel_response.status_code, 200)
        self.assertEqual(own_cancel_response.json()["code"], "00000")
        self.assertEqual(own_cancel_response.json()["data"]["status"], DroneAssignmentStatus.INACTIVE)

        other_cancel_response = self.client.post(f"/api/v1/drone-assignments/{other_assignment.id}/cancel")
        self.assertEqual(other_cancel_response.status_code, 404)
        self.assertEqual(other_cancel_response.json()["code"], "C0404")

    def test_own_scope_dispatcher_should_only_reactivate_owned_assignments(self):
        own_assignment = self._create_assignment(
            drone=self.drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.INACTIVE,
            created_by_tenant_member_id=self.dispatcher_member.id,
        )
        other_assignment = self._create_assignment(
            drone=self.secondary_drone,
            tenant_member=self.pilot_member,
            status=DroneAssignmentStatus.INACTIVE,
            created_by_tenant_member_id=self.same_tenant_other_dispatcher_member.id,
        )

        own_reactivate_response = self.client.post(f"/api/v1/drone-assignments/{own_assignment.id}/reactivate")
        self.assertEqual(own_reactivate_response.status_code, 200)
        self.assertEqual(own_reactivate_response.json()["code"], "00000")
        self.assertEqual(own_reactivate_response.json()["data"]["status"], DroneAssignmentStatus.ACTIVE)

        other_reactivate_response = self.client.post(f"/api/v1/drone-assignments/{other_assignment.id}/reactivate")
        self.assertEqual(other_reactivate_response.status_code, 403)
        self.assertEqual(other_reactivate_response.json()["code"], "A0403")
