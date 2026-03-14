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
    SystemRole,
    SystemRoleGroup,
    Tenant,
    TenantMember,
    TenantMemberStatus,
    TenantStatus,
)
from apps.access.services import AuthorizationReason, AuthzService
from apps.access.test_support import build_request, ensure_staff_profile, ensure_tenant_role_binding, grant_role_permissions

User = get_user_model()


class DummyOwnedObject:
    def __init__(self, created_by_staff_id):
        self.created_by_staff_id = created_by_staff_id


class AuthzServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u1", password="pass1234", status=1)
        self.staff = ensure_staff_profile(
            self.user,
            staff_no="S001",
            name="Alice",
        )
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            role_code="authz_service_test_role",
            role_name="授权服务测试角色",
            tenant_code="authz_service_tenant",
        )
        self.request = build_request(self.user, self.tenant)

    def test_authorize_with_own_scope_success(self):
        grant_role_permissions(self.role, {"auth.view_group": ScopeType.OWN}, group_name="authz-own-group")

        obj = DummyOwnedObject(created_by_staff_id=self.staff.id)
        decision = AuthzService.authorize(self.request, "auth.view_group", obj=obj)

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.scope, ScopeType.OWN)

    def test_authorize_denied_when_scope_missing(self):
        permission = Permission.objects.get(content_type__app_label="auth", codename="view_group")
        group = Group.objects.create(name="authz-no-scope-group")
        group.permissions.add(permission)
        SystemRoleGroup.objects.create(system_role=self.role, group=group, status=ScopeStatus.ACTIVE)

        decision = AuthzService.authorize(self.request, "auth.view_group")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, AuthorizationReason.SCOPE_NOT_CONFIGURED)


class AuthzApiSmokeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="admin", password="pass1234", status=1)
        self.staff = ensure_staff_profile(
            self.user,
            staff_no="S002",
            name="Bob",
        )
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            role_code="iam_admin_test_role",
            role_name="IAM 管理测试角色",
            tenant_code="iam_admin_tenant",
        )
        grant_role_permissions(
            self.role,
            {
                "access.manage_auth_groups": ScopeType.ALL,
                "access.manage_user_accounts": ScopeType.ALL,
            },
            group_name="iam-admin-group",
        )
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

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
        )
        response = self.client.patch(f"/internal/auth/users/{target.id}", {"staff": None}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("staff", response.data)


class UserSelfRegisterAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_auto__case_user_register_success(self):
        response = self.client.post(
            "/internal/auth/users/register",
            {
                "username": "register_user",
                "password": "pass1234",
                "name": "注册用户",
                "phone": "13800138000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["username"], "register_user")

        user = User.objects.get(username="register_user")
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertEqual(user.staff_profile.name, "注册用户")
        self.assertEqual(user.staff_profile.phone, "13800138000")

        audit_log = AuditLog.objects.get(action="USER_REGISTER", target_id=str(user.id))
        self.assertEqual(audit_log.actor_user_id, user.id)
        self.assertIsNone(audit_log.tenant_id)

    def test_auto__case_user_register_invalid_params(self):
        response = self.client.post(
            "/internal/auth/users/register",
            {
                "username": "register_invalid",
                "password": "",
                "name": "注册用户",
                "phone": "",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")

    def test_auto__case_user_register_idempotent_duplicate(self):
        User.objects.create_user(username="duplicate_user", password="pass1234", status=1)

        response = self.client.post(
            "/internal/auth/users/register",
            {
                "username": "duplicate_user",
                "password": "pass1234",
                "name": "重复用户",
                "phone": "13800138001",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(response.data["business_detail_code"], "USERNAME_ALREADY_EXISTS")


class UserPhoneRegisterAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_auto__case_user_phone_register_success(self):
        response = self.client.post(
            "/internal/auth/users/register/by-phone",
            {
                "phone": "13800138002",
                "sms_code": "123456",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["username"], "13800138002")

        user = User.objects.get(username="13800138002")
        self.assertTrue(user.is_active)
        self.assertEqual(user.staff_profile.phone, "13800138002")
        self.assertEqual(user.staff_profile.name, "手机用户8002")

        audit_log = AuditLog.objects.get(action="USER_REGISTER", target_id=str(user.id))
        self.assertEqual(audit_log.after_data["register_channel"], "phone")

    def test_auto__case_user_phone_register_invalid_params(self):
        response = self.client.post(
            "/internal/auth/users/register/by-phone",
            {
                "phone": "13800138003",
                "sms_code": "000000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")

    def test_auto__case_user_phone_register_idempotent_duplicate(self):
        first = self.client.post(
            "/internal/auth/users/register/by-phone",
            {
                "phone": "13800138004",
                "sms_code": "123456",
            },
            format="json",
        )
        self.assertEqual(first.status_code, 201)

        response = self.client.post(
            "/internal/auth/users/register/by-phone",
            {
                "phone": "13800138004",
                "sms_code": "123456",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(response.data["business_detail_code"], "PHONE_ALREADY_EXISTS")


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


class MeInvitationListAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="invite_user", password="pass1234", status=1)
        self.other_user = User.objects.create_user(username="invite_other", password="pass1234", status=1)
        self.role_pilot = SystemRole.objects.create(code="pilot_operator", name="飞手", status=1)
        self.role_planner = SystemRole.objects.create(code="route_planner", name="航线规划员", status=1)

        self.tenant_a = Tenant.objects.create(code="invite_a", name="邀请租户A", status=TenantStatus.ENABLED)
        self.tenant_b = Tenant.objects.create(code="invite_b", name="邀请租户B", status=TenantStatus.ENABLED)
        self.tenant_c = Tenant.objects.create(code="invite_c", name="邀请租户C", status=TenantStatus.ENABLED)

        self.pending_member = TenantMember.objects.create(
            tenant=self.tenant_a,
            user=self.user,
            display_name="张三",
            invitation_token="invite-token-001",
            status=TenantMemberStatus.PENDING,
        )
        self.pending_member.role_bindings.create(system_role=self.role_pilot, status=1)
        self.pending_member.role_bindings.create(system_role=self.role_planner, status=1)

        TenantMember.objects.create(
            tenant=self.tenant_b,
            user=self.user,
            display_name="张三",
            invitation_token=None,
            status=TenantMemberStatus.PENDING,
        )
        TenantMember.objects.create(
            tenant=self.tenant_b,
            user=self.other_user,
            display_name="李四",
            invitation_token="invite-token-002",
            status=TenantMemberStatus.PENDING,
        )
        TenantMember.objects.create(
            tenant=self.tenant_c,
            user=self.user,
            display_name="王五",
            invitation_token="invite-token-003",
            status=TenantMemberStatus.ACTIVE,
            joined_at=timezone.now(),
        )

    def test_auto__case_me_invitations_success(self):
        self.client.force_authenticate(self.user)

        response = self.client.get("/internal/auth/me/invitations")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(
            response.data["items"],
            [
                {
                    "member_id": self.pending_member.id,
                    "tenant_id": self.tenant_a.id,
                    "tenant_code": "invite_a",
                    "tenant_name": "邀请租户A",
                    "display_name": "张三",
                    "roles": ["pilot_operator", "route_planner"],
                    "positions": [],
                    "qualifications": [],
                    "invitation_token": "invite-token-001",
                }
            ],
        )

    def test_auto__case_me_invitations_permission_denied(self):
        response = self.client.get("/internal/auth/me/invitations")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_auto__case_me_invitations_empty(self):
        empty_user = User.objects.create_user(username="invite_empty", password="pass1234", status=1)
        self.client.force_authenticate(empty_user)

        response = self.client.get("/internal/auth/me/invitations")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["items"], [])


class TenantAuditLogListAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="tenant_auditor_user", password="pass1234", status=1)
        self.other_user = User.objects.create_user(username="tenant_auditor_other", password="pass1234", status=1)
        self.role_admin = SystemRole.objects.create(code="tenant_admin", name="租户管理员", status=1)
        self.role_viewer = SystemRole.objects.create(code="route_planner", name="航线规划员", status=1)
        self.tenant_a = Tenant.objects.create(code="tenant_audit_a", name="租户审计A", status=TenantStatus.ENABLED)
        self.tenant_b = Tenant.objects.create(code="tenant_audit_b", name="租户审计B", status=TenantStatus.ENABLED)

        member = TenantMember.objects.create(
            tenant=self.tenant_a,
            user=self.user,
            display_name="审计用户",
            status=TenantMemberStatus.ACTIVE,
            joined_at=timezone.now(),
        )
        member.role_bindings.create(system_role=self.role_admin, status=1)
        grant_role_permissions(
            self.role_admin,
            {"access.view_auth_audit_logs": ScopeType.ALL},
            group_name="tenant-audit-admin-group",
        )

        viewer_member = TenantMember.objects.create(
            tenant=self.tenant_a,
            user=self.other_user,
            display_name="普通成员",
            status=TenantMemberStatus.ACTIVE,
            joined_at=timezone.now(),
        )
        viewer_member.role_bindings.create(system_role=self.role_viewer, status=1)

        self.tenant_log = AuditLog.objects.create(
            tenant=self.tenant_a,
            actor_user=self.user,
            action="TENANT_MEMBER_INVITE",
            target_type="tenant_member",
            target_id="101",
        )
        AuditLog.objects.create(
            tenant=self.tenant_b,
            actor_user=self.user,
            action="TENANT_MEMBER_INVITE",
            target_type="tenant_member",
            target_id="202",
        )
        AuditLog.objects.create(
            tenant=None,
            actor_user=self.user,
            action="TENANT_CREATE",
            target_type="tenant",
            target_id=str(self.tenant_a.id),
        )

    def test_auto__case_tenant_audit_logs_success(self):
        self.client.force_authenticate(self.user)

        response = self.client.get("/internal/auth/tenant-audit-logs", HTTP_X_TENANT_CODE="tenant_audit_a")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["data"][0]["id"], self.tenant_log.id)
        self.assertEqual(response.data["data"][0]["action"], "TENANT_MEMBER_INVITE")

    def test_auto__case_tenant_audit_logs_permission_denied_not_authenticated(self):
        response = self.client.get("/internal/auth/tenant-audit-logs", HTTP_X_TENANT_CODE="tenant_audit_a")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_auto__case_tenant_audit_logs_permission_denied_missing_tenant_context(self):
        self.client.force_authenticate(self.user)

        response = self.client.get("/internal/auth/tenant-audit-logs")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "TENANT_CONTEXT_REQUIRED")

    def test_auto__case_tenant_audit_logs_permission_denied_forbidden_role(self):
        self.client.force_authenticate(self.other_user)

        response = self.client.get("/internal/auth/tenant-audit-logs", HTTP_X_TENANT_CODE="tenant_audit_a")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "PERMISSION_DENIED")


class MePermissionsAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="tenant_perm_user", password="pass1234", status=1)
        self.tenant = Tenant.objects.create(code="tenant_perm_a", name="权限租户A", status=TenantStatus.ENABLED)

        self.role_tenant_admin = SystemRole.objects.create(code="tenant_admin", name="租户管理员", status=1)
        self.role_auditor = SystemRole.objects.create(code="auditor", name="审计员", status=1)

        self.member = TenantMember.objects.create(
            tenant=self.tenant,
            user=self.user,
            display_name="张三",
            status=TenantMemberStatus.ACTIVE,
            joined_at=timezone.now(),
        )
        self.member.role_bindings.create(system_role=self.role_tenant_admin, status=1)
        self.member.role_bindings.create(system_role=self.role_auditor, status=1)

        grant_role_permissions(
            self.role_tenant_admin,
            {
                "access.view_user": ScopeType.ALL,
                "access.view_tenant_member": ScopeType.ALL,
                "access.manage_tenant_member": ScopeType.ALL,
                "access.assign_tenant_member_role": ScopeType.ALL,
            },
            group_name="tenant-admin-me-permissions-group",
        )
        grant_role_permissions(
            self.role_auditor,
            {"access.view_auth_audit_logs": ScopeType.ALL},
            group_name="auditor-me-permissions-group",
        )

    def test_auto__case_me_permissions_success(self):
        self.client.force_authenticate(self.user)

        response = self.client.get("/internal/auth/me/permissions", HTTP_X_TENANT_CODE=self.tenant.code)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["tenant_code"], self.tenant.code)
        self.assertEqual(response.data["roles"], ["tenant_admin", "auditor"])
        self.assertFalse(StaffProfile.objects.filter(user=self.user).exists())

        items_by_permission = {item["permission"]: item for item in response.data["items"]}
        self.assertEqual(items_by_permission["access.view_user"]["scope"], "ALL")
        self.assertEqual(items_by_permission["access.view_auth_audit_logs"]["scope"], "ALL")
        self.assertEqual(items_by_permission["access.view_tenant_member"]["scope"], "ALL")
        self.assertEqual(items_by_permission["access.manage_tenant_member"]["scope"], "ALL")
        self.assertEqual(items_by_permission["access.assign_tenant_member_role"]["scope"], "ALL")
        self.assertTrue(all(item["enabled"] is True for item in response.data["items"]))

    def test_auto__case_me_permissions_permission_denied_not_authenticated(self):
        response = self.client.get("/internal/auth/me/permissions", HTTP_X_TENANT_CODE=self.tenant.code)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_auto__case_me_permissions_permission_denied_missing_tenant_context(self):
        self.client.force_authenticate(self.user)

        response = self.client.get("/internal/auth/me/permissions")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "TENANT_CONTEXT_REQUIRED")

    def test_auto__case_me_permissions_permission_denied_membership_required(self):
        other_tenant = Tenant.objects.create(code="tenant_perm_b", name="权限租户B", status=TenantStatus.ENABLED)
        self.client.force_authenticate(self.user)

        response = self.client.get("/internal/auth/me/permissions", HTTP_X_TENANT_CODE=other_tenant.code)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "TENANT_MEMBERSHIP_REQUIRED")


class SuperuserRootPolicyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.superuser = User.objects.create_superuser(username="root_all", password="pass1234")
        self.tenant = Tenant.objects.create(code="root_tenant_a", name="Root租户A", status=TenantStatus.ENABLED)
        self.client.force_authenticate(self.superuser)

    def test_me_permissions_should_return_all_for_superuser(self):
        response = self.client.get("/internal/auth/me/permissions", HTTP_X_TENANT_CODE=self.tenant.code)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["tenant_code"], self.tenant.code)
        self.assertEqual(response.data["roles"], [])
        self.assertGreater(len(response.data["items"]), 0)
        self.assertTrue(all(item["scope"] == "ALL" for item in response.data["items"]))
        self.assertTrue(all(item["enabled"] is True for item in response.data["items"]))


