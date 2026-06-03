import base64
import json
import tempfile
import zipfile
from io import BytesIO
from urllib.parse import urlparse
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.iam_v2.models import Department, FixedRole, V2AccountProfile, V2AccountRoleAssignment
from apps.inspection_v2.models import WaypointRoute
from apps.resource_v2.models import DjiConnection

User = get_user_model()

PNG_DATA_URL = "data:image/png;base64,iVBORw0KGgo="
JPEG_RAW_BASE64 = "/9j/2Q=="
VALIDATION_MESSAGE = "只支持上传 jpg/jpeg/png/webp 图片，且大小不能超过 5MB"


def create_dispatcher(*, department: Department):
    user = User.objects.create_user(username="route_cover_dispatcher", password="pass1234", status=1)
    profile = V2AccountProfile.objects.create(
        user=user,
        department=department,
        name="route_cover_dispatcher",
        phone=f"138{user.id:08d}",
        email="route_cover_dispatcher@example.test",
    )
    V2AccountRoleAssignment.objects.create(
        account_profile=profile,
        role_code=FixedRole.TASK_MONITOR_DISPATCHER,
        assigned_by_user=user,
    )
    return user


class RouteCoverBase64Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.root = Department.objects.create(name="总部")
        self.owner_department = Department.objects.create(name="资源队", parent=self.root)
        self.dispatcher = create_dispatcher(department=self.owner_department)
        self.connection = DjiConnection.objects.create(
            owner_department=self.owner_department,
            name="route cover connection",
            base_url="https://dji.example.test",
            username="admin",
            password="secret",
            workspace_id="workspace-route-cover",
            access_token="token",
            created_by_user=self.dispatcher,
        )

    def authenticate(self):
        self.client.force_authenticate(self.dispatcher)

    def route_payload(self, *, name: str) -> dict:
        return {
            "name": name,
            "defaultAltitude": "120.00",
            "defaultSpeed": "8.50",
            "waypoints": [
                {
                    "sequence": 1,
                    "latitude": "31.23040000",
                    "longitude": "121.47370000",
                    "altitude": "120.00",
                    "speed": "8.50",
                    "heading": "90.00",
                    "hoverSeconds": 3,
                }
            ],
        }

    def kmz_file(self):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("waylines.wpml", b"<wpml></wpml>")
        return SimpleUploadedFile("route.kmz", buffer.getvalue(), content_type="application/vnd.google-earth.kmz")

    def multipart_route_payload(self, *, name: str) -> dict:
        payload = self.route_payload(name=name)
        payload["waypoints"] = json.dumps(payload["waypoints"])
        payload["djiConnectionId"] = self.connection.id
        payload["waylineType"] = 0
        payload["kmzFile"] = self.kmz_file()
        return payload

    def post_route(self, payload):
        with patch(
            "apps.inspection_v2.views.DjiConnectionGateway.upload_route",
            return_value={"dji_wayline_id": f"wayline-cover-{WaypointRoute.objects.count() + 1}", "download_url": "/waylines/cover/url"},
        ):
            return self.client.post("/api/v2/inspection/routes", payload, format="multipart")

    def storage_settings(self, media_root: str):
        return self.settings(
            MEDIA_ROOT=media_root,
            STORAGES={
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                    "OPTIONS": {"location": media_root, "base_url": "/media/"},
                },
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            },
        )

    def create_route_with_cover(self, *, name: str = "封面航线") -> dict:
        payload = self.multipart_route_payload(name=name)
        payload["coverImage"] = PNG_DATA_URL
        response = self.post_route(payload)
        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        return response.data["data"]

    def test_multipart_route_create_should_accept_data_url_cover_image(self):
        self.authenticate()
        payload = self.multipart_route_payload(name="base64 Data URL 航线")
        payload["coverImage"] = PNG_DATA_URL

        with tempfile.TemporaryDirectory() as media_root, self.storage_settings(media_root):
            response = self.post_route(payload)

        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        cover_url = response.data["data"]["coverImageUrl"]
        self.assertNotEqual(cover_url, "")
        self.assertTrue(urlparse(cover_url).path.endswith(".png"), cover_url)

    def test_json_route_update_should_accept_raw_base64_and_replace_cover(self):
        self.authenticate()

        with tempfile.TemporaryDirectory() as media_root, self.storage_settings(media_root):
            route = self.create_route_with_cover(name="raw base64 更新前航线")
            first_url = route["coverImageUrl"]
            payload = {"name": "raw base64 更新后航线"}
            payload["coverImage"] = JPEG_RAW_BASE64

            response = self.client.put(f"/api/v2/inspection/routes/{route['id']}", payload, format="json")

        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        cover_url = response.data["data"]["coverImageUrl"]
        self.assertNotEqual(cover_url, first_url)
        self.assertTrue(urlparse(cover_url).path.endswith(".jpg"), cover_url)

    def test_json_route_cover_should_reject_invalid_base64(self):
        self.authenticate()
        payload = self.multipart_route_payload(name="非法 base64 航线")
        payload["coverImage"] = "data:image/png;base64,not-base64"

        response = self.client.post("/api/v2/inspection/routes", payload, format="multipart")

        self.assertEqual(response.status_code, 400, getattr(response, "data", response.content))
        self.assertIn("coverImage", str(response.data))
        self.assertIn(VALIDATION_MESSAGE, str(response.data))

    def test_json_route_cover_should_reject_mime_signature_mismatch(self):
        self.authenticate()
        payload = self.multipart_route_payload(name="MIME 不匹配航线")
        payload["coverImage"] = f"data:image/png;base64,{JPEG_RAW_BASE64}"

        response = self.client.post("/api/v2/inspection/routes", payload, format="multipart")

        self.assertEqual(response.status_code, 400, getattr(response, "data", response.content))
        self.assertIn("coverImage", str(response.data))
        self.assertIn(VALIDATION_MESSAGE, str(response.data))

    def test_json_route_cover_should_reject_decoded_payload_over_5_mib(self):
        self.authenticate()
        oversize_bytes = b"\x89PNG\r\n\x1a\n" + (b"0" * (5 * 1024 * 1024))
        payload = self.multipart_route_payload(name="超大封面航线")
        payload["coverImage"] = "data:image/png;base64," + base64.b64encode(oversize_bytes).decode("ascii")

        with self.settings(DATA_UPLOAD_MAX_MEMORY_SIZE=10 * 1024 * 1024):
            response = self.client.post("/api/v2/inspection/routes", payload, format="multipart")

        self.assertEqual(response.status_code, 400, getattr(response, "data", response.content))
        self.assertIn("coverImage", str(response.data))
        self.assertIn(VALIDATION_MESSAGE, str(response.data))

    def test_json_route_update_should_preserve_cover_when_cover_is_empty_or_null(self):
        self.authenticate()

        with tempfile.TemporaryDirectory() as media_root, self.storage_settings(media_root):
            route = self.create_route_with_cover(name="空封面保留航线")
            first_url = route["coverImageUrl"]
            for empty_value in ("", None):
                payload = {"name": f"空封面保留航线 {empty_value!r}"}
                payload["coverImage"] = empty_value
                response = self.client.put(f"/api/v2/inspection/routes/{route['id']}", payload, format="json")
                self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
                self.assertEqual(response.data["data"]["coverImageUrl"], first_url)

    def test_multipart_route_cover_should_remain_supported(self):
        self.authenticate()
        payload = self.multipart_route_payload(name="multipart 兼容航线")
        payload["coverImage"] = SimpleUploadedFile("cover.webp", b"RIFFxxxxWEBP", content_type="image/webp")

        with tempfile.TemporaryDirectory() as media_root, self.storage_settings(media_root):
            response = self.post_route(payload)

        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        self.assertTrue(urlparse(response.data["data"]["coverImageUrl"]).path.endswith(".webp"))
        self.assertTrue(WaypointRoute.objects.filter(name="multipart 兼容航线").exists())
