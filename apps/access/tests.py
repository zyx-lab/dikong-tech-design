from datetime import timedelta

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission as AuthPermission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.utils import timezone
from rest_framework.test import APIClient
import yaml

from apps.access import admin as access_admin_module
from apps.access.models import (
    AuditLog,
    DirectoryStatus,
    Permission,
    QualificationType,
    RolePermissionGrant,
    TenantMemberQualification,
    Role,
    ScopeType,
    StaffProfile,
    Tenant,
    TenantMember,
    TenantMemberRole,
    TenantMemberRoleStatus,
    TenantMemberStatus,
    TenantStatus,
)
from apps.access.services import AuthorizationReason, AuthzService
from apps.access.services import log_action
from apps.access.test_support import (
    build_request,
    ensure_staff_profile,
    ensure_tenant_role_binding,
    grant_role_permissions,
)

User = get_user_model()


class DummyOwnedObject:
    def __init__(self, created_by_tenant_member_id):
        self.created_by_tenant_member_id = created_by_tenant_member_id


class TenantMemberModelConstraintTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="member_model_user", password="pass1234", status=1)
        self.staff = ensure_staff_profile(self.user, staff_no="TM-001", name="成员模型测试用户")
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            role_code="tenant_member_model_role",
            role_name="成员模型测试角色",
            tenant_code="tenant_member_model_tenant",
        )

    def test_member_role_model_should_reject_platform_admin(self):
        platform_role = Role.objects.create(code="platform_admin", name="平台管理员", status=DirectoryStatus.ACTIVE)

        with self.assertRaises(ValidationError):
            self.member.role_bindings.create(
                system_role=platform_role,
                status=TenantMemberRoleStatus.GRANTED,
                assigned_at=timezone.now(),
            )

    def test_member_role_model_should_reject_disabled_role(self):
        disabled_role = Role.objects.create(code="disabled_member_role", name="停用角色", status=DirectoryStatus.DISABLED)

        with self.assertRaises(ValidationError):
            self.member.role_bindings.create(
                system_role=disabled_role,
                status=TenantMemberRoleStatus.GRANTED,
                assigned_at=timezone.now(),
            )

    def test_member_role_model_should_require_assigned_at_for_granted_status(self):
        extra_role = Role.objects.create(code="assigned_at_required_role", name="授予时间必填角色", status=DirectoryStatus.ACTIVE)

        with self.assertRaises(ValidationError):
            self.member.role_bindings.create(system_role=extra_role, status=TenantMemberRoleStatus.GRANTED)

    def test_tenant_member_model_should_reject_invited_without_invitation_fields(self):
        with self.assertRaises(ValidationError):
            TenantMember.objects.create(
                tenant=self.tenant,
                user=User.objects.create_user(username="member_model_invited", password="pass1234", status=1),
                display_name="邀请态缺字段",
                status=TenantMemberStatus.INVITED,
            )

    def test_tenant_member_model_should_reject_active_without_join_metadata(self):
        with self.assertRaises(ValidationError):
            TenantMember.objects.create(
                tenant=self.tenant,
                user=User.objects.create_user(username="member_model_active", password="pass1234", status=1),
                display_name="激活态缺字段",
                status=TenantMemberStatus.ACTIVE,
            )

    def test_tenant_member_model_should_reject_active_with_invitation_token(self):
        with self.assertRaises(ValidationError):
            TenantMember.objects.create(
                tenant=self.tenant,
                user=User.objects.create_user(username="member_model_active_token", password="pass1234", status=1),
                display_name="激活态残留邀请 token",
                status=TenantMemberStatus.ACTIVE,
                invitation_token="active-token",
                responded_at=timezone.now(),
                joined_at=timezone.now(),
            )

    def test_tenant_member_model_should_reject_active_with_invitation_metadata(self):
        inviter = User.objects.create_user(username="member_model_inviter", password="pass1234", status=1)

        with self.assertRaises(ValidationError):
            TenantMember.objects.create(
                tenant=self.tenant,
                user=User.objects.create_user(username="member_model_active_invitation_meta", password="pass1234", status=1),
                display_name="激活态残留邀请元数据",
                status=TenantMemberStatus.ACTIVE,
                invited_by_user=inviter,
                invited_at=timezone.now(),
                expires_at=timezone.now() + timedelta(days=7),
                responded_at=timezone.now(),
                joined_at=timezone.now(),
            )

    def test_tenant_member_model_should_reject_user_without_staff_profile(self):
        bare_user = User.objects.create_user(username="member_model_without_staff", password="pass1234", status=1)

        with self.assertRaises(ValidationError):
            TenantMember.objects.create(
                tenant=self.tenant,
                user=bare_user,
                display_name="无档案成员",
                status=TenantMemberStatus.ACTIVE,
                responded_at=timezone.now(),
                joined_at=timezone.now(),
            )

    def test_member_qualification_model_should_reject_disabled_qualification_type(self):
        disabled_type = QualificationType.objects.create(
            code="disabled_qualification_type",
            name="停用资质",
            status=DirectoryStatus.DISABLED,
        )

        with self.assertRaises(ValidationError):
            TenantMemberQualification.objects.create(
                tenant_member=self.member,
                qualification_type=disabled_type,
            )

    def test_member_qualification_model_should_require_validity_fields(self):
        qualification_type = QualificationType.objects.create(
            code="validity_required_qualification_type",
            name="需有效期资质",
            status=DirectoryStatus.ACTIVE,
            requires_validity=True,
        )

        with self.assertRaises(ValidationError):
            TenantMemberQualification.objects.create(
                tenant_member=self.member,
                qualification_type=qualification_type,
                valid_from=timezone.localdate(),
            )

    def test_member_qualification_model_should_require_schema_payload_fields(self):
        qualification_type = QualificationType.objects.create(
            code="schema_required_qualification_type",
            name="需扩展字段资质",
            status=DirectoryStatus.ACTIVE,
            payload_schema_json={"required": ["license_level"]},
        )

        with self.assertRaises(ValidationError):
            TenantMemberQualification.objects.create(
                tenant_member=self.member,
                qualification_type=qualification_type,
                payload_json={},
            )


