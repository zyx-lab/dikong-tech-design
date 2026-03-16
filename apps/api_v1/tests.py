from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework.permissions import AllowAny
from rest_framework.test import APIClient
from rest_framework.views import APIView

from apps.access.models import DirectoryStatus, Role, ScopeType, Tenant, TenantStatus
from apps.access.test_support import grant_role_permissions
from apps.api_v1.business_response import attach_standard_envelope
from apps.drone.models import Drone
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
        self.assertEqual(response.data["data"]["endpoints"]["all_docs"], "http://testserver/docs/")
        self.assertEqual(response.data["data"]["endpoints"]["all_docs_schema"], "http://testserver/docs/schema/")

    def test_business_endpoint_permission_error_should_use_standard_envelope(self):
        response = self.client.get("/api/v1/drones")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data["code"], "A0401")
        self.assertEqual(response.data["msg"], "登录状态已失效")
        self.assertIsNone(response.data["data"])

    def test_attach_standard_envelope_should_normalize_into_standard_codes(self):
        self.assertEqual(
            attach_standard_envelope({"detail": "PERMISSION_DENIED"}, 403),
            {
                "code": "A0403",
                "msg": "无操作权限",
                "data": None,
            },
        )
        self.assertEqual(
            attach_standard_envelope({"detail": "resource not found"}, 404),
            {
                "code": "C0404",
                "msg": "资源不存在",
                "data": None,
            },
        )

    @override_settings(ROOT_URLCONF="apps.api_v1.tests")
    def test_unhandled_api_exception_should_return_internal_error_standard_code(self):
        with self.assertLogs("apps.access.exceptions", level="ERROR") as captured:
            response = self.client.get("/api/v1/__tests__/broken-business")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["code"], "E0001")
        self.assertEqual(response.json()["msg"], "系统异常")
        self.assertIsNone(response.json()["data"])
        self.assertTrue(any("Unhandled API exception on GET /api/v1/__tests__/broken-business" in item for item in captured.output))


class OpenApiDocsTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_swagger_ui_should_be_available_at_business_docs(self):
        response = self.client.get("/api/v1/docs/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/api/v1/docs/schema/")

    def test_business_schema_should_be_available_for_apifox_import(self):
        response = self.client.get("/api/v1/docs/schema/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.oai.openapi+json")
        self.assertEqual(response.json()["openapi"], "3.0.3")

    def test_business_schema_should_only_include_business_paths(self):
        response = self.client.get("/api/v1/docs/schema/")

        self.assertEqual(response.status_code, 200)
        paths = response.json()["paths"]
        self.assertIn("/api/v1/drones", paths)
        self.assertIn("/api/v1/missions", paths)
        self.assertNotIn("/internal/auth/users", paths)

    def test_schema_should_use_stable_enum_names(self):
        response = self.client.get("/api/v1/docs/schema/")

        self.assertEqual(response.status_code, 200)
        schemas = response.json()["components"]["schemas"]
        self.assertNotIn("StatusFe3Enum", schemas)
        self.assertNotIn("Status177Enum", schemas)
        self.assertNotIn("Status0d1Enum", schemas)
        self.assertNotIn("StatusC48Enum", schemas)

    def test_drone_list_should_document_filters_and_tenant_header(self):
        response = self.client.get("/api/v1/docs/schema/")

        self.assertEqual(response.status_code, 200)
        operation = response.json()["paths"]["/api/v1/drones"]["get"]
        parameters = {item["name"]: item for item in operation["parameters"]}

        self.assertIn("X-TENANT-CODE", parameters)
        self.assertIn("code", parameters)
        self.assertIn("status", parameters)
        self.assertIn("租户编码", parameters["X-TENANT-CODE"]["description"])
        self.assertIn("模糊匹配", parameters["code"]["description"])
        self.assertEqual(set(parameters["status"]["schema"]["enum"]), {"ENABLED", "DISABLED", "MAINTENANCE", "RETIRED"})
        self.assertIn("401", operation["responses"])
        self.assertIn("403", operation["responses"])

    def test_drone_create_should_document_business_examples_and_body_fields(self):
        response = self.client.get("/api/v1/docs/schema/")

        self.assertEqual(response.status_code, 200)
        schema = response.json()
        operation = schema["paths"]["/api/v1/drones"]["post"]
        duplicate_examples = operation["responses"]["409"]["content"]["application/json"]["examples"]
        duplicate_values = [item["value"] for item in duplicate_examples.values()]

        self.assertTrue(any(item["code"] == "C0101" for item in duplicate_values))
        drone_write_schema = schema["components"]["schemas"]["DroneWrite"]
        self.assertIn("租户内业务编码", drone_write_schema["properties"]["code"]["description"])
        self.assertIn("出厂序列号", drone_write_schema["properties"]["serial_no"]["description"])

    def test_drone_assignment_reactivate_should_document_state_conflict(self):
        response = self.client.get("/api/v1/docs/schema/")

        self.assertEqual(response.status_code, 200)
        operation = response.json()["paths"]["/api/v1/drone-assignments/{id}/reactivate"]["post"]
        conflict_examples = operation["responses"]["409"]["content"]["application/json"]["examples"]
        conflict_values = [item["value"] for item in conflict_examples.values()]

        self.assertTrue(any(item["code"] == "C0201" for item in conflict_values))
        self.assertTrue(any(item["msg"] == "存在同一无人机与飞手的 ACTIVE 分配，不能重复激活" for item in conflict_values))
        self.assertIn("X-TENANT-CODE", {item["name"] for item in operation["parameters"]})

    def test_route_list_should_document_filters_and_tenant_header(self):
        response = self.client.get("/api/v1/docs/schema/")

        self.assertEqual(response.status_code, 200)
        operation = response.json()["paths"]["/api/v1/routes"]["get"]
        parameters = {item["name"]: item for item in operation["parameters"]}

        self.assertIn("X-TENANT-CODE", parameters)
        self.assertIn("status", parameters)
        self.assertIn("route_type", parameters)
        self.assertIn("name", parameters)
        self.assertIn("租户编码", parameters["X-TENANT-CODE"]["description"])
        self.assertIn("模糊匹配", parameters["name"]["description"])
        self.assertEqual(set(parameters["status"]["schema"]["enum"]), {0, 1})
        self.assertEqual(set(parameters["route_type"]["schema"]["enum"]), {0})

    def test_waypoint_create_should_document_business_examples_and_body_fields(self):
        response = self.client.get("/api/v1/docs/schema/")

        self.assertEqual(response.status_code, 200)
        schema = response.json()
        operation = schema["paths"]["/api/v1/waypoints"]["post"]
        invalid_examples = operation["responses"]["400"]["content"]["application/json"]["examples"]
        invalid_values = [item["value"] for item in invalid_examples.values()]

        self.assertTrue(any(item.get("code") == "B0001" for item in invalid_values))
        self.assertTrue(
            any(item.get("data", {}).get("route") == ["仅允许向状态为正常的航线新增航点"] for item in invalid_values)
        )
        waypoint_create_schema = schema["components"]["schemas"]["WaypointCreate"]
        self.assertIn("必须唯一", waypoint_create_schema["properties"]["sequence"]["description"])
        self.assertIn("状态为 ACTIVE", waypoint_create_schema["properties"]["route"]["description"])

    def test_mission_start_should_document_state_conflict_and_body_restriction(self):
        response = self.client.get("/api/v1/docs/schema/")

        self.assertEqual(response.status_code, 200)
        operation = response.json()["paths"]["/api/v1/missions/{id}/start"]["post"]
        conflict_examples = operation["responses"]["409"]["content"]["application/json"]["examples"]
        invalid_examples = operation["responses"]["400"]["content"]["application/json"]["examples"]
        conflict_values = [item["value"] for item in conflict_examples.values()]
        invalid_values = [item["value"] for item in invalid_examples.values()]

        self.assertTrue(any(item["code"] == "C0201" for item in conflict_values))
        self.assertTrue(any(item["msg"] == "当前任务状态不允许启动" for item in conflict_values))
        self.assertTrue(any(item["msg"] == "start 请求不支持提交 body 参数" for item in invalid_values))
        self.assertIn("X-TENANT-CODE", {item["name"] for item in operation["parameters"]})

    def test_flight_record_list_and_complete_should_document_filters_and_state_conflict(self):
        response = self.client.get("/api/v1/docs/schema/")

        self.assertEqual(response.status_code, 200)
        schema = response.json()
        list_operation = schema["paths"]["/api/v1/flight-records"]["get"]
        parameters = {item["name"]: item for item in list_operation["parameters"]}

        self.assertIn("X-TENANT-CODE", parameters)
        self.assertIn("flight_no", parameters)
        self.assertIn("status", parameters)
        self.assertIn("模糊匹配", parameters["flight_no"]["description"])
        self.assertEqual(set(parameters["status"]["schema"]["enum"]), {0, 1, 2})

        complete_operation = schema["paths"]["/api/v1/flight-records/{id}/complete"]["post"]
        conflict_examples = complete_operation["responses"]["409"]["content"]["application/json"]["examples"]
        conflict_values = [item["value"] for item in conflict_examples.values()]
        self.assertTrue(any(item["code"] == "C0201" for item in conflict_values))
        self.assertTrue(any(item["msg"] == "当前飞行记录状态不允许完成" for item in conflict_values))

    def test_media_file_list_and_create_should_document_filters_and_body_fields(self):
        response = self.client.get("/api/v1/docs/schema/")

        self.assertEqual(response.status_code, 200)
        schema = response.json()
        list_operation = schema["paths"]["/api/v1/media-files"]["get"]
        parameters = {item["name"]: item for item in list_operation["parameters"]}

        self.assertIn("X-TENANT-CODE", parameters)
        self.assertIn("media_type", parameters)
        self.assertIn("file_name", parameters)
        self.assertIn("模糊匹配", parameters["file_name"]["description"])
        self.assertEqual(set(parameters["media_type"]["schema"]["enum"]), {1, 2})

        create_operation = schema["paths"]["/api/v1/media-files"]["post"]
        invalid_examples = create_operation["responses"]["400"]["content"]["application/json"]["examples"]
        invalid_values = [item["value"] for item in invalid_examples.values()]
        self.assertTrue(any(item.get("data", {}).get("flight_record") == ["仅允许绑定当前租户下的飞行记录"] for item in invalid_values))
        media_file_write_schema = schema["components"]["schemas"]["MediaFileWrite"]
        self.assertIn("媒体类型", media_file_write_schema["properties"]["media_type"]["description"])
        self.assertIn("原始文件访问地址", media_file_write_schema["properties"]["file_url"]["description"])

    def test_documented_business_operations_should_include_internal_error_response(self):
        response = self.client.get("/api/v1/docs/schema/")

        self.assertEqual(response.status_code, 200)
        schema = response.json()
        operations = [
            ("/api/v1/drones", "get"),
            ("/api/v1/drones/{id}/assignments/latest", "get"),
            ("/api/v1/drone-assignments/{id}/reactivate", "post"),
            ("/api/v1/routes/{id}/enable", "post"),
            ("/api/v1/waypoints", "post"),
            ("/api/v1/missions/{id}/start", "post"),
            ("/api/v1/flight-records/{id}/complete", "post"),
            ("/api/v1/media-files", "post"),
        ]

        for path, method in operations:
            operation = schema["paths"][path][method]
            self.assertIn("500", operation["responses"], msg=f"{method.upper()} {path} missing 500 response")

        internal_error_examples = schema["paths"]["/api/v1/missions/{id}/start"]["post"]["responses"]["500"]["content"][
            "application/json"
        ]["examples"]
        internal_error_values = [item["value"] for item in internal_error_examples.values()]
        self.assertTrue(any(item["code"] == "E0001" for item in internal_error_values))
        self.assertTrue(any(item["msg"] == "系统异常" for item in internal_error_values))


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
        self.assertEqual(response.data["code"], "A0403")
        self.assertEqual(response.data["msg"], "平台管理员不可访问租户业务接口")
        self.assertIsNone(response.data["data"])

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
        self.assertEqual(response.data["code"], "A0403")
        self.assertEqual(response.data["msg"], "平台管理员不可访问租户业务接口")
        self.assertIsNone(response.data["data"])