class SeedRolePermissionsCommandTests(TestCase):
    def test_seed_role_permissions_replace_mode(self):
        call_command("seed_role_permissions")

        tenant_admin = SystemRole.objects.get(code="tenant_admin")
        self.assertEqual(
            set(tenant_admin.group_links.filter(status=ScopeStatus.ACTIVE).values_list("group__name", flat=True)),
            {"租户治理权限组", "业务管理员权限组"},
        )

        dispatcher = SystemRole.objects.get(code="dispatcher")
        self.assertEqual(
            set(dispatcher.group_links.filter(status=ScopeStatus.ACTIVE).values_list("group__name", flat=True)),
            {"任务调度权限组"},
        )

        pilot_operator = SystemRole.objects.get(code="pilot_operator")
        self.assertEqual(
            set(pilot_operator.group_links.filter(status=ScopeStatus.ACTIVE).values_list("group__name", flat=True)),
            {"飞手操作权限组"},
        )

        business_admin = SystemRole.objects.get(code="business_admin")
        self.assertEqual(
            set(
                business_admin.group_links.filter(status=ScopeStatus.ACTIVE).values_list("group__name", flat=True)
            ),
            {"业务管理员权限组"},
        )


class CreateBusinessAdminAccountCommandTests(TestCase):
    def test_create_business_admin_account(self):
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
        root = User.objects.create_superuser(username="root_policy", password="pass1234")
        with self.assertRaises(ValidationError):
            StaffProfile.objects.create(
                user=root,
                staff_no="POL-001",
                name="Root Policy",
                employment_status=1,
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


class TenantDisableAPITests(TestCase):
    """租户停用 API 测试"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(username="tenant_operator", password="pass1234")
        self.client.force_authenticate(user=self.user)
        self.tenant = Tenant.objects.create(code="tenant_disable", name="待停用租户", status=TenantStatus.ENABLED)

    def test_auto__case_tenant_disable_success(self):
        response = self.client.post(f"/internal/auth/tenants/{self.tenant.id}/disable", format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], TenantStatus.DISABLED)

        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.status, TenantStatus.DISABLED)

        audit_log = AuditLog.objects.get(action="TENANT_DISABLE", target_id=str(self.tenant.id))
        self.assertIsNone(audit_log.tenant_id)
        self.assertEqual(audit_log.actor_user_id, self.user.id)
        self.assertEqual(audit_log.before_data["status"], TenantStatus.ENABLED)
        self.assertEqual(audit_log.after_data["status"], TenantStatus.DISABLED)

    def test_auto__case_tenant_disable_permission_denied(self):
        client = APIClient()
        response = client.post(f"/internal/auth/tenants/{self.tenant.id}/disable", format="json")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_auto__case_tenant_disable_resource_not_found(self):
        response = self.client.post("/internal/auth/tenants/99999/disable", format="json")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "TENANT_NOT_FOUND")

    def test_auto__case_tenant_disable_idempotent_duplicate(self):
        self.tenant.status = TenantStatus.DISABLED
        self.tenant.save(update_fields=["status", "updated_at"])

        response = self.client.post(f"/internal/auth/tenants/{self.tenant.id}/disable", format="json")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(response.data["business_detail_code"], "TENANT_ALREADY_DISABLED")


class TenantEnableAPITests(TestCase):
    """租户启用 API 测试"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(username="tenant_enable_operator", password="pass1234")
        self.client.force_authenticate(user=self.user)
        self.tenant = Tenant.objects.create(code="tenant_enable", name="待启用租户", status=TenantStatus.DISABLED)

    def test_auto__case_tenant_enable_success(self):
        response = self.client.post(f"/internal/auth/tenants/{self.tenant.id}/enable", format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["status"], TenantStatus.ENABLED)

        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.status, TenantStatus.ENABLED)

        audit_log = AuditLog.objects.get(action="TENANT_ENABLE", target_id=str(self.tenant.id))
        self.assertIsNone(audit_log.tenant_id)
        self.assertEqual(audit_log.actor_user_id, self.user.id)
        self.assertEqual(audit_log.before_data["status"], TenantStatus.DISABLED)
        self.assertEqual(audit_log.after_data["status"], TenantStatus.ENABLED)

    def test_auto__case_tenant_enable_permission_denied(self):
        client = APIClient()
        response = client.post(f"/internal/auth/tenants/{self.tenant.id}/enable", format="json")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_auto__case_tenant_enable_resource_not_found(self):
        response = self.client.post("/internal/auth/tenants/99999/enable", format="json")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "TENANT_NOT_FOUND")

    def test_auto__case_tenant_enable_idempotent_duplicate(self):
        self.tenant.status = TenantStatus.ENABLED
        self.tenant.save(update_fields=["status", "updated_at"])

        response = self.client.post(f"/internal/auth/tenants/{self.tenant.id}/enable", format="json")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(response.data["business_detail_code"], "TENANT_ALREADY_ENABLED")


