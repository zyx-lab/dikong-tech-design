import importlib.util
import json
import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.test import APIClient


class ApiV2DocsSyncTests(TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def _schema(self):
        response = self.client.get("/api/v2/docs/schema/")
        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        return response.json()

    def _schema_ref(self, schema, schema_value):
        while "$ref" in schema_value:
            schema_value = schema["components"]["schemas"][schema_value["$ref"].split("/")[-1]]
        return schema_value

    def _schema_data(self, schema, *, path: str, method: str):
        operation = schema["paths"][path][method.lower()]
        status_code = next(code for code in operation["responses"] if str(code).startswith("2"))
        response_schema = operation["responses"][status_code]["content"]["application/json"]["schema"]
        response_schema = self._schema_ref(schema, response_schema)
        return response_schema["properties"]["data"]

    def _list_item_properties(self, schema, *, path: str):
        data_schema = self._schema_data(schema, path=path, method="get")
        item_schema = data_schema["properties"]["list"]["items"]
        item_schema = self._schema_ref(schema, item_schema)
        return item_schema.get("properties", {})

    def _request_body_properties(self, schema, *, path: str, method: str, content_type: str):
        request_schema = schema["paths"][path][method.lower()]["requestBody"]["content"][content_type]["schema"]
        request_schema = self._schema_ref(schema, request_schema)
        return request_schema.get("properties", {})

    def _assert_properties_include(self, properties, expected):
        self.assertTrue(set(expected).issubset(properties), sorted(set(expected) - set(properties)))

    def _actual_v2_routes(self):
        def route_to_openapi(route):
            value = str(route).replace("^", "").replace("\\Z", "").replace("$", "")
            return re.sub(r"<(?:(\w+):)?(\w+)>", r"{\2}", value)

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

    def test_v2_schema_should_match_local_urlconf_and_docs_metadata(self):
        schema = self._schema()

        self.assertEqual(schema["info"]["title"], "低空平台 API v2")
        self.assertEqual(schema["info"]["version"], "2.0.0")
        self.assertTrue(all(path.startswith("/api/v2/") for path in schema["paths"]), sorted(schema["paths"]))
        documented = {
            path: {
                method.upper()
                for method in path_item
                if method.lower() in {"get", "post", "put", "patch", "delete"}
            }
            for path, path_item in schema["paths"].items()
        }
        self.assertEqual(documented, self._actual_v2_routes())

        docs_response = self.client.get("/api/v2/docs/")
        self.assertEqual(docs_response.status_code, 200)
        self.assertContains(docs_response, "/api/v2/docs/schema/")

    def test_v2_schema_should_document_representative_domain_fields(self):
        schema = self._schema()

        self._assert_properties_include(
            self._list_item_properties(schema, path="/api/v2/iam/accounts"),
            {"id", "username", "name", "phone", "email", "department", "roleCodes", "status"},
        )
        self._assert_properties_include(
            self._request_body_properties(
                schema,
                path="/api/v2/iam/accounts",
                method="post",
                content_type="application/json",
            ),
            {"username", "password", "name", "phone", "email", "departmentId", "roleCodes"},
        )
        me_profile_properties = self._schema_data(schema, path="/api/v2/iam/me/profile", method="get").get("properties", {})
        self._assert_properties_include(
            me_profile_properties,
            {"userId", "username", "accountProfileId", "name", "phone", "email", "roleCodes", "profiles", "qualifications"},
        )
        self._assert_properties_include(
            self._list_item_properties(schema, path="/api/v2/iam/accounts/{id}/profiles"),
            {"id", "accountProfileId", "profileType", "displayName", "level", "status"},
        )
        self._assert_properties_include(
            self._request_body_properties(
                schema,
                path="/api/v2/iam/accounts/{id}/profiles",
                method="post",
                content_type="application/json",
            ),
            {"profileType", "displayName", "level", "status", "remark"},
        )
        self._assert_properties_include(
            self._list_item_properties(schema, path="/api/v2/iam/accounts/{id}/qualifications"),
            {"id", "accountProfileId", "profileType", "qualificationType", "certificateNo", "isEffective"},
        )
        self._assert_properties_include(
            self._request_body_properties(
                schema,
                path="/api/v2/iam/accounts/{id}/qualifications",
                method="post",
                content_type="application/json",
            ),
            {"profileType", "qualificationType", "certificateNo", "issuedAt", "expiresAt", "status", "remark"},
        )
        self._assert_properties_include(
            self._list_item_properties(schema, path="/api/v2/resource/dji-connections"),
            {"id", "name", "baseUrl", "workspaceId", "status"},
        )
        self._assert_properties_include(
            self._request_body_properties(
                schema,
                path="/api/v2/resource/dji-connections",
                method="post",
                content_type="application/json",
            ),
            {"name", "baseUrl", "username", "password", "ownerDepartmentId"},
        )
        self._assert_properties_include(
            self._list_item_properties(schema, path="/api/v2/resource/drones"),
            {"id", "deviceSn", "name", "onlineStatus", "lastSeenAt", "latestTelemetry", "bindingId", "effectivePermissions"},
        )
        self._assert_properties_include(
            self._list_item_properties(schema, path="/api/v2/resource/dji-connections/mqtt-health"),
            {"connectionId", "status", "mqttAddr", "lastMessageAt", "messageCount"},
        )
        self._assert_properties_include(
            self._list_item_properties(schema, path="/api/v2/resource/dji-connections/{id}/mqtt-messages/latest"),
            {"connectionId", "topic", "topicKind", "deviceSn", "receivedAt", "sequence", "rawPayload"},
        )
        self._assert_properties_include(
            self._request_body_properties(
                schema,
                path="/api/v2/iam/profile-types",
                method="post",
                content_type="application/json",
            ),
            {"code", "name", "status", "sort", "remark"},
        )
        self._assert_properties_include(
            self._list_item_properties(schema, path="/api/v2/inspection/routes"),
            {"id", "name", "coverImageUrl", "waypoints", "djiFile"},
        )
        route_properties = self._list_item_properties(schema, path="/api/v2/inspection/routes")
        dji_file_schema = self._schema_ref(schema, route_properties["djiFile"])
        self._assert_properties_include(dji_file_schema.get("properties", {}), {"downloadUrl", "downloadUrlExpiresAt"})
        route_multipart = self._request_body_properties(
            schema,
            path="/api/v2/inspection/routes",
            method="post",
            content_type="multipart/form-data",
        )
        self.assertEqual(route_multipart["coverImage"]["type"], "string")
        self.assertEqual(route_multipart["coverImage"]["format"], "binary")
        self.assertEqual(route_multipart["waypoints"]["type"], "string")
        self.assertEqual(route_multipart["kmzFile"]["format"], "binary")
        self._assert_properties_include(route_multipart, {"djiConnectionId", "waylineType", "kmzFile"})
        self._assert_properties_include(
            self._list_item_properties(schema, path="/api/v2/inspection/missions"),
            {"id", "routeSnapshot", "droneId", "pilot", "status"},
        )
        self._assert_properties_include(
            self._request_body_properties(
                schema,
                path="/api/v2/inspection/camera/actions",
                method="post",
                content_type="application/json",
            ),
            {"droneId", "executorId", "payloadIndex", "action", "cameraType", "zoomFactor", "cameraMode", "resetMode"},
        )
        self._assert_properties_include(
            self._request_body_properties(
                schema,
                path="/api/v2/inspection/live/switch",
                method="post",
                content_type="application/json",
            ),
            {"videoId", "videoType"},
        )

    def test_v2_schema_should_include_frontend_usage_guide(self):
        schema = self._schema()
        description = schema["info"].get("description", "")

        for expected in ("前端接入流程", "Bearer Token", "标准响应", "资源发现与绑定", "MQTT"):
            with self.subTest(expected=expected):
                self.assertIn(expected, description)

    def test_v2_operations_should_include_frontend_usage_descriptions(self):
        schema = self._schema()
        missing = []
        too_short = []

        for path, path_item in schema["paths"].items():
            for method, operation in path_item.items():
                if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                    continue
                summary = str(operation.get("summary") or "").strip()
                description = str(operation.get("description") or "").strip()
                label = f"{method.upper()} {path}"
                if not summary:
                    missing.append(label)
                if "前端用法" not in description or "下一步" not in description or len(description) < 100:
                    too_short.append(label)

        self.assertEqual(missing, [])
        self.assertEqual(too_short, [])

    def test_v2_request_bodies_should_include_frontend_examples(self):
        schema = self._schema()
        missing = []

        for path, path_item in schema["paths"].items():
            for method, operation in path_item.items():
                if method.lower() not in {"post", "put", "patch", "delete"}:
                    continue
                content = operation.get("requestBody", {}).get("content", {})
                for media_type, media in content.items():
                    if not ("json" in media_type or media_type == "multipart/form-data"):
                        continue
                    if not media.get("examples") and "example" not in media:
                        missing.append(f"{method.upper()} {path} [{media_type}]")

        self.assertEqual(missing, [])

    def test_v2_frontend_guide_document_should_exist(self):
        guide_path = Path(settings.BASE_DIR) / "docs" / "api-v2-frontend-guide.md"
        text = guide_path.read_text(encoding="utf-8")

        for expected in ("# API v2 前端接入指南", "资源发现与绑定", "航线与任务", "MQTT"):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

    def test_v2_camera_action_docs_should_be_frontend_actionable(self):
        schema = self._schema()
        operation = schema["paths"]["/api/v2/inspection/camera/actions"]["post"]
        description = operation.get("description", "")
        guide_path = Path(settings.BASE_DIR) / "docs" / "api-v2-frontend-guide.md"
        guide_text = guide_path.read_text(encoding="utf-8")

        for expected in (
            "GET /api/v2/resource/gateways",
            "payload authority",
            "payload commands",
            "camera_mode_switch",
            "camera_focal_length_set",
            "camera_aim",
            "gimbal_reset",
            "upstream.authority",
            "upstream.command",
        ):
            with self.subTest(source="schema", expected=expected):
                self.assertIn(expected, description)
            with self.subTest(source="guide", expected=expected):
                self.assertIn(expected, guide_text)

        examples = operation["requestBody"]["content"]["application/json"]["examples"]
        self.assertTrue(
            {"photoTake", "switchToVideo", "recordingStart", "recordingStop", "focalLengthSetZoom", "cameraAim", "gimbalReset"}.issubset(
                examples
            ),
            sorted(examples),
        )


class ApiV2SchemaParityUtilityTests(TestCase):
    def _load_module(self):
        script_path = Path(settings.BASE_DIR) / "scripts" / "compare_v2_docs_schema.py"
        spec = importlib.util.spec_from_file_location("compare_v2_docs_schema", script_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_compare_v2_schema_should_report_no_diff_for_equal_schemas(self):
        module = self._load_module()
        schema = {
            "info": {"title": "低空平台 API v2", "version": "2.0.0"},
            "paths": {"/api/v2/example": {"get": {"operationId": "example", "responses": {"200": {}}}}},
            "components": {"schemas": {"Example": {"type": "object"}}},
        }

        diff = module.diff_schemas(schema, json.loads(json.dumps(schema)))

        self.assertEqual(diff["info_diff"], {})
        self.assertEqual(diff["paths_only_live"], [])
        self.assertEqual(diff["paths_only_local"], [])
        self.assertEqual(diff["method_diffs"], [])
        self.assertEqual(diff["operation_diffs"], [])
        self.assertEqual(diff["component_diffs"], {"schemas_only_live": [], "schemas_only_local": [], "schema_diffs": []})

    def test_compare_v2_schema_should_parse_curl_i_artifact_with_progress_noise(self):
        module = self._load_module()
        artifact = (
            "  % Total    % Received\n"
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            "\r\n"
            '{"info": {}, "paths": {}, "components": {"schemas": {}}}'
            "\n100 12345  100 12345\n"
        )

        parsed = module._json_schema_from_text(artifact)

        self.assertEqual(parsed["paths"], {})

    def test_compare_v2_schema_should_report_actionable_path_method_and_component_diffs(self):
        module = self._load_module()
        live = {
            "info": {"title": "live"},
            "paths": {
                "/api/v2/live-only": {"get": {"operationId": "liveOnly", "responses": {"200": {}}}},
                "/api/v2/shared": {
                    "get": {"operationId": "liveShared", "responses": {"200": {}}},
                    "post": {"operationId": "livePost", "responses": {"201": {}}},
                },
            },
            "components": {"schemas": {"Shared": {"type": "object"}, "LiveOnly": {"type": "object"}}},
        }
        local = {
            "info": {"title": "local"},
            "paths": {
                "/api/v2/local-only": {"get": {"operationId": "localOnly", "responses": {"200": {}}}},
                "/api/v2/shared": {
                    "get": {"operationId": "localShared", "responses": {"200": {}}},
                    "delete": {"operationId": "localDelete", "responses": {"200": {}}},
                },
            },
            "components": {"schemas": {"Shared": {"type": "array"}, "LocalOnly": {"type": "object"}}},
        }

        diff = module.diff_schemas(live, local)

        self.assertEqual(diff["paths_only_live"], ["/api/v2/live-only"])
        self.assertEqual(diff["paths_only_local"], ["/api/v2/local-only"])
        self.assertEqual(
            diff["method_diffs"],
            [{"path": "/api/v2/shared", "live_methods": ["GET", "POST"], "local_methods": ["DELETE", "GET"]}],
        )
        self.assertEqual(diff["operation_diffs"][0]["path"], "/api/v2/shared")
        self.assertEqual(diff["operation_diffs"][0]["method"], "GET")
        self.assertEqual(diff["component_diffs"]["schemas_only_live"], ["LiveOnly"])
        self.assertEqual(diff["component_diffs"]["schemas_only_local"], ["LocalOnly"])
        self.assertEqual(diff["component_diffs"]["schema_diffs"][0]["schema"], "Shared")
