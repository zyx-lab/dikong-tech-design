import json
from pathlib import Path
import re

from django.contrib.auth import get_user_model
from django.conf import settings
from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.test import APIClient

from apps.access.models import DirectoryStatus, UserStatus
from apps.iam_v2.models import (
    DEPARTMENT_ROLE_CODES,
    Department,
    FixedRole,
    ROLE_CODE_ORDER,
    V2AccountProfile,
    V2AccountRoleAssignment,
)
from apps.resource_v2.models import V2AuditLog

User = get_user_model()


def create_v2_actor(*, username: str, role_code: str | None, department: Department, is_platform_admin: bool = False):
    user = User.objects.create_user(username=username, password="pass1234", status=1, is_platform_admin=is_platform_admin)
    profile = V2AccountProfile.objects.create(user=user, department=department)
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
            "/api/v2/iam/session/register",
            "/api/v2/iam/session/register-by-phone",
            "/api/v2/iam/me/context",
            "/api/v2/iam/departments",
            "/api/v2/iam/departments/{id}",
            "/api/v2/iam/departments/{id}/enable",
            "/api/v2/iam/departments/{id}/disable",
            "/api/v2/iam/accounts",
            "/api/v2/iam/accounts/{id}",
            "/api/v2/iam/accounts/{id}/roles",
            "/api/v2/iam/roles",
            "/api/v2/resource/dji-connections",
            "/api/v2/resource/dji-connections/{id}",
            "/api/v2/resource/dji-connections/{id}/discover",
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
            "/api/v2/workforce/pilots",
            "/api/v2/workforce/pilots/{id}",
            "/api/v2/workforce/pilots/{pilot_id}/qualifications",
            "/api/v2/workforce/pilots/{pilot_id}/qualifications/{id}",
            "/api/v2/inspection/routes",
            "/api/v2/inspection/routes/{id}",
            "/api/v2/inspection/routes/{id}/kmz",
            "/api/v2/inspection/missions",
            "/api/v2/inspection/missions/{id}",
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
            "/api/v2/inspection/flight-records",
            "/api/v2/inspection/flight-records/{id}",
            "/api/v2/inspection/flight-records/{id}/refresh-media",
            "/api/v2/inspection/media-files",
            "/api/v2/inspection/media-files/{id}",
        }
        self.assertTrue(expected_paths.issubset(set(paths)))

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
            ("GET", "/api/v2/iam/departments"),
            ("GET", "/api/v2/iam/roles"),
            ("GET", "/api/v2/resource/audit-logs"),
            ("GET", "/api/v2/resource/dji-connections"),
            ("GET", "/api/v2/resource/docks"),
            ("GET", "/api/v2/resource/drones"),
            ("GET", "/api/v2/resource/gateways"),
            ("GET", "/api/v2/resource/payloads"),
            ("GET", "/api/v2/resource/share-groups"),
            ("GET", "/api/v2/workforce/pilots"),
            ("GET", "/api/v2/workforce/pilots/{pilot_id}/qualifications"),
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
            ("POST", "/api/v2/iam/departments"): "201",
            ("POST", "/api/v2/iam/session/register"): "201",
            ("POST", "/api/v2/iam/session/register-by-phone"): "201",
            ("POST", "/api/v2/inspection/missions"): "201",
            ("POST", "/api/v2/inspection/routes"): "201",
            ("POST", "/api/v2/resource/bindings"): "201",
            ("POST", "/api/v2/resource/dji-connections"): "201",
            ("POST", "/api/v2/resource/share-groups"): "201",
            ("POST", "/api/v2/resource/share-groups/{id}/departments"): "201",
            ("POST", "/api/v2/resource/share-groups/{id}/resources"): "201",
            ("POST", "/api/v2/workforce/pilots"): "201",
            ("POST", "/api/v2/workforce/pilots/{pilot_id}/qualifications"): "201",
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
            ("PUT", "/api/v2/iam/accounts/{id}"),
            ("PUT", "/api/v2/iam/accounts/{id}/roles"),
            ("POST", "/api/v2/iam/departments"),
            ("PUT", "/api/v2/iam/departments/{id}"),
            ("POST", "/api/v2/iam/session/login"),
            ("POST", "/api/v2/iam/session/refresh"),
            ("POST", "/api/v2/iam/session/register"),
            ("POST", "/api/v2/iam/session/register-by-phone"),
            ("PUT", "/api/v2/inspection/flight-records/{id}"),
            ("POST", "/api/v2/inspection/live/start"),
            ("POST", "/api/v2/inspection/live/stop"),
            ("POST", "/api/v2/inspection/live/switch"),
            ("POST", "/api/v2/inspection/live/update"),
            ("POST", "/api/v2/inspection/missions"),
            ("PUT", "/api/v2/inspection/missions/{id}"),
            ("POST", "/api/v2/inspection/missions/{id}/abort"),
            ("POST", "/api/v2/inspection/missions/{id}/cancel"),
            ("POST", "/api/v2/inspection/missions/{id}/fail"),
            ("POST", "/api/v2/inspection/routes"),
            ("PUT", "/api/v2/inspection/routes/{id}"),
            ("POST", "/api/v2/inspection/routes/{id}/kmz"),
            ("PUT", "/api/v2/inspection/routes/{id}/kmz"),
            ("POST", "/api/v2/inspection/telemetry/snapshots"),
            ("POST", "/api/v2/resource/bindings"),
            ("POST", "/api/v2/resource/dji-connections"),
            ("PUT", "/api/v2/resource/dji-connections/{id}"),
            ("POST", "/api/v2/resource/share-groups"),
            ("PUT", "/api/v2/resource/share-groups/{id}"),
            ("POST", "/api/v2/resource/share-groups/{id}/departments"),
            ("POST", "/api/v2/resource/share-groups/{id}/resources"),
            ("PUT", "/api/v2/resource/share-groups/{id}/resources/{resource_share_id}"),
            ("POST", "/api/v2/workforce/pilots"),
            ("PUT", "/api/v2/workforce/pilots/{id}"),
            ("POST", "/api/v2/workforce/pilots/{pilot_id}/qualifications"),
            ("PUT", "/api/v2/workforce/pilots/{pilot_id}/qualifications/{id}"),
        ]

        for method, path in request_body_operations:
            with self.subTest(method=method, path=path):
                self.assertIn("requestBody", schema["paths"][path][method.lower()])

    def test_v2_docs_should_be_available(self):
        response = self.client.get("/api/v2/docs/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/api/v2/docs/schema/")
        self.assertNotContains(response, "/api/v1")


