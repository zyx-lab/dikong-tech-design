from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission as AuthPermission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import (
    AuditLog,
    Permission,
    Role,
    ScopeType,
    StaffProfile,
    Tenant,
    TenantMember,
    TenantMemberRoleStatus,
    TenantMemberStatus,
    TenantStatus,
)
from apps.access.services import AuthorizationReason, AuthzService
from apps.access.test_support import (
    build_request,
    ensure_staff_profile,
    ensure_tenant_role_binding,
    grant_role_permissions,
)

User = get_user_model()


class DummyOwnedObject:
    def __init__(self, created_by_staff_id):
        self.created_by_staff_id = created_by_staff_id


class AuthzServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u1", password="pass1234", status=1)
        self.staff = ensure_staff_profile(self.user, staff_no="S001", name="Alice")
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            role_code="authz_service_test_role",
            role_name="授权服务测试角色",
            tenant_code="authz_service_tenant",
        )
        self.request = build_request(self.user, self.tenant)

    def test_authorize_with_own_scope_success(self):
        grant_role_permissions(self.role, {"auth.view_group": ScopeType.OWN})

        obj = DummyOwnedObject(created_by_staff_id=self.staff.id)
        decision = AuthzService.authorize(self.request, "auth.view_group", obj=obj)

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.scope, ScopeType.OWN)

    def test_authorize_denied_when_permission_not_granted(self):
        Permission.objects.create(
            code="auth.view_group",
            name="auth.view_group",
            module="auth",
            status=1,
        )

        decision = AuthzService.authorize(self.request, "auth.view_group")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, AuthorizationReason.PERMISSION_DENIED)


class UserRegisterApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_user_register_success(self):
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
        user = User.objects.get(username="register_user")
        self.assertEqual(user.staff_profile.name, "注册用户")
        self.assertTrue(AuditLog.objects.filter(action="USER_REGISTER", target_id=str(user.id)).exists())

    def test_user_phone_register_success(self):
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
        user = User.objects.get(username="13800138002")
        self.assertEqual(user.staff_profile.phone, "13800138002")
        self.assertTrue(AuditLog.objects.filter(action="USER_REGISTER_BY_PHONE", target_id=str(user.id)).exists())


class MeTenantAndInvitationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="tenant_user", password="pass1234", status=1)
        self.role_admin = Role.objects.create(code="tenant_admin", name="租户管理员", status=1)
        self.role_planner = Role.objects.create(code="route_planner", name="航线规划员", status=1)

        self.tenant_a = Tenant.objects.create(code="tenant_a", name="租户A", status=TenantStatus.ACTIVE)
        self.tenant_b = Tenant.objects.create(code="tenant_b", name="租户B", status=TenantStatus.ACTIVE)
        self.tenant_invite = Tenant.objects.create(code="tenant_invite", name="邀请租户", status=TenantStatus.ACTIVE)

        member_a = TenantMember.objects.create(
            tenant=self.tenant_a,
            user=self.user,
            display_name="张三",
            status=TenantMemberStatus.ACTIVE,
            responded_at=timezone.now(),
            joined_at=timezone.now(),
        )
        member_b = TenantMember.objects.create(
            tenant=self.tenant_b,
            user=self.user,
            display_name="张三",
            status=TenantMemberStatus.ACTIVE,
            responded_at=timezone.now(),
            joined_at=timezone.now(),
        )
        invited_member = TenantMember.objects.create(
            tenant=self.tenant_invite,
            user=self.user,
            display_name="张三",
            invitation_token="invite-token-001",
            invited_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7),
            status=TenantMemberStatus.INVITED,
        )

        member_a.role_bindings.create(system_role=self.role_admin, status=TenantMemberRoleStatus.GRANTED)
        member_b.role_bindings.create(system_role=self.role_planner, status=TenantMemberRoleStatus.GRANTED)
        invited_member.role_bindings.create(system_role=self.role_planner, status=TenantMemberRoleStatus.GRANTED)

    def test_me_tenants_success(self):
        self.client.force_authenticate(self.user)

        response = self.client.get("/internal/auth/me/tenants")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["tenants"]), 2)
        self.assertEqual(response.data["tenants"][0]["roles"], ["tenant_admin"])
        self.assertEqual(response.data["tenants"][1]["roles"], ["route_planner"])

    def test_me_invitations_success(self):
        self.client.force_authenticate(self.user)

        response = self.client.get("/internal/auth/me/invitations")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["items"]), 1)
        self.assertEqual(response.data["items"][0]["roles"], ["route_planner"])
        self.assertEqual(response.data["items"][0]["invitation_token"], "invite-token-001")


class MePermissionsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="tenant_perm_user", password="pass1234", status=1)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="tenant_perm_a",
            role_code="tenant_admin",
            role_name="租户管理员",
        )
        grant_role_permissions(
            self.role,
            {
                "access.view_user": ScopeType.ALL,
                "access.view_tenant_member": ScopeType.ALL,
                "access.manage_tenant_member": ScopeType.ALL,
                "access.assign_tenant_member_role": ScopeType.ALL,
            },
        )

    def test_me_permissions_success(self):
        self.client.force_authenticate(self.user)

        response = self.client.get("/internal/auth/me/permissions", HTTP_X_TENANT_CODE=self.tenant.code)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["roles"], ["tenant_admin"])
        items_by_permission = {item["permission"]: item for item in response.data["items"]}
        self.assertEqual(items_by_permission["access.view_user"]["scope"], "ALL")
        self.assertEqual(items_by_permission["access.manage_tenant_member"]["scope"], "ALL")


class TenantMemberInvitationFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(username="root_admin", password="pass1234")
        self.invited_user = User.objects.create_user(username="invited_user", password="pass1234", status=1)
        self.tenant = Tenant.objects.create(code="tenant-x", name="租户X", status=TenantStatus.ACTIVE)
        Role.objects.create(code="pilot_operator", name="飞手", status=1)

    def test_invite_and_confirm_flow(self):
        self.client.force_authenticate(self.admin)
        invite_response = self.client.post(
            "/internal/auth/tenant-members/invite",
            {
                "tenant_id": self.tenant.id,
                "user_id": self.invited_user.id,
                "display_name": "飞手张三",
                "role_codes": ["pilot_operator"],
            },
            format="json",
        )
        self.assertEqual(invite_response.status_code, 201)

        member = TenantMember.objects.get(tenant=self.tenant, user=self.invited_user)
        self.assertEqual(member.status, TenantMemberStatus.INVITED)

        self.client.force_authenticate(self.invited_user)
        confirm_response = self.client.post(
            "/internal/auth/tenant-members/confirm-invitation",
            {"invitation_token": member.invitation_token},
            format="json",
        )

        self.assertEqual(confirm_response.status_code, 200)
        member.refresh_from_db()
        self.assertEqual(member.status, TenantMemberStatus.ACTIVE)
        self.assertIsNone(member.invitation_token)
        self.assertEqual(
            list(member.role_bindings.filter(status=TenantMemberRoleStatus.GRANTED).values_list("system_role__code", flat=True)),
            ["pilot_operator"],
        )

    def test_reject_invitation_flow(self):
        member = TenantMember.objects.create(
            tenant=self.tenant,
            user=self.invited_user,
            display_name="拒绝成员",
            invitation_token="invite-reject-001",
            invited_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7),
            status=TenantMemberStatus.INVITED,
        )
        self.client.force_authenticate(self.invited_user)

        response = self.client.post(
            "/internal/auth/me/invitations/reject",
            {"invitation_token": member.invitation_token},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        member.refresh_from_db()
        self.assertEqual(member.status, TenantMemberStatus.REJECTED)
        self.assertIsNone(member.invitation_token)


class TenantMemberLifecycleApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(username="root_tenant_admin", password="pass1234")
        self.user = User.objects.create_user(username="member_user", password="pass1234", status=1)
        self.tenant = Tenant.objects.create(code="tenant-lc", name="租户LC", status=TenantStatus.ACTIVE)
        self.member = TenantMember.objects.create(
            tenant=self.tenant,
            user=self.user,
            display_name="成员A",
            status=TenantMemberStatus.ACTIVE,
            responded_at=timezone.now(),
            joined_at=timezone.now(),
        )
        self.client.force_authenticate(self.admin)

    def test_disable_then_enable_member(self):
        disable_response = self.client.post(f"/internal/auth/tenant-members/{self.member.id}/disable")
        self.assertEqual(disable_response.status_code, 200)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, TenantMemberStatus.DISABLED)

        enable_response = self.client.post(f"/internal/auth/tenant-members/{self.member.id}/enable")
        self.assertEqual(enable_response.status_code, 200)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, TenantMemberStatus.ACTIVE)


class TenantAdminInitializeApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(username="root_initializer", password="pass1234")
        self.target_user = User.objects.create_user(username="tenant_admin_u1", password="pass1234", status=1)
        self.tenant = Tenant.objects.create(code="tenant-init", name="初始化管理员租户", status=TenantStatus.ACTIVE)
        Role.objects.create(code="tenant_admin", name="租户管理员", status=1)
        self.client.force_authenticate(self.admin)

    def test_initialize_tenant_admin_success(self):
        response = self.client.post(
            f"/internal/auth/tenants/{self.tenant.id}/initialize-admin",
            {
                "user_id": self.target_user.id,
                "display_name": "租户管理员A",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        member = TenantMember.objects.get(tenant=self.tenant, user=self.target_user)
        self.assertEqual(member.status, TenantMemberStatus.ACTIVE)
        self.assertEqual(
            list(member.role_bindings.filter(status=TenantMemberRoleStatus.GRANTED).values_list("system_role__code", flat=True)),
            ["tenant_admin"],
        )


class SeedRolePermissionsCommandTests(TestCase):
    def test_seed_role_permissions_replace_mode(self):
        call_command("seed_role_permissions")

        tenant_admin = Role.objects.get(code="tenant_admin")
        self.assertTrue(
            tenant_admin.permission_grants.filter(permission__code="access.manage_tenant_member", scope_type=ScopeType.ALL).exists()
        )

        pilot_operator = Role.objects.get(code="pilot_operator")
        self.assertTrue(
            pilot_operator.permission_grants.filter(permission__code="drone.view_drone", scope_type=ScopeType.ASSIGNED).exists()
        )


class UserPermissionPolicyTests(TestCase):
    def test_direct_user_permissions_are_blocked(self):
        user = User.objects.create_user(username="u2", password="pass1234", status=1)
        perm = AuthPermission.objects.get(content_type__app_label="access", codename="manage_tenant")
        with self.assertRaises(PermissionDenied):
            user.user_permissions.add(perm)

    def test_direct_user_groups_are_blocked(self):
        user = User.objects.create_user(username="u3", password="pass1234", status=1)
        group = user.groups.model.objects.create(name="cap_x")
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
