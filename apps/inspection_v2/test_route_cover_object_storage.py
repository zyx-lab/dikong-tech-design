import json
import zipfile
from io import BytesIO
from urllib.parse import urlparse
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.storage import Storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.encoding import filepath_to_uri
from rest_framework.test import APIClient

from apps.access.models import DirectoryStatus
from apps.iam_v2.models import Department, FixedRole, V2AccountProfile, V2AccountQualification, V2AccountRoleProfile, V2AccountRoleAssignment
from apps.inspection_v2.models import WaypointRoute, route_cover_image_url
from apps.resource_v2.models import BindingStatus, DjiConnection, DroneResource, ResourceBinding, ResourceType

User = get_user_model()

OBJECT_STORAGE_SETTINGS = {
    "default": {"BACKEND": "apps.inspection_v2.test_route_cover_object_storage.FakeSignedMinioStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
PNG_DATA_URL = "data:image/png;base64,iVBORw0KGgo="
DJI_DOWNLOAD_URL = (
    "https://dji-download.example.test/waylines/object-storage.kmz"
    "?X-Amz-Date=20990101T000000Z&X-Amz-Expires=3600&X-Amz-Signature=test-signature"
)


class FakeSignedMinioStorage(Storage):
    saved_names: list[str] = []
    deleted_names: list[str] = []

    def _save(self, name, content) -> str:
        self.saved_names.append(name)
        return name

    def exists(self, name) -> bool:
        return False

    def delete(self, name) -> None:
        self.deleted_names.append(name)

    def url(self, name) -> str:
        return (
            "https://minio-public.example.test/dikong-route-covers/"
            f"{filepath_to_uri(name)}?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=fake-signature"
        )


def create_actor(*, username: str, role_code: str, department: Department):
    user = User.objects.create_user(username=username, password="pass1234", status=1)
    profile = V2AccountProfile.objects.create(
        user=user,
        department=department,
        name=username,
        phone=f"138{user.id:08d}",
        email=f"{username}@example.test",
    )
    V2AccountRoleAssignment.objects.create(account_profile=profile, role_code=role_code, assigned_by_user=user)
    return user, profile


@override_settings(STORAGES=OBJECT_STORAGE_SETTINGS)
class RouteCoverObjectStorageTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        FakeSignedMinioStorage.saved_names = []
        FakeSignedMinioStorage.deleted_names = []
        self.root = Department.objects.create(name="总部")
        self.department = Department.objects.create(name="资源队", parent=self.root)
        self.dispatcher, self.dispatcher_profile = create_actor(
            username="object_storage_dispatcher",
            role_code=FixedRole.TASK_MONITOR_DISPATCHER,
            department=self.department,
        )
        self.pilot_user, self.pilot_profile = create_actor(
            username="object_storage_pilot",
            role_code=FixedRole.PILOT,
            department=self.department,
        )
        V2AccountRoleProfile.objects.create(
            account_profile=self.pilot_profile,
            profile_type=FixedRole.PILOT,
            display_name="对象存储飞手",
            status=DirectoryStatus.ACTIVE,
            remark="当前有效",
        )
        V2AccountQualification.objects.create(
            account_profile=self.pilot_profile,
            profile_type=FixedRole.PILOT,
            qualification_type="多旋翼巡检",
            certificate_no="CERT-OBJECT-STORAGE",
            issued_at=timezone.now().date(),
            expires_at=timezone.now().date().replace(year=timezone.now().date().year + 1),
            status=DirectoryStatus.ACTIVE,
            remark="当前有效",
        )
        self.drone = self.create_drone()

    def create_drone(self):
        connection = DjiConnection.objects.create(
            owner_department=self.department,
            name="对象存储连接",
            base_url="https://dji.example.test",
            username="admin",
            password="secret",
            workspace_id="workspace-object-storage",
            access_token="token",
            created_by_user=self.dispatcher,
        )
        drone = DroneResource.objects.create(device_sn="OBJECT-STORAGE-DRONE", name="对象存储无人机", model="M30")
        ResourceBinding.objects.create(
            resource_type=ResourceType.DRONE,
            resource_object_id=drone.id,
            owner_department=self.department,
            dji_connection=connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=self.dispatcher,
        )
        return drone

    def authenticate(self):
        self.client.force_authenticate(self.dispatcher)

    def route_payload(self, *, name: str) -> dict:
        return {
            "name": name,
            "defaultAltitude": "120.00",
            "defaultSpeed": "8.50",
            "coverImage": PNG_DATA_URL,
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
        payload["djiConnectionId"] = DjiConnection.objects.get(owner_department=self.department).id
        payload["waylineType"] = 0
        payload["kmzFile"] = self.kmz_file()
        return payload

    def post_route(self, payload):
        with patch(
            "apps.inspection_v2.views.DjiConnectionGateway.upload_route",
            return_value={
                "dji_wayline_id": f"wayline-object-storage-{WaypointRoute.objects.count() + 1}",
                "download_url": "/waylines/object-storage/url",
            },
        ), patch(
            "apps.inspection_v2.views.DjiConnectionGateway.get_route_download_url",
            return_value=DJI_DOWNLOAD_URL,
        ):
            return self.client.post("/api/v2/inspection/routes", payload, format="multipart")

    def assert_object_storage_url(self, url: str, *, extension: str) -> None:
        self.assertFalse(url.startswith("/media/"), url)
        parsed = urlparse(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "minio-public.example.test")
        self.assertIn("/dikong-route-covers/inspection/routes/covers/", parsed.path)
        self.assertTrue(parsed.path.endswith(extension), parsed.path)
        self.assertIn("X-Amz-Signature=fake-signature", parsed.query)

    def create_route(self, *, name: str = "对象存储航线") -> dict:
        self.authenticate()
        response = self.post_route(self.multipart_route_payload(name=name))
        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        return response.data["data"]

    def test_route_cover_image_url_should_use_storage_generated_object_url(self):
        route = WaypointRoute.objects.create(
            owner_department=self.department,
            name="对象存储 URL 合约",
            cover_image=SimpleUploadedFile("cover.png", b"\x89PNG\r\n\x1a\n", content_type="image/png"),
            created_by_user=self.dispatcher,
        )

        expected = route.cover_image.storage.url(route.cover_image.name)
        self.assertEqual(route_cover_image_url(route), expected)
        self.assert_object_storage_url(expected, extension=".png")

    def test_route_cover_object_url_should_propagate_to_create_detail_list_and_mission_snapshot(self):
        route = self.create_route(name="对象存储传播航线")
        self.assert_object_storage_url(route["coverImageUrl"], extension=".png")

        detail = self.client.get(f"/api/v2/inspection/routes/{route['id']}")
        self.assertEqual(detail.status_code, 200, getattr(detail, "data", detail.content))
        self.assertEqual(detail.data["data"]["coverImageUrl"], route["coverImageUrl"])

        route_list = self.client.get("/api/v2/inspection/routes", {"keywords": "对象存储传播航线"})
        self.assertEqual(route_list.status_code, 200, getattr(route_list, "data", route_list.content))
        self.assertEqual(route_list.data["data"]["list"][0]["coverImageUrl"], route["coverImageUrl"])

        mission_response = self.client.post(
            "/api/v2/inspection/missions",
            {"name": "对象存储任务", "routeId": route["id"], "droneId": self.drone.id, "pilotAccountProfileId": self.pilot_profile.id},
            format="json",
        )
        self.assertEqual(mission_response.status_code, 201, getattr(mission_response, "data", mission_response.content))
        self.assertEqual(mission_response.data["data"]["routeSnapshot"]["coverImageUrl"], route["coverImageUrl"])

    def test_route_cover_object_url_should_keep_signed_query_parameters_when_querystring_auth_enabled(self):
        route = self.create_route(name="对象存储签名航线")

        parsed = urlparse(route["coverImageUrl"])
        self.assertIn("X-Amz-Algorithm=AWS4-HMAC-SHA256", parsed.query)
        self.assertIn("X-Amz-Signature=fake-signature", parsed.query)

    def test_multipart_route_cover_should_use_same_object_storage_url_contract(self):
        self.authenticate()
        payload = self.multipart_route_payload(name="multipart 对象存储航线")
        payload["coverImage"] = SimpleUploadedFile("cover.webp", b"RIFFxxxxWEBP", content_type="image/webp")

        response = self.post_route(payload)

        self.assertEqual(response.status_code, 201, getattr(response, "data", response.content))
        self.assert_object_storage_url(response.data["data"]["coverImageUrl"], extension=".webp")
