from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_support import ensure_staff_profile, ensure_tenant_role_binding, grant_role_permissions

User = get_user_model()


def DjiCloudPlatform():
    return apps.get_model("dji_bff", "DjiCloudPlatform")


class V2DjiPlatformApiTests(TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.user = User.objects.create_user(username="v2_platform_admin", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="V2 平台管理员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="v2_platform_tenant",
            role_code="tenant_admin",
            role_name="租户管理员",
        )
        grant_role_permissions(
            self.role,
            {
                "drone.view_drone": ScopeType.ALL,
                "drone.manage_drone": ScopeType.ALL,
            },
        )
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    def test_create_platform_should_store_config_set_default_and_redact_secrets(self):
        response = self.client.post(
            "/api/v2/iam/tenant/dji-platforms",
            {
                "name": "华南 DJI 平台",
                "base_url": "https://south.example.test/",
                "username": "adminPC1",
                "password": "secret",
                "login_flag": 1,
                "is_default": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        payload = response.data["data"]
        self.assertEqual(payload["name"], "华南 DJI 平台")
        self.assertEqual(payload["base_url"], "https://south.example.test")
        self.assertEqual(payload["username"], "adminPC1")
        self.assertTrue(payload["is_default"])
        self.assertNotIn("password", payload)
        self.assertNotIn("access_token", payload)
        self.assertNotIn("mqtt_password", payload)

        platform = DjiCloudPlatform().objects.get(tenant=self.tenant, name="华南 DJI 平台")
        self.assertEqual(platform.password, "secret")
        self.assertTrue(platform.is_default)

    def test_list_platforms_should_be_scoped_to_current_tenant_and_redact_secrets(self):
        other_user = User.objects.create_user(username="v2_other_admin", password="pass1234", status=1)
        ensure_staff_profile(other_user, name="其他管理员", employment_status=EmploymentStatus.ACTIVE)
        other_tenant, _member, _role = ensure_tenant_role_binding(
            other_user,
            tenant_code="v2_other_tenant",
            role_code="tenant_admin",
            role_name="租户管理员",
        )
        DjiCloudPlatform().objects.create(
            tenant=self.tenant,
            name="当前租户平台",
            base_url="https://current.example.test",
            username="current",
            password="secret",
            access_token="token",
            mqtt_password="mqtt-secret",
        )
        DjiCloudPlatform().objects.create(
            tenant=other_tenant,
            name="其他租户平台",
            base_url="https://other.example.test",
            username="other",
            password="secret",
        )

        response = self.client.get("/api/v2/iam/tenant/dji-platforms")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["total"], 1)
        item = response.data["data"]["list"][0]
        self.assertEqual(item["name"], "当前租户平台")
        self.assertNotIn("password", item)
        self.assertNotIn("access_token", item)
        self.assertNotIn("mqtt_password", item)
