"""Platform-scope IAM test events.

- 权限目录与角色目录读取
- 角色详情读取与不存在资源 404
- 平台审计日志按 tenant / operator / time 过滤并校验参数
- 平台租户创建 / 列表 / 详情 / 启停用生命周期
- initialize-admin 作为唯一平台侧成员写入口
- tenant 成员禁止访问 platform/*
"""

from datetime import timedelta

from django.utils import timezone

from apps.access.models import AuditLog, Role, Tenant, TenantStatus
from apps.access.test_live_base import LivePlatformOperatorApiTestCase, User
from apps.access.test_support import ensure_staff_profile, ensure_tenant_role_binding


class LivePlatformScopeTests(LivePlatformOperatorApiTestCase):
    def test_platform_directories_should_follow_new_routes(self):
        permissions_response = self.client.get("/api/v1/iam/platform/permissions")
        self.assertEqual(permissions_response.status_code, 200)
        permission_codes = [item["code"] for item in permissions_response.json()["data"]]
        self.assertIn("access.view_tenant", permission_codes)

        roles_response = self.client.get("/api/v1/iam/platform/roles")
        self.assertEqual(roles_response.status_code, 200)
        role_codes = [item["code"] for item in roles_response.json()["data"]]
        self.assertIn("platform_admin", role_codes)

    def test_platform_role_detail_should_return_seeded_role_payload(self):
        role = Role.objects.get(code="platform_admin")

        response = self.client.get(f"/api/v1/iam/platform/roles/{role.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["roleId"], role.id)
        self.assertEqual(response.json()["data"]["code"], "platform_admin")
        granted_permissions = [item["permission"] for item in response.json()["data"]["permissionGrants"]]
        self.assertIn("access.view_tenant", granted_permissions)

    def test_platform_role_detail_should_return_404_for_missing_role(self):
        response = self.client.get("/api/v1/iam/platform/roles/999999")

        self.assertEqual(response.status_code, 404)

    def test_platform_audit_logs_should_filter_by_tenant_operator_and_time_range(self):
        target_tenant = Tenant.objects.create(code="platform_audit_target", name="目标租户", status=TenantStatus.ACTIVE)
        other_tenant = Tenant.objects.create(code="platform_audit_other", name="其他租户", status=TenantStatus.ACTIVE)
        other_operator = User.objects.create_user(
            username="platform_operator_other",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        ensure_staff_profile(other_operator, name="其他平台管理员")

        older_log = AuditLog.objects.create(
            tenant=other_tenant,
            actor_user=other_operator,
            action="IAM_PLATFORM_TENANT_DISABLED",
            target_type="tenant",
            target_id=str(other_tenant.id),
        )
        recent_log = AuditLog.objects.create(
            tenant=target_tenant,
            actor_user=self.platform_user,
            action="IAM_PLATFORM_TENANT_CREATED",
            target_type="tenant",
            target_id=str(target_tenant.id),
        )
        AuditLog.objects.filter(id=older_log.id).update(created_at=timezone.now() - timedelta(days=2))
        AuditLog.objects.filter(id=recent_log.id).update(created_at=timezone.now() - timedelta(hours=1))

        response = self.client.get(
            "/api/v1/iam/platform/audit-logs",
            {
                "tenantId": target_tenant.id,
                "operatorUserId": self.platform_user.id,
                "action": "IAM_PLATFORM_TENANT_CREATED",
                "startAt": (timezone.now() - timedelta(days=1)).isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["total"], 1)
        self.assertEqual(response.json()["data"]["list"][0]["action"], "IAM_PLATFORM_TENANT_CREATED")
        self.assertEqual(response.json()["data"]["list"][0]["tenantId"], target_tenant.id)
        self.assertEqual(response.json()["data"]["list"][0]["operatorUserId"], self.platform_user.id)

    def test_platform_audit_logs_should_reject_invalid_params_and_support_sorting(self):
        older_log = AuditLog.objects.create(
            tenant=None,
            actor_user=self.platform_user,
            action="IAM_PLATFORM_AUDIT_OLDER",
            target_type="tenant",
            target_id="1",
        )
        recent_log = AuditLog.objects.create(
            tenant=None,
            actor_user=self.platform_user,
            action="IAM_PLATFORM_AUDIT_RECENT",
            target_type="tenant",
            target_id="2",
        )
        AuditLog.objects.filter(id=older_log.id).update(created_at=timezone.now() - timedelta(days=2))
        AuditLog.objects.filter(id=recent_log.id).update(created_at=timezone.now() - timedelta(hours=1))

        asc_response = self.client.get("/api/v1/iam/platform/audit-logs", {"sortOrder": "asc"})
        self.assertEqual(asc_response.status_code, 200)
        self.assertEqual(asc_response.json()["data"]["list"][0]["action"], "IAM_PLATFORM_AUDIT_OLDER")

        invalid_sort_by_response = self.client.get("/api/v1/iam/platform/audit-logs", {"sortBy": "tenantId"})
        self.assertEqual(invalid_sort_by_response.status_code, 400)
        self.assertEqual(invalid_sort_by_response.json()["code"], "B0001")
        self.assertIn("sortBy", invalid_sort_by_response.json()["data"])

        invalid_sort_order_response = self.client.get("/api/v1/iam/platform/audit-logs", {"sortOrder": "up"})
        self.assertEqual(invalid_sort_order_response.status_code, 400)
        self.assertEqual(invalid_sort_order_response.json()["code"], "B0001")
        self.assertIn("sortOrder", invalid_sort_order_response.json()["data"])

        invalid_tenant_id_response = self.client.get("/api/v1/iam/platform/audit-logs", {"tenantId": "oops"})
        self.assertEqual(invalid_tenant_id_response.status_code, 400)
        self.assertEqual(invalid_tenant_id_response.json()["code"], "B0001")
        self.assertIn("tenantId", invalid_tenant_id_response.json()["data"])

        invalid_operator_user_id_response = self.client.get("/api/v1/iam/platform/audit-logs", {"operatorUserId": "oops"})
        self.assertEqual(invalid_operator_user_id_response.status_code, 400)
        self.assertEqual(invalid_operator_user_id_response.json()["code"], "B0001")
        self.assertIn("operatorUserId", invalid_operator_user_id_response.json()["data"])

        unsupported_query_response = self.client.get("/api/v1/iam/platform/audit-logs", {"plan": "pro"})
        self.assertEqual(unsupported_query_response.status_code, 400)
        self.assertEqual(unsupported_query_response.json()["code"], "B0001")
        self.assertIn("query", unsupported_query_response.json()["data"])

        invalid_end_at_response = self.client.get("/api/v1/iam/platform/audit-logs", {"endAt": "not-a-date"})
        self.assertEqual(invalid_end_at_response.status_code, 400)
        self.assertEqual(invalid_end_at_response.json()["code"], "B0001")
        self.assertIn("endAt", invalid_end_at_response.json()["data"])

    def test_platform_tenants_should_follow_formal_lifecycle(self):
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

        missing_detail_response = self.client.get("/api/v1/iam/platform/tenants/999999")
        self.assertEqual(missing_detail_response.status_code, 404)

        disable_response = self.client.post(f"/api/v1/iam/platform/tenants/{tenant_data['tenantId']}/disable", {}, format="json")
        self.assertEqual(disable_response.status_code, 200)
        self.assertEqual(disable_response.json()["data"]["status"], "DISABLED")

        enable_response = self.client.post(f"/api/v1/iam/platform/tenants/{tenant_data['tenantId']}/enable", {}, format="json")
        self.assertEqual(enable_response.status_code, 200)
        self.assertEqual(enable_response.json()["data"]["status"], "ACTIVE")

    def test_platform_tenants_should_enforce_contract_filters_and_sorting(self):
        alpha = Tenant.objects.create(code="platform_alpha", name="Alpha Tenant", status=TenantStatus.ACTIVE)
        beta = Tenant.objects.create(code="platform_beta", name="Beta Tenant", status=TenantStatus.DISABLED)
        gamma = Tenant.objects.create(code="platform_gamma", name="Gamma Tenant", status=TenantStatus.ACTIVE)

        sorted_response = self.client.get(
            "/api/v1/iam/platform/tenants",
            {"keywords": "platform_", "sortBy": "tenantCode", "sortOrder": "asc", "pageSize": 2},
        )
        self.assertEqual(sorted_response.status_code, 200)
        self.assertEqual(sorted_response.json()["data"]["total"], 3)
        self.assertEqual(
            [item["tenantCode"] for item in sorted_response.json()["data"]["list"]],
            [alpha.code, beta.code],
        )

        status_filtered_response = self.client.get("/api/v1/iam/platform/tenants", {"status": "DISABLED", "keywords": "platform_"})
        self.assertEqual(status_filtered_response.status_code, 200)
        self.assertEqual(status_filtered_response.json()["data"]["total"], 1)
        self.assertEqual(status_filtered_response.json()["data"]["list"][0]["tenantCode"], beta.code)

        invalid_status_response = self.client.get("/api/v1/iam/platform/tenants", {"status": "PENDING"})
        self.assertEqual(invalid_status_response.status_code, 400)
        self.assertEqual(invalid_status_response.json()["code"], "B0001")
        self.assertIn("status", invalid_status_response.json()["data"])

        invalid_sort_by_response = self.client.get("/api/v1/iam/platform/tenants", {"sortBy": "status"})
        self.assertEqual(invalid_sort_by_response.status_code, 400)
        self.assertEqual(invalid_sort_by_response.json()["code"], "B0001")
        self.assertIn("sortBy", invalid_sort_by_response.json()["data"])

        invalid_sort_order_response = self.client.get("/api/v1/iam/platform/tenants", {"sortOrder": "up"})
        self.assertEqual(invalid_sort_order_response.status_code, 400)
        self.assertEqual(invalid_sort_order_response.json()["code"], "B0001")
        self.assertIn("sortOrder", invalid_sort_order_response.json()["data"])

        unsupported_query_response = self.client.get("/api/v1/iam/platform/tenants", {"plan": "pro"})
        self.assertEqual(unsupported_query_response.status_code, 400)
        self.assertEqual(unsupported_query_response.json()["code"], "B0001")
        self.assertIn("query", unsupported_query_response.json()["data"])

    def test_platform_tenant_create_should_reject_unknown_fields_and_duplicate_code(self):
        existing_tenant = Tenant.objects.create(code="platform_duplicate_code", name="已存在租户", status=TenantStatus.ACTIVE)
        self.assertIsNotNone(existing_tenant.id)

        invalid_field_response = self.client.post(
            "/api/v1/iam/platform/tenants",
            {"tenantCode": "platform_invalid_fields", "name": "错误租户", "status": "ACTIVE"},
            format="json",
        )
        self.assertEqual(invalid_field_response.status_code, 400)
        self.assertEqual(invalid_field_response.json()["code"], "B0001")
        self.assertIn("status", invalid_field_response.json()["data"])

        duplicate_response = self.client.post(
            "/api/v1/iam/platform/tenants",
            {"tenantCode": "platform_duplicate_code", "name": "重复租户"},
            format="json",
        )
        self.assertEqual(duplicate_response.status_code, 409)
        self.assertEqual(duplicate_response.json()["code"], "C0101")

    def test_platform_tenant_enable_disable_should_enforce_empty_body_and_state_rules(self):
        tenant = Tenant.objects.create(code="platform_state_guard", name="状态守卫租户", status=TenantStatus.ACTIVE)

        disable_body_response = self.client.post(
            f"/api/v1/iam/platform/tenants/{tenant.id}/disable",
            {"reason": "manual"},
            format="json",
        )
        self.assertEqual(disable_body_response.status_code, 400)
        self.assertEqual(disable_body_response.json()["code"], "B0001")
        self.assertIn("body", disable_body_response.json()["data"])

        missing_disable_response = self.client.post("/api/v1/iam/platform/tenants/999999/disable", {}, format="json")
        self.assertEqual(missing_disable_response.status_code, 404)

        disable_response = self.client.post(f"/api/v1/iam/platform/tenants/{tenant.id}/disable", {}, format="json")
        self.assertEqual(disable_response.status_code, 200)
        self.assertEqual(disable_response.json()["data"]["status"], "DISABLED")

        disable_again_response = self.client.post(f"/api/v1/iam/platform/tenants/{tenant.id}/disable", {}, format="json")
        self.assertEqual(disable_again_response.status_code, 409)
        self.assertEqual(disable_again_response.json()["code"], "C0203")

        enable_body_response = self.client.post(
            f"/api/v1/iam/platform/tenants/{tenant.id}/enable",
            {"reason": "manual"},
            format="json",
        )
        self.assertEqual(enable_body_response.status_code, 400)
        self.assertEqual(enable_body_response.json()["code"], "B0001")
        self.assertIn("body", enable_body_response.json()["data"])

        missing_enable_response = self.client.post("/api/v1/iam/platform/tenants/999999/enable", {}, format="json")
        self.assertEqual(missing_enable_response.status_code, 404)

        enable_response = self.client.post(f"/api/v1/iam/platform/tenants/{tenant.id}/enable", {}, format="json")
        self.assertEqual(enable_response.status_code, 200)
        self.assertEqual(enable_response.json()["data"]["status"], "ACTIVE")

        enable_again_response = self.client.post(f"/api/v1/iam/platform/tenants/{tenant.id}/enable", {}, format="json")
        self.assertEqual(enable_again_response.status_code, 409)
        self.assertEqual(enable_again_response.json()["code"], "C0203")

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

    def test_platform_initialize_admin_should_reject_unknown_fields_and_invalid_tenant_state(self):
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

        tenant.status = TenantStatus.DISABLED
        tenant.save(update_fields=["status", "updated_at"])
        disabled_tenant_response = self.client.post(
            f"/api/v1/iam/platform/tenants/{tenant.id}/initialize-admin",
            {"userId": user.id},
            format="json",
        )
        self.assertEqual(disabled_tenant_response.status_code, 409)
        self.assertEqual(disabled_tenant_response.json()["code"], "C0203")

        missing_tenant_response = self.client.post(
            "/api/v1/iam/platform/tenants/999999/initialize-admin",
            {"userId": user.id},
            format="json",
        )
        self.assertEqual(missing_tenant_response.status_code, 404)

        missing_user_response = self.client.post(
            f"/api/v1/iam/platform/tenants/{tenant.id}/initialize-admin",
            {"userId": 999999},
            format="json",
        )
        self.assertEqual(missing_user_response.status_code, 404)

    def test_tenant_member_should_be_forbidden_from_platform_scope(self):
        tenant_user = User.objects.create_user(username="tenant_only_user", password="pass1234", status=1)
        ensure_staff_profile(tenant_user, name="普通租户成员")
        ensure_tenant_role_binding(
            tenant_user,
            tenant_code="tenant_only_scope",
            role_code="tenant_admin",
            role_name="租户管理员",
        )
        tenant_client = self.new_client()
        login_response = tenant_client.post(
            "/api/v1/iam/session/login",
            {"username": "tenant_only_user", "password": "pass1234"},
            format="json",
        )
        token = login_response.json()["data"]["accessToken"]
        tenant_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        forbidden_requests = [
            ("GET", "/api/v1/iam/platform/permissions", None),
            ("GET", "/api/v1/iam/platform/roles", None),
            ("GET", f"/api/v1/iam/platform/roles/{Role.objects.get(code='platform_admin').id}", None),
            ("GET", "/api/v1/iam/platform/audit-logs", None),
            ("GET", "/api/v1/iam/platform/tenants", None),
            ("GET", "/api/v1/iam/platform/tenants/999999", None),
            ("POST", "/api/v1/iam/platform/tenants", {"tenantCode": "forbidden_platform_tenant", "name": "越权租户"}),
            ("POST", "/api/v1/iam/platform/tenants/999999/disable", {}),
            ("POST", "/api/v1/iam/platform/tenants/999999/enable", {}),
            ("POST", "/api/v1/iam/platform/tenants/999999/initialize-admin", {"userId": self.platform_user.id}),
        ]

        for method, path, payload in forbidden_requests:
            if method == "GET":
                response = tenant_client.get(path, payload)
            else:
                response = tenant_client.post(path, payload, format="json")
            self.assertEqual(response.status_code, 403, (method, path, response.json()))
            self.assertEqual(response.json()["code"], "A0403")
