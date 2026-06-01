from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import DirectoryStatus, Tenant, TenantStatus, UserStatus
from apps.iam_v2.models import Department, FixedRole, V2AccountProfile, V2AccountRoleAssignment
from apps.resource_v2.models import V2AuditLog

User = get_user_model()


def create_v2_actor(*, username: str, role_code: str | None, department: Department, is_platform_admin: bool = False):
    user = User.objects.create_user(username=username, password="pass1234", status=1, is_platform_admin=is_platform_admin)
    profile = V2AccountProfile.objects.create(user=user, department=department)
    if role_code is not None:
        V2AccountRoleAssignment.objects.create(account_profile=profile, role_code=role_code, assigned_by_user=user)
    return user, profile


class ApiV2SchemaBoundaryTests(TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def test_v2_schema_should_expose_only_new_v2_boundaries(self):
        response = self.client.get("/api/v2/docs/schema/")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        paths = response.json()["paths"]
        self.assertTrue(paths)
        self.assertTrue(all(path.startswith("/api/v2/") for path in paths), sorted(paths))

        expected_paths = {
            "/api/v2/iam/me/context",
            "/api/v2/iam/departments",
            "/api/v2/iam/departments/{id}",
            "/api/v2/iam/departments/{id}/enable",
            "/api/v2/iam/departments/{id}/disable",
            "/api/v2/iam/accounts",
            "/api/v2/iam/accounts/{id}",
            "/api/v2/iam/accounts/{id}/roles",
            "/api/v2/iam/roles",
            "/api/v2/resource/dji-connections",
            "/api/v2/resource/dji-connections/{id}",
            "/api/v2/resource/dji-connections/{id}/discover",
            "/api/v2/resource/drones",
            "/api/v2/resource/drones/{id}",
            "/api/v2/resource/docks",
            "/api/v2/resource/docks/{id}",
            "/api/v2/resource/payloads",
            "/api/v2/resource/payloads/{id}",
            "/api/v2/resource/summary",
            "/api/v2/resource/bindings",
            "/api/v2/resource/bindings/{id}",
            "/api/v2/resource/share-groups",
            "/api/v2/resource/share-groups/{id}",
            "/api/v2/resource/share-groups/{id}/departments",
            "/api/v2/resource/share-groups/{id}/departments/{department_id}",
            "/api/v2/resource/share-groups/{id}/resources",
            "/api/v2/resource/share-groups/{id}/resources/{resource_share_id}",
            "/api/v2/resource/audit-logs",
            "/api/v2/workforce/pilots",
            "/api/v2/workforce/pilots/{id}",
            "/api/v2/workforce/pilots/{pilot_id}/qualifications",
            "/api/v2/workforce/pilots/{pilot_id}/qualifications/{id}",
            "/api/v2/inspection/routes",
            "/api/v2/inspection/routes/{id}",
            "/api/v2/inspection/missions",
            "/api/v2/inspection/missions/{id}",
            "/api/v2/inspection/missions/{id}/start",
            "/api/v2/inspection/missions/{id}/complete",
            "/api/v2/inspection/missions/{id}/cancel",
            "/api/v2/inspection/missions/{id}/fail",
            "/api/v2/inspection/missions/{id}/abort",
            "/api/v2/inspection/active-flights",
            "/api/v2/inspection/active-flights/{id}",
            "/api/v2/inspection/telemetry/snapshots",
            "/api/v2/inspection/live/capacity",
            "/api/v2/inspection/live/start",
            "/api/v2/inspection/live/stop",
            "/api/v2/inspection/live/update",
            "/api/v2/inspection/live/switch",
            "/api/v2/inspection/flight-records",
            "/api/v2/inspection/flight-records/{id}",
            "/api/v2/inspection/flight-records/{id}/refresh-media",
            "/api/v2/inspection/media-files",
            "/api/v2/inspection/media-files/{id}",
        }
        self.assertTrue(expected_paths.issubset(set(paths)))

        removed_paths = {
            "/api/v2/__internal__/dji/sync/devices",
            "/api/v2/__internal__/dji/sync/media",
            "/api/v2/drones",
            "/api/v2/drones/available",
            "/api/v2/routes",
            "/api/v2/missions",
            "/api/v2/flight-records",
            "/api/v2/media-files",
            "/api/v2/resource/routes",
            "/api/v2/resource/routes/{id}",
            "/api/v2/resource/missions",
            "/api/v2/resource/missions/{id}",
            "/api/v2/resource/flight-records",
            "/api/v2/resource/flight-records/{id}",
            "/api/v2/resource/media-files",
            "/api/v2/resource/media-files/{id}",
            "/api/v2/iam/tenant/dji-platforms",
        }
        self.assertTrue(removed_paths.isdisjoint(set(paths)))

    def test_v2_docs_should_be_available(self):
        response = self.client.get("/api/v2/docs/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/api/v2/docs/schema/")


class IamV2ApiTests(TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.tenant = Tenant.objects.create(code="v2_tenant", name="V2 租户", status=TenantStatus.ACTIVE)
        self.root = Department.objects.create(tenant=self.tenant, name="总部")
        self.super_user, self.super_profile = create_v2_actor(
            username="v2_super",
            role_code=FixedRole.PLATFORM_SUPER_ADMIN,
            department=self.root,
        )
        self.client.force_authenticate(self.super_user)

    def test_me_context_should_return_department_and_fixed_roles_without_tenant_header(self):
        response = self.client.get("/api/v2/iam/me/context")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        data = response.data["data"]
        self.assertEqual(data["user"]["username"], "v2_super")
        self.assertEqual(data["department"]["id"], self.root.id)
        self.assertEqual(data["department"]["path"], f"/{self.root.id}/")
        self.assertEqual(data["roles"], [FixedRole.PLATFORM_SUPER_ADMIN])

    def test_me_context_should_allow_active_v2_profile_without_roles(self):
        no_role_user, _profile = create_v2_actor(
            username="v2_no_role",
            role_code=None,
            department=self.root,
        )
        self.client.force_authenticate(no_role_user)

        response = self.client.get("/api/v2/iam/me/context")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        data = response.data["data"]
        self.assertEqual(data["user"]["username"], "v2_no_role")
        self.assertEqual(data["department"]["id"], self.root.id)
        self.assertEqual(data["roles"], [])

    def test_platform_super_admin_should_create_child_department_and_reject_move(self):
        create_response = self.client.post(
            "/api/v2/iam/departments",
            {"name": "飞行一部", "parentId": self.root.id},
            format="json",
        )

        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        child_id = create_response.data["data"]["id"]
        child = Department.objects.get(pk=child_id)
        self.assertEqual(child.path, f"/{self.root.id}/{child.id}/")
        self.assertEqual(child.depth, 1)

        other_parent = Department.objects.create(tenant=self.tenant, name="备用部门", parent=self.root)
        move_response = self.client.put(
            f"/api/v2/iam/departments/{child.id}",
            {"name": "飞行一部", "parentId": other_parent.id},
            format="json",
        )

        self.assertEqual(move_response.status_code, 400, getattr(move_response, "data", move_response.content))
        child.refresh_from_db()
        self.assertEqual(child.parent_id, self.root.id)

    def test_platform_super_admin_should_rename_enable_and_disable_departments(self):
        child = Department.objects.create(tenant=self.tenant, name="待调整部门", parent=self.root)

        rename_response = self.client.put(
            f"/api/v2/iam/departments/{child.id}",
            {"name": "飞行二部", "parentId": self.root.id},
            format="json",
        )

        self.assertEqual(rename_response.status_code, 200, getattr(rename_response, "data", rename_response.content))
        child.refresh_from_db()
        self.assertEqual(child.name, "飞行二部")

        disable_response = self.client.post(f"/api/v2/iam/departments/{child.id}/disable", {}, format="json")
        self.assertEqual(disable_response.status_code, 200, getattr(disable_response, "data", disable_response.content))
        child.refresh_from_db()
        self.assertEqual(child.status, 0)

        enable_response = self.client.post(f"/api/v2/iam/departments/{child.id}/enable", {}, format="json")
        self.assertEqual(enable_response.status_code, 200, getattr(enable_response, "data", enable_response.content))
        child.refresh_from_db()
        self.assertEqual(child.status, 1)

    def test_platform_super_admin_department_management_should_write_audit_logs(self):
        create_response = self.client.post(
            "/api/v2/iam/departments",
            {"name": "审计部门", "parentId": self.root.id},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        department_id = create_response.data["data"]["id"]

        create_log = V2AuditLog.objects.get(
            action="create_department",
            target_type="v2_department",
            target_id=str(department_id),
        )
        self.assertEqual(create_log.actor_department_id, self.root.id)
        self.assertEqual(create_log.resource_owner_department_id, department_id)
        self.assertIsNone(create_log.before_data)
        self.assertEqual(create_log.after_data["name"], "审计部门")

        rename_response = self.client.put(
            f"/api/v2/iam/departments/{department_id}",
            {"name": "审计部门改名", "parentId": self.root.id},
            format="json",
        )
        self.assertEqual(rename_response.status_code, 200, getattr(rename_response, "data", rename_response.content))
        update_log = V2AuditLog.objects.get(
            action="update_department",
            target_type="v2_department",
            target_id=str(department_id),
        )
        self.assertEqual(update_log.actor_department_id, self.root.id)
        self.assertEqual(update_log.resource_owner_department_id, department_id)
        self.assertEqual(update_log.before_data["name"], "审计部门")
        self.assertEqual(update_log.after_data["name"], "审计部门改名")

        disable_response = self.client.post(f"/api/v2/iam/departments/{department_id}/disable", {}, format="json")
        self.assertEqual(disable_response.status_code, 200, getattr(disable_response, "data", disable_response.content))
        disable_log = V2AuditLog.objects.get(
            action="disable_department",
            target_type="v2_department",
            target_id=str(department_id),
        )
        self.assertEqual(disable_log.before_data["status"], DirectoryStatus.ACTIVE)
        self.assertEqual(disable_log.after_data["status"], DirectoryStatus.DISABLED)

        enable_response = self.client.post(f"/api/v2/iam/departments/{department_id}/enable", {}, format="json")
        self.assertEqual(enable_response.status_code, 200, getattr(enable_response, "data", enable_response.content))
        enable_log = V2AuditLog.objects.get(
            action="enable_department",
            target_type="v2_department",
            target_id=str(department_id),
        )
        self.assertEqual(enable_log.before_data["status"], DirectoryStatus.DISABLED)
        self.assertEqual(enable_log.after_data["status"], DirectoryStatus.ACTIVE)

    def test_rejected_department_management_should_not_write_audit_logs(self):
        child = Department.objects.create(tenant=self.tenant, name="待拒绝部门", parent=self.root)
        other_parent = Department.objects.create(tenant=self.tenant, name="备用父部门", parent=self.root)

        move_response = self.client.put(
            f"/api/v2/iam/departments/{child.id}",
            {"name": "非法移动", "parentId": other_parent.id},
            format="json",
        )
        self.assertEqual(move_response.status_code, 400, getattr(move_response, "data", move_response.content))
        self.assertFalse(
            V2AuditLog.objects.filter(
                action="update_department",
                target_type="v2_department",
                target_id=str(child.id),
            ).exists()
        )

        department_admin, _profile = create_v2_actor(
            username="department_audit_denied_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=self.root,
        )
        self.client.force_authenticate(department_admin)

        denied_create_response = self.client.post(
            "/api/v2/iam/departments",
            {"name": "越权部门", "parentId": self.root.id},
            format="json",
        )
        denied_update_response = self.client.put(
            f"/api/v2/iam/departments/{child.id}",
            {"name": "越权改名", "parentId": self.root.id},
            format="json",
        )

        self.assertEqual(denied_create_response.status_code, 403, getattr(denied_create_response, "data", denied_create_response.content))
        self.assertEqual(denied_update_response.status_code, 403, getattr(denied_update_response, "data", denied_update_response.content))
        self.assertFalse(V2AuditLog.objects.filter(target_type="v2_department").exists())

    def test_department_admin_should_not_create_or_manage_child_departments(self):
        department_admin, _profile = create_v2_actor(
            username="department_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=self.root,
        )
        self.client.force_authenticate(department_admin)

        create_response = self.client.post(
            "/api/v2/iam/departments",
            {"name": "越权子部门", "parentId": self.root.id},
            format="json",
        )

        self.assertEqual(create_response.status_code, 403, getattr(create_response, "data", create_response.content))

    def test_legacy_platform_admin_flag_should_not_grant_v2_super_admin(self):
        legacy_user = User.objects.create_user(
            username="legacy_platform_admin",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        self.client.force_authenticate(legacy_user)

        response = self.client.post(
            "/api/v2/iam/departments",
            {"name": "非法部门", "tenantId": self.tenant.id},
            format="json",
        )

        self.assertEqual(response.status_code, 403, getattr(response, "data", response.content))

    def test_roles_should_return_fixed_v2_role_codes(self):
        response = self.client.get("/api/v2/iam/roles")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        returned = [item["code"] for item in response.data["data"]["list"]]
        self.assertEqual(returned, [choice.value for choice in FixedRole])

    def test_platform_super_admin_should_manage_v2_accounts_and_audit_changes(self):
        child = Department.objects.create(tenant=self.tenant, name="飞行队", parent=self.root)
        other = Department.objects.create(tenant=self.tenant, name="保障队", parent=self.root)

        create_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "v2_pilot_account",
                "password": "pass1234",
                "departmentId": child.id,
                "roleCodes": [FixedRole.PILOT, FixedRole.DEPARTMENT_ADMIN],
                "status": DirectoryStatus.ACTIVE,
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        created = create_response.data["data"]
        account_id = created["id"]
        user_id = created["userId"]
        self.assertEqual(created["username"], "v2_pilot_account")
        self.assertEqual(created["department"]["id"], child.id)
        self.assertEqual(created["roleCodes"], [FixedRole.DEPARTMENT_ADMIN, FixedRole.PILOT])
        user = User.objects.get(pk=user_id)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_platform_admin)
        self.assertTrue(user.check_password("pass1234"))

        list_response = self.client.get(
            "/api/v2/iam/accounts",
            {"departmentId": child.id, "status": DirectoryStatus.ACTIVE, "keywords": "pilot"},
        )
        self.assertEqual(list_response.status_code, 200, getattr(list_response, "data", list_response.content))
        self.assertEqual(list_response.data["data"]["total"], 1)
        self.assertEqual(list_response.data["data"]["list"][0]["id"], account_id)

        update_response = self.client.put(
            f"/api/v2/iam/accounts/{account_id}",
            {
                "username": "v2_pilot_renamed",
                "password": "changed123",
                "departmentId": other.id,
                "status": DirectoryStatus.DISABLED,
            },
            format="json",
        )

        self.assertEqual(update_response.status_code, 200, getattr(update_response, "data", update_response.content))
        updated = update_response.data["data"]
        self.assertEqual(updated["username"], "v2_pilot_renamed")
        self.assertEqual(updated["department"]["id"], other.id)
        self.assertEqual(updated["status"], DirectoryStatus.DISABLED)
        user.refresh_from_db()
        profile = V2AccountProfile.objects.get(pk=account_id)
        self.assertEqual(user.status, UserStatus.DISABLED)
        self.assertFalse(user.is_active)
        self.assertTrue(user.check_password("changed123"))
        self.assertEqual(profile.department_id, other.id)
        self.assertEqual(profile.status, DirectoryStatus.DISABLED)

        self.client.force_authenticate(user)
        denied_context_response = self.client.get("/api/v2/iam/me/context")
        self.assertEqual(
            denied_context_response.status_code,
            401,
            getattr(denied_context_response, "data", denied_context_response.content),
        )

        self.client.force_authenticate(self.super_user)
        reactivate_response = self.client.put(
            f"/api/v2/iam/accounts/{account_id}",
            {
                "username": "v2_pilot_renamed",
                "departmentId": other.id,
                "status": DirectoryStatus.ACTIVE,
            },
            format="json",
        )
        self.assertEqual(reactivate_response.status_code, 200, getattr(reactivate_response, "data", reactivate_response.content))

        roles_response = self.client.put(
            f"/api/v2/iam/accounts/{account_id}/roles",
            {"roleCodes": [FixedRole.TASK_MONITOR_DISPATCHER, FixedRole.PLATFORM_SUPER_ADMIN]},
            format="json",
        )

        self.assertEqual(roles_response.status_code, 200, getattr(roles_response, "data", roles_response.content))
        self.assertEqual(roles_response.data["data"]["roleCodes"], [FixedRole.PLATFORM_SUPER_ADMIN, FixedRole.TASK_MONITOR_DISPATCHER])

        logged_actions = set(V2AuditLog.objects.filter(target_type="v2_account", target_id=str(account_id)).values_list("action", flat=True))
        self.assertTrue(
            {
                "create_account",
                "update_account",
                "change_account_department",
                "change_account_roles",
            }.issubset(logged_actions)
        )
        audit_payloads = V2AuditLog.objects.filter(target_type="v2_account", target_id=str(account_id)).values("before_data", "after_data")
        for payload in audit_payloads:
            self.assertNotIn("password", str(payload).lower())

    def test_department_admin_should_manage_only_own_department_business_accounts(self):
        child = Department.objects.create(tenant=self.tenant, name="飞行队", parent=self.root)
        other = Department.objects.create(tenant=self.tenant, name="保障队", parent=self.root)
        department_admin, _profile = create_v2_actor(
            username="child_department_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=child,
        )
        other_admin, _other_profile = create_v2_actor(
            username="other_department_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=other,
        )
        dispatcher, _dispatcher_profile = create_v2_actor(
            username="plain_dispatcher",
            role_code=FixedRole.TASK_MONITOR_DISPATCHER,
            department=child,
        )
        self.client.force_authenticate(department_admin)

        create_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "child_business_account",
                "password": "pass1234",
                "departmentId": child.id,
                "roleCodes": [FixedRole.PILOT, FixedRole.TASK_MONITOR_DISPATCHER],
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        account_id = create_response.data["data"]["id"]
        self.assertEqual(create_response.data["data"]["roleCodes"], [FixedRole.TASK_MONITOR_DISPATCHER, FixedRole.PILOT])

        denied_cross_department_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "cross_department_account",
                "password": "pass1234",
                "departmentId": other.id,
                "roleCodes": [FixedRole.PILOT],
            },
            format="json",
        )
        self.assertEqual(
            denied_cross_department_response.status_code,
            403,
            getattr(denied_cross_department_response, "data", denied_cross_department_response.content),
        )

        denied_system_role_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "illegal_system_role_account",
                "password": "pass1234",
                "departmentId": child.id,
                "roleCodes": [FixedRole.DEPARTMENT_ADMIN],
            },
            format="json",
        )
        self.assertEqual(
            denied_system_role_response.status_code,
            400,
            getattr(denied_system_role_response, "data", denied_system_role_response.content),
        )

        V2AccountRoleAssignment.objects.create(
            account_profile_id=account_id,
            role_code=FixedRole.DEPARTMENT_ADMIN,
            assigned_by_user=self.super_user,
        )
        replace_roles_response = self.client.put(
            f"/api/v2/iam/accounts/{account_id}/roles",
            {"roleCodes": [FixedRole.WORK_ORDER_HANDLER]},
            format="json",
        )

        self.assertEqual(replace_roles_response.status_code, 200, getattr(replace_roles_response, "data", replace_roles_response.content))
        self.assertEqual(replace_roles_response.data["data"]["roleCodes"], [FixedRole.DEPARTMENT_ADMIN, FixedRole.WORK_ORDER_HANDLER])

        other_user = User.objects.create_user(username="other_department_account", password="pass1234", status=1)
        other_profile = V2AccountProfile.objects.create(user=other_user, department=other)
        V2AccountRoleAssignment.objects.create(account_profile=other_profile, role_code=FixedRole.PILOT, assigned_by_user=other_admin)
        denied_other_account_response = self.client.put(
            f"/api/v2/iam/accounts/{other_profile.id}/roles",
            {"roleCodes": [FixedRole.WORK_ORDER_HANDLER]},
            format="json",
        )
        self.assertEqual(
            denied_other_account_response.status_code,
            403,
            getattr(denied_other_account_response, "data", denied_other_account_response.content),
        )

        self.client.force_authenticate(dispatcher)
        forbidden_list_response = self.client.get("/api/v2/iam/accounts")
        self.assertEqual(forbidden_list_response.status_code, 403, getattr(forbidden_list_response, "data", forbidden_list_response.content))

    def test_account_api_should_validate_duplicates_roles_departments_and_missing_accounts(self):
        child = Department.objects.create(tenant=self.tenant, name="飞行队", parent=self.root)
        User.objects.create_user(username="duplicate_v2_account", password="pass1234", status=1)

        duplicate_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "duplicate_v2_account",
                "password": "pass1234",
                "departmentId": child.id,
                "roleCodes": [FixedRole.PILOT],
            },
            format="json",
        )
        self.assertEqual(duplicate_response.status_code, 409, getattr(duplicate_response, "data", duplicate_response.content))

        invalid_role_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "invalid_role_account",
                "password": "pass1234",
                "departmentId": child.id,
                "roleCodes": ["unknown_role"],
            },
            format="json",
        )
        self.assertEqual(invalid_role_response.status_code, 400, getattr(invalid_role_response, "data", invalid_role_response.content))

        invalid_department_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "invalid_department_account",
                "password": "pass1234",
                "departmentId": 999999,
                "roleCodes": [FixedRole.PILOT],
            },
            format="json",
        )
        self.assertEqual(
            invalid_department_response.status_code,
            400,
            getattr(invalid_department_response, "data", invalid_department_response.content),
        )

        missing_account_update_response = self.client.put(
            "/api/v2/iam/accounts/999999",
            {"username": "missing", "departmentId": child.id, "status": DirectoryStatus.ACTIVE},
            format="json",
        )
        self.assertEqual(
            missing_account_update_response.status_code,
            404,
            getattr(missing_account_update_response, "data", missing_account_update_response.content),
        )

        missing_account_roles_response = self.client.put(
            "/api/v2/iam/accounts/999999/roles",
            {"roleCodes": [FixedRole.PILOT]},
            format="json",
        )
        self.assertEqual(
            missing_account_roles_response.status_code,
            404,
            getattr(missing_account_roles_response, "data", missing_account_roles_response.content),
        )
