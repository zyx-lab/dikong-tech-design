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
        self.assertEqual(response.data["endpoints"]["all_docs"], "http://testserver/docs/")
        self.assertEqual(response.data["endpoints"]["all_docs_schema"], "http://testserver/docs/schema/")

    def test_business_endpoint_permission_error_should_include_business_code(self):
        response = self.client.get("/api/v1/drones")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.data.get("business_code"), "PERMISSION_DENIED")
        self.assertEqual(response.data.get("business_detail_code"), "NOT_AUTHENTICATED")


class OpenApiDocsTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_swagger_ui_should_be_available_at_docs(self):
        response = self.client.get("/docs/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/docs/schema/")

    def test_schema_should_be_available_for_apifox_import(self):
        response = self.client.get("/docs/schema/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.oai.openapi+json")
        self.assertEqual(response.json()["openapi"], "3.0.3")

    def test_schema_should_include_internal_and_business_paths(self):
        response = self.client.get("/docs/schema/")

        self.assertEqual(response.status_code, 200)
        paths = response.json()["paths"]
        self.assertIn("/internal/auth/users", paths)
        self.assertIn("/internal/auth/tenant-members/invite", paths)
        self.assertIn("/api/v1/drones", paths)
        self.assertIn("/api/v1/missions", paths)

    def test_schema_should_use_stable_enum_names(self):
        response = self.client.get("/docs/schema/")

        self.assertEqual(response.status_code, 200)
        schemas = response.json()["components"]["schemas"]
        self.assertIn("ActiveDisabledStatusEnum", schemas)
        self.assertIn("DroneAssignmentStatusEnum", schemas)
        self.assertIn("DroneStatusEnum", schemas)
        self.assertIn("FlightRecordStatusEnum", schemas)
        self.assertIn("MissionStatusEnum", schemas)
        self.assertIn("RouteStatusEnum", schemas)
        self.assertIn("RouteTypeEnum", schemas)
        self.assertNotIn("StatusFe3Enum", schemas)
        self.assertNotIn("Status177Enum", schemas)
        self.assertNotIn("Status0d1Enum", schemas)
        self.assertNotIn("StatusC48Enum", schemas)

    def test_drone_list_should_document_filters_and_tenant_header(self):
        response = self.client.get("/docs/schema/")

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
        response = self.client.get("/docs/schema/")

        self.assertEqual(response.status_code, 200)
        schema = response.json()
        operation = schema["paths"]["/api/v1/drones"]["post"]
        duplicate_examples = operation["responses"]["409"]["content"]["application/json"]["examples"]
        duplicate_values = [item["value"] for item in duplicate_examples.values()]

        self.assertTrue(any(item["business_code"] == "IDEMPOTENT_DUPLICATE" for item in duplicate_values))
        drone_write_schema = schema["components"]["schemas"]["DroneWrite"]
        self.assertIn("租户内业务编码", drone_write_schema["properties"]["code"]["description"])
        self.assertIn("出厂序列号", drone_write_schema["properties"]["serial_no"]["description"])

    def test_drone_assignment_reactivate_should_document_state_conflict(self):
        response = self.client.get("/docs/schema/")

        self.assertEqual(response.status_code, 200)
        operation = response.json()["paths"]["/api/v1/drone-assignments/{id}/reactivate"]["post"]
        conflict_examples = operation["responses"]["409"]["content"]["application/json"]["examples"]
        conflict_values = [item["value"] for item in conflict_examples.values()]

        self.assertTrue(any(item["business_code"] == "STATE_CONFLICT" for item in conflict_values))
        self.assertTrue(any(item["business_detail_code"] == "STATE_CONFLICT" for item in conflict_values))
        self.assertIn("X-TENANT-CODE", {item["name"] for item in operation["parameters"]})

    def test_route_list_should_document_filters_and_tenant_header(self):
        response = self.client.get("/docs/schema/")

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
        response = self.client.get("/docs/schema/")

        self.assertEqual(response.status_code, 200)
        schema = response.json()
        operation = schema["paths"]["/api/v1/waypoints"]["post"]
        invalid_examples = operation["responses"]["400"]["content"]["application/json"]["examples"]
        invalid_values = [item["value"] for item in invalid_examples.values()]

        self.assertTrue(any(item.get("business_code") == "INVALID_PARAMS" for item in invalid_values))
        self.assertTrue(
            any(item.get("errors", {}).get("route") == ["仅允许向状态为正常的航线新增航点"] for item in invalid_values)
        )
        waypoint_create_schema = schema["components"]["schemas"]["WaypointCreate"]
        self.assertIn("必须唯一", waypoint_create_schema["properties"]["sequence"]["description"])
        self.assertIn("状态为 ACTIVE", waypoint_create_schema["properties"]["route"]["description"])

    def test_mission_start_should_document_state_conflict_and_body_restriction(self):
        response = self.client.get("/docs/schema/")

        self.assertEqual(response.status_code, 200)
        operation = response.json()["paths"]["/api/v1/missions/{id}/start"]["post"]
        conflict_examples = operation["responses"]["409"]["content"]["application/json"]["examples"]
        invalid_examples = operation["responses"]["400"]["content"]["application/json"]["examples"]
        conflict_values = [item["value"] for item in conflict_examples.values()]
        invalid_values = [item["value"] for item in invalid_examples.values()]

        self.assertTrue(any(item["business_code"] == "STATE_CONFLICT" for item in conflict_values))
        self.assertTrue(any(item["detail"] == "当前任务状态不允许启动" for item in conflict_values))
        self.assertTrue(any(item["detail"] == "start 请求不支持提交 body 参数" for item in invalid_values))
        self.assertIn("X-TENANT-CODE", {item["name"] for item in operation["parameters"]})


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
