from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework.permissions import AllowAny
from rest_framework.test import APIClient
from rest_framework.views import APIView

from apps.access.models import DirectoryStatus, Role, ScopeType, Tenant, TenantStatus
from apps.access.test_support import grant_role_permissions
from apps.api_v1.business_response import attach_standard_envelope
from apps.dji_bff.models import DjiDeviceIndex
from config.urls import urlpatterns as project_urlpatterns

User = get_user_model()


class BrokenBusinessView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        raise RuntimeError("boom")


urlpatterns = [
    path("api/v1/__tests__/broken-business", BrokenBusinessView.as_view(), name="broken-business"),
] + project_urlpatterns


class BusinessApiResponseContractTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_health_should_use_standard_envelope(self):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "00000")
        self.assertEqual(response.data["msg"], "success")
        self.assertEqual(response.data["data"]["status"], "ok")

    def test_root_should_use_standard_envelope(self):
        response = self.client.get("/api/v1/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], "00000")
        self.assertEqual(response.data["msg"], "success")
        self.assertIn("/api/v1/drones", response.data["data"]["endpoints"]["drones"])

    def test_attach_standard_envelope_should_normalize_error_payload(self):
        self.assertEqual(
            attach_standard_envelope({"detail": "resource not found"}, 404),
            {"code": "C0404", "msg": "资源不存在", "data": None},
        )

    @override_settings(ROOT_URLCONF="apps.api_v1.tests")
    def test_unhandled_api_exception_should_return_internal_error_standard_code(self):
        response = self.client.get("/api/v1/__tests__/broken-business")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["code"], "E0001")
        self.assertEqual(response.json()["msg"], "系统异常")


class OpenApiDocsTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_swagger_ui_should_be_available(self):
        response = self.client.get("/api/v1/docs/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/api/v1/docs/schema/")

    def test_business_schema_should_expose_refactored_paths(self):
        response = self.client.get("/api/v1/docs/schema/")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        paths = schema["paths"]

        self.assertIn("/api/v1/drones", paths)
        self.assertIn("/api/v1/drones/available", paths)
        self.assertIn("/api/v1/drones/{id}/live/capacity", paths)
        self.assertIn("/api/v1/drones/{id}/live/start", paths)
        self.assertIn("/api/v1/routes/{id}/download", paths)
        self.assertIn("/api/v1/missions/{id}/cancel", paths)
        self.assertIn("/api/v1/media-files/{id}/download", paths)
        self.assertIn("/api/v1/drone-assignments/{id}/cancel", paths)

    def test_business_schema_should_not_expose_removed_paths(self):
        response = self.client.get("/api/v1/docs/schema/")
        self.assertEqual(response.status_code, 200)
        paths = response.json()["paths"]

        self.assertNotIn("/api/v1/drones/{id}/enable", paths)
        self.assertNotIn("/api/v1/drones/{id}/disable", paths)
        self.assertNotIn("/api/v1/drones/{id}/maintenance", paths)
        self.assertNotIn("/api/v1/drones/{id}/retire", paths)
        self.assertNotIn("/api/v1/routes/{id}/enable", paths)
        self.assertNotIn("/api/v1/routes/{id}/disable", paths)
        self.assertNotIn("/api/v1/missions/{id}/start", paths)
        self.assertNotIn("/api/v1/missions/{id}/pause", paths)
        self.assertNotIn("/api/v1/missions/{id}/resume", paths)
        self.assertNotIn("/api/v1/missions/{id}/complete", paths)
        self.assertNotIn("/api/v1/missions/{id}/fail", paths)
        self.assertNotIn("/api/v1/drone-assignments/{id}/reactivate", paths)
        self.assertNotIn("post", paths["/api/v1/media-files"])
        self.assertNotIn("put", paths["/api/v1/media-files/{id}"])
        self.assertNotIn("patch", paths["/api/v1/media-files/{id}"])
        self.assertNotIn("delete", paths["/api/v1/media-files/{id}"])


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
        DjiDeviceIndex.objects.create(device_sn="BOUNDARY-SN-01", last_payload={"name": "边界设备"})

    def test_platform_admin_should_be_blocked_from_business_list_even_if_permission_matrix_is_misconfigured(self):
        grant_role_permissions(self.platform_role, {"drone.view_drone": ScopeType.ALL})
        response = self.client.get("/api/v1/drones")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "A0403")
        self.assertEqual(response.data["msg"], "平台管理员不可访问租户业务接口")

    def test_platform_admin_should_be_blocked_from_business_create_even_if_permission_matrix_is_misconfigured(self):
        grant_role_permissions(self.platform_role, {"drone.manage_drone": ScopeType.ALL})
        response = self.client.post(
            "/api/v1/drones",
            {"code": "DJ-BOUNDARY-02", "device_sn": "BOUNDARY-SN-01"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "A0403")


class RejectUnknownFieldsMixinTests(TestCase):
    def test_unknown_fields_should_be_rejected_with_expected_message(self):
        from rest_framework import serializers

        from apps.api_v1.serializers import RejectUnknownFieldsMixin

        class ExampleSerializer(RejectUnknownFieldsMixin, serializers.Serializer):
            known = serializers.CharField()

        serializer = ExampleSerializer(data={"known": "ok", "unexpected": "nope"})
        self.assertFalse(serializer.is_valid())
        self.assertEqual(serializer.errors, {"unexpected": ["该字段在此接口不可写"]})
