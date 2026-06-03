from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import DirectoryStatus, UserStatus
from apps.iam_v2.models import Department, FixedRole, V2AccountProfile, V2AccountRoleAssignment
from apps.workforce_v2.models import PilotProfile

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


class IamV2ProfileApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.root = Department.objects.create(name="总部")
        self.department = Department.objects.create(name="资源队", parent=self.root)
        self.other_department = Department.objects.create(name="保障队", parent=self.root)
        self.super_user, _ = create_v2_actor(
            username="profile_super",
            role_code=FixedRole.PLATFORM_SUPER_ADMIN,
            department=self.root,
        )
        self.department_admin, _ = create_v2_actor(
            username="profile_department_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=self.department,
        )
        self.pilot_user, self.pilot_account = create_v2_actor(
            username="profile_pilot",
            role_code=FixedRole.PILOT,
            department=self.department,
        )
        self.pilot = PilotProfile.objects.create(
            account_profile=self.pilot_account,
            display_name="个人资料飞手",
            level="A1",
            status=DirectoryStatus.ACTIVE,
            remark="夜航资质齐全",
        )

    def qualification_payload(self, *, certificate_no: str = "CERT-PROFILE-001", status: int = DirectoryStatus.ACTIVE) -> dict:
        issued_at = timezone.now().date()
        expires_at = issued_at.replace(year=issued_at.year + 1)
        return {
            "roleCode": FixedRole.PILOT,
            "qualificationType": "多旋翼巡检",
            "certificateNo": certificate_no,
            "issuedAt": issued_at.isoformat(),
            "expiresAt": expires_at.isoformat(),
            "status": status,
            "remark": "当前有效",
        }

    def test_account_api_should_create_v2_contact_fields_when_platform_admin_posts_account(self):
        self.client.force_authenticate(self.super_user)

        response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "contact_account",
                "password": "pass1234",
                "name": "联系人账号",
                "phone": "13800001001",
                "email": "contact@example.test",
                "departmentId": self.department.id,
                "roleCodes": [FixedRole.WORK_ORDER_HANDLER],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        data = response.data["data"]
        self.assertEqual(data["name"], "联系人账号")
        self.assertEqual(data["phone"], "13800001001")
        self.assertEqual(data["email"], "contact@example.test")

    def test_account_api_should_reject_duplicate_v2_phone(self):
        self.client.force_authenticate(self.super_user)

        first_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "first_phone_owner",
                "password": "pass1234",
                "name": "手机号占用账号",
                "phone": "13800001002",
                "email": "",
                "departmentId": self.department.id,
                "roleCodes": [FixedRole.WORK_ORDER_HANDLER],
            },
            format="json",
        )
        self.assertEqual(first_response.status_code, 201, getattr(first_response, "data", first_response.content))

        duplicate_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "second_phone_owner",
                "password": "pass1234",
                "name": "手机号重复账号",
                "phone": "13800001002",
                "email": "",
                "departmentId": self.department.id,
                "roleCodes": [FixedRole.WORK_ORDER_HANDLER],
            },
            format="json",
        )
        self.assertEqual(duplicate_response.status_code, 409, getattr(duplicate_response, "data", duplicate_response.content))
        self.assertIn("phone", duplicate_response.data["data"])

    def test_login_should_return_v2_account_summary_without_staff_profile(self):
        response = self.client.post(
            "/api/v2/iam/session/login",
            {"username": "profile_pilot", "password": "pass1234"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        user_data = response.data["data"]["user"]
        self.assertNotIn("staffProfile", user_data)
        self.assertEqual(user_data["username"], "profile_pilot")
        self.assertEqual(user_data["accountProfileId"], self.pilot_account.id)
        self.assertEqual(user_data["department"]["id"], self.department.id)
        self.assertEqual(user_data["roleCodes"], [FixedRole.PILOT])

    def test_me_profile_should_return_account_role_profiles_and_current_role_qualifications(self):
        self.client.force_authenticate(self.department_admin)
        qualification_response = self.client.post(
            f"/api/v2/iam/accounts/{self.pilot_account.id}/qualifications",
            self.qualification_payload(certificate_no="CERT-PROFILE-001"),
            format="json",
        )
        self.assertEqual(
            qualification_response.status_code,
            201,
            getattr(qualification_response, "data", qualification_response.content),
        )

        self.client.force_authenticate(self.pilot_user)
        response = self.client.get("/api/v2/iam/me/profile")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        data = response.data["data"]
        self.assertEqual(data["userId"], self.pilot_user.id)
        self.assertEqual(data["username"], "profile_pilot")
        self.assertEqual(data["accountProfileId"], self.pilot_account.id)
        self.assertEqual(data["department"]["id"], self.department.id)
        self.assertEqual(data["roleCodes"], [FixedRole.PILOT])
        self.assertEqual(data["roleProfiles"]["pilot"]["displayName"], "个人资料飞手")
        self.assertEqual(data["roleProfiles"]["departmentAdmin"], None)
        self.assertEqual(data["qualifications"][0]["roleCode"], FixedRole.PILOT)
        self.assertEqual(data["qualifications"][0]["qualificationType"], "多旋翼巡检")
        self.assertTrue(data["qualifications"][0]["isEffective"])

    def test_department_admin_should_manage_only_own_department_account_qualifications(self):
        other_user, other_account = create_v2_actor(
            username="profile_other_pilot",
            role_code=FixedRole.PILOT,
            department=self.other_department,
        )
        del other_user
        self.client.force_authenticate(self.department_admin)

        create_response = self.client.post(
            f"/api/v2/iam/accounts/{self.pilot_account.id}/qualifications",
            self.qualification_payload(certificate_no="CERT-MANAGE-001"),
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        qualification_id = create_response.data["data"]["id"]

        update_response = self.client.put(
            f"/api/v2/iam/accounts/{self.pilot_account.id}/qualifications/{qualification_id}",
            self.qualification_payload(certificate_no="CERT-UPDATED", status=DirectoryStatus.DISABLED),
            format="json",
        )
        self.assertEqual(update_response.status_code, 200, getattr(update_response, "data", update_response.content))
        self.assertFalse(update_response.data["data"]["isEffective"])

        denied_response = self.client.post(
            f"/api/v2/iam/accounts/{other_account.id}/qualifications",
            {"roleCode": FixedRole.PILOT, "qualificationType": "跨部门资质"},
            format="json",
        )
        self.assertEqual(denied_response.status_code, 403, getattr(denied_response, "data", denied_response.content))

    def test_account_qualification_api_should_reject_duplicates(self):
        self.client.force_authenticate(self.department_admin)

        first_response = self.client.post(
            f"/api/v2/iam/accounts/{self.pilot_account.id}/qualifications",
            self.qualification_payload(certificate_no="CERT-DUP"),
            format="json",
        )
        self.assertEqual(first_response.status_code, 201, getattr(first_response, "data", first_response.content))

        duplicate_response = self.client.post(
            f"/api/v2/iam/accounts/{self.pilot_account.id}/qualifications",
            self.qualification_payload(certificate_no="CERT-DUP"),
            format="json",
        )
        self.assertEqual(duplicate_response.status_code, 409, getattr(duplicate_response, "data", duplicate_response.content))

    def test_account_qualification_api_should_require_full_contract_fields(self):
        self.client.force_authenticate(self.department_admin)

        response = self.client.post(
            f"/api/v2/iam/accounts/{self.pilot_account.id}/qualifications",
            {"roleCode": FixedRole.PILOT, "qualificationType": "多旋翼巡检"},
            format="json",
        )

        self.assertEqual(response.status_code, 400, getattr(response, "data", response.content))
        self.assertIn("certificateNo", response.data["data"])
        self.assertIn("issuedAt", response.data["data"])
        self.assertIn("expiresAt", response.data["data"])
        self.assertIn("status", response.data["data"])
        self.assertIn("remark", response.data["data"])
