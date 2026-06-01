from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import Tenant, TenantStatus
from apps.iam_v2.models import Department, FixedRole, V2AccountProfile, V2AccountRoleAssignment
from apps.workforce_v2.models import PilotProfile

User = get_user_model()


def create_v2_actor(*, username: str, role_code: str | None, department: Department):
    user = User.objects.create_user(username=username, password="pass1234", status=1)
    profile = V2AccountProfile.objects.create(user=user, department=department)
    if role_code is not None:
        V2AccountRoleAssignment.objects.create(account_profile=profile, role_code=role_code, assigned_by_user=user)
    return user, profile


class WorkforceV2ApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(code="workforce_v2_tenant", name="飞手 v2 租户", status=TenantStatus.ACTIVE)
        self.department = Department.objects.create(tenant=self.tenant, name="飞行队")
        self.admin, _ = create_v2_actor(
            username="workforce_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=self.department,
        )
        self.dispatcher, _ = create_v2_actor(
            username="workforce_dispatcher",
            role_code=FixedRole.TASK_MONITOR_DISPATCHER,
            department=self.department,
        )
        self.pilot_user, self.pilot_account = create_v2_actor(
            username="workforce_pilot",
            role_code=FixedRole.PILOT,
            department=self.department,
        )
        self.non_pilot_user, self.non_pilot_account = create_v2_actor(
            username="workforce_non_pilot",
            role_code=FixedRole.TASK_MONITOR_DISPATCHER,
            department=self.department,
        )

    def authenticate(self, user):
        self.client.force_authenticate(user)

    def test_department_admin_should_manage_pilot_profile_and_qualification(self):
        self.authenticate(self.admin)
        denied_response = self.client.post(
            "/api/v2/workforce/pilots",
            {"accountProfileId": self.non_pilot_account.id, "displayName": "非飞手账号"},
            format="json",
        )
        self.assertEqual(denied_response.status_code, 403, getattr(denied_response, "data", denied_response.content))

        create_response = self.client.post(
            "/api/v2/workforce/pilots",
            {"accountProfileId": self.pilot_account.id, "displayName": "正式飞手", "phone": "13800000000"},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        pilot_id = create_response.data["data"]["id"]
        self.assertEqual(PilotProfile.objects.filter(pk=pilot_id).count(), 1)

        qualification_response = self.client.post(
            f"/api/v2/workforce/pilots/{pilot_id}/qualifications",
            {
                "qualificationType": "多旋翼巡检",
                "certificateNo": "CERT-001",
                "issuedAt": timezone.now().date().isoformat(),
                "expiresAt": timezone.now().date().replace(year=timezone.now().date().year + 1).isoformat(),
            },
            format="json",
        )
        self.assertEqual(
            qualification_response.status_code,
            201,
            getattr(qualification_response, "data", qualification_response.content),
        )

        self.authenticate(self.dispatcher)
        list_response = self.client.get("/api/v2/workforce/pilots")
        self.assertEqual(list_response.status_code, 200, getattr(list_response, "data", list_response.content))
        self.assertEqual(list_response.data["data"]["total"], 1)
