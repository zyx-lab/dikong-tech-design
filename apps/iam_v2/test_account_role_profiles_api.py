from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import DirectoryStatus, UserStatus
from apps.iam_v2.models import (
    Department,
    FixedRole,
    V2AccountProfile,
    V2AccountQualification,
    V2AccountRoleAssignment,
    V2AccountRoleProfile,
    V2Role,
)

User = get_user_model()


def create_v2_actor(*, username: str, role_code: str | None, department: Department):
    user = User.objects.create_user(username=username, password="pass1234", status=UserStatus.ACTIVE)
    profile = V2AccountProfile.objects.create(
        user=user,
        department=department,
        name=username,
        phone=f"138{user.id:08d}",
        email=f"{username}@example.test",
    )
    if role_code is not None:
        V2AccountRoleAssignment.objects.create(account_profile=profile, role_code=role_code, assigned_by_user=user)
    return user, profile


class IamV2AccountRoleProfilesApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.root = Department.objects.create(name="总部")
        self.department = Department.objects.create(name="飞行队", parent=self.root)
        self.super_user, self.super_profile = create_v2_actor(
            username="account_profile_super",
            role_code=FixedRole.PLATFORM_SUPER_ADMIN,
            department=self.root,
        )
        self.department_admin, self.department_admin_profile = create_v2_actor(
            username="account_profile_department_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=self.department,
        )
        self.pilot_user, self.pilot_account = create_v2_actor(
            username="account_profile_pilot",
            role_code=FixedRole.PILOT,
            department=self.department,
        )
        self.unprofiled_pilot_user, self.unprofiled_pilot_account = create_v2_actor(
            username="account_profile_unprofiled_pilot",
            role_code=FixedRole.PILOT,
            department=self.department,
        )
        self.dispatcher_user, self.dispatcher_account = create_v2_actor(
            username="account_profile_dispatcher",
            role_code=FixedRole.TASK_MONITOR_DISPATCHER,
            department=self.department,
        )

    def profile_payload(self, *, profile_type: str = FixedRole.PILOT, status: int = DirectoryStatus.ACTIVE) -> dict:
        return {
            "profileType": profile_type,
            "displayName": "夜航飞手",
            "level": "A1",
            "status": status,
            "remark": "可执行夜航巡检",
        }

    def qualification_payload(self, *, profile_type: str = FixedRole.PILOT, certificate_no: str = "CERT-IAM-001") -> dict:
        issued_at = timezone.now().date()
        expires_at = issued_at.replace(year=issued_at.year + 1)
        return {
            "profileType": profile_type,
            "qualificationType": "多旋翼巡检",
            "certificateNo": certificate_no,
            "issuedAt": issued_at.isoformat(),
            "expiresAt": expires_at.isoformat(),
            "status": DirectoryStatus.ACTIVE,
            "remark": "当前有效",
        }

    def create_pilot_profile_and_qualification(self, *, account_id: int, certificate_no: str = "CERT-IAM-001") -> None:
        profile_response = self.client.post(
            f"/api/v2/iam/accounts/{account_id}/profiles",
            self.profile_payload(),
            format="json",
        )
        self.assertEqual(profile_response.status_code, 201, getattr(profile_response, "data", profile_response.content))
        qualification_response = self.client.post(
            f"/api/v2/iam/accounts/{account_id}/qualifications",
            self.qualification_payload(certificate_no=certificate_no),
            format="json",
        )
        self.assertEqual(qualification_response.status_code, 201, getattr(qualification_response, "data", qualification_response.content))

    def test_deleted_account_role_profile_should_release_profile_type_key(self):
        self.client.force_authenticate(self.department_admin)
        create_response = self.client.post(
            f"/api/v2/iam/accounts/{self.pilot_account.id}/profiles",
            self.profile_payload(),
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))

        delete_response = self.client.delete(f"/api/v2/iam/accounts/{self.pilot_account.id}/profiles/{FixedRole.PILOT}")
        self.assertEqual(delete_response.status_code, 200, getattr(delete_response, "data", delete_response.content))

        recreate_response = self.client.post(
            f"/api/v2/iam/accounts/{self.pilot_account.id}/profiles",
            self.profile_payload(),
            format="json",
        )

        self.assertEqual(recreate_response.status_code, 201, getattr(recreate_response, "data", recreate_response.content))
        self.assertEqual(
            V2AccountRoleProfile.objects.filter(
                account_profile=self.pilot_account,
                profile_type=FixedRole.PILOT,
                deleted_at__isnull=True,
            ).count(),
            1,
        )

    def test_deleted_account_qualification_should_release_certificate_key(self):
        self.client.force_authenticate(self.department_admin)
        self.create_pilot_profile_and_qualification(account_id=self.pilot_account.id, certificate_no="CERT-RECREATE-001")
        qualification = V2AccountQualification.objects.get(
            account_profile=self.pilot_account,
            certificate_no="CERT-RECREATE-001",
        )

        delete_response = self.client.delete(f"/api/v2/iam/accounts/{self.pilot_account.id}/qualifications/{qualification.id}")
        self.assertEqual(delete_response.status_code, 200, getattr(delete_response, "data", delete_response.content))

        recreate_response = self.client.post(
            f"/api/v2/iam/accounts/{self.pilot_account.id}/qualifications",
            self.qualification_payload(certificate_no="CERT-RECREATE-001"),
            format="json",
        )

        self.assertEqual(recreate_response.status_code, 201, getattr(recreate_response, "data", recreate_response.content))
        self.assertEqual(
            V2AccountQualification.objects.filter(
                account_profile=self.pilot_account,
                certificate_no="CERT-RECREATE-001",
                deleted_at__isnull=True,
            ).count(),
            1,
        )

    def test_profile_types_should_expose_builtin_readonly_types_and_allow_custom_role_types(self):
        self.client.force_authenticate(self.super_user)

        list_response = self.client.get("/api/v2/iam/profile-types")

        self.assertEqual(list_response.status_code, 200, getattr(list_response, "data", list_response.content))
        returned = {item["code"]: item for item in list_response.data["data"]["list"]}
        self.assertEqual(
            set(returned),
            {
                FixedRole.PLATFORM_SUPER_ADMIN,
                FixedRole.DEPARTMENT_ADMIN,
                FixedRole.TASK_MONITOR_DISPATCHER,
                FixedRole.PILOT,
                FixedRole.WORK_ORDER_HANDLER,
            },
        )
        self.assertTrue(returned[FixedRole.PILOT]["isSystem"])
        readonly_response = self.client.put(
            f"/api/v2/iam/profile-types/{FixedRole.PILOT}",
            {"code": FixedRole.PILOT, "name": "不可改飞手", "status": DirectoryStatus.DISABLED, "sort": 1, "remark": ""},
            format="json",
        )
        self.assertEqual(readonly_response.status_code, 400, getattr(readonly_response, "data", readonly_response.content))

        V2Role.objects.create(
            code="maintenance_operator",
            name="维保员",
            status=DirectoryStatus.ACTIVE,
            assignable_by_department_admin=True,
            data_scope=V2Role.DataScope.DEPT_AND_CHILDREN,
            sort=80,
        )
        create_response = self.client.post(
            "/api/v2/iam/profile-types",
            {"code": "maintenance_operator", "name": "维保员档案", "status": DirectoryStatus.ACTIVE, "sort": 80, "remark": ""},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        self.assertFalse(create_response.data["data"]["isSystem"])

        missing_role_response = self.client.post(
            "/api/v2/iam/profile-types",
            {"code": "missing_role", "name": "缺失角色档案", "status": DirectoryStatus.ACTIVE, "sort": 90, "remark": ""},
            format="json",
        )
        self.assertEqual(missing_role_response.status_code, 400, getattr(missing_role_response, "data", missing_role_response.content))

    def test_account_profiles_and_qualifications_should_be_managed_under_account(self):
        self.client.force_authenticate(self.department_admin)

        denied_profile_response = self.client.post(
            f"/api/v2/iam/accounts/{self.dispatcher_account.id}/profiles",
            self.profile_payload(),
            format="json",
        )
        self.assertEqual(denied_profile_response.status_code, 400, getattr(denied_profile_response, "data", denied_profile_response.content))

        create_profile_response = self.client.post(
            f"/api/v2/iam/accounts/{self.pilot_account.id}/profiles",
            self.profile_payload(),
            format="json",
        )
        self.assertEqual(create_profile_response.status_code, 201, getattr(create_profile_response, "data", create_profile_response.content))
        profile = create_profile_response.data["data"]
        self.assertEqual(profile["accountProfileId"], self.pilot_account.id)
        self.assertEqual(profile["profileType"], FixedRole.PILOT)
        self.assertEqual(profile["displayName"], "夜航飞手")

        profiles_response = self.client.get(f"/api/v2/iam/accounts/{self.pilot_account.id}/profiles")
        self.assertEqual(profiles_response.status_code, 200, getattr(profiles_response, "data", profiles_response.content))
        self.assertEqual(profiles_response.data["data"]["total"], 1)

        missing_profile_qualification_response = self.client.post(
            f"/api/v2/iam/accounts/{self.unprofiled_pilot_account.id}/qualifications",
            self.qualification_payload(certificate_no="CERT-NO-PROFILE"),
            format="json",
        )
        self.assertEqual(
            missing_profile_qualification_response.status_code,
            400,
            getattr(missing_profile_qualification_response, "data", missing_profile_qualification_response.content),
        )

        qualification_response = self.client.post(
            f"/api/v2/iam/accounts/{self.pilot_account.id}/qualifications",
            self.qualification_payload(certificate_no="CERT-IAM-002"),
            format="json",
        )
        self.assertEqual(qualification_response.status_code, 201, getattr(qualification_response, "data", qualification_response.content))
        qualification = qualification_response.data["data"]
        self.assertEqual(qualification["accountProfileId"], self.pilot_account.id)
        self.assertEqual(qualification["profileType"], FixedRole.PILOT)
        self.assertTrue(qualification["isEffective"])

        qualifications_response = self.client.get(
            f"/api/v2/iam/accounts/{self.pilot_account.id}/qualifications",
            {"profileType": FixedRole.PILOT},
        )
        self.assertEqual(qualifications_response.status_code, 200, getattr(qualifications_response, "data", qualifications_response.content))
        self.assertEqual(qualifications_response.data["data"]["total"], 1)

    def test_account_filters_should_select_qualified_profile_accounts_without_returning_profile_summaries(self):
        self.client.force_authenticate(self.department_admin)
        self.create_pilot_profile_and_qualification(account_id=self.pilot_account.id)

        missing_profile_type_response = self.client.get("/api/v2/iam/accounts", {"qualified": "true"})
        self.assertEqual(
            missing_profile_type_response.status_code,
            400,
            getattr(missing_profile_type_response, "data", missing_profile_type_response.content),
        )

        response = self.client.get(
            "/api/v2/iam/accounts",
            {"roleCode": FixedRole.PILOT, "profileType": FixedRole.PILOT, "qualified": "true"},
        )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["total"], 1)
        account = response.data["data"]["list"][0]
        self.assertEqual(account["id"], self.pilot_account.id)
        self.assertNotIn("matchedProfile", account)
        self.assertNotIn("qualificationSummary", account)

    def test_me_profile_should_return_profiles_array_and_account_qualifications(self):
        self.client.force_authenticate(self.department_admin)
        self.create_pilot_profile_and_qualification(account_id=self.pilot_account.id)

        self.client.force_authenticate(self.pilot_user)
        response = self.client.get("/api/v2/iam/me/profile")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        data = response.data["data"]
        self.assertNotIn("roleProfiles", data)
        self.assertEqual(data["profiles"][0]["profileType"], FixedRole.PILOT)
        self.assertEqual(data["profiles"][0]["displayName"], "夜航飞手")
        self.assertEqual(data["qualifications"][0]["profileType"], FixedRole.PILOT)
        self.assertTrue(data["qualifications"][0]["isEffective"])

    def test_removing_role_should_disable_matching_profile_without_auto_reactivation(self):
        self.client.force_authenticate(self.department_admin)
        self.create_pilot_profile_and_qualification(account_id=self.pilot_account.id)

        remove_role_response = self.client.put(
            f"/api/v2/iam/accounts/{self.pilot_account.id}/roles",
            {"roleCodes": [FixedRole.TASK_MONITOR_DISPATCHER]},
            format="json",
        )
        self.assertEqual(remove_role_response.status_code, 200, getattr(remove_role_response, "data", remove_role_response.content))

        disabled_profile_response = self.client.get(f"/api/v2/iam/accounts/{self.pilot_account.id}/profiles/{FixedRole.PILOT}")
        self.assertEqual(disabled_profile_response.status_code, 200, getattr(disabled_profile_response, "data", disabled_profile_response.content))
        self.assertEqual(disabled_profile_response.data["data"]["status"], DirectoryStatus.DISABLED)

        restore_role_response = self.client.put(
            f"/api/v2/iam/accounts/{self.pilot_account.id}/roles",
            {"roleCodes": [FixedRole.PILOT]},
            format="json",
        )
        self.assertEqual(restore_role_response.status_code, 200, getattr(restore_role_response, "data", restore_role_response.content))
        still_disabled_response = self.client.get(f"/api/v2/iam/accounts/{self.pilot_account.id}/profiles/{FixedRole.PILOT}")
        self.assertEqual(still_disabled_response.data["data"]["status"], DirectoryStatus.DISABLED)
