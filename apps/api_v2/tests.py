import ast
import hashlib
import json
from pathlib import Path
import re

from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import connection
from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import DirectoryStatus, UserStatus
from apps.iam_v2.models import (
    DEPARTMENT_ROLE_CODES,
    Department,
    FixedRole,
    ROLE_CODE_ORDER,
    V2AccountQualification,
    V2AccountProfile,
    V2AccountRoleProfile,
    V2AccountRoleAssignment,
)
from apps.audit_v2.models import V2AuditLog

User = get_user_model()


def create_v2_actor(*, username: str, role_code: str | None, department: Department, is_platform_admin: bool = False):
    user = User.objects.create_user(username=username, password="pass1234", status=1, is_platform_admin=is_platform_admin)
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


class ApiV2SchemaBoundaryTests(TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def _schema(self):
        response = self.client.get("/api/v2/docs/schema/")
        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        return response.json()

    def _schema_data(self, schema, *, path: str, method: str, status_code: str | None = None):
        operation = schema["paths"][path][method.lower()]
        responses = operation["responses"]
        if status_code is None:
            status_code = next(code for code in responses if str(code).startswith("2"))
        response_schema = responses[status_code]["content"]["application/json"]["schema"]
        if "$ref" in response_schema:
            response_schema = schema["components"]["schemas"][response_schema["$ref"].split("/")[-1]]
        return response_schema["properties"]["data"]

    def _schema_ref(self, schema, schema_value):
        while "$ref" in schema_value:
            schema_value = schema["components"]["schemas"][schema_value["$ref"].split("/")[-1]]
        return schema_value

    def _request_body_properties(self, schema, *, path: str, method: str, content_type: str):
        request_schema = schema["paths"][path][method.lower()]["requestBody"]["content"][content_type]["schema"]
        request_schema = self._schema_ref(schema, request_schema)
        return request_schema.get("properties", {})

    def _actual_v2_routes(self):
        def route_to_openapi(route):
            value = str(route).replace("^", "").replace("\\Z", "").replace("$", "")
            value = re.sub(r"<(?:(\w+):)?(\w+)>", r"{\2}", value)
            return value

        def collect(patterns, prefix=""):
            routes = {}
            for pattern in patterns:
                current_prefix = prefix + route_to_openapi(pattern.pattern)
                if isinstance(pattern, URLResolver):
                    routes.update(collect(pattern.url_patterns, current_prefix))
                    continue
                if not isinstance(pattern, URLPattern):
                    continue
                path = f"/{current_prefix}".rstrip("/")
                if not path.startswith("/api/v2/") or path.startswith("/api/v2/docs"):
                    continue
                view_class = getattr(pattern.callback, "cls", None)
                if view_class is None:
                    continue
                methods = {
                    method.upper()
                    for method in ("get", "post", "put", "patch", "delete")
                    if hasattr(view_class, method)
                }
                routes[path] = methods
            return routes

        return collect(get_resolver().url_patterns)

    def test_v2_schema_should_expose_only_new_v2_boundaries(self):
        response = self.client.get("/api/v2/docs/schema/")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        paths = response.json()["paths"]
        self.assertTrue(paths)
        self.assertTrue(all(path.startswith("/api/v2/") for path in paths), sorted(paths))

        expected_paths = {
            "/api/v2/iam/session/login",
            "/api/v2/iam/session/refresh",
            "/api/v2/iam/session/logout",
            "/api/v2/iam/me/context",
            "/api/v2/iam/me/profile",
            "/api/v2/iam/departments",
            "/api/v2/iam/departments/{id}",
            "/api/v2/iam/departments/{id}/enable",
            "/api/v2/iam/departments/{id}/disable",
            "/api/v2/iam/profile-types",
            "/api/v2/iam/profile-types/{code}",
            "/api/v2/iam/accounts",
            "/api/v2/iam/accounts/{id}",
            "/api/v2/iam/accounts/{id}/profiles",
            "/api/v2/iam/accounts/{id}/profiles/{profile_type}",
            "/api/v2/iam/accounts/{id}/qualifications",
            "/api/v2/iam/accounts/{id}/qualifications/{qualification_id}",
            "/api/v2/iam/accounts/{id}/roles",
            "/api/v2/iam/roles",
            "/api/v2/resource/dji-connections",
            "/api/v2/resource/dji-connections/mqtt-health",
            "/api/v2/resource/dji-connections/{id}",
            "/api/v2/resource/dji-connections/{id}/discover",
            "/api/v2/resource/dji-connections/{id}/mqtt-messages/latest",
            "/api/v2/resource/drones",
            "/api/v2/resource/drones/{id}",
            "/api/v2/resource/docks",
            "/api/v2/resource/docks/{id}",
            "/api/v2/resource/gateways",
            "/api/v2/resource/gateways/{id}",
            "/api/v2/resource/payloads",
            "/api/v2/resource/payloads/{id}",
            "/api/v2/resource/summary",
            "/api/v2/resource/bindings",
            "/api/v2/resource/bindings/{id}",
            "/api/v2/resource/share-groups",
            "/api/v2/resource/share-groups/{id}",
            "/api/v2/resource/share-groups/{id}/departments",
            "/api/v2/resource/share-groups/{id}/departments/{department_id}",
            "/api/v2/resource/share-groups/{id}/resources",
            "/api/v2/resource/share-groups/{id}/resources/{resource_share_id}",
            "/api/v2/resource/audit-logs",
            "/api/v2/inspection/routes",
            "/api/v2/inspection/routes/{id}",
            "/api/v2/inspection/missions",
            "/api/v2/inspection/missions/{id}",
            "/api/v2/inspection/missions/{id}/preflight-check",
            "/api/v2/inspection/missions/{id}/cloud-execution/refresh",
            "/api/v2/inspection/missions/{id}/start",
            "/api/v2/inspection/missions/{id}/complete",
            "/api/v2/inspection/missions/{id}/cancel",
            "/api/v2/inspection/missions/{id}/fail",
            "/api/v2/inspection/missions/{id}/abort",
            "/api/v2/inspection/active-flights",
            "/api/v2/inspection/active-flights/{id}",
            "/api/v2/inspection/telemetry/snapshots",
            "/api/v2/inspection/live/capacity",
            "/api/v2/inspection/live/start",
            "/api/v2/inspection/live/stop",
            "/api/v2/inspection/live/update",
            "/api/v2/inspection/live/switch",
            "/api/v2/inspection/camera/actions",
            "/api/v2/inspection/flight-records",
            "/api/v2/inspection/flight-records/{id}",
            "/api/v2/inspection/flight-records/{id}/refresh-media",
            "/api/v2/inspection/media-files",
            "/api/v2/inspection/media-files/{id}",
            "/api/v2/inspection/media-files/{id}/refresh-url",
        }
        self.assertTrue(expected_paths.issubset(set(paths)))
        self.assertIn("delete", paths["/api/v2/inspection/routes/{id}"])
        self.assertIn("delete", paths["/api/v2/inspection/missions/{id}"])

        removed_paths = {
            "/api/v2/__internal__/dji/sync/devices",
            "/api/v2/__internal__/dji/sync/media",
            "/api/v2/drones",
            "/api/v2/drones/available",
            "/api/v2/routes",
            "/api/v2/missions",
            "/api/v2/flight-records",
            "/api/v2/media-files",
            "/api/v2/resource/routes",
            "/api/v2/resource/routes/{id}",
            "/api/v2/resource/missions",
            "/api/v2/resource/missions/{id}",
            "/api/v2/resource/flight-records",
            "/api/v2/resource/flight-records/{id}",
            "/api/v2/resource/media-files",
            "/api/v2/resource/media-files/{id}",
            "/api/v2/iam/tenant/dji-platforms",
            "/api/v2/iam/session/register",
            "/api/v2/iam/session/register-by-phone",
            "/api/v2/inspection/pilot-profiles",
            "/api/v2/inspection/pilot-profiles/{id}",
            "/api/v2/inspection/pilot-profiles/{id}/delete",
            "/api/v2/inspection/pilot-profiles/{id}/qualifications",
            "/api/v2/inspection/pilot-profiles/{id}/qualifications/{qualification_id}",
            "/api/v2/inspection/pilot-profiles/{id}/qualifications/{qualification_id}/delete",
            "/api/v2/workforce/pilots",
            "/api/v2/workforce/pilots/{id}",
            "/api/v2/workforce/pilots/{pilot_id}/qualifications",
            "/api/v2/workforce/pilots/{pilot_id}/qualifications/{id}",
        }
        self.assertTrue(removed_paths.isdisjoint(set(paths)))
        self.assertNotIn("/api/v1", response.content.decode("utf-8"))

    def test_v2_schema_should_not_expose_tenant_concepts(self):
        schema_text = json.dumps(self._schema(), ensure_ascii=False)

        for forbidden in ("tenantId", "/api/v2/iam/tenant", "tenant", "Tenant", "租户"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, schema_text)

    def test_v2_schema_should_match_implemented_urlconf_paths_and_methods(self):
        schema = self._schema()
        documented = {
            path: {
                method.upper()
                for method in path_item
                if method.lower() in {"get", "post", "put", "patch", "delete"}
            }
            for path, path_item in schema["paths"].items()
        }

        self.assertEqual(documented, self._actual_v2_routes())

    def test_v2_schema_success_responses_should_use_standard_json_envelope(self):
        schema = self._schema()
        missing = []
        malformed = []

        for path, path_item in schema["paths"].items():
            for method, operation in path_item.items():
                if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                    continue
                for status_code, response in operation.get("responses", {}).items():
                    if not str(status_code).startswith("2"):
                        continue
                    json_schema = response.get("content", {}).get("application/json", {}).get("schema")
                    if not json_schema:
                        missing.append(f"{method.upper()} {path} -> {status_code}")
                        continue
                    if "$ref" in json_schema:
                        json_schema = schema["components"]["schemas"][json_schema["$ref"].split("/")[-1]]
                    properties = json_schema.get("properties", {})
                    if not {"code", "msg", "data"}.issubset(properties):
                        malformed.append(f"{method.upper()} {path} -> {status_code}")

        self.assertEqual(missing, [])
        self.assertEqual(malformed, [])

    def test_v2_schema_should_describe_list_data_shapes(self):
        schema = self._schema()
        list_operations = [
            ("GET", "/api/v2/iam/accounts"),
            ("GET", "/api/v2/iam/accounts/{id}/profiles"),
            ("GET", "/api/v2/iam/accounts/{id}/qualifications"),
            ("GET", "/api/v2/iam/departments"),
            ("GET", "/api/v2/iam/profile-types"),
            ("GET", "/api/v2/iam/roles"),
            ("GET", "/api/v2/resource/audit-logs"),
            ("GET", "/api/v2/resource/dji-connections"),
            ("GET", "/api/v2/resource/dji-connections/mqtt-health"),
            ("GET", "/api/v2/resource/dji-connections/{id}/mqtt-messages/latest"),
            ("GET", "/api/v2/resource/docks"),
            ("GET", "/api/v2/resource/drones"),
            ("GET", "/api/v2/resource/gateways"),
            ("GET", "/api/v2/resource/payloads"),
            ("GET", "/api/v2/resource/share-groups"),
            ("GET", "/api/v2/inspection/active-flights"),
            ("GET", "/api/v2/inspection/flight-records"),
            ("GET", "/api/v2/inspection/media-files"),
            ("GET", "/api/v2/inspection/missions"),
            ("GET", "/api/v2/inspection/routes"),
        ]

        for method, path in list_operations:
            with self.subTest(method=method, path=path):
                data_schema = self._schema_data(schema, path=path, method=method)
                self.assertEqual(set(data_schema["properties"]), {"list", "total"})
                self.assertEqual(data_schema["properties"]["list"]["type"], "array")
                self.assertEqual(data_schema["properties"]["total"]["type"], "integer")

    def test_v2_schema_should_describe_current_success_status_codes(self):
        schema = self._schema()
        expected_statuses = {
            ("POST", "/api/v2/iam/accounts"): "201",
            ("POST", "/api/v2/iam/accounts/{id}/profiles"): "201",
            ("POST", "/api/v2/iam/accounts/{id}/qualifications"): "201",
            ("POST", "/api/v2/iam/departments"): "201",
            ("POST", "/api/v2/iam/profile-types"): "201",
            ("POST", "/api/v2/inspection/missions"): "201",
            ("POST", "/api/v2/inspection/routes"): "201",
            ("POST", "/api/v2/resource/bindings"): "201",
            ("POST", "/api/v2/resource/dji-connections"): "201",
            ("POST", "/api/v2/resource/share-groups"): "201",
            ("POST", "/api/v2/resource/share-groups/{id}/departments"): "201",
            ("POST", "/api/v2/resource/share-groups/{id}/resources"): "201",
            ("DELETE", "/api/v2/inspection/missions/{id}"): "200",
            ("DELETE", "/api/v2/resource/bindings/{id}"): "200",
            ("DELETE", "/api/v2/resource/share-groups/{id}/departments/{department_id}"): "200",
            ("DELETE", "/api/v2/resource/share-groups/{id}/resources/{resource_share_id}"): "200",
        }

        for (method, path), expected_status in expected_statuses.items():
            with self.subTest(method=method, path=path):
                operation = schema["paths"][path][method.lower()]
                success_statuses = {code for code in operation["responses"] if str(code).startswith("2")}
                self.assertEqual(success_statuses, {expected_status})

    def test_v2_schema_should_document_request_bodies_for_body_mutations(self):
        schema = self._schema()
        request_body_operations = [
            ("POST", "/api/v2/iam/accounts"),
            ("POST", "/api/v2/iam/accounts/{id}/profiles"),
            ("PUT", "/api/v2/iam/accounts/{id}/profiles/{profile_type}"),
            ("POST", "/api/v2/iam/accounts/{id}/qualifications"),
            ("PUT", "/api/v2/iam/accounts/{id}/qualifications/{qualification_id}"),
            ("PUT", "/api/v2/iam/accounts/{id}"),
            ("PUT", "/api/v2/iam/accounts/{id}/roles"),
            ("POST", "/api/v2/iam/departments"),
            ("PUT", "/api/v2/iam/departments/{id}"),
            ("POST", "/api/v2/iam/profile-types"),
            ("PUT", "/api/v2/iam/profile-types/{code}"),
            ("POST", "/api/v2/iam/session/login"),
            ("POST", "/api/v2/iam/session/refresh"),
            ("PUT", "/api/v2/inspection/flight-records/{id}"),
            ("POST", "/api/v2/inspection/live/start"),
            ("POST", "/api/v2/inspection/live/stop"),
            ("POST", "/api/v2/inspection/live/switch"),
            ("POST", "/api/v2/inspection/live/update"),
            ("POST", "/api/v2/inspection/camera/actions"),
            ("POST", "/api/v2/inspection/media-files/{id}/refresh-url"),
            ("POST", "/api/v2/inspection/missions"),
            ("PUT", "/api/v2/inspection/missions/{id}"),
            ("POST", "/api/v2/inspection/missions/{id}/abort"),
            ("POST", "/api/v2/inspection/missions/{id}/cancel"),
            ("POST", "/api/v2/inspection/missions/{id}/fail"),
            ("POST", "/api/v2/inspection/routes"),
            ("PUT", "/api/v2/inspection/routes/{id}"),
            ("POST", "/api/v2/inspection/telemetry/snapshots"),
            ("POST", "/api/v2/resource/bindings"),
            ("POST", "/api/v2/resource/dji-connections"),
            ("PUT", "/api/v2/resource/dji-connections/{id}"),
            ("POST", "/api/v2/resource/share-groups"),
            ("PUT", "/api/v2/resource/share-groups/{id}"),
            ("POST", "/api/v2/resource/share-groups/{id}/departments"),
            ("POST", "/api/v2/resource/share-groups/{id}/resources"),
            ("PUT", "/api/v2/resource/share-groups/{id}/resources/{resource_share_id}"),
        ]

        for method, path in request_body_operations:
            with self.subTest(method=method, path=path):
                self.assertIn("requestBody", schema["paths"][path][method.lower()])

    def test_v2_schema_should_document_route_cover_multipart_request_body(self):
        schema = self._schema()

        post_content = schema["paths"]["/api/v2/inspection/routes"]["post"]["requestBody"]["content"]
        self.assertNotIn("application/json", post_content)
        self.assertIn("multipart/form-data", post_content)
        post_multipart = self._request_body_properties(
            schema,
            path="/api/v2/inspection/routes",
            method="post",
            content_type="multipart/form-data",
        )
        self.assertIn("coverImage", post_multipart)
        self.assertIn("djiConnectionId", post_multipart)
        self.assertIn("kmzFile", post_multipart)
        self.assertNotIn("waypoints", post_multipart)
        self.assertNotIn("waylineType", post_multipart)
        self.assertNotIn("defaultAltitude", post_multipart)
        self.assertNotIn("defaultSpeed", post_multipart)
        self.assertEqual(post_multipart["coverImage"]["type"], "string")
        self.assertEqual(post_multipart["coverImage"]["format"], "binary")
        self.assertEqual(post_multipart["kmzFile"]["format"], "binary")

        put_content = schema["paths"]["/api/v2/inspection/routes/{id}"]["put"]["requestBody"]["content"]
        self.assertIn("application/json", put_content)
        self.assertIn("multipart/form-data", put_content)
        put_json = self._request_body_properties(
            schema,
            path="/api/v2/inspection/routes/{id}",
            method="put",
            content_type="application/json",
        )
        self.assertIn("coverImage", put_json)
        self.assertNotIn("waypoints", put_json)
        put_multipart = self._request_body_properties(
            schema,
            path="/api/v2/inspection/routes/{id}",
            method="put",
            content_type="multipart/form-data",
        )
        self.assertIn("kmzFile", put_multipart)
        self.assertIn("djiConnectionId", put_multipart)
        self.assertNotIn("waypoints", put_multipart)
        self.assertNotIn("waylineType", put_multipart)
        self.assertNotIn("defaultAltitude", put_multipart)
        self.assertNotIn("defaultSpeed", put_multipart)

    def test_v2_schema_should_document_camera_action_and_live_switch_fields(self):
        schema = self._schema()

        camera_body = self._request_body_properties(
            schema,
            path="/api/v2/inspection/camera/actions",
            method="post",
            content_type="application/json",
        )
        self.assertTrue(
            {
                "droneId",
                "executorId",
                "payloadIndex",
                "action",
                "cameraMode",
                "cameraType",
                "zoomFactor",
                "locked",
                "x",
                "y",
                "resetMode",
            }.issubset(camera_body),
            sorted(camera_body),
        )
        self.assertIn("camera_photo_take", camera_body["action"]["enum"])
        self.assertIn("gimbal_reset", camera_body["action"]["enum"])

        camera_operation = schema["paths"]["/api/v2/inspection/camera/actions"]["post"]
        camera_description = camera_operation.get("description", "")
        for expected in (
            "GET /api/v2/resource/gateways",
            "payload authority",
            "payload commands",
            "camera_focal_length_set",
            "camera_aim",
            "upstream.authority",
            "upstream.command",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, camera_description)

        camera_examples = camera_operation["requestBody"]["content"]["application/json"]["examples"]
        self.assertTrue(
            {"photoTake", "switchToVideo", "recordingStart", "recordingStop", "focalLengthSetZoom", "cameraAim", "gimbalReset"}.issubset(
                camera_examples
            ),
            sorted(camera_examples),
        )
        self.assertEqual(camera_examples["focalLengthSetZoom"]["value"]["action"], "camera_focal_length_set")
        self.assertEqual(camera_examples["cameraAim"]["value"]["action"], "camera_aim")

        live_switch_body = self._request_body_properties(
            schema,
            path="/api/v2/inspection/live/switch",
            method="post",
            content_type="application/json",
        )
        self.assertIn("videoType", live_switch_body)
        self.assertNotIn("video_type", live_switch_body)

    def test_v2_docs_should_be_available(self):
        response = self.client.get("/api/v2/docs/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/api/v2/docs/schema/")
        self.assertNotContains(response, "/api/v1")


class ApiV2ImplementationBoundaryTests(TestCase):
    def test_system_v1_and_legacy_internal_sync_routes_should_be_removed(self):
        client = APIClient()
        removed_routes = [
            "/api/v1/",
            "/api/v1/docs/",
            "/api/v1/docs/schema/",
            "/api/v1/iam/session/login",
            "/api/v1/drones",
            "/api/internal/dji/sync/devices",
            "/api/internal/dji/sync/media",
        ]

        for route in removed_routes:
            with self.subTest(route=route):
                response = client.post(route, {}, format="json")
                self.assertEqual(response.status_code, 404, getattr(response, "data", response.content))

    def test_v2_mainline_should_not_import_v1_or_legacy_modules(self):
        project_root = Path(settings.BASE_DIR)
        source_roots = [
            project_root / "config",
            project_root / "apps" / "access",
            project_root / "apps" / "api_contracts",
            project_root / "apps" / "api_v2",
            project_root / "apps" / "audit_v2",
            project_root / "apps" / "iam_v2",
            project_root / "apps" / "resource_v2",
            project_root / "apps" / "inspection_v2",
            project_root / "apps" / "dji_cloud",
        ]
        forbidden_imports = (
            "apps.api_v1",
            "apps.access.api_v1",
            "apps.dji_bff",
            "apps.drone",
            "apps.drone_assignment",
            "apps.route",
            "apps.waypoint",
            "apps.mission",
            "apps.flight_record",
            "apps.media_file",
        )
        offenders = []

        for source_root in source_roots:
            for path in source_root.rglob("*.py"):
                if "migrations" in path.parts or path.name == "tests.py" or path.name.startswith("test_"):
                    continue
                text = path.read_text(encoding="utf-8")
                for forbidden in forbidden_imports:
                    if forbidden in text:
                        offenders.append(f"{path.relative_to(project_root)} imports {forbidden}")

        self.assertEqual(offenders, [])

    def test_v2_component_imports_should_follow_one_way_layers(self):
        project_root = Path(settings.BASE_DIR)
        components = {
            "access",
            "api_contracts",
            "api_v2",
            "audit_v2",
            "common",
            "dji_cloud",
            "dji_mock",
            "iam_v2",
            "inspection_v2",
            "resource_v2",
            "system_v2",
        }
        allowed = {
            "common": set(),
            "api_contracts": {"common"},
            "audit_v2": {"common"},
            "access": {"api_contracts", "audit_v2", "common"},
            "dji_cloud": {"access", "api_contracts", "audit_v2", "common"},
            "iam_v2": {"access", "api_contracts", "audit_v2", "common"},
            "resource_v2": {"access", "api_contracts", "audit_v2", "common", "dji_cloud", "iam_v2"},
            "inspection_v2": {
                "access",
                "api_contracts",
                "audit_v2",
                "common",
                "dji_cloud",
                "iam_v2",
                "resource_v2",
            },
            "system_v2": {
                "access",
                "api_contracts",
                "audit_v2",
                "common",
                "dji_cloud",
                "iam_v2",
                "inspection_v2",
                "resource_v2",
            },
            "api_v2": {
                "access",
                "api_contracts",
                "audit_v2",
                "common",
                "dji_cloud",
                "dji_mock",
                "iam_v2",
                "inspection_v2",
                "resource_v2",
                "system_v2",
            },
            "dji_mock": {"common"},
        }
        offenders = []

        def source_component(path):
            parts = path.relative_to(project_root).parts
            return parts[1] if len(parts) >= 2 and parts[0] == "apps" else None

        def target_component(module):
            if not module or not module.startswith("apps."):
                return None
            parts = module.split(".")
            return parts[1] if len(parts) > 1 and parts[1] in components else None

        for source_root in (project_root / "apps").iterdir():
            if not source_root.is_dir() or source_root.name not in components:
                continue
            for path in source_root.rglob("*.py"):
                if "migrations" in path.parts or path.name == "tests.py" or path.name.startswith("test_"):
                    continue
                source = source_component(path)
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    modules = []
                    if isinstance(node, ast.ImportFrom):
                        modules = [node.module]
                    elif isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    for module in modules:
                        target = target_component(module)
                        if target and target != source and target not in allowed[source]:
                            offenders.append(f"{path.relative_to(project_root)}:{node.lineno} imports {module}")

        self.assertEqual(offenders, [])

    def test_v2_should_not_keep_wrong_owner_compatibility_modules(self):
        project_root = Path(settings.BASE_DIR)
        wrong_owner_paths = [
            project_root / "apps" / "api_v2" / "openapi.py",
            project_root / "apps" / "resource_v2" / "audit.py",
        ]

        existing = [str(path.relative_to(project_root)) for path in wrong_owner_paths if path.exists()]

        self.assertEqual(existing, [])

    def test_system_views_should_not_own_resource_summary_dependencies(self):
        project_root = Path(settings.BASE_DIR)
        path = project_root / "apps" / "system_v2" / "views.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        forbidden_prefixes = ("apps.resource_v2", "apps.inspection_v2")
        offenders = []

        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.ImportFrom):
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            for module in modules:
                if module and module.startswith(forbidden_prefixes):
                    offenders.append(f"{path.relative_to(project_root)}:{node.lineno} imports {module}")

        self.assertEqual(offenders, [])

    def test_resource_component_should_not_own_audit_log_read_api(self):
        project_root = Path(settings.BASE_DIR)
        scanned_paths = [
            project_root / "apps" / "resource_v2" / "serializers.py",
            project_root / "apps" / "resource_v2" / "urls.py",
            project_root / "apps" / "resource_v2" / "views.py",
        ]
        forbidden_fragments = ("V2AuditLog", "AuditLogReadSerializer", "AuditLogListView")
        offenders = []

        for path in scanned_paths:
            text = path.read_text(encoding="utf-8")
            for fragment in forbidden_fragments:
                if fragment in text:
                    offenders.append(f"{path.relative_to(project_root)} contains {fragment}")

        self.assertEqual(offenders, [])

    def test_dji_upstream_gateway_should_only_be_used_behind_resource_adapter(self):
        project_root = Path(settings.BASE_DIR)
        allowed_path = project_root / "apps" / "resource_v2" / "gateway.py"
        offenders = []

        for path in (project_root / "apps").rglob("*.py"):
            if "migrations" in path.parts or path.name == "tests.py" or path.name.startswith("test_"):
                continue
            if path == allowed_path or path.parts[-2:] == ("dji_cloud", "gateway.py"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.ImportFrom):
                    modules = [node.module]
                elif isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                for module in modules:
                    if module == "apps.dji_cloud.gateway":
                        offenders.append(f"{path.relative_to(project_root)}:{node.lineno} imports {module}")

        self.assertEqual(offenders, [])

    def test_dji_connection_gateway_should_only_be_constructed_by_adapter_factory(self):
        project_root = Path(settings.BASE_DIR)
        allowed_path = project_root / "apps" / "resource_v2" / "gateway.py"
        offenders = []

        for path in (project_root / "apps").rglob("*.py"):
            if "migrations" in path.parts or path.name == "tests.py" or path.name.startswith("test_"):
                continue
            if path == allowed_path:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "DjiConnectionGateway":
                    offenders.append(f"{path.relative_to(project_root)}:{node.lineno} constructs DjiConnectionGateway")

        self.assertEqual(offenders, [])

    def test_external_implementation_imports_should_stay_at_adapter_seams(self):
        project_root = Path(settings.BASE_DIR)
        allowed_by_module = {
            "urllib.request": {"apps/dji_cloud/gateway.py"},
            "storages.backends.s3": {"apps/access/storage_backends.py"},
            "redis": {"apps/resource_v2/mqtt.py"},
            "channels.layers": {"apps/resource_v2/mqtt.py"},
            "channels.db": {"apps/resource_v2/consumers.py"},
            "channels.generic.websocket": {"apps/resource_v2/consumers.py"},
            "paho.mqtt.client": {"apps/inspection_v2/management/commands/run_v2_dji_worker.py"},
        }
        offenders = []

        for path in (project_root / "apps").rglob("*.py"):
            if "migrations" in path.parts or path.name == "tests.py" or path.name.startswith("test_"):
                continue
            relative = str(path.relative_to(project_root))
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.ImportFrom):
                    modules = [node.module]
                elif isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                for module in modules:
                    for external_module, allowed_paths in allowed_by_module.items():
                        if module and (module == external_module or module.startswith(f"{external_module}.")):
                            if relative not in allowed_paths:
                                offenders.append(f"{relative}:{node.lineno} imports {module}")

        self.assertEqual(offenders, [])

    def test_v2_views_should_reuse_shared_empty_serializer_and_standard_error_helpers(self):
        project_root = Path(settings.BASE_DIR)
        scanned_paths = [
            project_root / "apps" / "iam_v2" / "views.py",
            project_root / "apps" / "inspection_v2" / "views.py",
            project_root / "apps" / "resource_v2" / "views.py",
            project_root / "apps" / "system_v2" / "base.py",
            project_root / "apps" / "system_v2" / "views.py",
        ]
        forbidden_fragments = (
            "class EmptySchemaSerializer",
            'standard_error_payload(StandardCode.NOT_FOUND, "资源不存在"',
            'standard_error_payload(StandardCode.DUPLICATE, "资源已存在"',
        )
        offenders = []

        for path in scanned_paths:
            text = path.read_text(encoding="utf-8")
            for fragment in forbidden_fragments:
                if fragment in text:
                    offenders.append(f"{path.relative_to(project_root)} contains {fragment}")

        self.assertEqual(offenders, [])

    def test_osd_telemetry_parsers_should_have_one_owner(self):
        project_root = Path(settings.BASE_DIR)
        mqtt_path = project_root / "apps" / "resource_v2" / "mqtt.py"
        inspection_services_path = project_root / "apps" / "inspection_v2" / "services.py"
        mqtt_text = mqtt_path.read_text(encoding="utf-8")
        inspection_services_text = inspection_services_path.read_text(encoding="utf-8")

        self.assertIn("def osd_reported_at", mqtt_text)
        self.assertIn("def osd_battery_percent", mqtt_text)
        self.assertNotIn("def _osd_reported_at", inspection_services_text)
        self.assertNotIn("def _osd_battery_percent", inspection_services_text)

    def test_client_ip_resolution_should_have_one_owner(self):
        project_root = Path(settings.BASE_DIR)
        owner_path = project_root / "apps" / "common" / "request.py"
        scanned_paths = [
            project_root / "apps" / "access" / "authentication.py",
            project_root / "apps" / "access" / "request_logging.py",
            project_root / "apps" / "access" / "session_services.py",
            project_root / "apps" / "audit_v2" / "services.py",
        ]

        self.assertIn("def resolve_client_ip", owner_path.read_text(encoding="utf-8"))
        for path in scanned_paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("def _resolve_client_ip", text, str(path.relative_to(project_root)))
            self.assertNotIn("def _client_ip", text, str(path.relative_to(project_root)))

    def test_json_body_loading_should_have_one_owner(self):
        project_root = Path(settings.BASE_DIR)
        owner_path = project_root / "apps" / "common" / "request.py"
        scanned_paths = [
            project_root / "apps" / "inspection_v2" / "dji_callbacks.py",
            project_root / "apps" / "dji_mock" / "views.py",
        ]

        self.assertIn("def load_json_body", owner_path.read_text(encoding="utf-8"))
        for path in scanned_paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("def _load_json", text, str(path.relative_to(project_root)))

    def test_route_cover_image_input_normalization_should_have_one_owner(self):
        project_root = Path(settings.BASE_DIR)
        path = project_root / "apps" / "inspection_v2" / "serializers.py"
        text = path.read_text(encoding="utf-8")

        self.assertIn("class RouteCoverImageInputMixin", text)
        self.assertEqual(text.count("def to_internal_value(self, data):"), 1)

    def test_prod_python_should_not_keep_exact_duplicate_functions_or_classes(self):
        project_root = Path(settings.BASE_DIR)
        blocks_by_hash = {}

        for path in (project_root / "apps").rglob("*.py"):
            if "migrations" in path.parts or path.name == "tests.py" or path.name.startswith("test_"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                line_count = getattr(node, "end_lineno", node.lineno) - node.lineno + 1
                if line_count < 4:
                    continue
                digest = hashlib.sha1(ast.dump(node, include_attributes=False).encode("utf-8")).hexdigest()
                blocks_by_hash.setdefault(digest, []).append(
                    f"{path.relative_to(project_root)}:{node.lineno} {type(node).__name__} {node.name}"
                )

        offenders = [locations for locations in blocks_by_hash.values() if len(locations) > 1]

        self.assertEqual(offenders, [])

    def test_lod_relationship_traversal_knowledge_should_stay_at_owner_helpers(self):
        project_root = Path(settings.BASE_DIR)
        allowed_by_fragment = {
            ".account_profile.department": {"apps/iam_v2/models.py"},
            "pilot_account_profile.user_id": {"apps/inspection_v2/models.py"},
            "flight_record.mission.cloud_execution": {"apps/inspection_v2/services.py"},
            "mission.route.cloud_file": {"apps/inspection_v2/services.py"},
        }
        forbidden_fragments = (
            "context.department.id",
            "context.department.path",
            "context.user.id",
            "context.user.username",
            "pilot_account.role_assignments.filter",
            "account.user.set_password",
            "account.user.save",
            "account.role_assignments.values_list",
            "account.role_assignments.filter",
            "profile.role_profiles.select_related",
            "state.flight_record.mission_id",
            "route.cover_image.name",
            "route.cover_image.storage",
            "route.waypoints.all",
            "route.waypoints.order_by",
            "mission.resource_assignments.all",
            "record.media_files.values_list",
            "mission.drone.device_sn",
            "mission.drone.online_status",
            "mission.drone.name",
            "mission.dock.device_sn",
            "mission.dock.online_status",
            "mission.executor.device_sn",
            "mission.executor.online_status",
            "mission.route.name",
            "mission.pilot_account_profile.name",
            "account.user.username",
            'source="route.name"',
            'source="drone.device_sn"',
            'source="drone.name"',
            'source="mission.name"',
            'source="mission.route.name"',
            "binding.dji_connection.workspace_id",
            "binding.dji_connection.name",
            "binding.owner_department.name",
            "binding.owner_department.path",
            "binding.permission.code",
            "binding.permission.status",
            'source="permission.code"',
            "obj.media_sync_state.status",
            'source="media_sync_state.last_synced"',
            'source="media_sync_state.next_run_at"',
            "execution.workspace_id or execution.dji_connection.workspace_id",
        )
        max_occurrences = {
            "flight_record.mission.cloud_execution": 1,
            "mission.route.cloud_file": 1,
        }
        offenders = []
        occurrence_count = {fragment: 0 for fragment in allowed_by_fragment}

        for path in (project_root / "apps").rglob("*.py"):
            if "migrations" in path.parts or path.name == "tests.py" or path.name.startswith("test_"):
                continue
            relative = str(path.relative_to(project_root))
            text = path.read_text(encoding="utf-8")
            for fragment, allowed_paths in allowed_by_fragment.items():
                count = text.count(fragment)
                occurrence_count[fragment] += count
                if count and relative not in allowed_paths:
                    offenders.append(f"{relative} contains {fragment}")
            for fragment in forbidden_fragments:
                if fragment in text:
                    offenders.append(f"{relative} contains {fragment}")

        for fragment, max_count in max_occurrences.items():
            if occurrence_count[fragment] > max_count:
                offenders.append(f"{fragment} appears {occurrence_count[fragment]} times")

        self.assertEqual(offenders, [])

    def test_system_v1_string_references_should_be_limited_to_dji_protocol_surfaces(self):
        project_root = Path(settings.BASE_DIR)
        forbidden_fragment = "/api/" + "v1"
        scanned_roots = [
            project_root / "apps",
            project_root / "config",
            project_root / "scripts",
            project_root / "tools",
            project_root / "README.md",
        ]
        text_suffixes = {".py", ".md", ".sh", ".txt", ".yaml", ".yml", ".json"}
        offenders = []

        def is_allowed(path: Path) -> bool:
            relative = path.relative_to(project_root)
            return (
                str(relative) == "README.md"
                or str(relative) == "apps/dji_cloud/gateway.py"
                or relative.parts[:2] == ("apps", "dji_mock")
            )

        def should_skip(path: Path) -> bool:
            relative_parts = set(path.relative_to(project_root).parts)
            if relative_parts.intersection({".git", "__pycache__", ".pytest_cache", ".mypy_cache"}):
                return True
            if path.name == "tests.py" or path.name.startswith("test_"):
                return True
            return path.suffix not in text_suffixes

        files = []
        for root in scanned_roots:
            if root.is_file():
                files.append(root)
            elif root.exists():
                files.extend(path for path in root.rglob("*") if path.is_file())

        for path in files:
            if should_skip(path) or is_allowed(path):
                continue
            text = path.read_text(encoding="utf-8")
            if forbidden_fragment in text:
                offenders.append(str(path.relative_to(project_root)))

        self.assertEqual(offenders, [])

    def test_legacy_tables_should_be_removed_while_v2_and_auth_tables_remain(self):
        table_names = set(connection.introspection.table_names())
        legacy_tables = {
            "auth_audit_logs",
            "staff_profiles",
            "tenants",
            "roles",
            "permissions",
            "role_permission_grants",
            "qualification_types",
            "tenant_members",
            "tenant_member_roles",
            "tenant_member_qualifications",
            "dji_workspace_configs",
            "dji_cloud_platforms",
            "dji_device_indexes",
            "tenant_route_indexes",
            "tenant_media_indexes",
            "drones",
            "drone_assignments",
            "routes",
            "waypoints",
            "missions",
            "flight_records",
            "media_files",
        }
        required_tables = {
            "auth_users",
            "auth_sessions",
            "v2_departments",
            "v2_account_profiles",
            "v2_dji_connections",
            "v2_drone_resources",
            "v2_waypoint_routes",
            "v2_inspection_missions",
            "v2_cloud_media_files",
        }

        self.assertEqual(sorted(legacy_tables.intersection(table_names)), [])
        self.assertTrue(required_tables.issubset(table_names), sorted(required_tables - table_names))

    def test_default_local_chain_script_should_use_v2_routes_only(self):
        script_path = Path(settings.BASE_DIR) / "scripts" / "local_chain_client.sh"
        text = script_path.read_text(encoding="utf-8")

        self.assertIn('USERNAME="${USERNAME:-jnu_super}"', text)
        self.assertNotIn("v2_test_platform_super_admin", text)
        self.assertIn("/api/v2/iam/session/login", text)
        self.assertNotIn("/api/v1", text)

    def test_regression_server_script_should_seed_jnu_frontend_accounts(self):
        script_path = Path(settings.BASE_DIR) / "scripts" / "start_regression_server.sh"
        text = script_path.read_text(encoding="utf-8")
        command_lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]

        self.assertIn("bootstrap_v2_system --frontend-test-accounts --frontend-prefix jnu", text)
        self.assertLess(
            command_lines.index("python manage.py migrate"),
            command_lines.index(
                'python manage.py bootstrap_v2_system --frontend-test-accounts --frontend-prefix jnu --frontend-password "$FRONTEND_TEST_ACCOUNT_PASSWORD"'
            ),
        )
        self.assertNotIn("bootstrap_frontend_test_tenant", text)
        self.assertNotIn("fe_frontend_lab", text)


