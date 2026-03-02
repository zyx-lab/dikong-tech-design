from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import GroupPermissionScope, ScopeStatus, ScopeType, StaffProfile, StaffType, StaffTypeGroup
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

        self.staff_type = StaffType.objects.create(code="ops_admin", name="Ops Admin", status=1)
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

    def test_superuser_with_legacy_staff_must_clear_before_update(self):
        target = User.objects.create_superuser(username="root_legacy", password="pass1234")
        StaffProfile.objects.create(
            user=target,
            staff_no="S903",
            name="Legacy Root",
            employment_status=1,
            staff_type=self.staff_type,
        )
        response = self.client.patch(f"/internal/auth/users/{target.id}", {"is_active": True}, format="json")
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

    def test_superuser_can_clear_existing_staff_with_null_payload(self):
        target = User.objects.create_superuser(username="root2", password="pass1234")
        StaffProfile.objects.create(
            user=target,
            staff_no="S902",
            name="Legacy Root Staff",
            employment_status=1,
            staff_type=self.staff_type,
        )
        response = self.client.patch(f"/internal/auth/users/{target.id}", {"staff": None}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(StaffProfile.objects.filter(user=target).exists())


class SeedRolePermissionsCommandTests(TestCase):
    def test_seed_role_permissions_replace_mode(self):
        call_command("seed_role_permissions")

        ops_admin = StaffType.objects.get(code="ops_admin")
        self.assertEqual(
            set(ops_admin.group_links.filter(status=ScopeStatus.ACTIVE).values_list("group__name", flat=True)),
            {"权限策略管理组", "账号与人员查看组", "审计日志只读组"},
        )


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
