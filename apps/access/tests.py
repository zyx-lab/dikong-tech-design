from io import StringIO
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import AuditLog, AuthSession, DirectoryStatus, Permission, Role, Tenant, TenantMemberStatus, TenantStatus, UserStatus
from apps.access.test_support import ensure_staff_profile, ensure_tenant_role_binding

User = get_user_model()


class IamApiTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()

    def login(self, *, username: str, password: str) -> dict:
        response = self.client.post(
            "/api/v1/iam/session/login",
            {"username": username, "password": password},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.json())
        data = response.json()["data"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {data['accessToken']}")
        return data


class SessionApiTests(IamApiTestCase):
    def test_register_login_refresh_logout_should_follow_bearer_session_contract(self):
        register_response = self.client.post(
            "/api/v1/iam/session/register",
            {
                "username": "iam_register_user",
                "password": "pass1234",
                "name": "张三",
                "phone": "13800138000",
            },
            format="json",
        )
        self.assertEqual(register_response.status_code, 201)
        self.assertEqual(register_response.json()["code"], "00000")
        self.assertEqual(register_response.json()["data"]["status"], "ACTIVE")
        self.assertNotIn("accessToken", register_response.json()["data"])

        login_response = self.client.post(
            "/api/v1/iam/session/login",
            {
                "username": "iam_register_user",
                "password": "pass1234",
            },
            format="json",
        )
        self.assertEqual(login_response.status_code, 200)
        login_data = login_response.json()["data"]
        self.assertEqual(login_data["tokenType"], "Bearer")
        self.assertEqual(login_data["expiresIn"], 7200)
        self.assertEqual(login_data["refreshExpiresIn"], 604800)
        self.assertEqual(login_data["user"]["status"], "ACTIVE")
        self.assertFalse(login_data["user"]["hasPlatformAccess"])
        access_token = login_data["accessToken"]
        refresh_token = login_data["refreshToken"]

        refresh_response = self.client.post(
            "/api/v1/iam/session/refresh",
            {"refreshToken": refresh_token},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, 200)
        refresh_data = refresh_response.json()["data"]
        self.assertNotEqual(refresh_data["refreshToken"], refresh_token)
        self.assertNotEqual(refresh_data["accessToken"], access_token)

        old_refresh_response = self.client.post(
            "/api/v1/iam/session/refresh",
            {"refreshToken": refresh_token},
            format="json",
        )
        self.assertEqual(old_refresh_response.status_code, 401)
        self.assertEqual(old_refresh_response.json()["code"], "A0401")

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_data['accessToken']}")
        logout_response = self.client.post("/api/v1/iam/session/logout", {}, format="json")
        self.assertEqual(logout_response.status_code, 200)
        self.assertIsNone(logout_response.json()["data"])

        profile_response = self.client.get("/api/v1/iam/me/profile")
        self.assertEqual(profile_response.status_code, 401)
        self.assertEqual(profile_response.json()["code"], "A0401")

    def test_register_by_phone_should_use_mock_sms_and_report_phone_duplicate(self):
        invalid_sms_response = self.client.post(
            "/api/v1/iam/session/register-by-phone",
            {"phone": "13900139000", "smsCode": "000000", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(invalid_sms_response.status_code, 400)
        self.assertEqual(invalid_sms_response.json()["code"], "B0001")

        first_response = self.client.post(
            "/api/v1/iam/session/register-by-phone",
            {"phone": "13900139000", "smsCode": "123456", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(first_response.json()["data"]["username"], "13900139000")

        duplicate_response = self.client.post(
            "/api/v1/iam/session/register-by-phone",
            {"phone": "13900139000", "smsCode": "123456", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(duplicate_response.status_code, 409)
        self.assertEqual(duplicate_response.json()["code"], "C0102")

    def test_request_contract_should_reject_removed_or_unknown_fields(self):
        legacy_register_response = self.client.post(
            "/api/v1/iam/session/register-by-phone",
            {"phone": "13900139001", "sms_code": "123456", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(legacy_register_response.status_code, 400)
        self.assertEqual(legacy_register_response.json()["code"], "B0001")
        self.assertIn("sms_code", legacy_register_response.json()["data"])

    def test_superuser_should_not_be_allowed_to_login_formal_iam(self):
        User.objects.create_superuser(username="root_formal_forbidden", password="pass1234")

        response = self.client.post(
            "/api/v1/iam/session/login",
            {"username": "root_formal_forbidden", "password": "pass1234"},
            format="json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "A0401")

    def test_disabling_user_should_revoke_all_active_auth_sessions(self):
        user = User.objects.create_user(username="session_revoke_user", password="pass1234", status=UserStatus.ACTIVE)
        ensure_staff_profile(user, name="会话撤销用户")

        client_a = APIClient()
        client_b = APIClient()
        response_a = client_a.post(
            "/api/v1/iam/session/login",
            {"username": "session_revoke_user", "password": "pass1234"},
            format="json",
        )
        response_b = client_b.post(
            "/api/v1/iam/session/login",
            {"username": "session_revoke_user", "password": "pass1234"},
            format="json",
        )
        token_a = response_a.json()["data"]["accessToken"]
        self.assertEqual(AuthSession.objects.filter(user=user, revoked_at__isnull=True).count(), 2)

        user.status = UserStatus.DISABLED
        user.save(update_fields=["status", "updated_at"])

        self.assertEqual(AuthSession.objects.filter(user=user, revoked_at__isnull=True).count(), 0)

        client_a.credentials(HTTP_AUTHORIZATION=f"Bearer {token_a}")
        profile_response = client_a.get("/api/v1/iam/me/profile")
        self.assertEqual(profile_response.status_code, 401)
        self.assertEqual(profile_response.json()["code"], "A0401")


class MeScopeTests(IamApiTestCase):
    def test_business_user_without_membership_should_read_profile_and_empty_tenants(self):
        user = User.objects.create_user(username="me_unassigned", password="pass1234", status=1)
        ensure_staff_profile(user, name="未入租用户")
        self.login(username="me_unassigned", password="pass1234")

        profile_response = self.client.get("/api/v1/iam/me/profile")
        self.assertEqual(profile_response.status_code, 200)
        self.assertEqual(profile_response.json()["data"]["userId"], user.id)
        self.assertEqual(profile_response.json()["data"]["status"], "ACTIVE")

        tenants_response = self.client.get("/api/v1/iam/me/tenants")
        self.assertEqual(tenants_response.status_code, 200)
        self.assertEqual(tenants_response.json()["data"]["list"], [])
        self.assertEqual(tenants_response.json()["data"]["total"], 0)

    def test_platform_operator_should_be_forbidden_from_me_scope(self):
        platform_user = User.objects.create_user(
            username="platform_operator_only",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        self.login(username="platform_operator_only", password="pass1234")

        response = self.client.get("/api/v1/iam/me/profile")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "A0403")


class TenantScopeTests(IamApiTestCase):
    def setUp(self):
        super().setUp()
        call_command("seed_role_permissions", stdout=StringIO())
        self.admin_user = User.objects.create_user(username="tenant_admin_api", password="pass1234", status=1)
        ensure_staff_profile(self.admin_user, name="租户管理员")
        self.tenant, self.admin_member, self.admin_role = ensure_tenant_role_binding(
            self.admin_user,
            tenant_code="tenant_api_demo",
            role_code="tenant_admin",
            role_name="租户管理员",
            display_name="管理员",
        )
        self.login(username="tenant_admin_api", password="pass1234")
        self.client.credentials(
            HTTP_AUTHORIZATION=self.client._credentials["HTTP_AUTHORIZATION"],
            HTTP_X_TENANT_CODE=self.tenant.code,
        )

    def test_tenant_me_should_return_current_context(self):
        response = self.client.get("/api/v1/iam/tenant/me")

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["tenant"]["tenantCode"], self.tenant.code)
        self.assertEqual(data["member"]["memberId"], self.admin_member.id)
        self.assertEqual(data["member"]["roleCodes"], ["tenant_admin"])

    def test_tenant_scope_should_require_valid_header(self):
        bare_client = APIClient()
        bare_client.credentials(HTTP_AUTHORIZATION=self.client._credentials["HTTP_AUTHORIZATION"])
        missing_header_response = bare_client.get("/api/v1/iam/tenant/me")
        self.assertEqual(missing_header_response.status_code, 400)
        self.assertEqual(missing_header_response.json()["code"], "B0001")

        invalid_header_response = bare_client.get("/api/v1/iam/tenant/me", HTTP_X_TENANT_CODE="bad tenant code")
        self.assertEqual(invalid_header_response.status_code, 400)
        self.assertEqual(invalid_header_response.json()["code"], "B0001")

    def test_tenant_member_crud_should_follow_formal_contract(self):
        new_user = User.objects.create_user(username="tenant_member_target", password="pass1234", status=1)
        ensure_staff_profile(new_user, name="新成员")

        create_response = self.client.post(
            "/api/v1/iam/tenant/members",
            {
                "userId": new_user.id,
                "displayName": "",
                "roleCodes": ["business_admin"],
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        created = create_response.json()["data"]
        self.assertIsNone(created["displayName"])
        self.assertEqual(created["status"], "ACTIVE")
        self.assertEqual(created["roleCodes"], ["business_admin"])
        member_id = created["memberId"]

        detail_response = self.client.get(f"/api/v1/iam/tenant/members/{member_id}")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()["data"]["userId"], new_user.id)

        update_response = self.client.patch(
            f"/api/v1/iam/tenant/members/{member_id}",
            {"displayName": "新显示名"},
            format="json",
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()["data"]["displayName"], "新显示名")

        replace_roles_response = self.client.put(
            f"/api/v1/iam/tenant/members/{member_id}/roles",
            {"roleCodes": ["dispatcher", "auditor", "dispatcher"]},
            format="json",
        )
        self.assertEqual(replace_roles_response.status_code, 200)
        self.assertEqual(replace_roles_response.json()["data"]["roleCodes"], ["dispatcher", "auditor"])

        disable_response = self.client.post(f"/api/v1/iam/tenant/members/{member_id}/disable", {}, format="json")
        self.assertEqual(disable_response.status_code, 200)
        self.assertEqual(disable_response.json()["data"]["status"], "DISABLED")

        enable_response = self.client.post(f"/api/v1/iam/tenant/members/{member_id}/enable", {}, format="json")
        self.assertEqual(enable_response.status_code, 200)
        self.assertEqual(enable_response.json()["data"]["status"], "ACTIVE")

    def test_tenant_members_should_reject_duplicate_membership_and_protect_last_admin(self):
        duplicate_response = self.client.post(
            "/api/v1/iam/tenant/members",
            {"userId": self.admin_user.id},
            format="json",
        )
        self.assertEqual(duplicate_response.status_code, 409)
        self.assertEqual(duplicate_response.json()["code"], "C0103")

        remove_last_admin_response = self.client.put(
            f"/api/v1/iam/tenant/members/{self.admin_member.id}/roles",
            {"roleCodes": []},
            format="json",
        )
        self.assertEqual(remove_last_admin_response.status_code, 409)
        self.assertEqual(remove_last_admin_response.json()["code"], "C0203")

        disable_last_admin_response = self.client.post(
            f"/api/v1/iam/tenant/members/{self.admin_member.id}/disable",
            {},
            format="json",
        )
        self.assertEqual(disable_last_admin_response.status_code, 409)
        self.assertEqual(disable_last_admin_response.json()["code"], "C0203")

    def test_tenant_request_contract_and_audit_log_filters_should_follow_spec(self):
        new_user = User.objects.create_user(username="tenant_extra_field_user", password="pass1234", status=1)
        ensure_staff_profile(new_user, name="额外字段成员")

        create_response = self.client.post(
            "/api/v1/iam/tenant/members",
            {"userId": new_user.id, "tenantId": self.tenant.id},
            format="json",
        )
        self.assertEqual(create_response.status_code, 400)
        self.assertEqual(create_response.json()["code"], "B0001")
        self.assertIn("tenantId", create_response.json()["data"])

        older_log = AuditLog.objects.create(
            tenant=self.tenant,
            actor_user=self.admin_user,
            action="IAM_MEMBER_OLDER",
            target_type="tenant_member",
            target_id=str(self.admin_member.id),
        )
        recent_log = AuditLog.objects.create(
            tenant=self.tenant,
            actor_user=self.admin_user,
            action="IAM_MEMBER_RECENT",
            target_type="tenant_member",
            target_id=str(self.admin_member.id),
        )
        older_at = timezone.now() - timedelta(days=2)
        recent_at = timezone.now() - timedelta(hours=1)
        AuditLog.objects.filter(id=older_log.id).update(created_at=older_at)
        AuditLog.objects.filter(id=recent_log.id).update(created_at=recent_at)

        response = self.client.get(
            "/api/v1/iam/tenant/audit-logs",
            {"startAt": (timezone.now() - timedelta(days=1)).isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["total"], 1)
        self.assertEqual(response.json()["data"]["list"][0]["action"], "IAM_MEMBER_RECENT")

    def test_tenant_roles_and_members_list_should_be_non_platform_and_admin_only(self):
        list_response = self.client.get("/api/v1/iam/tenant/members")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["data"]["total"], 1)

        roles_response = self.client.get("/api/v1/iam/tenant/roles")
        self.assertEqual(roles_response.status_code, 200)
        role_codes = [item["code"] for item in roles_response.json()["data"]]
        self.assertIn("tenant_admin", role_codes)
        self.assertNotIn("platform_admin", role_codes)

        non_admin_user = User.objects.create_user(username="tenant_non_admin", password="pass1234", status=1)
        ensure_staff_profile(non_admin_user, name="普通成员")
        ensure_tenant_role_binding(
            non_admin_user,
            tenant=self.tenant,
            role_code="auditor",
            role_name="审计员",
        )

        non_admin_client = APIClient()
        login_response = non_admin_client.post(
            "/api/v1/iam/session/login",
            {"username": "tenant_non_admin", "password": "pass1234"},
            format="json",
        )
        token = login_response.json()["data"]["accessToken"]
        non_admin_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_TENANT_CODE=self.tenant.code)

        forbidden_response = non_admin_client.get("/api/v1/iam/tenant/members")
        self.assertEqual(forbidden_response.status_code, 403)
        self.assertEqual(forbidden_response.json()["code"], "A0403")


class PlatformScopeTests(IamApiTestCase):
    def setUp(self):
        super().setUp()
        call_command("seed_role_permissions", stdout=StringIO())
        self.platform_user = User.objects.create_user(
            username="platform_operator_api",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        self.login(username="platform_operator_api", password="pass1234")

    def test_platform_directories_and_tenants_should_follow_new_routes(self):
        permissions_response = self.client.get("/api/v1/iam/platform/permissions")
        self.assertEqual(permissions_response.status_code, 200)
        permission_codes = [item["code"] for item in permissions_response.json()["data"]]
        self.assertIn("access.view_tenant", permission_codes)

        roles_response = self.client.get("/api/v1/iam/platform/roles")
        self.assertEqual(roles_response.status_code, 200)
        role_codes = [item["code"] for item in roles_response.json()["data"]]
        self.assertIn("platform_admin", role_codes)

        create_tenant_response = self.client.post(
            "/api/v1/iam/platform/tenants",
            {"tenantCode": "platform_created_tenant", "name": "平台创建租户", "remark": "备注"},
            format="json",
        )
        self.assertEqual(create_tenant_response.status_code, 201)
        tenant_data = create_tenant_response.json()["data"]
        self.assertEqual(tenant_data["status"], "ACTIVE")
        self.assertIsNone(tenant_data["plan"])

        tenants_response = self.client.get("/api/v1/iam/platform/tenants")
        self.assertEqual(tenants_response.status_code, 200)
        self.assertGreaterEqual(tenants_response.json()["data"]["total"], 1)

        detail_response = self.client.get(f"/api/v1/iam/platform/tenants/{tenant_data['tenantId']}")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()["data"]["tenantCode"], "platform_created_tenant")

        disable_response = self.client.post(f"/api/v1/iam/platform/tenants/{tenant_data['tenantId']}/disable", {}, format="json")
        self.assertEqual(disable_response.status_code, 200)
        self.assertEqual(disable_response.json()["data"]["status"], "DISABLED")

        enable_response = self.client.post(f"/api/v1/iam/platform/tenants/{tenant_data['tenantId']}/enable", {}, format="json")
        self.assertEqual(enable_response.status_code, 200)
        self.assertEqual(enable_response.json()["data"]["status"], "ACTIVE")

    def test_platform_initialize_admin_should_be_only_formal_member_write_exception(self):
        tenant = Tenant.objects.create(code="bootstrap_tenant", name="待修复租户", status=TenantStatus.ACTIVE)
        user = User.objects.create_user(username="bootstrap_admin_user", password="pass1234", status=1)
        ensure_staff_profile(user, name="待初始化管理员")

        init_response = self.client.post(
            f"/api/v1/iam/platform/tenants/{tenant.id}/initialize-admin",
            {"userId": user.id, "displayName": ""},
            format="json",
        )
        self.assertEqual(init_response.status_code, 200)
        init_data = init_response.json()["data"]
        self.assertEqual(init_data["tenantId"], tenant.id)
        self.assertEqual(init_data["userId"], user.id)
        self.assertEqual(init_data["roleCodes"], ["tenant_admin"])
        self.assertIsNone(init_data["displayName"])

        repeat_response = self.client.post(
            f"/api/v1/iam/platform/tenants/{tenant.id}/initialize-admin",
            {"userId": user.id},
            format="json",
        )
        self.assertEqual(repeat_response.status_code, 409)
        self.assertEqual(repeat_response.json()["code"], "C0203")

    def test_platform_initialize_admin_should_reject_unknown_fields(self):
        tenant = Tenant.objects.create(code="bootstrap_tenant_extra", name="待修复租户2", status=TenantStatus.ACTIVE)
        user = User.objects.create_user(username="bootstrap_admin_user_2", password="pass1234", status=1)
        ensure_staff_profile(user, name="待初始化管理员2")

        response = self.client.post(
            f"/api/v1/iam/platform/tenants/{tenant.id}/initialize-admin",
            {"userId": user.id, "roleCodes": ["tenant_admin"]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "B0001")
        self.assertIn("roleCodes", response.json()["data"])

    def test_tenant_member_should_be_forbidden_from_platform_scope(self):
        tenant_user = User.objects.create_user(username="tenant_only_user", password="pass1234", status=1)
        ensure_staff_profile(tenant_user, name="普通租户成员")
        ensure_tenant_role_binding(
            tenant_user,
            tenant_code="tenant_only_scope",
            role_code="tenant_admin",
            role_name="租户管理员",
        )
        tenant_client = APIClient()
        login_response = tenant_client.post(
            "/api/v1/iam/session/login",
            {"username": "tenant_only_user", "password": "pass1234"},
            format="json",
        )
        token = login_response.json()["data"]["accessToken"]
        tenant_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = tenant_client.get("/api/v1/iam/platform/tenants")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "A0403")


class IamSchemaTests(IamApiTestCase):
    def test_business_schema_should_expose_new_iam_paths_only(self):
        response = self.client.get("/api/v1/docs/schema/")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        self.assertIn("/api/v1/iam/session/login", schema["paths"])
        self.assertIn("/api/v1/iam/tenant/members", schema["paths"])
        self.assertIn("/api/v1/iam/platform/tenants", schema["paths"])
        self.assertNotIn("/internal/auth/login", schema["paths"])
        self.assertNotIn("/api/v1/iam/me/permissions", schema["paths"])
        self.assertIn("BearerAuth", schema["components"]["securitySchemes"])

    def test_old_internal_auth_routes_should_be_unmounted(self):
        response = self.client.get("/internal/auth/login")
        self.assertEqual(response.status_code, 404)