class UserModelConstraintTests(TestCase):
    def test_user_model_should_reject_superuser_and_platform_admin_both_true(self):
        user = User(
            username="invalid_root_platform_user",
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
            is_active=True,
            status=1,
        )
        user.set_password("pass1234")

        with self.assertRaises(ValidationError):
            user.save()


class RolePermissionGrantModelConstraintTests(TestCase):
    def test_role_permission_grant_should_require_resource_code_for_assigned_scope(self):
        role = Role.objects.create(code="grant_scope_role", name="授权范围测试角色", status=DirectoryStatus.ACTIVE)
        permission = Permission.objects.create(
            code="grant.scope.permission",
            name="grant.scope.permission",
            module="grant",
            resource_code="",
            status=DirectoryStatus.ACTIVE,
        )

        with self.assertRaises(ValidationError):
            RolePermissionGrant.objects.create(
                role=role,
                permission=permission,
                scope_type=ScopeType.ASSIGNED,
            )


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

        obj = DummyOwnedObject(created_by_tenant_member_id=self.member.id)
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

    def test_log_action_should_infer_tenant_from_request_context(self):
        audit_log = log_action(
            request=self.request,
            action="AUTHZ_TEST_ACTION",
            target_type="mission",
            target_id=123,
        )

        self.assertEqual(audit_log.tenant, self.tenant)


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


class InternalOpenApiDocsTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _load_schema(self):
        response = self.client.get("/internal/docs/schema/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("application/vnd.oai.openapi"))
        return yaml.safe_load(response.content)

    def test_swagger_ui_should_be_available_at_internal_docs(self):
        response = self.client.get("/internal/docs/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/internal/docs/schema/")

    def test_internal_schema_should_only_include_internal_paths(self):
        schema = self._load_schema()
        self.assertEqual(schema["openapi"], "3.0.3")
        self.assertIn("/internal/auth/login", schema["paths"])
        self.assertIn("/internal/auth/tenant-members", schema["paths"])
        self.assertNotIn("/api/v1/drones", schema["paths"])

    def test_login_and_me_permissions_should_document_internal_contract(self):
        schema = self._load_schema()

        login_operation = schema["paths"]["/internal/auth/login"]["post"]
        self.assertEqual(login_operation["summary"], "用户名密码登录")
        login_request_ref = login_operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        login_request_schema = schema["components"]["schemas"][login_request_ref.split("/")[-1]]
        self.assertIn("username", login_request_schema["properties"])
        self.assertIn("password", login_request_schema["properties"])

        login_success_ref = login_operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        login_success_schema = schema["components"]["schemas"][login_success_ref.split("/")[-1]]
        self.assertIn("business_code", login_success_schema["properties"])
        self.assertIn("business_detail_code", login_success_schema["properties"])
        self.assertNotIn("code", login_success_schema["properties"])

        permission_operation = schema["paths"]["/internal/auth/me/permissions"]["get"]
        parameters = {item["name"]: item for item in permission_operation["parameters"]}
        self.assertIn("X-TENANT-CODE", parameters)
        self.assertIn("租户成员访问租户侧 internal IAM 接口时通常必填", parameters["X-TENANT-CODE"]["description"])
        self.assertEqual(permission_operation["summary"], "获取当前会话权限矩阵")

    def test_confirm_invitation_should_document_stateful_responses(self):
        schema = self._load_schema()

        operation = schema["paths"]["/internal/auth/tenant-members/confirm-invitation"]["post"]
        self.assertEqual(operation["summary"], "确认加入租户")
        self.assertIn("404", operation["responses"])
        self.assertIn("409", operation["responses"])
        examples = operation["responses"]["409"]["content"]["application/json"]["examples"]
        example_values = [item["value"] for item in examples.values()]
        self.assertTrue(any(item["business_detail_code"] == "INVITATION_STATUS_INVALID" for item in example_values))


class MeTenantAndInvitationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="tenant_user", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="张三")
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

        now = timezone.now()
        member_a.role_bindings.create(
            system_role=self.role_admin,
            status=TenantMemberRoleStatus.GRANTED,
            assigned_at=now,
        )
        member_b.role_bindings.create(
            system_role=self.role_planner,
            status=TenantMemberRoleStatus.GRANTED,
            assigned_at=now,
        )
        invited_member.role_bindings.create(
            system_role=self.role_planner,
            status=TenantMemberRoleStatus.GRANTED,
            assigned_at=now,
        )

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


class PlatformAdminIdentityApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.root = User.objects.create_superuser(username="root_platform_ops", password="pass1234")
        self.platform_user = User.objects.create_user(
            username="platform_ops_user",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        ensure_staff_profile(self.platform_user, name="平台管理员")
        self.platform_role = Role.objects.create(code="platform_admin", name="平台管理员", status=1)
        grant_role_permissions(
            self.platform_role,
            {
                "access.view_user": ScopeType.ALL,
                "access.manage_user_accounts": ScopeType.ALL,
                "access.view_tenant": ScopeType.ALL,
                "access.view_auth_audit_logs": ScopeType.ALL,
            },
        )
        self.tenant_a = Tenant.objects.create(code="platform-scope-a", name="平台租户A", status=TenantStatus.ACTIVE)
        self.tenant_b = Tenant.objects.create(code="platform-scope-b", name="平台租户B", status=TenantStatus.ACTIVE)
        self.user_a = User.objects.create_user(username="platform_scope_user_a", password="pass1234", status=1)
        self.user_b = User.objects.create_user(username="platform_scope_user_b", password="pass1234", status=1)
        ensure_staff_profile(self.user_a, name="租户A成员")
        ensure_staff_profile(self.user_b, name="租户B成员")
        TenantMember.objects.create(
            tenant=self.tenant_a,
            user=self.user_a,
            display_name="租户A成员",
            status=TenantMemberStatus.ACTIVE,
            responded_at=timezone.now(),
            joined_at=timezone.now(),
        )
        AuditLog.objects.create(tenant=None, action="PLATFORM_ONLY_ACTION", target_type="tenant", target_id="0")
        AuditLog.objects.create(tenant=self.tenant_a, action="TENANT_ONLY_ACTION", target_type="tenant_member", target_id="1")
        TenantMember.objects.create(
            tenant=self.tenant_b,
            user=self.user_b,
            display_name="租户B成员",
            status=TenantMemberStatus.ACTIVE,
            responded_at=timezone.now(),
            joined_at=timezone.now(),
        )

    def test_superuser_can_create_platform_admin_user(self):
        self.client.force_authenticate(self.root)

        response = self.client.post(
            "/internal/auth/users",
            {
                "username": "platform_api_created",
                "password": "pass1234",
                "is_platform_admin": True,
                "staff": {
                    "name": "平台运营账号",
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        created = User.objects.get(username="platform_api_created")
        self.assertTrue(created.is_platform_admin)
        self.assertEqual(created.staff_profile.name, "平台运营账号")

    def test_platform_admin_cannot_set_platform_admin_flag_via_api(self):
        self.client.force_authenticate(self.platform_user)

        response = self.client.post(
            "/internal/auth/users",
            {
                "username": "platform_flag_denied",
                "password": "pass1234",
                "is_platform_admin": True,
                "staff": {
                    "name": "越权平台账号",
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("is_platform_admin", response.data)
        self.assertFalse(User.objects.filter(username="platform_flag_denied").exists())

    def test_platform_admin_can_list_all_users_without_tenant_context(self):
        self.client.force_authenticate(self.platform_user)

        response = self.client.get("/internal/auth/users")

        self.assertEqual(response.status_code, 200)
        returned_ids = {item["id"] for item in response.data["results"]}
        self.assertIn(self.platform_user.id, returned_ids)
        self.assertIn(self.user_a.id, returned_ids)
        self.assertIn(self.user_b.id, returned_ids)

    def test_platform_admin_can_list_all_tenants_without_tenant_context(self):
        self.client.force_authenticate(self.platform_user)

        response = self.client.get("/internal/auth/tenants")

        self.assertEqual(response.status_code, 200)
        returned_ids = {item["id"] for item in response.data["data"]}
        self.assertEqual(returned_ids, {self.tenant_a.id, self.tenant_b.id})

    def test_platform_admin_me_permissions_should_not_require_tenant_context(self):
        self.client.force_authenticate(self.platform_user)

        response = self.client.get("/internal/auth/me/permissions")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["roles"], ["platform_admin"])
        self.assertIsNone(response.data["tenant_code"])
        items_by_permission = {item["permission"]: item for item in response.data["items"]}
        self.assertEqual(items_by_permission["access.view_user"]["scope"], "ALL")
        self.assertEqual(items_by_permission["access.view_tenant"]["scope"], "ALL")

    def test_platform_admin_global_audit_logs_should_only_return_platform_logs(self):
        self.client.force_authenticate(self.platform_user)

        response = self.client.get("/internal/auth/audit-logs")

        self.assertEqual(response.status_code, 200)
        actions = {item["action"] for item in response.data["results"]}
        self.assertEqual(actions, {"PLATFORM_ONLY_ACTION"})

    def test_platform_admin_should_be_blocked_from_tenant_member_list_even_if_permission_matrix_is_misconfigured(self):
        grant_role_permissions(
            self.platform_role,
            {
                "access.view_tenant_member": ScopeType.ALL,
            },
        )
        self.client.force_authenticate(self.platform_user)

        response = self.client.get("/internal/auth/tenant-members")

        self.assertEqual(response.status_code, 403)

    def test_platform_admin_should_be_blocked_from_tenant_member_invite_even_if_permission_matrix_is_misconfigured(self):
        invite_target = User.objects.create_user(username="platform_invite_target", password="pass1234", status=1)
        grant_role_permissions(
            self.platform_role,
            {
                "access.manage_tenant_member": ScopeType.ALL,
            },
        )
        self.client.force_authenticate(self.platform_user)

        response = self.client.post(
            "/internal/auth/tenant-members/invite",
            {
                "tenant_id": self.tenant_a.id,
                "user_id": invite_target.id,
                "display_name": "不应允许的平台邀请",
                "role_codes": [],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)


class TenantMemberInvitationFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(username="root_admin", password="pass1234")
        self.invited_user = User.objects.create_user(username="invited_user", password="pass1234", status=1)
        ensure_staff_profile(self.invited_user, name="受邀用户")
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

    def test_invite_superuser_should_return_invalid_params(self):
        root_user = User.objects.create_superuser(username="invited_root_user", password="pass1234")
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            "/internal/auth/tenant-members/invite",
            {
                "tenant_id": self.tenant.id,
                "user_id": root_user.id,
                "display_name": "根账号",
                "role_codes": ["pilot_operator"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertFalse(TenantMember.objects.filter(tenant=self.tenant, user=root_user).exists())

    def test_invite_platform_admin_should_return_invalid_params(self):
        platform_user = User.objects.create_user(
            username="invited_platform_user",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            "/internal/auth/tenant-members/invite",
            {
                "tenant_id": self.tenant.id,
                "user_id": platform_user.id,
                "display_name": "平台管理员",
                "role_codes": ["pilot_operator"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertFalse(TenantMember.objects.filter(tenant=self.tenant, user=platform_user).exists())


class TenantMemberLifecycleApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(username="root_tenant_admin", password="pass1234")
        self.user = User.objects.create_user(username="member_user", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="成员A")
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


class TenantMemberMemberNoBoundaryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(username="root_member_no", password="pass1234")
        self.tenant_a = Tenant.objects.create(code="tenant-member-no-a", name="租户A", status=TenantStatus.ACTIVE)
        self.tenant_b = Tenant.objects.create(code="tenant-member-no-b", name="租户B", status=TenantStatus.ACTIVE)
        self.user_a = User.objects.create_user(username="member_no_user_a", password="pass1234", status=1)
        self.user_b = User.objects.create_user(username="member_no_user_b", password="pass1234", status=1)
        self.user_c = User.objects.create_user(username="member_no_user_c", password="pass1234", status=1)
        ensure_staff_profile(self.user_a, name="成员A")
        ensure_staff_profile(self.user_b, name="成员B")
        ensure_staff_profile(self.user_c, name="成员C")
        self.client.force_authenticate(self.admin)

    def test_create_member_should_reject_duplicate_member_no_in_same_tenant(self):
        TenantMember.objects.create(
            tenant=self.tenant_a,
            user=self.user_a,
            display_name="成员A",
            member_no="MN-001",
            status=TenantMemberStatus.ACTIVE,
            responded_at=timezone.now(),
            joined_at=timezone.now(),
        )

        response = self.client.post(
            "/internal/auth/tenant-members",
            {
                "tenant_id": self.tenant_a.id,
                "user_id": self.user_b.id,
                "display_name": "成员B",
                "member_no": "MN-001",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("member_no", response.data)

    def test_create_member_should_allow_same_member_no_in_other_tenant(self):
        TenantMember.objects.create(
            tenant=self.tenant_a,
            user=self.user_a,
            display_name="成员A",
            member_no="MN-001",
            status=TenantMemberStatus.ACTIVE,
            responded_at=timezone.now(),
            joined_at=timezone.now(),
        )

        response = self.client.post(
            "/internal/auth/tenant-members",
            {
                "tenant_id": self.tenant_b.id,
                "user_id": self.user_b.id,
                "display_name": "成员B",
                "member_no": "MN-001",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            TenantMember.objects.filter(
                tenant=self.tenant_b,
                user=self.user_b,
                member_no="MN-001",
            ).exists()
        )

    def test_create_member_should_reject_superuser_target(self):
        root_user = User.objects.create_superuser(username="member_no_root_target", password="pass1234")

        response = self.client.post(
            "/internal/auth/tenant-members",
            {
                "tenant_id": self.tenant_a.id,
                "user_id": root_user.id,
                "display_name": "根账号成员",
                "member_no": "MN-ROOT",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertFalse(TenantMember.objects.filter(tenant=self.tenant_a, user=root_user).exists())

    def test_create_member_should_reject_platform_admin_target(self):
        platform_user = User.objects.create_user(
            username="member_no_platform_target",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )

        response = self.client.post(
            "/internal/auth/tenant-members",
            {
                "tenant_id": self.tenant_a.id,
                "user_id": platform_user.id,
                "display_name": "平台管理员成员",
                "member_no": "MN-PLATFORM",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertFalse(TenantMember.objects.filter(tenant=self.tenant_a, user=platform_user).exists())

    def test_update_member_should_reject_duplicate_member_no_in_same_tenant(self):
        member_a = TenantMember.objects.create(
            tenant=self.tenant_a,
            user=self.user_a,
            display_name="成员A",
            member_no="MN-001",
            status=TenantMemberStatus.ACTIVE,
            responded_at=timezone.now(),
            joined_at=timezone.now(),
        )
        member_b = TenantMember.objects.create(
            tenant=self.tenant_a,
            user=self.user_b,
            display_name="成员B",
            member_no="MN-002",
            status=TenantMemberStatus.ACTIVE,
            responded_at=timezone.now(),
            joined_at=timezone.now(),
        )

        response = self.client.patch(
            f"/internal/auth/tenant-members/{member_b.id}",
            {"member_no": member_a.member_no},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("member_no", response.data)
        member_b.refresh_from_db()
        self.assertEqual(member_b.member_no, "MN-002")


class TenantAdminScopeBoundaryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = User.objects.create_user(username="tenant_scope_admin", password="pass1234", status=1)
        self.current_user = User.objects.create_user(username="tenant_scope_user", password="pass1234", status=1)
        self.other_user = User.objects.create_user(username="tenant_scope_other", password="pass1234", status=1)
        self.invited_user = User.objects.create_user(username="tenant_scope_invited", password="pass1234", status=1)
        ensure_staff_profile(self.current_user, name="当前租户成员")
        ensure_staff_profile(self.other_user, name="其他租户成员")
        ensure_staff_profile(self.invited_user, name="待邀请成员")
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.admin_user,
            tenant_code="tenant_scope_a",
            role_code="tenant_admin",
            role_name="租户管理员",
        )
        grant_role_permissions(
            self.role,
            {
                "access.view_user": ScopeType.ALL,
                "access.view_tenant": ScopeType.ALL,
                "access.view_tenant_member": ScopeType.ALL,
                "access.manage_tenant_member": ScopeType.ALL,
                "access.assign_tenant_member_role": ScopeType.ALL,
                "access.view_auth_audit_logs": ScopeType.ALL,
            },
        )
        self.other_tenant = Tenant.objects.create(code="tenant_scope_b", name="租户B", status=TenantStatus.ACTIVE)
        TenantMember.objects.create(
            tenant=self.tenant,
            user=self.current_user,
            display_name="当前租户成员",
            status=TenantMemberStatus.ACTIVE,
            responded_at=timezone.now(),
            joined_at=timezone.now(),
        )
        self.other_member = TenantMember.objects.create(
            tenant=self.other_tenant,
            user=self.other_user,
            display_name="其他租户成员",
            status=TenantMemberStatus.ACTIVE,
            responded_at=timezone.now(),
            joined_at=timezone.now(),
        )
        Role.objects.create(code="platform_admin", name="平台管理员", status=1)
        Role.objects.create(code="pilot_operator", name="飞手", status=1)
        AuditLog.objects.create(tenant=self.tenant, action="TENANT_A_ACTION", target_type="tenant_member", target_id="1")
        AuditLog.objects.create(tenant=self.other_tenant, action="TENANT_B_ACTION", target_type="tenant_member", target_id="2")
        self.client.force_authenticate(self.admin_user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    def test_user_list_should_only_return_current_tenant_users(self):
        response = self.client.get("/internal/auth/users")

        self.assertEqual(response.status_code, 200)
        returned_ids = {item["id"] for item in response.data["results"]}
        self.assertIn(self.admin_user.id, returned_ids)
        self.assertIn(self.current_user.id, returned_ids)
        self.assertNotIn(self.other_user.id, returned_ids)

    def test_tenant_member_list_should_only_return_current_tenant_members(self):
        response = self.client.get("/internal/auth/tenant-members")

        self.assertEqual(response.status_code, 200)
        tenant_ids = {item["tenant"] for item in response.data["results"]}
        self.assertEqual(tenant_ids, {self.tenant.id})

    def test_tenant_list_should_only_return_current_tenant(self):
        response = self.client.get("/internal/auth/tenants")

        self.assertEqual(response.status_code, 200)
        returned_ids = {item["id"] for item in response.data["data"]}
        self.assertEqual(returned_ids, {self.tenant.id})

    def test_tenant_detail_should_not_allow_cross_tenant_lookup(self):
        response = self.client.get(f"/internal/auth/tenants/{self.other_tenant.id}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["business_code"], "RESOURCE_NOT_FOUND")

    def test_global_audit_log_list_should_only_return_current_tenant_logs_for_tenant_admin(self):
        response = self.client.get("/internal/auth/audit-logs")

        self.assertEqual(response.status_code, 200)
        actions = {item["action"] for item in response.data["results"]}
        self.assertEqual(actions, {"TENANT_A_ACTION"})

    def test_invite_should_reject_other_tenant_id(self):
        response = self.client.post(
            "/internal/auth/tenant-members/invite",
            {
                "tenant_id": self.other_tenant.id,
                "user_id": self.invited_user.id,
                "display_name": "跨租户邀请",
                "role_codes": ["pilot_operator"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")

    def test_role_assign_should_reject_platform_admin(self):
        current_member = TenantMember.objects.get(tenant=self.tenant, user=self.current_user)

        response = self.client.post(
            f"/internal/auth/tenant-members/{current_member.id}/roles",
            {"role_codes": ["platform_admin"]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("role_codes", response.data)

    def test_role_assign_should_not_allow_cross_tenant_member(self):
        response = self.client.post(
            f"/internal/auth/tenant-members/{self.other_member.id}/roles",
            {"role_codes": ["pilot_operator"]},
            format="json",
        )

        self.assertEqual(response.status_code, 404)


class TenantAdminInitializeApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(username="root_initializer", password="pass1234")
        self.target_user = User.objects.create_user(username="tenant_admin_u1", password="pass1234", status=1)
        ensure_staff_profile(self.target_user, name="租户管理员候选")
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

    def test_initialize_tenant_admin_should_reject_duplicate_member_no(self):
        another_user = User.objects.create_user(username="tenant_admin_u2", password="pass1234", status=1)
        ensure_staff_profile(another_user, name="现有成员")
        TenantMember.objects.create(
            tenant=self.tenant,
            user=another_user,
            display_name="现有成员",
            member_no="INIT-001",
            status=TenantMemberStatus.ACTIVE,
            responded_at=timezone.now(),
            joined_at=timezone.now(),
        )

        response = self.client.post(
            f"/internal/auth/tenants/{self.tenant.id}/initialize-admin",
            {
                "user_id": self.target_user.id,
                "display_name": "租户管理员A",
                "member_no": "INIT-001",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")

    def test_initialize_tenant_admin_should_reject_target_without_staff_profile(self):
        bare_user = User.objects.create_user(username="tenant_admin_no_staff", password="pass1234", status=1)

        response = self.client.post(
            f"/internal/auth/tenants/{self.tenant.id}/initialize-admin",
            {
                "user_id": bare_user.id,
                "display_name": "无档案管理员",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertFalse(TenantMember.objects.filter(tenant=self.tenant, user=bare_user).exists())

    def test_initialize_tenant_admin_should_reject_superuser_target(self):
        root_user = User.objects.create_superuser(username="tenant_admin_root_target", password="pass1234")

        response = self.client.post(
            f"/internal/auth/tenants/{self.tenant.id}/initialize-admin",
            {
                "user_id": root_user.id,
                "display_name": "根账号管理员",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertFalse(TenantMember.objects.filter(tenant=self.tenant, user=root_user).exists())

    def test_initialize_tenant_admin_should_reject_platform_admin_target(self):
        platform_user = User.objects.create_user(
            username="tenant_admin_platform_target",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )

        response = self.client.post(
            f"/internal/auth/tenants/{self.tenant.id}/initialize-admin",
            {
                "user_id": platform_user.id,
                "display_name": "平台管理员",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["business_code"], "INVALID_PARAMS")
        self.assertFalse(TenantMember.objects.filter(tenant=self.tenant, user=platform_user).exists())


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
        self.assertTrue(
            pilot_operator.permission_grants.filter(permission__code="mission.view_mission", scope_type=ScopeType.ASSIGNED).exists()
        )
        self.assertTrue(
            pilot_operator.permission_grants.filter(
                permission__code="flight_record.view_flight_record",
                scope_type=ScopeType.ASSIGNED,
            ).exists()
        )
        self.assertTrue(
            pilot_operator.permission_grants.filter(
                permission__code="flight_record.manage_flight_record",
                scope_type=ScopeType.ASSIGNED,
            ).exists()
        )
        self.assertTrue(
            pilot_operator.permission_grants.filter(
                permission__code="media_file.view_media_file",
                scope_type=ScopeType.ASSIGNED,
            ).exists()
        )
        self.assertTrue(
            pilot_operator.permission_grants.filter(
                permission__code="media_file.manage_media_file",
                scope_type=ScopeType.ASSIGNED,
            ).exists()
        )

        platform_admin = Role.objects.get(code="platform_admin")
        self.assertFalse(platform_admin.permission_grants.filter(permission__code="access.view_tenant_member").exists())
        self.assertFalse(platform_admin.permission_grants.filter(permission__code="access.manage_tenant_member").exists())
        self.assertFalse(platform_admin.permission_grants.filter(permission__code="access.assign_tenant_member_role").exists())
        self.assertTrue(
            platform_admin.permission_grants.filter(permission__code="access.view_auth_audit_logs", scope_type=ScopeType.ALL).exists()
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
                name="Root Policy",
                employment_status=1,
            )

    def test_superuser_cannot_bind_tenant_member(self):
        root = User.objects.create_superuser(username="root_policy_member", password="pass1234")
        tenant = Tenant.objects.create(code="tenant-policy-root", name="租户Root", status=TenantStatus.ACTIVE)
        with self.assertRaises(ValidationError):
            TenantMember.objects.create(
                tenant=tenant,
                user=root,
                display_name="Root Member",
                status=TenantMemberStatus.ACTIVE,
                responded_at=timezone.now(),
                joined_at=timezone.now(),
            )

    def test_platform_admin_cannot_bind_tenant_member(self):
        platform_user = User.objects.create_user(
            username="platform_policy_member",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        tenant = Tenant.objects.create(code="tenant-policy-platform", name="租户Platform", status=TenantStatus.ACTIVE)
        with self.assertRaises(ValidationError):
            TenantMember.objects.create(
                tenant=tenant,
                user=platform_user,
                display_name="Platform Member",
                status=TenantMemberStatus.ACTIVE,
                responded_at=timezone.now(),
                joined_at=timezone.now(),
            )


class AdminConfigTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.superuser = User.objects.create_superuser(username="admin_root", password="pass1234")
        self.role = Role.objects.create(code="admin_config_role", name="Admin Config Role", status=DirectoryStatus.ACTIVE)
        self.permission_all = Permission.objects.create(
            code="mission.view_mission",
            name="view mission",
            module="mission",
            status=DirectoryStatus.ACTIVE,
        )
        self.permission_assigned = Permission.objects.create(
            code="drone.view_drone",
            name="view drone",
            module="drone",
            resource_code="drone",
            status=DirectoryStatus.ACTIVE,
        )
        RolePermissionGrant.objects.create(role=self.role, permission=self.permission_all, scope_type=ScopeType.ALL)
        RolePermissionGrant.objects.create(role=self.role, permission=self.permission_assigned, scope_type=ScopeType.ASSIGNED)
        self.member_user = User.objects.create_user(username="admin_member", password="pass1234", status=1)
        ensure_staff_profile(self.member_user, name="Admin Member")
        self.tenant, self.member, _ = ensure_tenant_role_binding(
            self.member_user,
            tenant_code="admin_config_tenant",
            role_code=self.role.code,
            role_name=self.role.name,
            member_no="M-001",
        )
        self.qualification_type = QualificationType.objects.create(
            code="admin_qualification",
            name="管理资质",
            status=DirectoryStatus.ACTIVE,
        )
        TenantMemberQualification.objects.create(
            tenant_member=self.member,
            qualification_type=self.qualification_type,
        )
        self.user_admin = admin.site._registry[User]
        self.role_admin = admin.site._registry[Role]
        self.tenant_member_admin = admin.site._registry[TenantMember]
        self._imported_admin_module_name = access_admin_module.__name__

    def build_request(self, method="get"):
        request = getattr(self.factory, method)("/admin/")
        request.user = self.superuser
        return request

    def test_catalog_admins_should_be_read_only(self):
        readonly_admins = (
            admin.site._registry[Role],
            admin.site._registry[Permission],
            admin.site._registry[RolePermissionGrant],
            admin.site._registry[TenantMemberRole],
            admin.site._registry[AuditLog],
        )
        get_request = self.build_request("get")
        post_request = self.build_request("post")

        for model_admin in readonly_admins:
            self.assertFalse(model_admin.has_add_permission(get_request))
            self.assertTrue(model_admin.has_change_permission(get_request))
            self.assertFalse(model_admin.has_change_permission(post_request))
            self.assertFalse(model_admin.has_delete_permission(get_request))

    def test_role_admin_should_render_permission_aggregation(self):
        matrix_summary = str(self.role_admin.permission_matrix_summary(self.role))
        module_summary = str(self.role_admin.module_scope_summary(self.role))

        self.assertIn("mission.view_mission", matrix_summary)
        self.assertIn("drone.view_drone", matrix_summary)
        self.assertIn("ALL", matrix_summary)
        self.assertIn("ASSIGNED", matrix_summary)
        self.assertIn("mission", module_summary)
        self.assertIn("drone", module_summary)

    def test_tenant_member_admin_should_keep_role_and_qualification_inlines(self):
        inline_models = [inline.model for inline in self.tenant_member_admin.inlines]
        self.assertIn(TenantMemberRole, inline_models)
        self.assertIn(TenantMemberQualification, inline_models)

    def test_user_admin_should_render_tenant_membership_summary(self):
        user = self.user_admin.get_queryset(self.build_request()).get(pk=self.member_user.pk)
        membership_summary = str(self.user_admin.tenant_membership_summary(user))

        self.assertIn("admin_config_tenant", membership_summary)
        self.assertIn("M-001", membership_summary)
        self.assertIn("admin_config_role", membership_summary)

    def test_tenant_member_admin_should_render_qualification_summary(self):
        member = self.tenant_member_admin.get_queryset(self.build_request()).get(pk=self.member.pk)
        qualification_summary = str(self.tenant_member_admin.qualification_summary(member))

        self.assertIn("admin_qualification", qualification_summary)
        self.assertIn("active", qualification_summary)

    def test_role_admin_should_render_member_binding_preview(self):
        role = self.role_admin.get_queryset(self.build_request()).get(pk=self.role.pk)
        member_binding_preview = str(self.role_admin.member_binding_preview(role))

        self.assertIn("admin_config_tenant", member_binding_preview)
        self.assertIn("M-001", member_binding_preview)
