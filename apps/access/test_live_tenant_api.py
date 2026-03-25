"""Tenant-scope IAM test events.

- tenant/me 读取当前租户上下文
- tenant/* 强制要求有效 X-TENANT-CODE
- 成员创建/详情/更新/角色替换/启用/停用生命周期
- 重复入租与最后一个 tenant_admin 保护
- 审计日志按时间过滤
- 角色目录与成员目录仅 tenant_admin 可访问
"""

from datetime import timedelta

from django.utils import timezone

from apps.access.models import AuditLog, TenantMemberStatus, TenantStatus
from apps.access.test_live_base import LiveTenantAdminApiTestCase, User
from apps.access.test_support import ensure_staff_profile, ensure_tenant_role_binding


class LiveTenantScopeTests(LiveTenantAdminApiTestCase):
    def test_tenant_me_should_return_current_context(self):
        response = self.client.get("/api/v1/iam/tenant/me")

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["tenant"]["tenantCode"], self.tenant.code)
        self.assertEqual(data["member"]["memberId"], self.admin_member.id)
        self.assertEqual(data["member"]["roleCodes"], ["tenant_admin"])

    def test_tenant_scope_should_require_valid_header(self):
        bare_client = self.new_client()
        bare_client.credentials(HTTP_AUTHORIZATION=self.client._credentials["HTTP_AUTHORIZATION"])
        missing_header_response = bare_client.get("/api/v1/iam/tenant/me")
        self.assertEqual(missing_header_response.status_code, 400)
        self.assertEqual(missing_header_response.json()["code"], "B0001")

        invalid_header_response = bare_client.get("/api/v1/iam/tenant/me", HTTP_X_TENANT_CODE="bad tenant code")
        self.assertEqual(invalid_header_response.status_code, 400)
        self.assertEqual(invalid_header_response.json()["code"], "B0001")

    def test_tenant_scope_should_forbid_disabled_tenant_and_inactive_membership(self):
        disabled_tenant = self.tenant
        disabled_tenant.status = TenantStatus.DISABLED
        disabled_tenant.save(update_fields=["status", "updated_at"])

        disabled_tenant_response = self.client.get("/api/v1/iam/tenant/me")
        self.assertEqual(disabled_tenant_response.status_code, 403)
        self.assertEqual(disabled_tenant_response.json()["code"], "A0403")

        inactive_member_user = User.objects.create_user(username="tenant_scope_inactive_member", password="pass1234", status=1)
        ensure_staff_profile(inactive_member_user, name="停用成员")
        inactive_tenant, inactive_member, _ = ensure_tenant_role_binding(
            inactive_member_user,
            tenant_code="tenant_scope_inactive_member_tenant",
            role_code="tenant_admin",
            role_name="租户管理员",
        )
        inactive_member.status = TenantMemberStatus.DISABLED
        inactive_member.save(update_fields=["status", "responded_at", "joined_at", "updated_at"])

        inactive_member_client = self.new_client()
        self.authenticate_client(
            inactive_member_client,
            username="tenant_scope_inactive_member",
            password="pass1234",
            tenant_code=inactive_tenant.code,
        )
        inactive_member_response = inactive_member_client.get("/api/v1/iam/tenant/me")
        self.assertEqual(inactive_member_response.status_code, 403)
        self.assertEqual(inactive_member_response.json()["code"], "A0403")

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

    def test_tenant_member_create_should_return_404_for_ineligible_user(self):
        missing_user_response = self.client.post(
            "/api/v1/iam/tenant/members",
            {"userId": 999999, "roleCodes": ["dispatcher"]},
            format="json",
        )
        self.assertEqual(missing_user_response.status_code, 404)

        platform_user = User.objects.create_user(
            username="tenant_member_ineligible_platform",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        ensure_staff_profile(platform_user, name="不合格平台用户")
        platform_user_response = self.client.post(
            "/api/v1/iam/tenant/members",
            {"userId": platform_user.id},
            format="json",
        )
        self.assertEqual(platform_user_response.status_code, 404)

    def test_tenant_members_list_should_support_filters_and_sorting_contract(self):
        scenarios = [
            ("tenant_filter_alpha", "Beta"),
            ("tenant_filter_beta", ""),
            ("tenant_filter_gamma", "Alpha"),
        ]
        created_members = []
        for username, display_name in scenarios:
            user = User.objects.create_user(username=username, password="pass1234", status=1)
            ensure_staff_profile(user, name=username)
            response = self.client.post(
                "/api/v1/iam/tenant/members",
                {"userId": user.id, "displayName": display_name, "roleCodes": ["dispatcher"]},
                format="json",
            )
            self.assertEqual(response.status_code, 201)
            created_members.append((user, response.json()["data"]))

        disabled_member_id = created_members[1][1]["memberId"]
        disable_response = self.client.post(f"/api/v1/iam/tenant/members/{disabled_member_id}/disable", {}, format="json")
        self.assertEqual(disable_response.status_code, 200)

        default_sorted_response = self.client.get("/api/v1/iam/tenant/members", {"keywords": "tenant_filter_"})
        self.assertEqual(default_sorted_response.status_code, 200)
        self.assertEqual(
            [item["username"] for item in default_sorted_response.json()["data"]["list"]],
            ["tenant_filter_gamma", "tenant_filter_alpha", "tenant_filter_beta"],
        )
        self.assertEqual(default_sorted_response.json()["data"]["list"][-1]["displayName"], None)

        status_filtered_response = self.client.get(
            "/api/v1/iam/tenant/members",
            {"keywords": "tenant_filter_", "status": "DISABLED"},
        )
        self.assertEqual(status_filtered_response.status_code, 200)
        self.assertEqual(status_filtered_response.json()["data"]["total"], 1)
        self.assertEqual(status_filtered_response.json()["data"]["list"][0]["username"], "tenant_filter_beta")

        user_id_filtered_response = self.client.get("/api/v1/iam/tenant/members", {"userId": created_members[0][0].id})
        self.assertEqual(user_id_filtered_response.status_code, 200)
        self.assertEqual(user_id_filtered_response.json()["data"]["total"], 1)
        self.assertEqual(user_id_filtered_response.json()["data"]["list"][0]["username"], "tenant_filter_alpha")

        username_sorted_response = self.client.get(
            "/api/v1/iam/tenant/members",
            {"keywords": "tenant_filter_", "sortBy": "username", "sortOrder": "desc"},
        )
        self.assertEqual(username_sorted_response.status_code, 200)
        self.assertEqual(
            [item["username"] for item in username_sorted_response.json()["data"]["list"]],
            ["tenant_filter_gamma", "tenant_filter_beta", "tenant_filter_alpha"],
        )

    def test_tenant_members_list_should_reject_invalid_query_params_and_sort_rules(self):
        unsupported_query_response = self.client.get("/api/v1/iam/tenant/members", {"tenantId": self.tenant.id})
        self.assertEqual(unsupported_query_response.status_code, 400)
        self.assertEqual(unsupported_query_response.json()["code"], "B0001")
        self.assertIn("query", unsupported_query_response.json()["data"])

        invalid_status_response = self.client.get("/api/v1/iam/tenant/members", {"status": "INVITED"})
        self.assertEqual(invalid_status_response.status_code, 400)
        self.assertEqual(invalid_status_response.json()["code"], "B0001")
        self.assertIn("status", invalid_status_response.json()["data"])

        invalid_user_id_response = self.client.get("/api/v1/iam/tenant/members", {"userId": "oops"})
        self.assertEqual(invalid_user_id_response.status_code, 400)
        self.assertEqual(invalid_user_id_response.json()["code"], "B0001")
        self.assertIn("userId", invalid_user_id_response.json()["data"])

        invalid_sort_by_response = self.client.get("/api/v1/iam/tenant/members", {"sortBy": "tenantId"})
        self.assertEqual(invalid_sort_by_response.status_code, 400)
        self.assertEqual(invalid_sort_by_response.json()["code"], "B0001")
        self.assertIn("sortBy", invalid_sort_by_response.json()["data"])

        invalid_sort_order_response = self.client.get("/api/v1/iam/tenant/members", {"sortOrder": "up"})
        self.assertEqual(invalid_sort_order_response.status_code, 400)
        self.assertEqual(invalid_sort_order_response.json()["code"], "B0001")
        self.assertIn("sortOrder", invalid_sort_order_response.json()["data"])

    def test_tenant_member_detail_and_patch_should_return_404_for_missing_member(self):
        detail_response = self.client.get("/api/v1/iam/tenant/members/999999")
        self.assertEqual(detail_response.status_code, 404)

        patch_response = self.client.patch(
            "/api/v1/iam/tenant/members/999999",
            {"displayName": "不存在成员"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 404)

    def test_tenant_member_mutations_should_enforce_strict_and_empty_body_contracts(self):
        user = User.objects.create_user(username="tenant_contract_member", password="pass1234", status=1)
        ensure_staff_profile(user, name="成员契约用户")
        create_response = self.client.post(
            "/api/v1/iam/tenant/members",
            {"userId": user.id, "displayName": "待清空", "roleCodes": ["dispatcher"]},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        member_id = create_response.json()["data"]["memberId"]

        clear_display_name_response = self.client.patch(
            f"/api/v1/iam/tenant/members/{member_id}",
            {"displayName": ""},
            format="json",
        )
        self.assertEqual(clear_display_name_response.status_code, 200)
        self.assertIsNone(clear_display_name_response.json()["data"]["displayName"])

        invalid_patch_response = self.client.patch(
            f"/api/v1/iam/tenant/members/{member_id}",
            {"display_name": "旧字段"},
            format="json",
        )
        self.assertEqual(invalid_patch_response.status_code, 400)
        self.assertEqual(invalid_patch_response.json()["code"], "B0001")
        self.assertIn("display_name", invalid_patch_response.json()["data"])

        invalid_roles_field_response = self.client.put(
            f"/api/v1/iam/tenant/members/{member_id}/roles",
            {"roles": ["auditor"]},
            format="json",
        )
        self.assertEqual(invalid_roles_field_response.status_code, 400)
        self.assertEqual(invalid_roles_field_response.json()["code"], "B0001")
        self.assertIn("roles", invalid_roles_field_response.json()["data"])

        missing_role_codes_response = self.client.put(
            f"/api/v1/iam/tenant/members/{member_id}/roles",
            {},
            format="json",
        )
        self.assertEqual(missing_role_codes_response.status_code, 400)
        self.assertEqual(missing_role_codes_response.json()["code"], "B0001")
        self.assertIn("roleCodes", missing_role_codes_response.json()["data"])

        invalid_role_code_response = self.client.put(
            f"/api/v1/iam/tenant/members/{member_id}/roles",
            {"roleCodes": ["platform_admin"]},
            format="json",
        )
        self.assertEqual(invalid_role_code_response.status_code, 409)
        self.assertEqual(invalid_role_code_response.json()["code"], "C0203")
        self.assertIn("roleCodes", invalid_role_code_response.json()["data"])

        disable_body_response = self.client.post(
            f"/api/v1/iam/tenant/members/{member_id}/disable",
            {"reason": "manual"},
            format="json",
        )
        self.assertEqual(disable_body_response.status_code, 400)
        self.assertEqual(disable_body_response.json()["code"], "B0001")
        self.assertIn("body", disable_body_response.json()["data"])

        disable_response = self.client.post(f"/api/v1/iam/tenant/members/{member_id}/disable", {}, format="json")
        self.assertEqual(disable_response.status_code, 200)
        self.assertEqual(disable_response.json()["data"]["status"], "DISABLED")

        disable_again_response = self.client.post(f"/api/v1/iam/tenant/members/{member_id}/disable", {}, format="json")
        self.assertEqual(disable_again_response.status_code, 409)
        self.assertEqual(disable_again_response.json()["code"], "C0203")

        enable_body_response = self.client.post(
            f"/api/v1/iam/tenant/members/{member_id}/enable",
            {"reason": "manual"},
            format="json",
        )
        self.assertEqual(enable_body_response.status_code, 400)
        self.assertEqual(enable_body_response.json()["code"], "B0001")
        self.assertIn("body", enable_body_response.json()["data"])

        enable_response = self.client.post(f"/api/v1/iam/tenant/members/{member_id}/enable", {}, format="json")
        self.assertEqual(enable_response.status_code, 200)
        self.assertEqual(enable_response.json()["data"]["status"], "ACTIVE")

        enable_again_response = self.client.post(f"/api/v1/iam/tenant/members/{member_id}/enable", {}, format="json")
        self.assertEqual(enable_again_response.status_code, 409)
        self.assertEqual(enable_again_response.json()["code"], "C0203")

    def test_tenant_audit_logs_should_enforce_permissions_filters_and_sorting(self):
        auditor_user = User.objects.create_user(username="tenant_auditor_user", password="pass1234", status=1)
        ensure_staff_profile(auditor_user, name="租户审计员")
        ensure_tenant_role_binding(
            auditor_user,
            tenant=self.tenant,
            role_code="auditor",
            role_name="审计员",
        )

        dispatcher_user = User.objects.create_user(username="tenant_dispatcher_user", password="pass1234", status=1)
        ensure_staff_profile(dispatcher_user, name="租户调度员")
        ensure_tenant_role_binding(
            dispatcher_user,
            tenant=self.tenant,
            role_code="dispatcher",
            role_name="调度员",
        )

        older_log = AuditLog.objects.create(
            tenant=self.tenant,
            actor_user=auditor_user,
            action="TENANT_AUDIT_OLDER",
            target_type="tenant_member",
            target_id=str(self.admin_member.id),
        )
        recent_log = AuditLog.objects.create(
            tenant=self.tenant,
            actor_user=self.admin_user,
            action="TENANT_AUDIT_RECENT",
            target_type="tenant_member",
            target_id=str(self.admin_member.id),
        )
        older_at = timezone.now() - timedelta(days=2)
        recent_at = timezone.now() - timedelta(hours=1)
        AuditLog.objects.filter(id=older_log.id).update(created_at=older_at)
        AuditLog.objects.filter(id=recent_log.id).update(created_at=recent_at)

        auditor_client = self.new_client()
        self.authenticate_client(
            auditor_client,
            username="tenant_auditor_user",
            password="pass1234",
            tenant_code=self.tenant.code,
        )
        auditor_response = auditor_client.get("/api/v1/iam/tenant/audit-logs")
        self.assertEqual(auditor_response.status_code, 200)

        dispatcher_client = self.new_client()
        self.authenticate_client(
            dispatcher_client,
            username="tenant_dispatcher_user",
            password="pass1234",
            tenant_code=self.tenant.code,
        )
        dispatcher_response = dispatcher_client.get("/api/v1/iam/tenant/audit-logs")
        self.assertEqual(dispatcher_response.status_code, 403)
        self.assertEqual(dispatcher_response.json()["code"], "A0403")

        filtered_response = self.client.get(
            "/api/v1/iam/tenant/audit-logs",
            {
                "action": "TENANT_AUDIT_OLDER",
                "operatorUserId": auditor_user.id,
                "endAt": (timezone.now() - timedelta(days=1)).isoformat(),
            },
        )
        self.assertEqual(filtered_response.status_code, 200)
        self.assertEqual(filtered_response.json()["data"]["total"], 1)
        self.assertEqual(filtered_response.json()["data"]["list"][0]["action"], "TENANT_AUDIT_OLDER")

        asc_response = self.client.get("/api/v1/iam/tenant/audit-logs", {"sortOrder": "asc"})
        self.assertEqual(asc_response.status_code, 200)
        self.assertEqual(asc_response.json()["data"]["list"][0]["action"], "TENANT_AUDIT_OLDER")

        invalid_sort_by_response = self.client.get("/api/v1/iam/tenant/audit-logs", {"sortBy": "tenantId"})
        self.assertEqual(invalid_sort_by_response.status_code, 400)
        self.assertEqual(invalid_sort_by_response.json()["code"], "B0001")
        self.assertIn("sortBy", invalid_sort_by_response.json()["data"])

        invalid_sort_order_response = self.client.get("/api/v1/iam/tenant/audit-logs", {"sortOrder": "up"})
        self.assertEqual(invalid_sort_order_response.status_code, 400)
        self.assertEqual(invalid_sort_order_response.json()["code"], "B0001")
        self.assertIn("sortOrder", invalid_sort_order_response.json()["data"])

        invalid_operator_user_id_response = self.client.get("/api/v1/iam/tenant/audit-logs", {"operatorUserId": "oops"})
        self.assertEqual(invalid_operator_user_id_response.status_code, 400)
        self.assertEqual(invalid_operator_user_id_response.json()["code"], "B0001")
        self.assertIn("operatorUserId", invalid_operator_user_id_response.json()["data"])

        unsupported_query_response = self.client.get("/api/v1/iam/tenant/audit-logs", {"tenantId": self.tenant.id})
        self.assertEqual(unsupported_query_response.status_code, 400)
        self.assertEqual(unsupported_query_response.json()["code"], "B0001")
        self.assertIn("query", unsupported_query_response.json()["data"])

        invalid_start_at_response = self.client.get("/api/v1/iam/tenant/audit-logs", {"startAt": "not-a-date"})
        self.assertEqual(invalid_start_at_response.status_code, 400)
        self.assertEqual(invalid_start_at_response.json()["code"], "B0001")
        self.assertIn("startAt", invalid_start_at_response.json()["data"])

        invalid_end_at_response = self.client.get("/api/v1/iam/tenant/audit-logs", {"endAt": "not-a-date"})
        self.assertEqual(invalid_end_at_response.status_code, 400)
        self.assertEqual(invalid_end_at_response.json()["code"], "B0001")
        self.assertIn("endAt", invalid_end_at_response.json()["data"])

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

        non_admin_client = self.new_client()
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

        forbidden_roles_response = non_admin_client.get("/api/v1/iam/tenant/roles")
        self.assertEqual(forbidden_roles_response.status_code, 403)
        self.assertEqual(forbidden_roles_response.json()["code"], "A0403")