class TenantInitializeAdminAPITests(TestCase):
    """初始化租户管理员 API 测试"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(username="tenant_admin_initializer", password="pass1234")
        self.client.force_authenticate(user=self.user)
        self.tenant = Tenant.objects.create(code="tenant_init_admin", name="初始化管理员租户", status=TenantStatus.ENABLED)
        self.target_user = User.objects.create_user(username="tenant_admin_u1", password="pass1234", status=1)
        self.admin_role = SystemRole.objects.create(code="tenant_admin", name="租户管理员", status=1)

    def test_auto__case_tenant_initialize_admin_success(self):
        response = self.client.post(
            f"/internal/auth/tenants/{self.tenant.id}/initialize-admin",
            {
                "user_id": self.target_user.id,
                "display_name": "租户管理员A",
                "positions": [{"code": "tenant_lead", "name": "租户负责人"}],
                "qualifications": [
                    {"code": "security_training", "name": "安全培训", "valid_until": "2026-12-31"}
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["roles"], ["tenant_admin"])
        self.assertEqual(response.data["status"], TenantMemberStatus.ACTIVE)

        member = TenantMember.objects.get(tenant=self.tenant, user=self.target_user)
        self.assertEqual(member.status, TenantMemberStatus.ACTIVE)
        self.assertIsNotNone(member.joined_at)
        self.assertEqual(
            list(member.role_bindings.filter(status=1).values_list("system_role__code", flat=True)),
            ["tenant_admin"],
        )
        self.assertEqual(list(member.positions.values_list("code", flat=True)), ["tenant_lead"])
        self.assertEqual(list(member.qualifications.values_list("code", flat=True)), ["security_training"])

        audit_log = AuditLog.objects.get(action="TENANT_ADMIN_INITIALIZE", target_id=str(member.id))
        self.assertIsNone(audit_log.tenant_id)
        self.assertEqual(audit_log.actor_user_id, self.user.id)
        self.assertEqual(audit_log.after_data["tenant_id"], self.tenant.id)

    def test_auto__case_tenant_initialize_admin_invalid_params(self):
        response = self.client.post(
            f"/internal/auth/tenants/{self.tenant.id}/initialize-admin",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")

    def test_auto__case_tenant_initialize_admin_permission_denied(self):
        client = APIClient()
        response = client.post(
            f"/internal/auth/tenants/{self.tenant.id}/initialize-admin",
            {"user_id": self.target_user.id, "display_name": "租户管理员A"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_auto__case_tenant_initialize_admin_resource_not_found(self):
        response = self.client.post(
            "/internal/auth/tenants/99999/initialize-admin",
            {"user_id": self.target_user.id, "display_name": "租户管理员A"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "TENANT_NOT_FOUND")

    def test_auto__case_tenant_initialize_admin_state_conflict(self):
        self.tenant.status = TenantStatus.DISABLED
        self.tenant.save(update_fields=["status", "updated_at"])

        response = self.client.post(
            f"/internal/auth/tenants/{self.tenant.id}/initialize-admin",
            {"user_id": self.target_user.id, "display_name": "租户管理员A"},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "STATE_CONFLICT")
        self.assertEqual(response.data["business_detail_code"], "TENANT_STATUS_INVALID")

    def test_auto__case_tenant_initialize_admin_idempotent_duplicate(self):
        existing_member = TenantMember.objects.create(
            tenant=self.tenant,
            user=self.target_user,
            display_name="既有管理员",
            status=TenantMemberStatus.ACTIVE,
            joined_at=timezone.now(),
        )
        existing_member.role_bindings.create(system_role=self.admin_role, status=1)

        other_user = User.objects.create_user(username="tenant_admin_u2", password="pass1234", status=1)
        response = self.client.post(
            f"/internal/auth/tenants/{self.tenant.id}/initialize-admin",
            {"user_id": other_user.id, "display_name": "租户管理员B"},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(response.data["business_detail_code"], "TENANT_ADMIN_ALREADY_INITIALIZED")


class TenantSetPlanAPITests(TestCase):
    """租户套餐配置 API 测试"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(username="tenant_plan_operator", password="pass1234")
        self.client.force_authenticate(user=self.user)
        self.tenant = Tenant.objects.create(
            code="tenant_plan",
            name="套餐租户",
            status=TenantStatus.ENABLED,
            plan="basic",
        )

    def test_auto__case_tenant_set_plan_success(self):
        response = self.client.post(
            f"/internal/auth/tenants/{self.tenant.id}/set-plan",
            {"plan": "enterprise"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["plan"], "enterprise")

        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.plan, "enterprise")

        audit_log = AuditLog.objects.get(action="TENANT_PLAN_CHANGE", target_id=str(self.tenant.id))
        self.assertIsNone(audit_log.tenant_id)
        self.assertEqual(audit_log.actor_user_id, self.user.id)
        self.assertEqual(audit_log.before_data["plan"], "basic")
        self.assertEqual(audit_log.after_data["plan"], "enterprise")

    def test_auto__case_tenant_set_plan_invalid_params(self):
        response = self.client.post(
            f"/internal/auth/tenants/{self.tenant.id}/set-plan",
            {"plan": ""},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")

    def test_auto__case_tenant_set_plan_permission_denied(self):
        client = APIClient()
        response = client.post(
            f"/internal/auth/tenants/{self.tenant.id}/set-plan",
            {"plan": "enterprise"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_auto__case_tenant_set_plan_resource_not_found(self):
        response = self.client.post(
            "/internal/auth/tenants/99999/set-plan",
            {"plan": "enterprise"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "TENANT_NOT_FOUND")

    def test_auto__case_tenant_set_plan_idempotent_duplicate(self):
        response = self.client.post(
            f"/internal/auth/tenants/{self.tenant.id}/set-plan",
            {"plan": "basic"},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(response.data["business_detail_code"], "TENANT_PLAN_UNCHANGED")


class TenantMemberDisableAPITests(TestCase):
    """租户成员停用 API 测试"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(username="tenant_member_operator", password="pass1234")
        self.client.force_authenticate(user=self.user)
        self.tenant = Tenant.objects.create(code="tenant_member_disable", name="成员停用租户", status=TenantStatus.ENABLED)
        self.target_user = User.objects.create_user(username="tenant_member_disable_u1", password="pass1234", status=1)
        self.member = TenantMember.objects.create(
            tenant=self.tenant,
            user=self.target_user,
            display_name="被停用成员",
            status=TenantMemberStatus.ACTIVE,
            joined_at=timezone.now(),
        )

    def test_auto__case_tenant_member_disable_success(self):
        response = self.client.post(f"/internal/auth/tenant-members/{self.member.id}/disable", format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["member_id"], self.member.id)
        self.assertEqual(response.data["status"], TenantMemberStatus.DISABLED)

        self.member.refresh_from_db()
        self.assertEqual(self.member.status, TenantMemberStatus.DISABLED)

        audit_log = AuditLog.objects.get(action="TENANT_MEMBER_DISABLE", target_id=str(self.member.id))
        self.assertEqual(audit_log.tenant_id, self.tenant.id)
        self.assertEqual(audit_log.actor_user_id, self.user.id)
        self.assertEqual(audit_log.before_data["status"], TenantMemberStatus.ACTIVE)
        self.assertEqual(audit_log.after_data["status"], TenantMemberStatus.DISABLED)

    def test_auto__case_tenant_member_disable_permission_denied(self):
        client = APIClient()

        response = client.post(f"/internal/auth/tenant-members/{self.member.id}/disable", format="json")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_auto__case_tenant_member_disable_resource_not_found(self):
        response = self.client.post("/internal/auth/tenant-members/99999/disable", format="json")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "TENANT_MEMBER_NOT_FOUND")

    def test_auto__case_tenant_member_disable_idempotent_duplicate(self):
        self.member.status = TenantMemberStatus.DISABLED
        self.member.save(update_fields=["status", "updated_at"])

        response = self.client.post(f"/internal/auth/tenant-members/{self.member.id}/disable", format="json")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(response.data["business_detail_code"], "TENANT_MEMBER_ALREADY_DISABLED")


class TenantMemberEnableAPITests(TestCase):
    """租户成员启用 API 测试"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_superuser(username="tenant_member_enabler", password="pass1234")
        self.client.force_authenticate(user=self.user)
        self.tenant = Tenant.objects.create(code="tenant_member_enable", name="成员启用租户", status=TenantStatus.ENABLED)
        self.target_user = User.objects.create_user(username="tenant_member_enable_u1", password="pass1234", status=1)
        self.member = TenantMember.objects.create(
            tenant=self.tenant,
            user=self.target_user,
            display_name="待启用成员",
            status=TenantMemberStatus.DISABLED,
            joined_at=None,
        )

    def test_auto__case_tenant_member_enable_success(self):
        response = self.client.post(f"/internal/auth/tenant-members/{self.member.id}/enable", format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["member_id"], self.member.id)
        self.assertEqual(response.data["status"], TenantMemberStatus.ACTIVE)

        self.member.refresh_from_db()
        self.assertEqual(self.member.status, TenantMemberStatus.ACTIVE)
        self.assertIsNotNone(self.member.joined_at)

        audit_log = AuditLog.objects.get(action="TENANT_MEMBER_ENABLE", target_id=str(self.member.id))
        self.assertEqual(audit_log.tenant_id, self.tenant.id)
        self.assertEqual(audit_log.actor_user_id, self.user.id)
        self.assertEqual(audit_log.before_data["status"], TenantMemberStatus.DISABLED)
        self.assertEqual(audit_log.after_data["status"], TenantMemberStatus.ACTIVE)

    def test_auto__case_tenant_member_enable_permission_denied(self):
        client = APIClient()

        response = client.post(f"/internal/auth/tenant-members/{self.member.id}/enable", format="json")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_auto__case_tenant_member_enable_resource_not_found(self):
        response = self.client.post("/internal/auth/tenant-members/99999/enable", format="json")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "TENANT_MEMBER_NOT_FOUND")

    def test_auto__case_tenant_member_enable_idempotent_duplicate(self):
        self.member.status = TenantMemberStatus.ACTIVE
        self.member.joined_at = timezone.now()
        self.member.save(update_fields=["status", "joined_at", "updated_at"])

        response = self.client.post(f"/internal/auth/tenant-members/{self.member.id}/enable", format="json")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "IDEMPOTENT_DUPLICATE")
        self.assertEqual(response.data["business_detail_code"], "TENANT_MEMBER_ALREADY_ENABLED")


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
                "positions": [{"code": "pilot_operator", "name": "飞手"}],
                "qualifications": [{"code": "uav_license", "name": "无人机执照"}],
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
        self.assertEqual(list(member.positions.values_list("code", flat=True)), ["pilot_operator"])
        self.assertEqual(list(member.qualifications.values_list("code", flat=True)), ["uav_license"])

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


class MeInvitationRejectAPITests(TestCase):
    """当前用户拒绝租户邀请 API 测试"""

    def setUp(self):
        self.client = APIClient()
        self.invited_user = User.objects.create_user(username="tenant_member_reject_u1", password="pass1234", status=1)
        self.other_user = User.objects.create_user(username="tenant_member_reject_u2", password="pass1234", status=1)
        self.tenant = Tenant.objects.create(code="tenant-reject", name="租户拒绝测试", status=TenantStatus.ENABLED)
        self.role = SystemRole.objects.create(code="pilot_operator", name="飞手", status=1)
        self.member = TenantMember.objects.create(
            tenant=self.tenant,
            user=self.invited_user,
            display_name="王六",
            invitation_token="token-reject-001",
            status=TenantMemberStatus.PENDING,
        )
        self.member.role_bindings.create(system_role=self.role, status=1)

    def test_auto__case_me_invitation_reject_success(self):
        self.client.force_authenticate(user=self.invited_user)

        response = self.client.post(
            "/internal/auth/me/invitations/reject",
            {"invitation_token": "token-reject-001"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business_code"], "SUCCESS")
        self.assertEqual(response.data["business_detail_code"], "OK")
        self.assertEqual(response.data["message"], "您已拒绝该租户邀请")
        self.assertFalse(TenantMember.objects.filter(id=self.member.id).exists())

        audit_log = AuditLog.objects.get(action="TENANT_MEMBER_REJECT_INVITATION", target_id=str(self.member.id))
        self.assertEqual(audit_log.tenant_id, self.tenant.id)
        self.assertEqual(audit_log.actor_user_id, self.invited_user.id)
        self.assertEqual(audit_log.before_data["status"], TenantMemberStatus.PENDING)
        self.assertEqual(audit_log.after_data["result"], "rejected")

    def test_auto__case_me_invitation_reject_invalid_params(self):
        self.client.force_authenticate(user=self.invited_user)

        response = self.client.post(
            "/internal/auth/me/invitations/reject",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertEqual(response.data["business_detail_code"], "VALIDATION_ERROR")

    def test_auto__case_me_invitation_reject_permission_denied(self):
        response = self.client.post(
            "/internal/auth/me/invitations/reject",
            {"invitation_token": "token-reject-001"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "NOT_AUTHENTICATED")

    def test_auto__case_me_invitation_reject_wrong_user(self):
        self.client.force_authenticate(user=self.other_user)

        response = self.client.post(
            "/internal/auth/me/invitations/reject",
            {"invitation_token": "token-reject-001"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "INVITATION_NOT_ALLOWED")

    def test_auto__case_me_invitation_reject_not_found(self):
        self.client.force_authenticate(user=self.invited_user)

        response = self.client.post(
            "/internal/auth/me/invitations/reject",
            {"invitation_token": "missing-token"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(response.data["business_detail_code"], "INVITATION_NOT_FOUND")

    def test_auto__case_me_invitation_reject_state_conflict(self):
        self.client.force_authenticate(user=self.invited_user)
        self.member.status = TenantMemberStatus.ACTIVE
        self.member.joined_at = timezone.now()
        self.member.save(update_fields=["status", "joined_at", "updated_at"])

        response = self.client.post(
            "/internal/auth/me/invitations/reject",
            {"invitation_token": "token-reject-001"},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["business_code"], "STATE_CONFLICT")
        self.assertEqual(response.data["business_detail_code"], "INVITATION_STATUS_INVALID")
