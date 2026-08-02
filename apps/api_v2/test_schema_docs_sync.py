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

    def test_v2_schema_should_document_frontend_bootstrap_and_current_routes(self):
        schema = self._schema()
        description = schema["info"]["description"]

        for expected in (
            "10 分钟接入流程",
            "只调用 `/api/v2/*`",
            "旧 API v1 已移除",
            "POST /api/v2/iam/session/login",
            "GET /api/v2/system/menus/current",
            "bootstrap_v2_system --reset",
            "bootstrap_v2_system --frontend-test-accounts --frontend-prefix jnu",
            "--frontend-password '123456'",
            "jnu_super",
            "jnu_dispatcher",
            "jnu_pilot` 会同步飞手档案和一条有效 `pilot` 资质",
            "docs/api-v2-frontend-guide.md",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, description)

    def test_v2_schema_should_not_expose_fake_permission_sync_delete(self):
        schema = self._schema()

        self.assertEqual(set(schema["paths"]["/api/v2/iam/permissions/sync"].keys()), {"post"})
        self.assertIn("delete", schema["paths"]["/api/v2/iam/roles/{id}"])
        self.assertEqual(schema["paths"]["/api/v2/iam/roles/{id}"]["delete"]["summary"], "删除 v2 角色")

    def test_frontend_guide_should_list_every_public_v2_operation(self):
        schema = self._schema()
        guide_path = Path(settings.BASE_DIR) / "docs" / "api-v2-frontend-guide.md"
        guide_text = guide_path.read_text(encoding="utf-8")

        missing = []
        for path, path_item in sorted(schema["paths"].items()):
            for method in sorted(path_item):
                if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                    continue
                marker = f"`{method.upper()} {path}`"
                if marker not in guide_text:
                    missing.append(marker)

        self.assertEqual(missing, [])

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
            {
                "id",
                "deviceSn",
                "name",
                "onlineStatus",
                "lastSeenAt",
                "latestTelemetry",
                "bindingId",
                "djiConnectionId",
                "djiConnectionName",
                "effectivePermissions",
            },
        )
        self._assert_properties_include(
            self._request_body_properties(
                schema,
                path="/api/v2/resource/cameras",
                method="post",
                content_type="application/json",
            ),
            {"deviceSn", "name", "model", "webrtcUrl", "resultsWsUrl", "apiKey"},
        )
        self._assert_properties_include(
            self._list_item_properties(schema, path="/api/v2/resource/cameras"),
            {"id", "deviceSn", "name", "bindingId", "ownerDepartment", "playbackPath"},
        )
        camera_playback = self._schema_data(schema, path="/api/v2/resource/cameras/{id}/playback", method="get")
        self._assert_properties_include(camera_playback.get("properties", {}), {"cameraId", "name", "video", "resultsWebSocketPath"})
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
        self.assertEqual(route_multipart["kmzFile"]["format"], "binary")
        self._assert_properties_include(route_multipart, {"djiConnectionId", "kmzFile"})
        self.assertNotIn("waypoints", route_multipart)
        self.assertNotIn("waylineType", route_multipart)
        self.assertNotIn("defaultAltitude", route_multipart)
        self.assertNotIn("defaultSpeed", route_multipart)
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
        self._assert_properties_include(
            self._request_body_properties(
                schema,
                path="/api/v2/inspection/media-files/{id}/refresh-url",
                method="post",
                content_type="application/json",
            ),
            {"urlType"},
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

    def test_v2_dji_upstream_docs_should_cover_frontend_proxy_endpoints(self):
        schema = self._schema()
        expected_schema_terms = {
            ("post", "/api/v2/resource/dji-connections/{id}/discover"): ("DJI 上游调用", "资源绑定接口"),
            ("post", "/api/v2/inspection/routes"): ("DJI 上游调用", "DJI wayline 文件库", "data.djiFile"),
            ("get", "/api/v2/inspection/routes/{id}"): ("DJI 上游调用", "downloadUrl"),
            ("put", "/api/v2/inspection/routes/{id}"): ("DJI 上游调用", "只要传了 `kmzFile`"),
            ("delete", "/api/v2/inspection/routes/{id}"): ("DJI 上游调用", "best-effort"),
            ("post", "/api/v2/inspection/missions/{id}/preflight-check"): ("DJI 上游调用", "live capacity", "canStart", "executorId"),
            ("post", "/api/v2/inspection/missions/{id}/start"): ("DJI 上游调用", "启动直播", "wayline flight task"),
            ("post", "/api/v2/inspection/missions/{id}/cloud-execution/refresh"): ("DJI 上游调用", "jobs", "cloudExecution"),
            ("post", "/api/v2/inspection/missions/{id}/complete"): ("DJI 上游调用", "媒体同步", "停止直播失败不会阻断"),
            ("post", "/api/v2/inspection/missions/{id}/cancel"): ("DJI 上游调用", "DJI 取消失败"),
            ("post", "/api/v2/inspection/missions/{id}/fail"): ("DJI 上游调用", "不主动取消 DJI wayline job"),
            ("post", "/api/v2/inspection/missions/{id}/abort"): ("DJI 上游调用", "安全中止"),
            ("get", "/api/v2/inspection/live/capacity"): ("DJI 上游调用", "live capacity", "payloadIndex"),
            ("post", "/api/v2/inspection/live/start"): ("DJI 上游调用", "live stream start"),
            ("post", "/api/v2/inspection/live/stop"): ("DJI 上游调用", "live stream stop"),
            ("post", "/api/v2/inspection/live/update"): ("DJI 上游调用", "live stream update"),
            ("post", "/api/v2/inspection/live/switch"): ("DJI 上游调用", "live stream switch"),
            ("post", "/api/v2/inspection/camera/actions"): ("DJI 上游调用", "payload authority", "payload commands"),
            ("post", "/api/v2/inspection/flight-records/{id}/refresh-media"): ("DJI 上游调用", "media files", "djiJobId"),
            ("post", "/api/v2/inspection/media-files/{id}/refresh-url"): ("DJI 上游调用", "signed URL", "urlType"),
        }

        for (method, path), terms in expected_schema_terms.items():
            description = schema["paths"][path][method]["description"]
            for term in terms:
                with self.subTest(method=method, path=path, term=term):
                    self.assertIn(term, description)

        guide_path = Path(settings.BASE_DIR) / "docs" / "api-v2-frontend-guide.md"
        guide_text = guide_path.read_text(encoding="utf-8")
        for expected in (
            "## 内部调用 DJI 上云 API 的接口",
            "POST /api/v2/inspection/flight-records/{id}/refresh-media",
            "GET /api/v1/manage/workspaces/{workspace_id}/devices/bound?domain=0",
            "POST /api/v1/wayline/workspaces/{workspace_id}/waylines/files/upload",
            "POST /api/v1/wayline/workspaces/{workspace_id}/flight-tasks",
            "POST /api/v2/inspection/missions/{id}/preflight-check",
            "POST /api/v2/inspection/missions/{id}/cloud-execution/refresh",
            "POST /api/v1/manage/live/streams/start",
            "POST /api/v1/control/devices/{gatewaySn}/payload/commands",
            "GET /api/v1/media/workspaces/{workspace_id}/files",
            "POST /api/v2/inspection/media-files/{id}/refresh-url",
            "GET /api/v2/inspection/media-files",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, guide_text)


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
