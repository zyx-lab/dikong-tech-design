from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import DirectoryStatus, Role, ScopeType, Tenant, TenantStatus
from apps.access.test_support import grant_role_permissions
from apps.drone.models import Drone

User = get_user_model()


class BusinessApiResponseContractTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_health_should_include_business_code(self):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("business_code"), "SUCCESS")
        self.assertEqual(response.data.get("business_detail_code"), "OK")

    def test_root_should_include_business_code(self):
        response = self.client.get("/api/v1/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("business_code"), "SUCCESS")
        self.assertEqual(response.data.get("business_detail_code"), "OK")

    def test_business_endpoint_permission_error_should_include_business_code(self):
        response = self.client.get("/api/v1/drones")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data.get("business_code"), "PERMISSION_DENIED")
        self.assertEqual(response.data.get("business_detail_code"), "NOT_AUTHENTICATED")


class BusinessApiTenantBoundaryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(code="api_v1_boundary_tenant", name="业务租户", status=TenantStatus.ACTIVE)
        self.platform_user = User.objects.create_user(
            username="api_v1_platform_admin",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        self.platform_role = Role.objects.create(code="platform_admin", name="平台管理员", status=DirectoryStatus.ACTIVE)
        self.client.force_authenticate(self.platform_user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    def test_platform_admin_should_be_blocked_from_business_list_even_if_permission_matrix_is_misconfigured(self):
        grant_role_permissions(self.platform_role, {"drone.view_drone": ScopeType.ALL})
        Drone.objects.create(
            tenant=self.tenant,
            code="DJ-BOUNDARY-01",
            name="边界无人机",
            model="Matrice 4",
            serial_no="BOUNDARY-SN-01",
        )

        response = self.client.get("/api/v1/drones")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "FORBIDDEN")
        self.assertEqual(response.data["detail"], "platform admin cannot access tenant business api")

    def test_platform_admin_should_be_blocked_from_business_create_even_if_permission_matrix_is_misconfigured(self):
        grant_role_permissions(self.platform_role, {"drone.manage_drone": ScopeType.ALL})

        response = self.client.post(
            "/api/v1/drones",
            {
                "code": "DJ-BOUNDARY-02",
                "name": "边界新建",
                "model": "Matrice 4T",
                "serial_no": "BOUNDARY-SN-02",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["business_code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["business_detail_code"], "FORBIDDEN")
        self.assertEqual(response.data["detail"], "platform admin cannot access tenant business api")
