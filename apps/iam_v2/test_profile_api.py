from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import DirectoryStatus, UserStatus
from apps.iam_v2.models import Department, FixedRole, V2AccountProfile, V2AccountRoleProfile, V2AccountRoleAssignment

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
        self.pilot_profile = V2AccountRoleProfile.objects.create(
            account_profile=self.pilot_account,
            profile_type=FixedRole.PILOT,
            display_name="个人资料飞手",
            level="A1",
            status=DirectoryStatus.ACTIVE,
            remark="夜航资质齐全",
        )

    def qualification_payload(self, *, certificate_no: str = "CERT-PROFILE-001", status: int = DirectoryStatus.ACTIVE) -> dict:
        issued_at = timezone.now().date()
        expires_at = issued_at.replace(year=issued_at.year + 1)
        return {
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
            {"profileType": FixedRole.PILOT, **self.qualification_payload(certificate_no="CERT-PROFILE-001")},
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
        self.assertEqual(data["profiles"][0]["profileType"], FixedRole.PILOT)
        self.assertEqual(data["profiles"][0]["displayName"], "个人资料飞手")
        self.assertEqual(data["qualifications"][0]["qualificationType"], "多旋翼巡检")
        self.assertEqual(data["qualifications"][0]["profileType"], FixedRole.PILOT)
        self.assertNotIn("roleCode", data["qualifications"][0])
        self.assertTrue(data["qualifications"][0]["isEffective"])