class ApiV2SessionTests(TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.root = Department.objects.create(name="总部")
        self.user, _profile = create_v2_actor(
            username="v2_session_user",
            role_code=FixedRole.PLATFORM_SUPER_ADMIN,
            department=self.root,
        )

    def test_v2_session_login_refresh_logout_should_not_use_v1_route(self):
        login_response = self.client.post(
            "/api/v2/iam/session/login",
            {"username": "v2_session_user", "password": "pass1234"},
            format="json",
        )

        self.assertEqual(login_response.status_code, 200, getattr(login_response, "data", login_response.content))
        login_data = login_response.data["data"]
        self.assertEqual(login_data["tokenType"], "Bearer")
        self.assertEqual(login_data["user"]["username"], "v2_session_user")
        self.assertNotIn("staffProfile", login_data["user"])
        self.assertEqual(login_data["user"]["accountProfileId"], self.user.v2_account_profile.id)
        self.assertEqual(login_data["user"]["name"], "v2_session_user")
        self.assertEqual(login_data["user"]["department"]["id"], self.root.id)
        self.assertEqual(login_data["user"]["roleCodes"], [FixedRole.PLATFORM_SUPER_ADMIN])
        access_token = login_data["accessToken"]
        refresh_token = login_data["refreshToken"]

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        context_response = self.client.get("/api/v2/iam/me/context")
        self.assertEqual(context_response.status_code, 200, getattr(context_response, "data", context_response.content))
        self.assertEqual(context_response.data["data"]["roles"], [FixedRole.PLATFORM_SUPER_ADMIN])

        refresh_response = self.client.post(
            "/api/v2/iam/session/refresh",
            {"refreshToken": refresh_token},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, 200, getattr(refresh_response, "data", refresh_response.content))
        refresh_data = refresh_response.data["data"]
        self.assertNotEqual(refresh_data["accessToken"], access_token)
        self.assertNotEqual(refresh_data["refreshToken"], refresh_token)

        old_refresh_response = self.client.post(
            "/api/v2/iam/session/refresh",
            {"refreshToken": refresh_token},
            format="json",
        )
        self.assertEqual(old_refresh_response.status_code, 401)
        self.assertEqual(old_refresh_response.data["code"], "A0401")

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_data['accessToken']}")
        logout_response = self.client.post("/api/v2/iam/session/logout", {}, format="json")
        self.assertEqual(logout_response.status_code, 200, getattr(logout_response, "data", logout_response.content))
        self.assertIsNone(logout_response.data["data"])

        revoked_context_response = self.client.get("/api/v2/iam/me/context")
        self.assertEqual(revoked_context_response.status_code, 401)
        self.assertEqual(revoked_context_response.data["code"], "A0401")

    def test_v2_session_login_should_ignore_stale_bearer_header(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer stale-access-token")

        login_response = self.client.post(
            "/api/v2/iam/session/login",
            {"username": "v2_session_user", "password": "pass1234"},
            format="json",
        )

        self.assertEqual(login_response.status_code, 200, getattr(login_response, "data", login_response.content))
        self.assertEqual(login_response.data["data"]["user"]["username"], "v2_session_user")

    def test_v2_session_refresh_should_ignore_stale_bearer_header(self):
        login_response = self.client.post(
            "/api/v2/iam/session/login",
            {"username": "v2_session_user", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(login_response.status_code, 200, getattr(login_response, "data", login_response.content))

        self.client.credentials(HTTP_AUTHORIZATION="Bearer stale-access-token")
        refresh_response = self.client.post(
            "/api/v2/iam/session/refresh",
            {"refreshToken": login_response.data["data"]["refreshToken"]},
            format="json",
        )

        self.assertEqual(refresh_response.status_code, 200, getattr(refresh_response, "data", refresh_response.content))
        self.assertNotEqual(refresh_response.data["data"]["accessToken"], login_response.data["data"]["accessToken"])


class IamV2ApiTests(TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.root = Department.objects.create(name="总部")
        self.super_user, self.super_profile = create_v2_actor(
            username="v2_super",
            role_code=FixedRole.PLATFORM_SUPER_ADMIN,
            department=self.root,
        )
        self.client.force_authenticate(self.super_user)

    def test_me_context_should_return_department_and_fixed_roles_without_tenant_header(self):
        response = self.client.get("/api/v2/iam/me/context")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        data = response.data["data"]
        self.assertEqual(data["user"]["username"], "v2_super")
        self.assertEqual(data["department"]["id"], self.root.id)
        self.assertEqual(data["department"]["path"], f"/{self.root.id}/")
        self.assertNotIn("tenantId", data["department"])
        self.assertEqual(data["roles"], [FixedRole.PLATFORM_SUPER_ADMIN])

    def test_me_context_should_allow_active_v2_profile_without_roles(self):
        no_role_user, _profile = create_v2_actor(
            username="v2_no_role",
            role_code=None,
            department=self.root,
        )
        self.client.force_authenticate(no_role_user)

        response = self.client.get("/api/v2/iam/me/context")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        data = response.data["data"]
        self.assertEqual(data["user"]["username"], "v2_no_role")
        self.assertEqual(data["department"]["id"], self.root.id)
        self.assertEqual(data["roles"], [])

    def test_platform_super_admin_should_create_child_department_and_reject_move(self):
        create_response = self.client.post(
            "/api/v2/iam/departments",
            {"name": "飞行一部", "parentId": self.root.id},
            format="json",
        )

        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        child_id = create_response.data["data"]["id"]
        child = Department.objects.get(pk=child_id)
        self.assertEqual(child.path, f"/{self.root.id}/{child.id}/")
        self.assertEqual(child.depth, 1)
        self.assertNotIn("tenantId", create_response.data["data"])

        other_parent = Department.objects.create(name="备用部门", parent=self.root)
        move_response = self.client.put(
            f"/api/v2/iam/departments/{child.id}",
            {"name": "飞行一部", "parentId": other_parent.id},
            format="json",
        )

        self.assertEqual(move_response.status_code, 400, getattr(move_response, "data", move_response.content))
        child.refresh_from_db()
        self.assertEqual(child.parent_id, self.root.id)

    def test_department_api_should_not_expose_or_accept_tenant_concept(self):
        list_response = self.client.get("/api/v2/iam/departments")

        self.assertEqual(list_response.status_code, 200, getattr(list_response, "data", list_response.content))
        self.assertGreaterEqual(list_response.data["data"]["total"], 1)
        self.assertNotIn("tenantId", list_response.data["data"]["list"][0])

        missing_parent_response = self.client.post(
            "/api/v2/iam/departments",
            {"name": "缺少父部门"},
            format="json",
        )
        self.assertEqual(
            missing_parent_response.status_code,
            400,
            getattr(missing_parent_response, "data", missing_parent_response.content),
        )
        self.assertNotIn("tenant", str(missing_parent_response.data).lower())
        self.assertNotIn("租户", str(missing_parent_response.data))

        legacy_tenant_response = self.client.post(
            "/api/v2/iam/departments",
            {"name": "旧字段部门", "parentId": self.root.id, "tenantId": 1},
            format="json",
        )
        self.assertEqual(
            legacy_tenant_response.status_code,
            400,
            getattr(legacy_tenant_response, "data", legacy_tenant_response.content),
        )
        self.assertNotIn("tenant", str(legacy_tenant_response.data).lower())
        self.assertNotIn("租户", str(legacy_tenant_response.data))

    def test_platform_super_admin_should_rename_enable_and_disable_departments(self):
        child = Department.objects.create(name="待调整部门", parent=self.root)

        rename_response = self.client.put(
            f"/api/v2/iam/departments/{child.id}",
            {"name": "飞行二部", "parentId": self.root.id},
            format="json",
        )

        self.assertEqual(rename_response.status_code, 200, getattr(rename_response, "data", rename_response.content))
        child.refresh_from_db()
        self.assertEqual(child.name, "飞行二部")

        disable_response = self.client.post(f"/api/v2/iam/departments/{child.id}/disable", {}, format="json")
        self.assertEqual(disable_response.status_code, 200, getattr(disable_response, "data", disable_response.content))
        child.refresh_from_db()
        self.assertEqual(child.status, 0)

        enable_response = self.client.post(f"/api/v2/iam/departments/{child.id}/enable", {}, format="json")
        self.assertEqual(enable_response.status_code, 200, getattr(enable_response, "data", enable_response.content))
        child.refresh_from_db()
        self.assertEqual(child.status, 1)

    def test_platform_super_admin_department_management_should_write_audit_logs(self):
        create_response = self.client.post(
            "/api/v2/iam/departments",
            {"name": "审计部门", "parentId": self.root.id},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        department_id = create_response.data["data"]["id"]

        create_log = V2AuditLog.objects.get(
            action="create_department",
            target_type="v2_department",
            target_id=str(department_id),
        )
        self.assertEqual(create_log.actor_department_id, self.root.id)
        self.assertEqual(create_log.resource_owner_department_id, department_id)
        self.assertIsNone(create_log.before_data)
        self.assertEqual(create_log.after_data["name"], "审计部门")

        rename_response = self.client.put(
            f"/api/v2/iam/departments/{department_id}",
            {"name": "审计部门改名", "parentId": self.root.id},
            format="json",
        )
        self.assertEqual(rename_response.status_code, 200, getattr(rename_response, "data", rename_response.content))
        update_log = V2AuditLog.objects.get(
            action="update_department",
            target_type="v2_department",
            target_id=str(department_id),
        )
        self.assertEqual(update_log.actor_department_id, self.root.id)
        self.assertEqual(update_log.resource_owner_department_id, department_id)
        self.assertEqual(update_log.before_data["name"], "审计部门")
        self.assertEqual(update_log.after_data["name"], "审计部门改名")

        disable_response = self.client.post(f"/api/v2/iam/departments/{department_id}/disable", {}, format="json")
        self.assertEqual(disable_response.status_code, 200, getattr(disable_response, "data", disable_response.content))
        disable_log = V2AuditLog.objects.get(
            action="disable_department",
            target_type="v2_department",
            target_id=str(department_id),
        )
        self.assertEqual(disable_log.before_data["status"], DirectoryStatus.ACTIVE)
        self.assertEqual(disable_log.after_data["status"], DirectoryStatus.DISABLED)

        enable_response = self.client.post(f"/api/v2/iam/departments/{department_id}/enable", {}, format="json")
        self.assertEqual(enable_response.status_code, 200, getattr(enable_response, "data", enable_response.content))
        enable_log = V2AuditLog.objects.get(
            action="enable_department",
            target_type="v2_department",
            target_id=str(department_id),
        )
        self.assertEqual(enable_log.before_data["status"], DirectoryStatus.DISABLED)
        self.assertEqual(enable_log.after_data["status"], DirectoryStatus.ACTIVE)

    def test_audited_action_should_truncate_long_client_request_id(self):
        request_id = "r" * 100

        create_response = self.client.post(
            "/api/v2/iam/departments",
            {"name": "长请求 ID 审计部门", "parentId": self.root.id},
            format="json",
            HTTP_X_REQUEST_ID=request_id,
        )

        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        audit_log = V2AuditLog.objects.get(action="create_department", target_type="v2_department")
        self.assertEqual(audit_log.request_id, request_id[:64])

    def test_rejected_department_management_should_not_write_audit_logs(self):
        child = Department.objects.create(name="待拒绝部门", parent=self.root)
        other_parent = Department.objects.create(name="备用父部门", parent=self.root)

        move_response = self.client.put(
            f"/api/v2/iam/departments/{child.id}",
            {"name": "非法移动", "parentId": other_parent.id},
            format="json",
        )
        self.assertEqual(move_response.status_code, 400, getattr(move_response, "data", move_response.content))
        self.assertFalse(
            V2AuditLog.objects.filter(
                action="update_department",
                target_type="v2_department",
                target_id=str(child.id),
            ).exists()
        )

        department_admin, _profile = create_v2_actor(
            username="department_audit_denied_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=self.root,
        )
        self.client.force_authenticate(department_admin)

        denied_create_response = self.client.post(
            "/api/v2/iam/departments",
            {"name": "越权部门", "parentId": self.root.id},
            format="json",
        )
        denied_update_response = self.client.put(
            f"/api/v2/iam/departments/{child.id}",
            {"name": "越权改名", "parentId": self.root.id},
            format="json",
        )

        self.assertEqual(denied_create_response.status_code, 403, getattr(denied_create_response, "data", denied_create_response.content))
        self.assertEqual(denied_update_response.status_code, 403, getattr(denied_update_response, "data", denied_update_response.content))
        self.assertFalse(V2AuditLog.objects.filter(target_type="v2_department").exists())

    def test_department_admin_should_not_create_or_manage_child_departments(self):
        department_admin, _profile = create_v2_actor(
            username="department_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=self.root,
        )
        self.client.force_authenticate(department_admin)

        create_response = self.client.post(
            "/api/v2/iam/departments",
            {"name": "越权子部门", "parentId": self.root.id},
            format="json",
        )

        self.assertEqual(create_response.status_code, 403, getattr(create_response, "data", create_response.content))

    def test_legacy_platform_admin_flag_should_not_grant_v2_super_admin(self):
        legacy_user = User.objects.create_user(
            username="legacy_platform_admin",
            password="pass1234",
            status=1,
            is_platform_admin=True,
        )
        self.client.force_authenticate(legacy_user)

        response = self.client.post(
            "/api/v2/iam/departments",
            {"name": "非法部门", "parentId": self.root.id},
            format="json",
        )

        self.assertEqual(response.status_code, 403, getattr(response, "data", response.content))

    def test_roles_should_return_global_v2_role_catalog_for_super_admin(self):
        response = self.client.get("/api/v2/iam/roles")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        returned = [item["code"] for item in response.data["data"]["list"]]
        self.assertEqual(returned, ROLE_CODE_ORDER)
        self.assertIn(FixedRole.PLATFORM_SUPER_ADMIN, returned)

    def test_account_roles_replace_should_preserve_existing_platform_identity(self):
        response = self.client.put(
            f"/api/v2/iam/accounts/{self.super_profile.id}/roles",
            {"roleCodes": [FixedRole.DEPARTMENT_ADMIN]},
            format="json",
        )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(response.data["data"]["roleCodes"], [FixedRole.PLATFORM_SUPER_ADMIN, FixedRole.DEPARTMENT_ADMIN])

        context_response = self.client.get("/api/v2/iam/me/context")
        self.assertEqual(context_response.status_code, 200, getattr(context_response, "data", context_response.content))
        self.assertEqual(context_response.data["data"]["roles"], [FixedRole.PLATFORM_SUPER_ADMIN, FixedRole.DEPARTMENT_ADMIN])

    def test_platform_super_admin_should_manage_v2_accounts_and_audit_changes(self):
        child = Department.objects.create(name="飞行队", parent=self.root)
        other = Department.objects.create(name="保障队", parent=self.root)

        create_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "v2_pilot_account",
                "password": "pass1234",
                "name": "v2 飞手账号",
                "phone": "13800002001",
                "email": "v2_pilot_account@example.test",
                "departmentId": child.id,
                "roleCodes": [FixedRole.PILOT, FixedRole.DEPARTMENT_ADMIN],
                "status": DirectoryStatus.ACTIVE,
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        created = create_response.data["data"]
        account_id = created["id"]
        user_id = created["userId"]
        self.assertEqual(created["username"], "v2_pilot_account")
        self.assertEqual(created["name"], "v2 飞手账号")
        self.assertEqual(created["phone"], "13800002001")
        self.assertEqual(created["email"], "v2_pilot_account@example.test")
        self.assertEqual(created["department"]["id"], child.id)
        self.assertNotIn("tenantId", created["department"])
        self.assertEqual(created["roleCodes"], [FixedRole.DEPARTMENT_ADMIN, FixedRole.PILOT])
        user = User.objects.get(pk=user_id)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_platform_admin)
        self.assertTrue(user.check_password("pass1234"))

        list_response = self.client.get(
            "/api/v2/iam/accounts",
            {"departmentId": child.id, "status": DirectoryStatus.ACTIVE, "keywords": "pilot"},
        )
        self.assertEqual(list_response.status_code, 200, getattr(list_response, "data", list_response.content))
        self.assertEqual(list_response.data["data"]["total"], 1)
        self.assertEqual(list_response.data["data"]["list"][0]["id"], account_id)

        update_response = self.client.put(
            f"/api/v2/iam/accounts/{account_id}",
            {
                "username": "v2_pilot_renamed",
                "password": "changed123",
                "name": "v2 飞手改名",
                "phone": "13800002002",
                "email": "",
                "departmentId": other.id,
                "status": DirectoryStatus.DISABLED,
            },
            format="json",
        )

        self.assertEqual(update_response.status_code, 200, getattr(update_response, "data", update_response.content))
        updated = update_response.data["data"]
        self.assertEqual(updated["username"], "v2_pilot_renamed")
        self.assertEqual(updated["name"], "v2 飞手改名")
        self.assertEqual(updated["phone"], "13800002002")
        self.assertEqual(updated["email"], "")
        self.assertEqual(updated["department"]["id"], other.id)
        self.assertEqual(updated["status"], DirectoryStatus.DISABLED)
        user.refresh_from_db()
        profile = V2AccountProfile.objects.get(pk=account_id)
        self.assertEqual(user.status, UserStatus.DISABLED)
        self.assertFalse(user.is_active)
        self.assertTrue(user.check_password("changed123"))
        self.assertEqual(profile.department_id, other.id)
        self.assertEqual(profile.status, DirectoryStatus.DISABLED)

        self.client.force_authenticate(user)
        denied_context_response = self.client.get("/api/v2/iam/me/context")
        self.assertEqual(
            denied_context_response.status_code,
            401,
            getattr(denied_context_response, "data", denied_context_response.content),
        )

        self.client.force_authenticate(self.super_user)
        reactivate_response = self.client.put(
            f"/api/v2/iam/accounts/{account_id}",
            {
                "username": "v2_pilot_renamed",
                "name": "v2 飞手改名",
                "phone": "13800002002",
                "email": "",
                "departmentId": other.id,
                "status": DirectoryStatus.ACTIVE,
            },
            format="json",
        )
        self.assertEqual(reactivate_response.status_code, 200, getattr(reactivate_response, "data", reactivate_response.content))

        denied_platform_role_response = self.client.put(
            f"/api/v2/iam/accounts/{account_id}/roles",
            {"roleCodes": [FixedRole.TASK_MONITOR_DISPATCHER, FixedRole.PLATFORM_SUPER_ADMIN]},
            format="json",
        )

        self.assertEqual(
            denied_platform_role_response.status_code,
            400,
            getattr(denied_platform_role_response, "data", denied_platform_role_response.content),
        )

        roles_response = self.client.put(
            f"/api/v2/iam/accounts/{account_id}/roles",
            {"roleCodes": [FixedRole.TASK_MONITOR_DISPATCHER]},
            format="json",
        )

        self.assertEqual(roles_response.status_code, 200, getattr(roles_response, "data", roles_response.content))
        self.assertEqual(roles_response.data["data"]["roleCodes"], [FixedRole.TASK_MONITOR_DISPATCHER])

        logged_actions = set(V2AuditLog.objects.filter(target_type="v2_account", target_id=str(account_id)).values_list("action", flat=True))
        self.assertTrue(
            {
                "create_account",
                "update_account",
                "change_account_department",
                "change_account_roles",
            }.issubset(logged_actions)
        )
        audit_payloads = V2AuditLog.objects.filter(target_type="v2_account", target_id=str(account_id)).values("before_data", "after_data")
        for payload in audit_payloads:
            self.assertNotIn("password", str(payload).lower())

    def test_department_admin_should_manage_only_own_department_business_accounts(self):
        child = Department.objects.create(name="飞行队", parent=self.root)
        other = Department.objects.create(name="保障队", parent=self.root)
        department_admin, _profile = create_v2_actor(
            username="child_department_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=child,
        )
        other_admin, _other_profile = create_v2_actor(
            username="other_department_admin",
            role_code=FixedRole.DEPARTMENT_ADMIN,
            department=other,
        )
        dispatcher, _dispatcher_profile = create_v2_actor(
            username="plain_dispatcher",
            role_code=FixedRole.TASK_MONITOR_DISPATCHER,
            department=child,
        )
        self.client.force_authenticate(department_admin)

        create_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "child_business_account",
                "password": "pass1234",
                "name": "子部门业务账号",
                "phone": "13800003001",
                "email": "",
                "departmentId": child.id,
                "roleCodes": [FixedRole.PILOT, FixedRole.TASK_MONITOR_DISPATCHER],
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        account_id = create_response.data["data"]["id"]
        self.assertEqual(create_response.data["data"]["roleCodes"], [FixedRole.TASK_MONITOR_DISPATCHER, FixedRole.PILOT])

        denied_cross_department_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "cross_department_account",
                "password": "pass1234",
                "name": "跨部门账号",
                "phone": "13800003002",
                "email": "",
                "departmentId": other.id,
                "roleCodes": [FixedRole.PILOT],
            },
            format="json",
        )
        self.assertEqual(
            denied_cross_department_response.status_code,
            403,
            getattr(denied_cross_department_response, "data", denied_cross_department_response.content),
        )

        denied_system_role_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "illegal_system_role_account",
                "password": "pass1234",
                "name": "非法系统角色账号",
                "phone": "13800003003",
                "email": "",
                "departmentId": child.id,
                "roleCodes": [FixedRole.DEPARTMENT_ADMIN],
            },
            format="json",
        )
        self.assertEqual(
            denied_system_role_response.status_code,
            400,
            getattr(denied_system_role_response, "data", denied_system_role_response.content),
        )

        denied_platform_role_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "illegal_platform_role_account",
                "password": "pass1234",
                "name": "非法平台角色账号",
                "phone": "13800003004",
                "email": "",
                "departmentId": child.id,
                "roleCodes": [FixedRole.PLATFORM_SUPER_ADMIN],
            },
            format="json",
        )
        self.assertEqual(
            denied_platform_role_response.status_code,
            400,
            getattr(denied_platform_role_response, "data", denied_platform_role_response.content),
        )

        V2AccountRoleAssignment.objects.create(
            account_profile_id=account_id,
            role_code=FixedRole.DEPARTMENT_ADMIN,
            assigned_by_user=self.super_user,
        )
        denied_protected_role_replace_response = self.client.put(
            f"/api/v2/iam/accounts/{account_id}/roles",
            {"roleCodes": [FixedRole.DEPARTMENT_ADMIN]},
            format="json",
        )
        self.assertEqual(
            denied_protected_role_replace_response.status_code,
            400,
            getattr(denied_protected_role_replace_response, "data", denied_protected_role_replace_response.content),
        )

        denied_platform_role_replace_response = self.client.put(
            f"/api/v2/iam/accounts/{account_id}/roles",
            {"roleCodes": [FixedRole.PLATFORM_SUPER_ADMIN]},
            format="json",
        )
        self.assertEqual(
            denied_platform_role_replace_response.status_code,
            400,
            getattr(denied_platform_role_replace_response, "data", denied_platform_role_replace_response.content),
        )

        replace_roles_response = self.client.put(
            f"/api/v2/iam/accounts/{account_id}/roles",
            {"roleCodes": [FixedRole.WORK_ORDER_HANDLER]},
            format="json",
        )

        self.assertEqual(replace_roles_response.status_code, 200, getattr(replace_roles_response, "data", replace_roles_response.content))
        self.assertEqual(replace_roles_response.data["data"]["roleCodes"], [FixedRole.DEPARTMENT_ADMIN, FixedRole.WORK_ORDER_HANDLER])

        other_user = User.objects.create_user(username="other_department_account", password="pass1234", status=1)
        other_profile = V2AccountProfile.objects.create(
            user=other_user,
            department=other,
            name="other_department_account",
            phone=f"138{other_user.id:08d}",
            email="other_department_account@example.test",
        )
        V2AccountRoleAssignment.objects.create(account_profile=other_profile, role_code=FixedRole.PILOT, assigned_by_user=other_admin)
        denied_other_account_response = self.client.put(
            f"/api/v2/iam/accounts/{other_profile.id}/roles",
            {"roleCodes": [FixedRole.WORK_ORDER_HANDLER]},
            format="json",
        )
        self.assertEqual(
            denied_other_account_response.status_code,
            403,
            getattr(denied_other_account_response, "data", denied_other_account_response.content),
        )

        child_candidate_user, child_candidate_profile = create_v2_actor(
            username="child_candidate_pilot",
            role_code=FixedRole.PILOT,
            department=child,
        )
        other_candidate_user, other_candidate_profile = create_v2_actor(
            username="other_candidate_pilot",
            role_code=FixedRole.PILOT,
            department=other,
        )
        issued_at = timezone.now().date()
        expires_at = issued_at.replace(year=issued_at.year + 1)
        for account_profile, certificate_no in (
            (child_candidate_profile, "CANDIDATE-CHILD"),
            (other_candidate_profile, "CANDIDATE-OTHER"),
        ):
            V2AccountRoleProfile.objects.create(
                account_profile=account_profile,
                profile_type=FixedRole.PILOT,
                display_name=account_profile.name,
                status=DirectoryStatus.ACTIVE,
            )
            V2AccountQualification.objects.create(
                account_profile=account_profile,
                profile_type=FixedRole.PILOT,
                qualification_type="多旋翼巡检",
                certificate_no=certificate_no,
                issued_at=issued_at,
                expires_at=expires_at,
                status=DirectoryStatus.ACTIVE,
            )
        del child_candidate_user, other_candidate_user

        self.client.force_authenticate(dispatcher)
        list_response = self.client.get("/api/v2/iam/accounts")
        self.assertEqual(list_response.status_code, 200, getattr(list_response, "data", list_response.content))
        listed_ids = {item["id"] for item in list_response.data["data"]["list"]}
        self.assertIn(child_candidate_profile.id, listed_ids)
        self.assertNotIn(other_candidate_profile.id, listed_ids)

        candidate_response = self.client.get(
            "/api/v2/iam/accounts",
            {"roleCode": FixedRole.PILOT, "profileType": FixedRole.PILOT, "qualified": "true"},
        )
        self.assertEqual(candidate_response.status_code, 200, getattr(candidate_response, "data", candidate_response.content))
        candidate_ids = {item["id"] for item in candidate_response.data["data"]["list"]}
        self.assertEqual(candidate_ids, {child_candidate_profile.id})

    def test_account_api_should_validate_duplicates_roles_departments_and_missing_accounts(self):
        child = Department.objects.create(name="飞行队", parent=self.root)
        User.objects.create_user(username="duplicate_v2_account", password="pass1234", status=1)

        duplicate_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "duplicate_v2_account",
                "password": "pass1234",
                "name": "重复用户名账号",
                "phone": "13800004001",
                "email": "",
                "departmentId": child.id,
                "roleCodes": [FixedRole.PILOT],
            },
            format="json",
        )
        self.assertEqual(duplicate_response.status_code, 409, getattr(duplicate_response, "data", duplicate_response.content))

        invalid_role_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "invalid_role_account",
                "password": "pass1234",
                "name": "非法角色账号",
                "phone": "13800004002",
                "email": "",
                "departmentId": child.id,
                "roleCodes": ["unknown_role"],
            },
            format="json",
        )
        self.assertEqual(invalid_role_response.status_code, 400, getattr(invalid_role_response, "data", invalid_role_response.content))

        platform_identity_role_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "platform_identity_role_account",
                "password": "pass1234",
                "name": "平台身份角色账号",
                "phone": "13800004003",
                "email": "",
                "departmentId": child.id,
                "roleCodes": [FixedRole.PLATFORM_SUPER_ADMIN],
            },
            format="json",
        )
        self.assertEqual(
            platform_identity_role_response.status_code,
            400,
            getattr(platform_identity_role_response, "data", platform_identity_role_response.content),
        )

        invalid_department_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "invalid_department_account",
                "password": "pass1234",
                "name": "非法部门账号",
                "phone": "13800004004",
                "email": "",
                "departmentId": 999999,
                "roleCodes": [FixedRole.PILOT],
            },
            format="json",
        )
        self.assertEqual(
            invalid_department_response.status_code,
            400,
            getattr(invalid_department_response, "data", invalid_department_response.content),
        )

        missing_account_update_response = self.client.put(
            "/api/v2/iam/accounts/999999",
            {
                "username": "missing",
                "name": "缺失账号",
                "phone": "13800004005",
                "email": "",
                "departmentId": child.id,
                "status": DirectoryStatus.ACTIVE,
            },
            format="json",
        )
        self.assertEqual(
            missing_account_update_response.status_code,
            404,
            getattr(missing_account_update_response, "data", missing_account_update_response.content),
        )

        missing_account_roles_response = self.client.put(
            "/api/v2/iam/accounts/999999/roles",
            {"roleCodes": [FixedRole.PILOT]},
            format="json",
        )
        self.assertEqual(
            missing_account_roles_response.status_code,
            404,
            getattr(missing_account_roles_response, "data", missing_account_roles_response.content),
        )
