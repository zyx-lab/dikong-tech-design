from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import (
    AuditLog,
    GroupPermissionScope,
    ScopeStatus,
    ScopeType,
    StaffProfile,
    StaffType,
    StaffTypeGroup,
    SystemRole,
    Tenant,
    TenantMember,
    TenantMemberStatus,
    TenantStatus,
)
from apps.access.services import AuthorizationReason, AuthzService

User = get_user_model()


class DummyOwnedObject:
    def __init__(self, created_by_staff_id):
        self.created_by_staff_id = created_by_staff_id


class AuthzServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u1", password="pass1234", status=1)
        self.staff_type = StaffType.objects.create(code="dispatcher", name="Dispatcher", status=1)
        self.staff = StaffProfile.objects.create(
            user=self.user,
            staff_no="S001",
            name="Alice",
            employment_status=1,
            staff_type=self.staff_type,
        )

        self.group = Group.objects.create(name="cap_staff_self")
        StaffTypeGroup.objects.create(staff_type=self.staff_type, group=self.group, status=ScopeStatus.ACTIVE)

        self.permission = Permission.objects.get(content_type__app_label="auth", codename="view_group")
        self.group.permissions.add(self.permission)

    def test_authorize_with_own_scope_success(self):
        GroupPermissionScope.objects.create(
            group=self.group,
            permission=self.permission,
            scope_type=ScopeType.OWN,
            status=ScopeStatus.ACTIVE,
        )

        obj = DummyOwnedObject(created_by_staff_id=self.staff.id)
        decision = AuthzService.authorize(self.user, "auth.view_group", obj=obj)

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.scope, ScopeType.OWN)

    def test_authorize_denied_when_scope_missing(self):
        decision = AuthzService.authorize(self.user, "auth.view_group")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, AuthorizationReason.SCOPE_NOT_CONFIGURED)


class AuthzApiSmokeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="admin", password="pass1234", status=1)

        self.staff_type = StaffType.objects.create(code="ops_admin", name="Admin管理员", status=1)
        StaffProfile.objects.create(
            user=self.user,
            staff_no="S002",
            name="Bob",
            employment_status=1,
            staff_type=self.staff_type,
        )

        auth_group = Group.objects.create(name="cap_auth_admin")
        StaffTypeGroup.objects.create(staff_type=self.staff_type, group=auth_group, status=ScopeStatus.ACTIVE)

        manage_group_perm = Permission.objects.get(content_type__app_label="access", codename="manage_auth_groups")
        manage_user_perm = Permission.objects.get(content_type__app_label="access", codename="manage_user_accounts")
        auth_group.permissions.add(manage_group_perm, manage_user_perm)
        GroupPermissionScope.objects.create(
            group=auth_group,
            permission=manage_group_perm,
            scope_type=ScopeType.ALL,
            status=ScopeStatus.ACTIVE,
        )
        GroupPermissionScope.objects.create(
            group=auth_group,
            permission=manage_user_perm,
            scope_type=ScopeType.ALL,
            status=ScopeStatus.ACTIVE,
        )

        self.client.force_authenticate(self.user)

    def test_group_list_api(self):
        response = self.client.get("/internal/auth/groups")
        self.assertEqual(response.status_code, 200)

    def test_user_create_api(self):
        payload = {
            "username": "new_user",
            "password": "pass1234",
            "status": 1,
            "is_active": True,
            "is_staff": False,
            "staff": {
                "staff_no": "S003",
                "name": "Chris",
                "employment_status": 1,
                "staff_type": self.staff_type.id,
            },
        }
        response = self.client.post("/internal/auth/users", payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["username"], "new_user")
        self.assertEqual(response.data["staff"]["staff_no"], "S003")

    def test_user_create_without_staff_should_fail_for_non_superuser(self):
        payload = {
            "username": "new_user_no_staff",
            "password": "pass1234",
            "status": 1,
            "is_active": True,
            "is_staff": False,
        }
        response = self.client.post("/internal/auth/users", payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("staff", response.data)

    def test_user_create_with_is_superuser_should_be_rejected(self):
        payload = {
            "username": "try_super_from_api",
            "password": "pass1234",
            "status": 1,
            "is_active": True,
            "is_staff": True,
            "is_superuser": True,
            "staff": {
                "staff_no": "S910",
                "name": "Should Fail",
                "employment_status": 1,
                "staff_type": self.staff_type.id,
            },
        }
        response = self.client.post("/internal/auth/users", payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("is_superuser", response.data)

    def test_superuser_update_with_staff_payload_should_fail(self):
        target = User.objects.create_superuser(username="root1", password="pass1234")
        payload = {
            "staff": {
                "staff_no": "S900",
                "name": "Root Staff",
                "employment_status": 1,
                "staff_type": self.staff_type.id,
            }
        }
        response = self.client.patch(f"/internal/auth/users/{target.id}", payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("staff", response.data)

    def test_non_superuser_cannot_clear_staff(self):
        target = User.objects.create_user(username="u_clear_staff", password="pass1234", status=1)
        StaffProfile.objects.create(
            user=target,
            staff_no="S901",
            name="Clear Staff",
            employment_status=1,
            staff_type=self.staff_type,
        )
        response = self.client.patch(f"/internal/auth/users/{target.id}", {"staff": None}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("staff", response.data)


class MeTenantListAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="tenant_user", password="pass1234", status=1)
        self.role_admin = SystemRole.objects.create(code="tenant_admin", name="租户管理员", status=1)
        self.role_planner = SystemRole.objects.create(code="route_planner", name="航线规划员", status=1)

        self.tenant_a = Tenant.objects.create(code="tenant_a", name="租户A", status=TenantStatus.ENABLED)
        self.tenant_b = Tenant.objects.create(code="tenant_b", name="租户B", status=TenantStatus.ENABLED)
        self.tenant_pending = Tenant.objects.create(code="tenant_p", name="租户P", status=TenantStatus.ENABLED)

        member_a = TenantMember.objects.create(
            tenant=self.tenant_a,
            user=self.user,
            display_name="张三",
            status=TenantMemberStatus.ACTIVE,
            joined_at=timezone.now(),
        )
        member_b = TenantMember.objects.create(
            tenant=self.tenant_b,
            user=self.user,
            display_name="张三",
            status=TenantMemberStatus.ACTIVE,
            joined_at=timezone.now(),
        )
        TenantMember.objects.create(
            tenant=self.tenant_pending,
            user=self.user,
            display_name="张三",
            status=TenantMemberStatus.PENDING,
        )

        member_a.role_bindings.create(system_role=self.role_admin, status=1)
        member_b.role_bindings.create(system_role=self.role_planner, status=1)

    def test_auto__case_me_tenants_success(self):
        self.client.force_authenticate(self.user)

        response = self.client.get("/internal/auth/me/tenants")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["username"], self.user.username)
        self.assertEqual(response.data["default_tenant"], self.tenant_a.id)
        self.assertEqual(
            response.data["tenants"],
            [
                {
                    "tenant_id": self.tenant_a.id,
                    "tenant_code": "tenant_a",
                    "tenant_name": "租户A",
                    "roles": ["tenant_admin"],
                },
                {
                    "tenant_id": self.tenant_b.id,
                    "tenant_code": "tenant_b",
                    "tenant_name": "租户B",
                    "roles": ["route_planner"],
                },
            ],
        )

    def test_auto__case_me_tenants_permission_denied(self):
        response = self.client.get("/internal/auth/me/tenants")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_auto__case_me_tenants_empty(self):
        other_user = User.objects.create_user(username="tenant_user_empty", password="pass1234", status=1)
        self.client.force_authenticate(other_user)

        response = self.client.get("/internal/auth/me/tenants")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["tenants"], [])
        self.assertIsNone(response.data["default_tenant"])


class SuperuserRootPolicyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.superuser = User.objects.create_superuser(username="root_all", password="pass1234")
        self.client.force_authenticate(self.superuser)

    def test_me_permissions_should_return_all_for_superuser(self):
        response = self.client.get("/internal/auth/me/permissions")
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data["items"]), 0)
        self.assertTrue(all(item["scope"] == "ALL" for item in response.data["items"]))
        self.assertTrue(all(item["enabled"] is True for item in response.data["items"]))


