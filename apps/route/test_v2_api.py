from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_support import ensure_staff_profile, ensure_tenant_role_binding, grant_role_permissions
from apps.dji_bff.gateway import GatewayResponse
from apps.dji_bff.models import TenantRouteIndex
from apps.route.models import Route

User = get_user_model()


def DjiCloudPlatform():
    return apps.get_model("dji_bff", "DjiCloudPlatform")


class FakeV2RouteGateway:
    KMZ_CONTENT_TYPE = "application/vnd.google-earth.kmz"
    init_platform_ids = []
    upload_calls = []
    delete_calls = []
    upload_counter = 0

    def __init__(self, *, platform=None, **kwargs):
        self.platform = platform
        self.init_platform_ids.append(platform.id if platform is not None else None)

    @classmethod
    def reset(cls):
        cls.init_platform_ids = []
        cls.upload_calls = []
        cls.delete_calls = []
        cls.upload_counter = 0

    def _workspace_id(self):
        return f"workspace-{self.platform.id}"

    def upload_route(self, *, route_name, file_obj):
        self.__class__.upload_counter += 1
        file_obj.seek(0)
        content = file_obj.read()
        wayline_id = f"wayline-{self.platform.id}-{self.upload_counter}"
        self.upload_calls.append(
            {
                "platform_id": self.platform.id,
                "route_name": route_name,
                "file_name": getattr(file_obj, "name", ""),
                "content": content,
                "wayline_id": wayline_id,
            }
        )
        return {
            "dji_wayline_id": wayline_id,
            "download_url": f"https://dji.example.test/{wayline_id}.kmz",
        }

    def download_route_file(self, download_url):
        return GatewayResponse(
            status_code=200,
            headers={"Content-Type": self.KMZ_CONTENT_TYPE},
            data=b"verified-kmz",
        )

    def get_route_download_url(self, wayline_id):
        return f"https://dji.example.test/{wayline_id}.kmz"

    def delete_route(self, wayline_id):
        self.delete_calls.append(wayline_id)


