from contextlib import redirect_stderr
from io import StringIO

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import path
from drf_spectacular.drainage import reset_generator_stats
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
        self.assertIn("/api/v1/flight-records", response.data["data"]["endpoints"]["flight_records"])

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

    def _operation(self, schema, *, path, method):
        return schema["paths"][path][method]

    def _resolve_route_write_schema(self, schema, *, path, method):
        operation = self._operation(schema, path=path, method=method)
        request_body = operation["requestBody"]["content"]
        media_schema = request_body.get("multipart/form-data") or request_body.get("application/json")
        if media_schema is None:
            media_schema = next(iter(request_body.values()))
        schema_ref = media_schema["schema"]["$ref"]
        schema_name = schema_ref.split("/")[-1]
        return schema_name, schema["components"]["schemas"][schema_name]

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
        self.assertIn("/api/v1/routes/{id}/kmz", paths)
        self.assertNotIn("/api/v1/missions/{id}/cancel", paths)
        self.assertIn("/api/v1/media-files/{id}/download", paths)
        self.assertIn("/api/v1/media-files/bind-mission", paths)
        self.assertEqual(schema["paths"]["/api/v1/media-files/bind-mission"].keys(), {"post"})
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
        self.assertNotIn("/api/v1/routes/{id}/download", paths)
        self.assertNotIn("/api/v1/routes/{id}/publish", paths)
        self.assertNotIn("/api/v1/routes/{id}/xml", paths)
        self.assertNotIn("/api/v1/missions/{id}/start", paths)
        self.assertNotIn("/api/v1/missions/{id}/pause", paths)
        self.assertNotIn("/api/v1/missions/{id}/resume", paths)
        self.assertNotIn("/api/v1/missions/{id}/complete", paths)
        self.assertNotIn("/api/v1/missions/{id}/fail", paths)
        self.assertNotIn("/api/v1/drone-assignments/{id}/reactivate", paths)
        self.assertNotIn("patch", paths["/api/v1/routes/{id}"])
        self.assertNotIn("post", paths["/api/v1/media-files"])
        self.assertNotIn("put", paths["/api/v1/media-files/{id}"])
        self.assertNotIn("patch", paths["/api/v1/media-files/{id}"])

    def test_business_schema_should_lock_route_kmz_only_write_contract(self):
        response = self.client.get("/api/v1/docs/schema/")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        self.assertIn("/api/v1/routes/{id}/kmz", schema["paths"])
        self.assertNotIn("/api/v1/routes/{id}/download", schema["paths"])
        self.assertNotIn("patch", schema["paths"]["/api/v1/routes/{id}"])
        self.assertNotIn("/api/v1/routes/{id}/publish", schema["paths"])
        self.assertNotIn("/api/v1/routes/{id}/xml", schema["paths"])
        create_schema_name, create_schema = self._resolve_route_write_schema(
            schema,
            path="/api/v1/routes",
            method="post",
        )
        update_schema_name, update_schema = self._resolve_route_write_schema(
            schema,
            path="/api/v1/routes/{id}",
            method="put",
        )

        self.assertEqual(create_schema_name, "RouteCreate")
        self.assertEqual(update_schema_name, "RouteUpdate")
        self.assertEqual(set(create_schema.get("properties", {}).keys()), {"name", "kmz_file"})
        self.assertEqual(set(update_schema.get("properties", {}).keys()), {"name", "kmz_file"})
        self.assertNotIn("PatchedRouteUpdate", schema["components"]["schemas"])
        for removed_field in ("waypoints", "description", "flight_height", "speed", "start_point"):
            self.assertNotIn(removed_field, create_schema.get("properties", {}))
            self.assertNotIn(removed_field, update_schema.get("properties", {}))

    def test_business_schema_should_describe_bound_drone_pool_and_current_duplicate_codes(self):
        response = self.client.get("/api/v1/docs/schema/")
        self.assertEqual(response.status_code, 200)
        schema = response.json()

        drone_claim = schema["components"]["schemas"]["DroneClaim"]
        self.assertIn("已绑定", drone_claim["properties"]["device_sn"]["description"])
        self.assertIn("已绑定", schema["paths"]["/api/v1/drones/available"]["get"]["summary"])
        self.assertIn("已绑定", schema["paths"]["/api/v1/drones"]["post"]["summary"])

        flight_record_create = schema["paths"]["/api/v1/flight-records"]["post"]
        self.assertNotIn("IDEMPOTENT_DUPLICATE", flight_record_create["responses"]["400"]["description"])
        self.assertIn("C0101", flight_record_create["responses"]["400"]["description"])

    def test_business_schema_should_lock_current_operation_surface_and_bodyless_actions(self):
        response = self.client.get("/api/v1/docs/schema/")
        self.assertEqual(response.status_code, 200)
        schema = response.json()

        expected_methods = {
            "/api/v1/drones": {"get", "post"},
            "/api/v1/drones/{id}": {"get", "put", "delete"},
            "/api/v1/routes": {"get", "post"},
            "/api/v1/routes/{id}": {"get", "put", "delete"},
            "/api/v1/missions": {"get", "post"},
            "/api/v1/missions/{id}": {"get", "put", "delete"},
            "/api/v1/flight-records": {"get", "post"},
            "/api/v1/flight-records/{id}": {"get", "put"},
            "/api/v1/drone-assignments": {"get", "post"},
            "/api/v1/drone-assignments/{id}": {"get"},
            "/api/v1/media-files": {"get"},
            "/api/v1/media-files/{id}": {"get", "delete"},
            "/api/v1/media-files/bind-mission": {"post"},
        }
        for path, methods in expected_methods.items():
            self.assertEqual(set(schema["paths"][path].keys()), methods)

        for path, method in (
            ("/api/v1/drones/{id}", "delete"),
            ("/api/v1/routes/{id}", "delete"),
            ("/api/v1/routes/{id}/kmz", "get"),
            ("/api/v1/missions/{id}", "delete"),
            ("/api/v1/flight-records/{id}/complete", "post"),
            ("/api/v1/flight-records/{id}/abort", "post"),
            ("/api/v1/drone-assignments/{id}/cancel", "post"),
            ("/api/v1/media-files/{id}", "delete"),
            ("/api/v1/media-files/{id}/download", "get"),
        ):
            self.assertNotIn("requestBody", self._operation(schema, path=path, method=method))

    def test_business_schema_should_describe_media_sync_fields_without_type_hint_warnings(self):
        reset_generator_stats()
        stderr = StringIO()
        with redirect_stderr(stderr):
            response = self.client.get("/api/v1/docs/schema/")

        self.assertEqual(response.status_code, 200)
        schema = response.json()
        media_file_read = schema["components"]["schemas"]["MediaFileRead"]
        properties = media_file_read["properties"]

        self.assertEqual(properties["dji_file_id"]["type"], "string")
        self.assertEqual(properties["sync_status"]["type"], "string")
        self.assertEqual(properties["last_sync_at"]["type"], "string")
        self.assertEqual(properties["last_sync_at"]["format"], "date-time")
        self.assertTrue(properties["last_sync_at"]["nullable"])
        self.assertNotIn("unable to resolve type hint", stderr.getvalue())

    def test_business_schema_should_describe_current_business_error_responses(self):
        response = self.client.get("/api/v1/docs/schema/")
        self.assertEqual(response.status_code, 200)
        schema = response.json()

        expected_responses = {
            ("/api/v1/drones", "get"): {"200", "401", "403", "500"},
            ("/api/v1/drones", "post"): {"201", "400", "401", "403", "409", "500"},
            ("/api/v1/drones/{id}", "get"): {"200", "401", "403", "404", "500"},
            ("/api/v1/drones/{id}", "put"): {"200", "400", "401", "403", "404", "409", "500"},
            ("/api/v1/drones/{id}", "delete"): {"200", "401", "403", "404", "500"},
            ("/api/v1/drones/{id}/live/capacity", "get"): {"200", "401", "403", "404", "500"},
            ("/api/v1/drones/{id}/live/start", "post"): {"200", "400", "401", "403", "404", "500"},
            ("/api/v1/drones/{id}/live/stop", "post"): {"200", "400", "401", "403", "404", "500"},
            ("/api/v1/drones/{id}/live/video-quality", "post"): {"200", "400", "401", "403", "404", "500"},
            ("/api/v1/drones/{id}/live/video-source", "post"): {"200", "400", "401", "403", "404", "500"},
            ("/api/v1/routes", "get"): {"200", "401", "403", "500"},
            ("/api/v1/routes", "post"): {"201", "400", "401", "403", "500"},
            ("/api/v1/routes/{id}", "get"): {"200", "401", "403", "404", "500"},
            ("/api/v1/routes/{id}", "put"): {"200", "400", "401", "403", "404", "500"},
            ("/api/v1/routes/{id}", "delete"): {"200", "400", "401", "403", "404", "500"},
            ("/api/v1/routes/{id}/kmz", "get"): {"200", "401", "403", "404", "500"},
            ("/api/v1/missions", "get"): {"200", "401", "403", "500"},
            ("/api/v1/missions", "post"): {"201", "400", "401", "403", "500"},
            ("/api/v1/missions/{id}", "get"): {"200", "401", "403", "404", "500"},
            ("/api/v1/missions/{id}", "put"): {"200", "400", "401", "403", "404", "500"},
            ("/api/v1/missions/{id}", "delete"): {"200", "400", "401", "403", "404", "500"},
            ("/api/v1/drone-assignments", "get"): {"200", "401", "403", "500"},
            ("/api/v1/drone-assignments", "post"): {"201", "400", "401", "403", "409", "500"},
            ("/api/v1/drone-assignments/{id}", "get"): {"200", "401", "403", "404", "500"},
            ("/api/v1/drone-assignments/{id}/cancel", "post"): {"200", "400", "401", "403", "404", "500"},
            ("/api/v1/media-files", "get"): {"200", "401", "403", "500"},
            ("/api/v1/media-files/{id}", "get"): {"200", "401", "403", "404", "500"},
            ("/api/v1/media-files/{id}", "delete"): {"200", "400", "401", "403", "404", "500"},
            ("/api/v1/media-files/{id}/download", "get"): {"302", "401", "403", "404", "500"},
            ("/api/v1/media-files/bind-mission", "post"): {"200", "400", "401", "403", "500"},
        }

        for (path, method), statuses in expected_responses.items():
            operation = self._operation(schema, path=path, method=method)
            self.assertTrue(
                statuses.issubset(set(operation["responses"].keys())),
                msg=f"{method.upper()} {path} responses drifted: {sorted(operation['responses'].keys())}",
            )

    def test_business_schema_should_describe_route_delete_result_shape(self):
        response = self.client.get("/api/v1/docs/schema/")
        self.assertEqual(response.status_code, 200)
        schema = response.json()

        delete_schema = self._operation(schema, path="/api/v1/routes/{id}", method="delete")["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        data_schema = delete_schema["properties"]["data"]
        if "$ref" in data_schema:
            data_schema = schema["components"]["schemas"][data_schema["$ref"].split("/")[-1]]
        self.assertEqual(data_schema["type"], "object")
        self.assertEqual(set(data_schema["properties"].keys()), {"id", "deleted"})


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