class SeedRolePermissionsCommandTests(TestCase):
    def test_seed_role_permissions_replace_mode(self):
        call_command("seed_role_permissions")

        ops_admin = StaffType.objects.get(code="ops_admin")
        self.assertEqual(
            set(ops_admin.group_links.filter(status=ScopeStatus.ACTIVE).values_list("group__name", flat=True)),
            {"权限策略管理组", "账号与人员查看组", "审计日志只读组", "无人机管理组"},
        )

        dispatcher = StaffType.objects.get(code="dispatcher")
        self.assertEqual(
            set(dispatcher.group_links.filter(status=ScopeStatus.ACTIVE).values_list("group__name", flat=True)),
            {"账号与人员查看组", "无人机全量查看组", "无人机分配管理组"},
        )

        pilot_operator = StaffType.objects.get(code="pilot_operator")
        self.assertEqual(
            set(pilot_operator.group_links.filter(status=ScopeStatus.ACTIVE).values_list("group__name", flat=True)),
            {"员工自助访问组", "无人机按分配查看组"},
        )

        business_admin = StaffType.objects.get(code="business_admin")
        self.assertEqual(
            set(
                business_admin.group_links.filter(status=ScopeStatus.ACTIVE).values_list("group__name", flat=True)
            ),
            {"业务管理员权限组"},
        )


class CreateBusinessAdminAccountCommandTests(TestCase):
    def test_create_business_admin_account(self):
        call_command("seed_role_permissions")
        call_command(
            "create_business_admin_account",
            "--username",
            "biz_root",
            "--password",
            "pass1234",
            "--staff-no",
            "BS-001",
            "--name",
            "业务管理员A",
        )

        user = User.objects.get(username="biz_root")
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertEqual(user.status, 1)

        staff = StaffProfile.objects.get(user=user)
        self.assertEqual(staff.staff_no, "BS-001")
        self.assertEqual(staff.name, "业务管理员A")
        self.assertEqual(staff.staff_type.code, "business_admin")


class UserPermissionPolicyTests(TestCase):
    def test_direct_user_permissions_are_blocked(self):
        user = User.objects.create_user(username="u2", password="pass1234", status=1)
        perm = Permission.objects.get(content_type__app_label="access", codename="manage_auth_groups")
        with self.assertRaises(PermissionDenied):
            user.user_permissions.add(perm)

    def test_direct_user_groups_are_blocked(self):
        user = User.objects.create_user(username="u3", password="pass1234", status=1)
        group = Group.objects.create(name="cap_x")
        with self.assertRaises(PermissionDenied):
            user.groups.add(group)

    def test_superuser_cannot_bind_staff_profile(self):
        staff_type = StaffType.objects.create(code="policy_test", name="策略测试岗位", status=1)
        root = User.objects.create_superuser(username="root_policy", password="pass1234")
        with self.assertRaises(ValidationError):
            StaffProfile.objects.create(
                user=root,
                staff_no="POL-001",
                name="Root Policy",
                employment_status=1,
                staff_type=staff_type,
            )


