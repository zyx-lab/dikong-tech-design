from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import UserStatus
from apps.iam_v2.models import Department, FixedRole, V2AccountProfile, V2AccountRoleAssignment

User = get_user_model()


class InspectionPilotProfileRouteRemovalTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.root = Department.objects.create(name="总部")
        self.department = Department.objects.create(name="飞行队", parent=self.root)
        self.admin = User.objects.create_user(username="removed_pilot_profile_admin", password="pass1234", status=UserStatus.ACTIVE)
        self.profile = V2AccountProfile.objects.create(
            user=self.admin,
            department=self.department,
            name="removed_pilot_profile_admin",
            phone="13800009001",
            email="removed_pilot_profile_admin@example.test",
        )
        V2AccountRoleAssignment.objects.create(
            account_profile=self.profile,
            role_code=FixedRole.DEPARTMENT_ADMIN,
            assigned_by_user=self.admin,
        )

    def test_old_inspection_pilot_profile_routes_should_be_removed(self):
        self.client.force_authenticate(self.admin)

        old_list_response = self.client.get("/api/v2/inspection/pilot-profiles")
        old_detail_response = self.client.get("/api/v2/inspection/pilot-profiles/1")
        old_qualification_response = self.client.get("/api/v2/inspection/pilot-profiles/1/qualifications")

        self.assertEqual(old_list_response.status_code, 404, getattr(old_list_response, "data", old_list_response.content))
        self.assertEqual(old_detail_response.status_code, 404, getattr(old_detail_response, "data", old_detail_response.content))
        self.assertEqual(old_qualification_response.status_code, 404, getattr(old_qualification_response, "data", old_qualification_response.content))
