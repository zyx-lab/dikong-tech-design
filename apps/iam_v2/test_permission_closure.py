from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import DirectoryStatus, UserStatus
from apps.iam_v2.models import (
    Department,
    V2AccountProfile,
    V2AccountRoleAssignment,
    V2Menu,
    V2MenuPermissionBinding,
    V2Permission,
    V2Role,
    V2RoleMenuGrant,
    V2RolePermissionGrant,
)
from apps.resource_v2.models import V2AuditLog

User = get_user_model()


def create_v2_account(*, username: str, department: Department, role_codes=None):
    user = User.objects.create_user(username=username, password="pass1234", status=UserStatus.ACTIVE)
    profile = V2AccountProfile.objects.create(
        user=user,
        department=department,
        name=username,
        phone=f"139{user.id:08d}",
        email=f"{username}@example.test",
    )
    for role_code in role_codes or []:
        V2AccountRoleAssignment.objects.create(account_profile=profile, role_code=role_code, assigned_by_user=user)
    return user, profile


class V2PermissionClosureTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.root = Department.objects.create(name="总部")
        self.flight = Department.objects.create(name="飞行队", parent=self.root)
        self.child = Department.objects.create(name="飞行一班", parent=self.flight)
        self.other = Department.objects.create(name="保障队", parent=self.root)

        self.super_role = V2Role.objects.get(code="platform_super_admin")
        self.dept_role = V2Role.objects.get(code="department_admin")
        self.viewer_role = V2Role.objects.create(
            code="account_viewer",
            name="账号查看员",
            assignable_by_department_admin=True,
            data_scope=V2Role.DataScope.DEPT_AND_CHILDREN,
        )
        self.operator_role = V2Role.objects.get(code="task_monitor_dispatcher")
        self.account_read = V2Permission.objects.get(code="iam:account:read")
        self.account_create = V2Permission.objects.get(code="iam:account:create")
        self.role_assign = V2Permission.objects.get(code="iam:account:assign_role")

        self.super_user, self.super_profile = create_v2_account(
            username="v2_permission_super",
            department=self.root,
            role_codes=[self.super_role.code],
        )
        self.dept_admin, self.dept_admin_profile = create_v2_account(
            username="v2_permission_dept_admin",
            department=self.flight,
            role_codes=[self.dept_role.code],
        )

    def bind_permission(self, role: V2Role, permission: V2Permission):
        V2RolePermissionGrant.objects.get_or_create(role=role, permission=permission)

    def test_role_menu_assignment_should_auto_grant_menu_bound_permissions(self):
        menu = V2Menu.objects.create(
            name="用户管理",
            code="test.iam.accounts",
            menu_type=V2Menu.MenuType.MENU,
            path="/iam/accounts",
            sort=10,
        )
        V2MenuPermissionBinding.objects.create(menu=menu, permission=self.account_read)
        viewer, _profile = create_v2_account(
            username="menu_closure_viewer",
            department=self.flight,
            role_codes=[self.viewer_role.code],
        )
        self.client.force_authenticate(self.super_user)

        response = self.client.put(
            f"/api/v2/iam/roles/{self.viewer_role.id}/menus",
            {"menuIds": [menu.id]},
            format="json",
        )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertTrue(V2RoleMenuGrant.objects.filter(role=self.viewer_role, menu=menu).exists())
        self.assertTrue(
            V2RolePermissionGrant.objects.filter(
                role=self.viewer_role,
                permission=self.account_read,
                source=V2RolePermissionGrant.GrantSource.MENU,
            ).exists()
        )

        self.client.force_authenticate(viewer)
        context_response = self.client.get("/api/v2/iam/me/context")
        self.assertEqual(context_response.status_code, 200, getattr(context_response, "data", context_response.content))
        self.assertIn("iam:account:read", context_response.data["data"]["permissions"])

        menu_response = self.client.get("/api/v2/system/menus/current")
        self.assertEqual(menu_response.status_code, 200, getattr(menu_response, "data", menu_response.content))
        self.assertEqual(menu_response.data["data"]["list"][0]["name"], "用户管理")

    def test_current_menu_should_hide_button_without_bound_permission_and_api_should_403(self):
        menu = V2Menu.objects.create(
            name="用户管理",
            code="test.button.accounts",
            menu_type=V2Menu.MenuType.MENU,
            path="/iam/accounts",
            sort=10,
        )
        button = V2Menu.objects.create(
            parent=menu,
            name="新增",
            code="test.button.accounts.create",
            menu_type=V2Menu.MenuType.BUTTON,
            sort=20,
        )
        V2MenuPermissionBinding.objects.create(menu=menu, permission=self.account_read)
        V2MenuPermissionBinding.objects.create(menu=button, permission=self.account_create)
        V2RoleMenuGrant.objects.create(role=self.viewer_role, menu=menu)
        self.bind_permission(self.viewer_role, self.account_read)
        viewer, _profile = create_v2_account(
            username="button_hidden_viewer",
            department=self.flight,
            role_codes=[self.viewer_role.code],
        )
        self.client.force_authenticate(viewer)

        menu_response = self.client.get("/api/v2/system/menus/current")
        self.assertEqual(menu_response.status_code, 200, getattr(menu_response, "data", menu_response.content))
        self.assertEqual(menu_response.data["data"]["list"][0]["children"], [])

        create_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "button_hidden_created",
                "password": "pass1234",
                "name": "按钮隐藏创建",
                "phone": "13900001001",
                "email": "",
                "departmentId": self.flight.id,
                "roleCodes": [],
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 403, getattr(create_response, "data", create_response.content))

    def test_permission_changes_should_take_effect_without_relogin(self):
        self.bind_permission(self.viewer_role, self.account_read)
        viewer, _profile = create_v2_account(
            username="immediate_effect_viewer",
            department=self.flight,
            role_codes=[self.viewer_role.code],
        )
        login_response = self.client.post(
            "/api/v2/iam/session/login",
            {"username": "immediate_effect_viewer", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(login_response.status_code, 200, getattr(login_response, "data", login_response.content))
        access_token = login_response.data["data"]["accessToken"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        allowed_response = self.client.get("/api/v2/iam/accounts")
        self.assertEqual(allowed_response.status_code, 200, getattr(allowed_response, "data", allowed_response.content))

        V2RolePermissionGrant.objects.filter(role=self.viewer_role, permission=self.account_read).delete()

        denied_response = self.client.get("/api/v2/iam/accounts")
        self.assertEqual(denied_response.status_code, 403, getattr(denied_response, "data", denied_response.content))

    def test_department_admin_should_manage_own_department_and_children_only(self):
        self.bind_permission(self.dept_role, self.account_read)
        self.bind_permission(self.dept_role, self.account_create)
        self.bind_permission(self.dept_role, self.role_assign)
        child_user, child_profile = create_v2_account(
            username="child_business_user",
            department=self.child,
            role_codes=[self.operator_role.code],
        )
        other_user, other_profile = create_v2_account(
            username="other_business_user",
            department=self.other,
            role_codes=[self.operator_role.code],
        )
        del child_user, other_user
        self.client.force_authenticate(self.dept_admin)

        list_response = self.client.get("/api/v2/iam/accounts")
        self.assertEqual(list_response.status_code, 200, getattr(list_response, "data", list_response.content))
        returned_ids = {item["id"] for item in list_response.data["data"]["list"]}
        self.assertIn(self.dept_admin_profile.id, returned_ids)
        self.assertIn(child_profile.id, returned_ids)
        self.assertNotIn(other_profile.id, returned_ids)

        create_child_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "created_child_operator",
                "password": "pass1234",
                "name": "下级部门操作员",
                "phone": "13900001002",
                "email": "",
                "departmentId": self.child.id,
                "roleCodes": [self.operator_role.code],
            },
            format="json",
        )
        self.assertEqual(create_child_response.status_code, 201, getattr(create_child_response, "data", create_child_response.content))

        denied_all_scope_role = V2Role.objects.create(
            code="global_operator",
            name="全局操作员",
            assignable_by_department_admin=False,
            data_scope=V2Role.DataScope.ALL,
        )
        denied_role_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "illegal_all_scope_operator",
                "password": "pass1234",
                "name": "非法全局角色",
                "phone": "13900001003",
                "email": "",
                "departmentId": self.child.id,
                "roleCodes": [denied_all_scope_role.code],
            },
            format="json",
        )
        self.assertEqual(denied_role_response.status_code, 400, getattr(denied_role_response, "data", denied_role_response.content))

    def test_bootstrap_v2_system_command_should_create_initialized_login_context(self):
        call_command(
            "bootstrap_v2_system",
            "--reset",
            "--username",
            "bootstrap_super",
            "--password",
            "pass1234",
            "--noinput",
            verbosity=0,
        )

        login_response = self.client.post(
            "/api/v2/iam/session/login",
            {"username": "bootstrap_super", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(login_response.status_code, 200, getattr(login_response, "data", login_response.content))
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login_response.data['data']['accessToken']}")

        context_response = self.client.get("/api/v2/iam/me/context")
        self.assertEqual(context_response.status_code, 200, getattr(context_response, "data", context_response.content))
        self.assertIn("platform_super_admin", context_response.data["data"]["roles"])
        self.assertIn("iam:account:read", context_response.data["data"]["permissions"])
        self.assertEqual(context_response.data["data"]["dataScopes"], ["ALL"])

        menu_response = self.client.get("/api/v2/system/menus/current")
        self.assertEqual(menu_response.status_code, 200, getattr(menu_response, "data", menu_response.content))

        def collect_names(items):
            names = set()
            for item in items:
                names.add(item["name"])
                names.update(collect_names(item.get("children", [])))
            return names

        menu_names = collect_names(menu_response.data["data"]["list"])
        self.assertIn("资源共享组", menu_names)
        self.assertNotIn("用户组管理", menu_names)

        self.assertFalse(User.objects.filter(username="admin").exists())
        self.assertFalse(V2AuditLog.objects.filter(action="bootstrap_v2_system").exists())