class TenantAPITests(TestCase):
    """Tenant 创建 API 测试"""

    def setUp(self):
        self.client = APIClient()
        # 使用 superuser 通过权限验证
        self.user = User.objects.create_superuser(username="tenant_admin", password="pass1234")
        self.client.force_authenticate(user=self.user)

    def test_auto__case_tenant_create_success(self):
        """创建租户成功"""
        response = self.client.post(
            "/internal/auth/tenants",
            {"code": "test_tenant", "name": "测试租户"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["code"], "test_tenant")
        self.assertEqual(response.data["name"], "测试租户")
        self.assertEqual(response.data["status"], TenantStatus.ENABLED)
        # 验证数据库
        tenant = Tenant.objects.get(code="test_tenant")
        self.assertEqual(tenant.name, "测试租户")

    def test_auto__case_tenant_create_invalid_params(self):
        """创建租户参数非法 - 缺少必填字段"""
        response = self.client.post("/internal/auth/tenants", {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")

    def test_auto__case_tenant_create_permission_denied(self):
        """创建租户无权限"""
        # 未认证用户
        client = APIClient()
        response = client.post(
            "/internal/auth/tenants",
            {"code": "test_tenant", "name": "测试租户"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_auto__case_tenant_create_duplicate(self):
        """创建租户编码重复"""
        Tenant.objects.create(code="existing", name="已存在")
        response = self.client.post(
            "/internal/auth/tenants",
            {"code": "existing", "name": "重复"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")


class TenantListAPITests(TestCase):
    """租户列表 API 测试"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(username="tenant_admin", password="pass1234")
        self.client.force_authenticate(user=self.user)
        # 创建测试租户
        Tenant.objects.create(code="tenant1", name="租户1")
        Tenant.objects.create(code="tenant2", name="租户2")

    def test_auto__case_tenant_list_success(self):
        """获取租户列表成功"""
        response = self.client.get("/internal/auth/tenants")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["count"], 2)
        self.assertIn("data", response.data)

    def test_auto__case_tenant_list_permission_denied(self):
        """获取租户列表权限拒绝"""
        client = APIClient()  # 未认证
        response = client.get("/internal/auth/tenants")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")


class TenantDetailAPITests(TestCase):
    """租户详情 API 测试"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(username="tenant_admin", password="pass1234")
        self.client.force_authenticate(user=self.user)
        self.tenant = Tenant.objects.create(code="tenant1", name="租户1")

    def test_auto__case_tenant_detail_success(self):
        """获取租户详情成功"""
        response = self.client.get(f"/internal/auth/tenants/{self.tenant.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["data"]["code"], "tenant1")

    def test_auto__case_tenant_detail_not_found(self):
        """获取租户详情不存在"""
        response = self.client.get("/internal/auth/tenants/99999")
        self.assertEqual(response.status_code, 404)


class TenantMemberInviteAPITests(TestCase):
    """租户成员邀请 API 测试"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(username="tenant_inviter", password="pass1234")
        self.client.force_authenticate(user=self.user)
        self.tenant = Tenant.objects.create(code="tenant-a", name="租户A", status=TenantStatus.ENABLED)
        self.target_user = User.objects.create_user(username="tenant_member_u1", password="pass1234", status=1)
        self.role = SystemRole.objects.create(code="pilot_operator", name="飞手", status=1)

    def test_auto__case_tenant_member_invite_success(self):
        response = self.client.post(
            "/internal/auth/tenant-members/invite",
            {
                "tenant_id": self.tenant.id,
                "user_id": self.target_user.id,
                "display_name": "张三",
                "roles": ["pilot_operator"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["message"], "邀请发送成功")

        member = TenantMember.objects.get(tenant=self.tenant, user=self.target_user)
        self.assertEqual(member.status, TenantMemberStatus.PENDING)
        self.assertIsNone(member.joined_at)
        self.assertTrue(member.invitation_token)
        self.assertEqual(list(member.role_bindings.values_list("system_role__code", flat=True)), ["pilot_operator"])

        audit_log = AuditLog.objects.get(action="TENANT_MEMBER_INVITE", target_id=str(member.id))
        self.assertEqual(audit_log.tenant_id, self.tenant.id)

    def test_auto__case_tenant_member_invite_invalid_params(self):
        response = self.client.post(
            "/internal/auth/tenant-members/invite",
            {
                "user_id": self.target_user.id,
                "display_name": "张三",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")

    def test_auto__case_tenant_member_invite_permission_denied(self):
        client = APIClient()
        response = client.post(
            "/internal/auth/tenant-members/invite",
            {
                "tenant_id": self.tenant.id,
                "user_id": self.target_user.id,
                "display_name": "张三",
                "roles": ["pilot_operator"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_auto__case_tenant_member_invite_resource_not_found(self):
        response = self.client.post(
            "/internal/auth/tenant-members/invite",
            {
                "tenant_id": 99999,
                "user_id": self.target_user.id,
                "display_name": "张三",
                "roles": ["pilot_operator"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "TENANT_NOT_FOUND")

    def test_auto__case_tenant_member_invite_state_conflict(self):
        TenantMember.objects.create(
            tenant=self.tenant,
            user=self.target_user,
            display_name="张三",
            status=TenantMemberStatus.ACTIVE,
        )

        response = self.client.post(
            "/internal/auth/tenant-members/invite",
            {
                "tenant_id": self.tenant.id,
                "user_id": self.target_user.id,
                "display_name": "张三",
                "roles": ["pilot_operator"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "STATE_CONFLICT")
        self.assertEqual(response.data["business_detail_code"], "TENANT_MEMBER_EXISTS")


class TenantMemberConfirmInvitationAPITests(TestCase):
    """租户成员确认邀请 API 测试"""

    def setUp(self):
        self.client = APIClient()
        self.invited_user = User.objects.create_user(username="tenant_member_u2", password="pass1234", status=1)
        self.other_user = User.objects.create_user(username="tenant_member_u3", password="pass1234", status=1)
        self.tenant = Tenant.objects.create(code="tenant-b", name="租户B", status=TenantStatus.ENABLED)
        self.role = SystemRole.objects.create(code="route_planner", name="航线规划员", status=1)
        self.member = TenantMember.objects.create(
            tenant=self.tenant,
            user=self.invited_user,
            display_name="李四",
            invitation_token="token-confirm-001",
            status=TenantMemberStatus.PENDING,
        )
        self.member.role_bindings.create(system_role=self.role, status=1)

    def test_auto__case_tenant_member_confirm_invitation_success(self):
        self.client.force_authenticate(user=self.invited_user)
        response = self.client.post(
            "/internal/auth/tenant-members/confirm-invitation",
            {"invitation_token": "token-confirm-001"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["member_id"], self.member.id)
        self.assertEqual(response.data["roles"], ["route_planner"])

        self.member.refresh_from_db()
        self.assertEqual(self.member.status, TenantMemberStatus.ACTIVE)
        self.assertIsNotNone(self.member.joined_at)

        audit_log = AuditLog.objects.get(action="TENANT_MEMBER_CONFIRM_INVITATION", target_id=str(self.member.id))
        self.assertEqual(audit_log.tenant_id, self.tenant.id)
        self.assertEqual(audit_log.actor_user_id, self.invited_user.id)

    def test_auto__case_tenant_member_confirm_invitation_invalid_params(self):
        self.client.force_authenticate(user=self.invited_user)
        response = self.client.post(
            "/internal/auth/tenant-members/confirm-invitation",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")

    def test_auto__case_tenant_member_confirm_invitation_permission_denied(self):
        response = self.client.post(
            "/internal/auth/tenant-members/confirm-invitation",
            {"invitation_token": "token-confirm-001"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_auto__case_tenant_member_confirm_invitation_wrong_user(self):
        self.client.force_authenticate(user=self.other_user)
        response = self.client.post(
            "/internal/auth/tenant-members/confirm-invitation",
            {"invitation_token": "token-confirm-001"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "INVITATION_NOT_ALLOWED")

    def test_auto__case_tenant_member_confirm_invitation_not_found(self):
        self.client.force_authenticate(user=self.invited_user)
        response = self.client.post(
            "/internal/auth/tenant-members/confirm-invitation",
            {"invitation_token": "missing-token"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "INVITATION_NOT_FOUND")

    def test_auto__case_tenant_member_confirm_invitation_duplicate(self):
        self.client.force_authenticate(user=self.invited_user)
        self.member.status = TenantMemberStatus.ACTIVE
        self.member.joined_at = timezone.now()
        self.member.save(update_fields=["status", "joined_at", "updated_at"])

        response = self.client.post(
            "/internal/auth/tenant-members/confirm-invitation",
            {"invitation_token": "token-confirm-001"},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(response.data["business_detail_code"], "INVITATION_ALREADY_CONFIRMED")

    def test_auto__case_tenant_member_confirm_invitation_state_conflict(self):
        self.client.force_authenticate(user=self.invited_user)
        self.member.status = TenantMemberStatus.DISABLED
        self.member.save(update_fields=["status", "updated_at"])

        response = self.client.post(
            "/internal/auth/tenant-members/confirm-invitation",
            {"invitation_token": "token-confirm-001"},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "STATE_CONFLICT")
        self.assertEqual(response.data["business_detail_code"], "INVITATION_STATUS_INVALID")
