"""Me-scope IAM test events.

- 未入租业务账号读取 profile / tenants 空列表
- 仅返回 ACTIVE 租户 + ACTIVE 成员关系
- me/tenants 拒绝未声明 query 参数
- platform_operator 禁止访问 me/*
"""

from apps.access.models import TenantMemberStatus, TenantStatus
from apps.access.test_live_base import LiveIamApiTestCase, User
from apps.access.test_support import ensure_staff_profile, ensure_tenant_role_binding


class LiveMeScopeTests(LiveIamApiTestCase):
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

    def test_me_tenants_should_only_return_active_memberships_in_active_tenants(self):
        user = User.objects.create_user(username="me_active_only", password="pass1234", status=1)
        ensure_staff_profile(user, name="多租户用户")

        active_tenant, active_member, _ = ensure_tenant_role_binding(
            user,
            tenant_code="me_active_tenant",
            role_code="dispatcher",
            role_name="调度员",
            display_name="调度成员",
        )
        disabled_tenant, _, _ = ensure_tenant_role_binding(
            user,
            tenant_code="me_disabled_tenant",
            role_code="auditor",
            role_name="审计员",
            display_name="停用租户成员",
        )
        disabled_tenant.status = TenantStatus.DISABLED
        disabled_tenant.save(update_fields=["status", "updated_at"])

        _, disabled_member, _ = ensure_tenant_role_binding(
            user,
            tenant_code="me_disabled_member_tenant",
            role_code="business_admin",
            role_name="业务管理员",
            display_name="已停用成员",
        )
        disabled_member.status = TenantMemberStatus.DISABLED
        disabled_member.save(update_fields=["status", "updated_at"])

        self.login(username="me_active_only", password="pass1234")

        response = self.client.get("/api/v1/iam/me/tenants")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["total"], 1)
        self.assertEqual(response.json()["data"]["list"][0]["tenantCode"], active_tenant.code)
        self.assertEqual(response.json()["data"]["list"][0]["memberId"], active_member.id)
        self.assertEqual(response.json()["data"]["list"][0]["roleCodes"], ["dispatcher"])

    def test_me_tenants_should_reject_unsupported_query_params(self):
        user = User.objects.create_user(username="me_query_guard", password="pass1234", status=1)
        ensure_staff_profile(user, name="查询守卫用户")
        ensure_tenant_role_binding(
            user,
            tenant_code="me_query_guard_tenant",
            role_code="dispatcher",
            role_name="调度员",
        )
        self.login(username="me_query_guard", password="pass1234")

        response = self.client.get("/api/v1/iam/me/tenants", {"status": "ACTIVE"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "B0001")
        self.assertIn("query", response.json()["data"])
        self.assertIn("status", response.json()["data"]["query"][0])

    def test_me_tenants_should_follow_pagination_contract(self):
        user = User.objects.create_user(username="me_pagination_user", password="pass1234", status=1)
        ensure_staff_profile(user, name="分页用户")

        tenant_codes = []
        for idx in range(101):
            tenant_code = f"me_page_{idx:03d}"
            ensure_tenant_role_binding(
                user,
                tenant_code=tenant_code,
                role_code="dispatcher",
                role_name="调度员",
            )
            tenant_codes.append(tenant_code)

        self.login(username="me_pagination_user", password="pass1234")

        default_response = self.client.get("/api/v1/iam/me/tenants")
        self.assertEqual(default_response.status_code, 200)
        self.assertEqual(default_response.json()["data"]["total"], 101)
        self.assertEqual(len(default_response.json()["data"]["list"]), 20)
        self.assertEqual(default_response.json()["data"]["list"][0]["tenantCode"], tenant_codes[0])
        self.assertEqual(default_response.json()["data"]["list"][-1]["tenantCode"], tenant_codes[19])

        page_six_response = self.client.get("/api/v1/iam/me/tenants", {"pageNum": 6, "pageSize": 20})
        self.assertEqual(page_six_response.status_code, 200)
        self.assertEqual(len(page_six_response.json()["data"]["list"]), 1)
        self.assertEqual(page_six_response.json()["data"]["list"][0]["tenantCode"], tenant_codes[100])

        capped_response = self.client.get("/api/v1/iam/me/tenants", {"pageSize": 999})
        self.assertEqual(capped_response.status_code, 200)
        self.assertEqual(len(capped_response.json()["data"]["list"]), 100)
        self.assertEqual(capped_response.json()["data"]["list"][0]["tenantCode"], tenant_codes[0])
        self.assertEqual(capped_response.json()["data"]["list"][-1]["tenantCode"], tenant_codes[99])

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