class ApiV2ImplementationBoundaryTests(TestCase):
    def test_v2_mainline_should_not_import_v1_or_legacy_dji_bff_modules(self):
        project_root = Path(settings.BASE_DIR)
        app_names = ("api_v2", "iam_v2", "resource_v2", "workforce_v2", "inspection_v2")
        forbidden_imports = ("apps.api_v1", "apps.access.api_v1", "apps.dji_bff")
        offenders = []

        for app_name in app_names:
            for path in (project_root / "apps" / app_name).rglob("*.py"):
                if "migrations" in path.parts or path.name == "tests.py" or path.name.startswith("test_"):
                    continue
                text = path.read_text(encoding="utf-8")
                for forbidden in forbidden_imports:
                    if forbidden in text:
                        offenders.append(f"{path.relative_to(project_root)} imports {forbidden}")

        self.assertEqual(offenders, [])

    def test_default_local_chain_script_should_use_v2_routes_only(self):
        script_path = Path(settings.BASE_DIR) / "scripts" / "local_chain_client.sh"
        text = script_path.read_text(encoding="utf-8")

        self.assertIn("/api/v2/iam/session/login", text)
        self.assertNotIn("/api/v1", text)


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

    def test_roles_should_return_fixed_v2_department_role_codes(self):
        response = self.client.get("/api/v2/iam/roles")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        returned = [item["code"] for item in response.data["data"]["list"]]
        self.assertEqual(returned, [role_code for role_code in ROLE_CODE_ORDER if role_code in DEPARTMENT_ROLE_CODES])
        self.assertNotIn(FixedRole.PLATFORM_SUPER_ADMIN, returned)

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
                "departmentId": other.id,
                "status": DirectoryStatus.DISABLED,
            },
            format="json",
        )

        self.assertEqual(update_response.status_code, 200, getattr(update_response, "data", update_response.content))
        updated = update_response.data["data"]
        self.assertEqual(updated["username"], "v2_pilot_renamed")
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
        other_profile = V2AccountProfile.objects.create(user=other_user, department=other)
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

        self.client.force_authenticate(dispatcher)
        forbidden_list_response = self.client.get("/api/v2/iam/accounts")
        self.assertEqual(forbidden_list_response.status_code, 403, getattr(forbidden_list_response, "data", forbidden_list_response.content))

    def test_account_api_should_validate_duplicates_roles_departments_and_missing_accounts(self):
        child = Department.objects.create(name="飞行队", parent=self.root)
        User.objects.create_user(username="duplicate_v2_account", password="pass1234", status=1)

        duplicate_response = self.client.post(
            "/api/v2/iam/accounts",
            {
                "username": "duplicate_v2_account",
                "password": "pass1234",
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
            {"username": "missing", "departmentId": child.id, "status": DirectoryStatus.ACTIVE},
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