class V2RouteApiTests(TestCase):
    KMZ_CONTENT_TYPE = "application/vnd.google-earth.kmz"

    def setUp(self):
        super().setUp()
        FakeV2RouteGateway.reset()
        self.media_root = TemporaryDirectory()
        self.override_settings = override_settings(MEDIA_ROOT=self.media_root.name)
        self.override_settings.enable()
        self.addCleanup(self.override_settings.disable)
        self.addCleanup(self.media_root.cleanup)

        self.client = APIClient()
        self.user = User.objects.create_user(username="v2_route_admin", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="V2 航线管理员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="v2_route_tenant",
            role_code="v2_route_role",
            role_name="V2 航线角色",
        )
        grant_role_permissions(
            self.role,
            {
                "route.view_route": ScopeType.ALL,
                "route.manage_route": ScopeType.ALL,
            },
        )
        self.platform_a = DjiCloudPlatform().objects.create(
            tenant=self.tenant,
            name="平台 A",
            base_url="https://a.example.test",
            username="admin-a",
            password="secret",
        )
        self.platform_b = DjiCloudPlatform().objects.create(
            tenant=self.tenant,
            name="平台 B",
            base_url="https://b.example.test",
            username="admin-b",
            password="secret",
        )
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    @staticmethod
    def _build_test_kmz(route_name="route") -> bytes:
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("template.kml", f"<kml><Document><name>{route_name}</name></Document></kml>")
            archive.writestr("waylines.wpml", f"<wpml>{route_name}</wpml>")
        return buffer.getvalue()

    def _kmz_upload(self, name: str, content: bytes | None = None):
        return SimpleUploadedFile(
            name,
            content if content is not None else self._build_test_kmz(),
            content_type=self.KMZ_CONTENT_TYPE,
        )

    def _create_route(self, *, name="本地航线", kmz_bytes=None):
        return self.client.post(
            "/api/v2/routes",
            {
                "name": name,
                "kmz_file": self._kmz_upload("route.kmz", kmz_bytes),
            },
            format="multipart",
        )

    def _response_body(self, response):
        return b"".join(response.streaming_content)

    @patch("apps.api_v2.views.DjiGateway", FakeV2RouteGateway)
    def test_create_should_store_local_kmz_without_platform_or_dji_upload(self):
        kmz_bytes = self._build_test_kmz("local-only")

        response = self._create_route(name="本地保存航线", kmz_bytes=kmz_bytes)

        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        data = response.data["data"]
        self.assertNotIn("dji_platform", data)
        self.assertEqual(data["name"], "本地保存航线")
        self.assertFalse(data["is_published"])
        self.assertEqual(data["dispatches"], [])
        route = Route.objects.get(id=data["id"])
        self.assertIsNone(route.dji_platform_id)
        self.assertTrue(route.kmz_file.name)
        with route.kmz_file.open("rb") as stored_file:
            self.assertEqual(stored_file.read(), kmz_bytes)
        self.assertEqual(TenantRouteIndex.objects.filter(route=route).count(), 0)
        self.assertEqual(FakeV2RouteGateway.upload_calls, [])

    @patch("apps.api_v2.views.DjiGateway", FakeV2RouteGateway)
    def test_dispatch_should_upload_stored_kmz_to_selected_platform(self):
        kmz_bytes = self._build_test_kmz("dispatch-a")
        create_response = self._create_route(name="待下发航线", kmz_bytes=kmz_bytes)
        route_id = create_response.data["data"]["id"]

        response = self.client.post(
            f"/api/v2/routes/{route_id}/dispatch",
            {"platform_id": self.platform_a.id},
            format="json",
        )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        route = Route.objects.get(id=route_id)
        route_index = TenantRouteIndex.objects.get(route=route, dji_platform=self.platform_a)
        self.assertTrue(route_index.is_published)
        self.assertEqual(route_index.workspace_id, f"workspace-{self.platform_a.id}")
        self.assertEqual(route_index.dji_wayline_id, f"wayline-{self.platform_a.id}-1")
        self.assertEqual(FakeV2RouteGateway.upload_calls[0]["platform_id"], self.platform_a.id)
        self.assertEqual(FakeV2RouteGateway.upload_calls[0]["content"], kmz_bytes)
        dispatch = response.data["data"]["dispatches"][0]
        self.assertEqual(dispatch["dji_platform"], self.platform_a.id)
        self.assertTrue(dispatch["is_published"])

    @patch("apps.api_v2.views.DjiGateway", FakeV2RouteGateway)
    def test_same_route_can_dispatch_to_multiple_platforms(self):
        route_id = self._create_route(name="多平台航线").data["data"]["id"]

        response_a = self.client.post(
            f"/api/v2/routes/{route_id}/dispatch",
            {"platform_id": self.platform_a.id},
            format="json",
        )
        response_b = self.client.post(
            f"/api/v2/routes/{route_id}/dispatch",
            {"platform_id": self.platform_b.id},
            format="json",
        )

        self.assertEqual(response_a.status_code, 200, getattr(response_a, "data", response_a.content))
        self.assertEqual(response_b.status_code, 200, getattr(response_b, "data", response_b.content))
        self.assertEqual(TenantRouteIndex.objects.filter(route_id=route_id).count(), 2)
        self.assertTrue(TenantRouteIndex.objects.filter(route_id=route_id, dji_platform=self.platform_a).exists())
        self.assertTrue(TenantRouteIndex.objects.filter(route_id=route_id, dji_platform=self.platform_b).exists())

    @patch("apps.api_v2.views.DjiGateway", FakeV2RouteGateway)
    def test_redispatch_should_replace_same_platform_index_and_delete_old_wayline_after_commit(self):
        route_id = self._create_route(name="重复下发航线").data["data"]["id"]
        first_response = self.client.post(
            f"/api/v2/routes/{route_id}/dispatch",
            {"platform_id": self.platform_a.id},
            format="json",
        )
        self.assertEqual(first_response.status_code, 200, getattr(first_response, "data", first_response.content))
        old_wayline_id = TenantRouteIndex.objects.get(route_id=route_id, dji_platform=self.platform_a).dji_wayline_id

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            response = self.client.post(
                f"/api/v2/routes/{route_id}/dispatch",
                {"platform_id": self.platform_a.id},
                format="json",
            )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        route_index = TenantRouteIndex.objects.get(route_id=route_id, dji_platform=self.platform_a)
        self.assertNotEqual(route_index.dji_wayline_id, old_wayline_id)
        self.assertEqual(TenantRouteIndex.objects.filter(route_id=route_id, dji_platform=self.platform_a).count(), 1)
        self.assertEqual(FakeV2RouteGateway.delete_calls, [])
        for callback in callbacks:
            callback()
        self.assertEqual(FakeV2RouteGateway.delete_calls, [old_wayline_id])

    @patch("apps.api_v2.views.DjiGateway", FakeV2RouteGateway)
    def test_kmz_download_should_return_local_stored_kmz(self):
        kmz_bytes = self._build_test_kmz("download-local")
        route_id = self._create_route(name="下载本地航线", kmz_bytes=kmz_bytes).data["data"]["id"]

        response = self.client.get(f"/api/v2/routes/{route_id}/kmz")

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.KMZ_CONTENT_TYPE, response["Content-Type"])
        self.assertEqual(self._response_body(response), kmz_bytes)
        self.assertEqual(FakeV2RouteGateway.upload_calls, [])

    @patch("apps.api_v2.views.DjiGateway", FakeV2RouteGateway)
    def test_replacing_local_kmz_should_mark_existing_dispatches_unpublished(self):
        route_id = self._create_route(name="待替换航线").data["data"]["id"]
        dispatch_response = self.client.post(
            f"/api/v2/routes/{route_id}/dispatch",
            {"platform_id": self.platform_a.id},
            format="json",
        )
        self.assertEqual(dispatch_response.status_code, 200, getattr(dispatch_response, "data", dispatch_response.content))
        FakeV2RouteGateway.upload_calls = []
        updated_kmz_bytes = self._build_test_kmz("updated-local")

        response = self.client.put(
            f"/api/v2/routes/{route_id}",
            {
                "name": "替换后航线",
                "kmz_file": self._kmz_upload("updated.kmz", updated_kmz_bytes),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        route = Route.objects.get(id=route_id)
        self.assertEqual(route.name, "替换后航线")
        with route.kmz_file.open("rb") as stored_file:
            self.assertEqual(stored_file.read(), updated_kmz_bytes)
        route_index = TenantRouteIndex.objects.get(route=route, dji_platform=self.platform_a)
        self.assertFalse(route_index.is_published)
        self.assertEqual(FakeV2RouteGateway.upload_calls, [])

    @patch("apps.api_v2.views.DjiGateway", FakeV2RouteGateway)
    def test_platform_filter_should_return_routes_dispatched_to_that_platform(self):
        route_a_id = self._create_route(name="平台 A 航线").data["data"]["id"]
        route_b_id = self._create_route(name="平台 B 航线").data["data"]["id"]
        self.client.post(f"/api/v2/routes/{route_a_id}/dispatch", {"platform_id": self.platform_a.id}, format="json")
        self.client.post(f"/api/v2/routes/{route_b_id}/dispatch", {"platform_id": self.platform_b.id}, format="json")

        response = self.client.get(f"/api/v2/routes?platform_id={self.platform_a.id}")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        route_ids = [item["id"] for item in response.data["data"]["list"]]
        self.assertEqual(route_ids, [route_a_id])
