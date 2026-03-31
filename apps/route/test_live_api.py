"""Route live HTTP contract tests."""

import json
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_live_base import LiveDjiGatewayApiTestCase, User
from apps.access.test_support import ensure_staff_profile, ensure_tenant_role_binding, grant_role_permissions


class LiveRouteXmlSourceApiTests(LiveDjiGatewayApiTestCase):
    VALID_XML_BYTES = b'<?xml version="1.0" encoding="UTF-8"?><kml><Document><name>live-route</name></Document></kml>'

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="route_live_admin", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="实时航线管理员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="route_live_tenant",
            role_code="route_live_role",
            role_name="实时航线角色",
        )
        grant_role_permissions(
            self.role,
            {
                "route.view_route": ScopeType.ALL,
                "route.manage_route": ScopeType.ALL,
            },
        )
        session = self.login(username="route_live_admin", password="pass1234", tenant_code=self.tenant.code)
        self.auth_headers = {
            "Authorization": f"Bearer {session['accessToken']}",
            "X-Tenant-Code": self.tenant.code,
        }

    def _post_multipart(self, path: str, *, fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]):
        boundary = f"----LiveBoundary{uuid.uuid4().hex}"
        body = bytearray()
        for key, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")
        for key, (filename, content, content_type) in files.items():
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(
                f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode("utf-8")
            )
            body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
            body.extend(content)
            body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        headers = dict(self.auth_headers)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        request = Request(
            url=f"{self.live_server_url}{path}",
            data=bytes(body),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, dict(response.headers.items()), response.read()
        except HTTPError as exc:
            return exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read()

    def test_route_xml_upload_and_readback_should_follow_http_contract(self):
        create_status, _, create_raw = self._post_multipart(
            "/api/v1/routes",
            fields={"name": "实时 XML 航线"},
            files={"xml_file": ("live-route.xml", self.VALID_XML_BYTES, "application/xml")},
        )

        self.assertEqual(create_status, 201, create_raw.decode("utf-8", errors="ignore"))
        create_data = json.loads(create_raw.decode("utf-8"))["data"]
        self.assertFalse(create_data["is_published"])
        route_id = create_data["id"]

        detail_response = self.client.get(f"/api/v1/routes/{route_id}")
        self.assertEqual(detail_response.status_code, 200)
        detail_data = detail_response.json()["data"]
        self.assertNotIn("waypoints", detail_data)

        xml_response = self.client.get(f"/api/v1/routes/{route_id}/xml")
        self.assertEqual(xml_response.status_code, 200)
        self.assertIn("application/xml", xml_response.headers.get("Content-Type", ""))
        self.assertEqual(xml_response.text.encode("utf-8"), self.VALID_XML_BYTES)

    def test_route_publish_should_follow_http_contract(self):
        create_status, _, create_raw = self._post_multipart(
            "/api/v1/routes",
            fields={"name": "实时发布航线"},
            files={"xml_file": ("live-publish.xml", self.VALID_XML_BYTES, "application/xml")},
        )

        self.assertEqual(create_status, 201, create_raw.decode("utf-8", errors="ignore"))
        route_id = json.loads(create_raw.decode("utf-8"))["data"]["id"]

        publish_response = self.client.post(f"/api/v1/routes/{route_id}/publish")

        self.assertEqual(publish_response.status_code, 200, publish_response.text)
        self.assertTrue(publish_response.json()["data"]["is_published"])
