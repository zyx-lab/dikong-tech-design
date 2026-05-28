import os
import socket
import subprocess
import time
import unittest
import uuid
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


def _object_storage_tests_enabled():
    return os.getenv("OBJECT_STORAGE_INTEGRATION_TESTS", "").lower() in {"1", "true", "yes", "on"}


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


@unittest.skipUnless(
    _object_storage_tests_enabled(),
    "set OBJECT_STORAGE_INTEGRATION_TESTS=true to run object storage integration tests",
)
class V2RouteObjectStorageIntegrationTests(TestCase):
    KMZ_CONTENT_TYPE = "application/vnd.google-earth.kmz"
    MINIO_IMAGE = "minio/minio:latest"
    MINIO_ACCESS_KEY = "minioadmin"
    MINIO_SECRET_KEY = "minioadmin"
    MINIO_BUCKET = "dikong-route-tests"
    minio_container_name = ""
    started_minio_container = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            import boto3
            from botocore.config import Config
            import storages.backends.s3
        except ImportError as exc:
            raise unittest.SkipTest("install django-storages[s3] to run object storage integration tests") from exc

        cls.boto3 = boto3
        cls.Config = Config
        cls.s3_storage_module = storages.backends.s3
        cls.bucket_name = (
            os.getenv("OBJECT_STORAGE_TEST_BUCKET")
            or os.getenv("AWS_STORAGE_BUCKET_NAME")
            or cls.MINIO_BUCKET
        )

        cls.endpoint_url = (
            os.getenv("OBJECT_STORAGE_TEST_ENDPOINT_URL")
            or os.getenv("AWS_S3_ENDPOINT_URL")
            or os.getenv("OBJECT_STORAGE_ENDPOINT_URL")
            or None
        )
        cls.access_key = (
            os.getenv("OBJECT_STORAGE_TEST_ACCESS_KEY_ID")
            or os.getenv("AWS_ACCESS_KEY_ID")
            or os.getenv("OBJECT_STORAGE_ACCESS_KEY_ID")
            or None
        )
        cls.secret_key = (
            os.getenv("OBJECT_STORAGE_TEST_SECRET_ACCESS_KEY")
            or os.getenv("AWS_SECRET_ACCESS_KEY")
            or os.getenv("OBJECT_STORAGE_SECRET_ACCESS_KEY")
            or None
        )
        cls.region_name = (
            os.getenv("OBJECT_STORAGE_TEST_REGION_NAME")
            or os.getenv("AWS_S3_REGION_NAME")
            or os.getenv("OBJECT_STORAGE_REGION_NAME")
            or None
        )
        cls.addressing_style = (
            os.getenv("OBJECT_STORAGE_TEST_ADDRESSING_STYLE")
            or os.getenv("AWS_S3_ADDRESSING_STYLE")
            or "path"
        )
        if cls.endpoint_url is None and cls._autostart_minio_enabled():
            cls._start_minio_container()
        if cls.endpoint_url is None:
            raise unittest.SkipTest("object storage endpoint is required when MinIO autostart is disabled")

        cls.access_key = cls.access_key or cls.MINIO_ACCESS_KEY
        cls.secret_key = cls.secret_key or cls.MINIO_SECRET_KEY
        cls.region_name = cls.region_name or "us-east-1"
        cls._ensure_bucket()

    @classmethod
    def tearDownClass(cls):
        if cls.started_minio_container:
            subprocess.run(["docker", "rm", "-f", cls.minio_container_name], check=False, capture_output=True, text=True)
        super().tearDownClass()

    @classmethod
    def _autostart_minio_enabled(cls) -> bool:
        return os.getenv("OBJECT_STORAGE_TEST_AUTOSTART_MINIO", "true").lower() in {"1", "true", "yes", "on"}

    @classmethod
    def _find_free_port(cls) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    @classmethod
    def _start_minio_container(cls):
        port = cls._find_free_port()
        cls.endpoint_url = f"http://127.0.0.1:{port}"
        cls.access_key = cls.access_key or cls.MINIO_ACCESS_KEY
        cls.secret_key = cls.secret_key or cls.MINIO_SECRET_KEY
        cls.region_name = cls.region_name or "us-east-1"
        cls.minio_container_name = f"dikong-route-minio-test-{uuid.uuid4().hex[:12]}"
        image = os.getenv("OBJECT_STORAGE_TEST_MINIO_IMAGE", cls.MINIO_IMAGE)
        try:
            subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--rm",
                    "--name",
                    cls.minio_container_name,
                    "-e",
                    f"MINIO_ROOT_USER={cls.access_key}",
                    "-e",
                    f"MINIO_ROOT_PASSWORD={cls.secret_key}",
                    "-p",
                    f"127.0.0.1:{port}:9000",
                    image,
                    "server",
                    "/data",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise unittest.SkipTest(f"unable to start MinIO Docker container: {exc}") from exc
        cls.started_minio_container = True
        try:
            cls._wait_for_minio()
        except Exception:
            subprocess.run(["docker", "rm", "-f", cls.minio_container_name], check=False, capture_output=True, text=True)
            cls.started_minio_container = False
            raise

    @classmethod
    def _build_class_s3_client(cls):
        return cls.boto3.client(
            service_name="s3",
            endpoint_url=cls.endpoint_url,
            aws_access_key_id=cls.access_key,
            aws_secret_access_key=cls.secret_key,
            region_name=cls.region_name,
            config=cls.Config(s3={"addressing_style": cls.addressing_style or "path"}),
        )

    @classmethod
    def _wait_for_minio(cls):
        deadline = time.time() + 30
        last_error = None
        while time.time() < deadline:
            try:
                cls._build_class_s3_client().list_buckets()
                return
            except Exception as exc:
                last_error = exc
                time.sleep(0.5)
        raise unittest.SkipTest(f"MinIO Docker container did not become ready: {last_error}")

    @classmethod
    def _ensure_bucket(cls):
        client = cls._build_class_s3_client()
        try:
            client.head_bucket(Bucket=cls.bucket_name)
            return
        except Exception:
            pass
        client.create_bucket(Bucket=cls.bucket_name)

    def setUp(self):
        super().setUp()
        self.storage_prefix = f"integration-tests/v2-routes/{uuid.uuid4().hex}"
        self.s3_client = self._build_s3_client()
        self.storage_options = self._build_storage_options()
        self.override_settings = override_settings(
            STORAGES={
                "default": {
                    "BACKEND": "storages.backends.s3.S3Storage",
                    "OPTIONS": self.storage_options,
                },
                "staticfiles": {
                    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
                },
            }
        )
        self.override_settings.enable()
        self.addCleanup(self.override_settings.disable)
        self.addCleanup(self._cleanup_storage_prefix)

        self.client = APIClient()
        self.user = User.objects.create_user(username=f"v2_route_s3_{uuid.uuid4().hex[:8]}", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="V2 对象存储航线管理员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code=f"v2_route_s3_{uuid.uuid4().hex[:8]}",
            role_code="v2_route_s3_role",
            role_name="V2 对象存储航线角色",
        )
        grant_role_permissions(
            self.role,
            {
                "route.view_route": ScopeType.ALL,
                "route.manage_route": ScopeType.ALL,
            },
        )
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    def _build_s3_client(self):
        kwargs = {
            "service_name": "s3",
            "endpoint_url": self.endpoint_url,
            "aws_access_key_id": self.access_key,
            "aws_secret_access_key": self.secret_key,
            "region_name": self.region_name,
        }
        if self.addressing_style:
            kwargs["config"] = self.Config(s3={"addressing_style": self.addressing_style})
        return self.boto3.client(**kwargs)

    def _build_storage_options(self):
        options = {
            "bucket_name": self.bucket_name,
            "location": self.storage_prefix,
            "querystring_auth": True,
            "default_acl": "private",
        }
        optional_options = {
            "endpoint_url": self.endpoint_url,
            "access_key": self.access_key,
            "secret_key": self.secret_key,
            "region_name": self.region_name,
            "addressing_style": self.addressing_style,
        }
        options.update({key: value for key, value in optional_options.items() if value})
        return options

    def _cleanup_storage_prefix(self):
        paginator = self.s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket_name, Prefix=f"{self.storage_prefix}/"):
            objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if objects:
                self.s3_client.delete_objects(Bucket=self.bucket_name, Delete={"Objects": objects})

    @staticmethod
    def _build_test_kmz(route_name="route") -> bytes:
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("template.kml", f"<kml><Document><name>{route_name}</name></Document></kml>")
            archive.writestr("waylines.wpml", f"<wpml>{route_name}</wpml>")
        return buffer.getvalue()

    def _kmz_upload(self, name: str, content: bytes):
        return SimpleUploadedFile(name, content, content_type=self.KMZ_CONTENT_TYPE)

    def _object_key_for(self, route: Route) -> str:
        return f"{self.storage_prefix}/{route.kmz_file.name}".strip("/")

    def _read_object_bytes(self, route: Route) -> bytes:
        response = self.s3_client.get_object(Bucket=self.bucket_name, Key=self._object_key_for(route))
        try:
            return response["Body"].read()
        finally:
            response["Body"].close()

    def _response_body(self, response):
        return b"".join(response.streaming_content)

    def test_create_update_and_download_should_round_trip_kmz_through_object_storage(self):
        initial_kmz = self._build_test_kmz("object-storage-initial")
        updated_kmz = self._build_test_kmz("object-storage-updated")

        create_response = self.client.post(
            "/api/v2/routes",
            {
                "name": "对象存储航线",
                "kmz_file": self._kmz_upload("route.kmz", initial_kmz),
            },
            format="multipart",
        )

        self.assertEqual(create_response.status_code, 201, getattr(create_response, "data", create_response.content))
        route = Route.objects.get(id=create_response.data["data"]["id"])
        self.assertIsInstance(route.kmz_file.storage, self.s3_storage_module.S3Storage)
        self.assertEqual(self._read_object_bytes(route), initial_kmz)

        download_response = self.client.get(f"/api/v2/routes/{route.id}/kmz")
        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(self._response_body(download_response), initial_kmz)

        update_response = self.client.put(
            f"/api/v2/routes/{route.id}",
            {
                "kmz_file": self._kmz_upload("updated.kmz", updated_kmz),
            },
            format="multipart",
        )

        self.assertEqual(update_response.status_code, 200, getattr(update_response, "data", update_response.content))
        route.refresh_from_db()
        self.assertEqual(self._read_object_bytes(route), updated_kmz)
        updated_download_response = self.client.get(f"/api/v2/routes/{route.id}/kmz")
        self.assertEqual(updated_download_response.status_code, 200)
        self.assertEqual(self._response_body(updated_download_response), updated_kmz)
