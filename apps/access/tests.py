from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import (
    AuditLog,
    GroupPermissionScope,
    RegistrationApplication,
    RegistrationApplicationStatus,
    ScopeStatus,
    ScopeType,
    StaffProfile,
    StaffType,
    StaffTypeGroup,
)
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


class RegistrationApplicationBusinessApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        StaffType.objects.create(code="pilot_operator", name="飞手操作员", status=1, is_registrable=True)

    def test_submit_application_should_create_pending_record(self):
        payload = {
            "name": "申请人A",
            "phone": "13800138000",
            "email": "applicant_a@example.com",
            "requested_staff_type_code": "pilot_operator",
            "requested_org_id": 1001,
            "application_note": "希望尽快入职",
        }
        response = self.client.post("/api/v1/registration-applications", payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], RegistrationApplicationStatus.PENDING_REVIEW)
        self.assertTrue(response.data["application_no"].startswith("RA"))

        application = RegistrationApplication.objects.get(application_no=response.data["application_no"])
        self.assertEqual(application.name, "申请人A")
        self.assertEqual(application.requested_staff_type_code, "pilot_operator")
        self.assertTrue(
            AuditLog.objects.filter(
                action="REGISTRATION_APPLICATION_CREATE",
                target_type="registration_application",
                target_id=str(application.id),
            ).exists()
        )

    def test_duplicate_pending_phone_or_email_should_be_rejected(self):
        first = {
            "name": "申请人A",
            "phone": "13800138001",
            "email": "applicant_b@example.com",
            "requested_staff_type_code": "pilot_operator",
        }
        self.assertEqual(
            self.client.post("/api/v1/registration-applications", first, format="json").status_code,
            201,
        )

        response_same_phone = self.client.post(
            "/api/v1/registration-applications",
            {
                "name": "申请人B",
                "phone": "13800138001",
                "email": "another@example.com",
                "requested_staff_type_code": "pilot_operator",
            },
            format="json",
        )
        self.assertEqual(response_same_phone.status_code, 400)
        self.assertIn("phone", response_same_phone.data)

        response_same_email = self.client.post(
            "/api/v1/registration-applications",
            {
                "name": "申请人C",
                "phone": "13800138002",
                "email": "applicant_b@example.com",
                "requested_staff_type_code": "pilot_operator",
            },
            format="json",
        )
        self.assertEqual(response_same_email.status_code, 400)
        self.assertIn("email", response_same_email.data)

    def test_status_query_should_work_by_application_no(self):
        response = self.client.post(
            "/api/v1/registration-applications",
            {
                "name": "申请人D",
                "phone": "13800138003",
                "email": "applicant_d@example.com",
                "requested_staff_type_code": "pilot_operator",
            },
            format="json",
        )
        application_no = response.data["application_no"]

        status_response = self.client.get(f"/api/v1/registration-applications/{application_no}/status")
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.data["status"], RegistrationApplicationStatus.PENDING_REVIEW)


class RegistrationApplicationInternalApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.registrable_staff_type = StaffType.objects.create(
            code="pilot_operator",
            name="飞手操作员",
            status=1,
            is_registrable=True,
        )

        self.ops_staff_type = StaffType.objects.create(code="ops_admin", name="运营管理员", status=1)
        self.reviewer = User.objects.create_user(username="ops_reviewer", password="pass1234", status=1)
        StaffProfile.objects.create(
            user=self.reviewer,
            staff_no="OPS-001",
            name="审核员A",
            employment_status=1,
            staff_type=self.ops_staff_type,
        )

        group = Group.objects.create(name="注册审核管理组")
        view_perm = Permission.objects.get(content_type__app_label="access", codename="view_registration_application")
        manage_perm = Permission.objects.get(content_type__app_label="access", codename="manage_registration_application")
        group.permissions.add(view_perm, manage_perm)
        StaffTypeGroup.objects.create(staff_type=self.ops_staff_type, group=group, status=ScopeStatus.ACTIVE)
        GroupPermissionScope.objects.create(
            group=group,
            permission=view_perm,
            scope_type=ScopeType.ALL,
            status=ScopeStatus.ACTIVE,
        )
        GroupPermissionScope.objects.create(
            group=group,
            permission=manage_perm,
            scope_type=ScopeType.ALL,
            status=ScopeStatus.ACTIVE,
        )

        self.application = RegistrationApplication.objects.create(
            application_no="RA20260302000001",
            name="申请人E",
            phone="13800138010",
            email="applicant_e@example.com",
            requested_staff_type_code="pilot_operator",
            status=RegistrationApplicationStatus.PENDING_REVIEW,
        )

        self.client.force_authenticate(self.reviewer)

    def test_list_and_detail_should_require_and_use_permissions(self):
        list_response = self.client.get("/internal/auth/registration-applications")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["count"], 1)

        detail_response = self.client.get(f"/internal/auth/registration-applications/{self.application.id}")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["application_no"], self.application.application_no)

    def test_approve_should_create_user_and_staff(self):
        response = self.client.post(
            f"/internal/auth/registration-applications/{self.application.id}/approve",
            {
                "username": "pilot_apply_1",
                "password": "pass1234",
                "staff_no": "P-REG-001",
                "review_comment": "审核通过",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], RegistrationApplicationStatus.APPROVED_ACCOUNT_CREATED)

        created_user = User.objects.get(username="pilot_apply_1")
        created_staff = StaffProfile.objects.get(user=created_user)
        self.assertEqual(created_staff.staff_no, "P-REG-001")
        self.assertEqual(created_staff.staff_type.code, "pilot_operator")
        self.assertEqual(created_staff.phone, "13800138010")

        self.application.refresh_from_db()
        self.assertEqual(self.application.created_user_id, created_user.id)
        self.assertEqual(self.application.created_staff_id, created_staff.id)
        self.assertEqual(self.application.reviewer_user_id, self.reviewer.id)

        self.assertTrue(
            AuditLog.objects.filter(
                action="REGISTRATION_APPLICATION_APPROVE",
                target_type="registration_application",
                target_id=str(self.application.id),
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="REGISTRATION_ACCOUNT_CREATE",
                target_type="user",
                target_id=str(created_user.id),
            ).exists()
        )

    def test_reject_should_update_status(self):
        response = self.client.post(
            f"/internal/auth/registration-applications/{self.application.id}/reject",
            {"review_comment": "资料不完整"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], RegistrationApplicationStatus.REJECTED)

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, RegistrationApplicationStatus.REJECTED)
        self.assertEqual(self.application.review_comment, "资料不完整")

        self.assertTrue(
            AuditLog.objects.filter(
                action="REGISTRATION_APPLICATION_REJECT",
                target_type="registration_application",
                target_id=str(self.application.id),
            ).exists()
        )

    def test_non_pending_application_should_not_be_approved_twice(self):
        self.application.status = RegistrationApplicationStatus.REJECTED
        self.application.save(update_fields=["status", "updated_at"])

        response = self.client.post(
            f"/internal/auth/registration-applications/{self.application.id}/approve",
            {
                "username": "pilot_apply_2",
                "password": "pass1234",
                "staff_no": "P-REG-002",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "APPLICATION_NOT_PENDING_REVIEW")


class SeedRolePermissionsCommandTests(TestCase):
    def test_seed_role_permissions_replace_mode(self):
        call_command("seed_role_permissions")

        ops_admin = StaffType.objects.get(code="ops_admin")
        self.assertEqual(
            set(ops_admin.group_links.filter(status=ScopeStatus.ACTIVE).values_list("group__name", flat=True)),
            {"权限策略管理组", "账号与人员查看组", "审计日志只读组", "无人机管理组"},
        )

        dispatcher = StaffType.objects.get(code="dispatcher")
        self.assertEqual(
            set(dispatcher.group_links.filter(status=ScopeStatus.ACTIVE).values_list("group__name", flat=True)),
            {"账号与人员查看组", "无人机全量查看组", "无人机分配管理组"},
        )

        pilot_operator = StaffType.objects.get(code="pilot_operator")
        self.assertEqual(
            set(pilot_operator.group_links.filter(status=ScopeStatus.ACTIVE).values_list("group__name", flat=True)),
            {"员工自助访问组", "无人机按分配查看组"},
        )

        business_super_admin = StaffType.objects.get(code="business_super_admin")
        self.assertEqual(
            set(
                business_super_admin.group_links.filter(status=ScopeStatus.ACTIVE).values_list("group__name", flat=True)
            ),
            {"业务超级权限组"},
        )
        self.assertFalse(StaffType.objects.get(code="ops_admin").is_registrable)
        self.assertTrue(StaffType.objects.get(code="pilot_operator").is_registrable)


class CreateBusinessSuperAccountCommandTests(TestCase):
    def test_create_business_super_account(self):
        call_command("seed_role_permissions")
        call_command(
            "create_business_super_account",
            "--username",
            "biz_root",
            "--password",
            "pass1234",
            "--staff-no",
            "BS-001",
            "--name",
            "业务超级A",
        )

        user = User.objects.get(username="biz_root")
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertEqual(user.status, 1)

        staff = StaffProfile.objects.get(user=user)
        self.assertEqual(staff.staff_no, "BS-001")
        self.assertEqual(staff.name, "业务超级A")
        self.assertEqual(staff.staff_type.code, "business_super_admin")


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
