import shutil
import tempfile
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.models import EmploymentStatus, ScopeType, Tenant, TenantStatus
from apps.access.test_support import ensure_staff_profile, ensure_tenant_role_binding, grant_role_permissions
from apps.dji_bff.gateway import DjiGatewayUpstreamError
from apps.dji_bff.models import TenantRouteIndex
from apps.dji_mock.state import mock_dji_state
from apps.dji_mock.test_support import MockDjiUpstreamTestMixin
from apps.drone.models import Drone
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route
from apps.route.services import build_route_kmz_from_xml

User = get_user_model()


class RouteXmlPackagingTests(TestCase):
    def setUp(self):
        self._media_root = tempfile.mkdtemp(prefix="route-xml-packaging-tests-")
        self._media_override = override_settings(MEDIA_ROOT=self._media_root)
        self._media_override.enable()
        self.addCleanup(self._media_override.disable)
        self.addCleanup(lambda: shutil.rmtree(self._media_root, ignore_errors=True))

    def test_build_route_kmz_from_xml_should_wrap_xml_bytes_in_kmz_archive(self):
        tenant = Tenant.objects.create(
            code="route_xml_pkg_tenant",
            name="Route XML 包装租户",
            status=TenantStatus.ACTIVE,
        )
        route = Route.objects.create(tenant=tenant, name="包装航线")
        route.xml_file.save("pack.xml", ContentFile(b"<route><node /></route>"), save=True)

        kmz_file = build_route_kmz_from_xml(route)

        self.assertTrue(kmz_file.name.endswith(".kmz"))
        archive = ZipFile(BytesIO(kmz_file.read()))
        self.assertEqual(archive.namelist(), ["route.xml"])
        self.assertEqual(archive.read("route.xml"), b"<route><node /></route>")


class RouteXmlSourceApiTests(MockDjiUpstreamTestMixin, TestCase):
    VALID_XML_BYTES = b'<?xml version="1.0" encoding="UTF-8"?><kml><Document><name>route</name></Document></kml>'
    UPDATED_XML_BYTES = (
        b'<?xml version="1.0" encoding="UTF-8"?><kml><Document><name>route-updated</name></Document></kml>'
    )

    def setUp(self):
        self._media_root = tempfile.mkdtemp(prefix="route-xml-tests-")
        self._media_override = override_settings(MEDIA_ROOT=self._media_root)
        self._media_override.enable()
        self.addCleanup(self._media_override.disable)
        self.addCleanup(lambda: shutil.rmtree(self._media_root, ignore_errors=True))
        super().setUp()
        self.client = APIClient()
        self.user = User.objects.create_user(username="route_admin", password="pass1234", status=1)
        ensure_staff_profile(self.user, name="航线管理员", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.member, self.role = ensure_tenant_role_binding(
            self.user,
            tenant_code="route_test_tenant",
            role_code="route_test_role",
            role_name="航线测试角色",
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

    def _upload_xml_route(self, *, name="XML 航线", xml_bytes=None):
        return self.client.post(
            "/api/v1/routes",
            {
                "name": name,
                "xml_file": SimpleUploadedFile(
                    "route.xml",
                    xml_bytes if xml_bytes is not None else self.VALID_XML_BYTES,
                    content_type="application/xml",
                ),
            },
            format="multipart",
        )

    def _attach_xml_draft_or_fail(self, route: Route, *, xml_bytes: bytes, filename: str):
        self.assertTrue(
            hasattr(route, "xml_file"),
            "Route model must expose xml_file local draft storage.",
        )
        route.xml_file.save(filename, ContentFile(xml_bytes), save=True)
        route.refresh_from_db()
        return route

    def _response_body(self, response):
        return b"".join(response.streaming_content)

    def test_create_should_accept_multipart_xml_and_mark_route_unpublished(self):
        response = self._upload_xml_route(name="城市巡检 XML")

        self.assertEqual(response.status_code, 201, response.data)
        data = response.data["data"]
        self.assertEqual(data["name"], "城市巡检 XML")
        self.assertFalse(data["is_published"])
        route = Route.objects.get(id=data["id"])
        self.assertTrue(hasattr(route, "xml_file"), "Route model must expose xml_file local draft storage.")
        self.assertTrue(bool(route.xml_file.name))
        self.assertTrue(default_storage.exists(route.xml_file.name))
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertFalse(route_index.is_published)

    def test_create_should_reject_legacy_waypoints_json_write_contract(self):
        response = self.client.post(
            "/api/v1/routes",
            {
                "name": "旧写入契约",
                "xml_file": SimpleUploadedFile("legacy-create.xml", self.VALID_XML_BYTES, content_type="application/xml"),
                "route_type": 0,
                "drone_type_id": 2,
                "total_distance": "123.45",
                "estimated_duration": 60,
                "waypoints": "[]",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("waypoints", response.data["data"], response.data)
        self.assertIn("route_type", response.data["data"], response.data)
        self.assertIn("total_distance", response.data["data"], response.data)
        self.assertIn("estimated_duration", response.data["data"], response.data)
        self.assertIn("drone_type_id", response.data["data"], response.data)
        self.assertEqual(response.data["data"]["waypoints"], ["该字段在此接口不可写"])
        self.assertEqual(response.data["data"]["route_type"], ["该字段在此接口不可写"])
        self.assertEqual(response.data["data"]["total_distance"], ["该字段在此接口不可写"])
        self.assertEqual(response.data["data"]["estimated_duration"], ["该字段在此接口不可写"])
        self.assertEqual(response.data["data"]["drone_type_id"], ["该字段在此接口不可写"])

    def test_create_should_reject_unparseable_xml(self):
        response = self._upload_xml_route(name="坏 XML", xml_bytes=b"<kml><Document>")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["data"], {"xml_file": ["上传文件必须是可解析 XML"]})

    def test_detail_should_hide_waypoints_and_xml_endpoint_should_return_raw_xml(self):
        route = Route.objects.create(tenant=self.tenant, name="详情 XML")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="",
            is_published=False,
        )
        self._attach_xml_draft_or_fail(route, xml_bytes=self.VALID_XML_BYTES, filename="detail.xml")

        detail_response = self.client.get(f"/api/v1/routes/{route.id}")
        self.assertEqual(detail_response.status_code, 200, detail_response.data)
        detail_data = detail_response.data["data"]
        self.assertNotIn("waypoints", detail_data)

        xml_response = self.client.get(f"/api/v1/routes/{route.id}/xml")
        self.assertEqual(xml_response.status_code, 200)
        self.assertIn("application/xml", xml_response["Content-Type"])
        self.assertEqual(self._response_body(xml_response), self.VALID_XML_BYTES)

    def test_put_should_replace_xml_reset_publish_flag_and_delete_old_local_xml_file(self):
        route = Route.objects.create(tenant=self.tenant, name="更新前 XML")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="mock-wayline-existing",
            is_published=True,
        )
        route = self._attach_xml_draft_or_fail(route, xml_bytes=self.VALID_XML_BYTES, filename="before-put.xml")

        old_xml_name = route.xml_file.name
        self.assertTrue(default_storage.exists(old_xml_name))

        update_response = self.client.put(
            f"/api/v1/routes/{route.id}",
            {
                "name": "更新后 XML",
                "xml_file": SimpleUploadedFile("route-updated.xml", self.UPDATED_XML_BYTES, content_type="application/xml"),
            },
            format="multipart",
        )

        self.assertEqual(update_response.status_code, 200, update_response.data)
        self.assertFalse(update_response.data["data"]["is_published"])

        route.refresh_from_db()
        self.assertNotEqual(route.xml_file.name, old_xml_name)
        self.assertFalse(default_storage.exists(old_xml_name))
        self.assertFalse(TenantRouteIndex.objects.get(route=route).is_published)
        xml_response = self.client.get(f"/api/v1/routes/{route.id}/xml")
        self.assertEqual(xml_response.status_code, 200)
        self.assertEqual(self._response_body(xml_response), self.UPDATED_XML_BYTES)

    def test_xml_should_return_404_when_backing_file_is_missing(self):
        route = Route.objects.create(tenant=self.tenant, name="缺失 XML")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="",
            is_published=False,
        )
        route = self._attach_xml_draft_or_fail(route, xml_bytes=self.VALID_XML_BYTES, filename="missing.xml")
        default_storage.delete(route.xml_file.name)

        response = self.client.get(f"/api/v1/routes/{route.id}/xml")

        self.assertEqual(response.status_code, 404, getattr(response, "data", None))

    def test_put_should_reject_legacy_waypoints_json_write_contract(self):
        route = Route.objects.create(tenant=self.tenant, name="旧 PUT 契约")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="",
            is_published=False,
        )

        response = self.client.put(
            f"/api/v1/routes/{route.id}",
            {
                "name": "旧 PUT 契约更新",
                "xml_file": SimpleUploadedFile("legacy-put.xml", self.UPDATED_XML_BYTES, content_type="application/xml"),
                "route_type": 0,
                "total_distance": "222.20",
                "estimated_duration": 120,
                "waypoints": "[]",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("waypoints", response.data["data"], response.data)
        self.assertIn("route_type", response.data["data"], response.data)
        self.assertIn("total_distance", response.data["data"], response.data)
        self.assertIn("estimated_duration", response.data["data"], response.data)
        self.assertEqual(response.data["data"]["waypoints"], ["该字段在此接口不可写"])
        self.assertEqual(response.data["data"]["route_type"], ["该字段在此接口不可写"])
        self.assertEqual(response.data["data"]["total_distance"], ["该字段在此接口不可写"])
        self.assertEqual(response.data["data"]["estimated_duration"], ["该字段在此接口不可写"])

    def test_patch_and_download_endpoints_should_be_removed(self):
        route = Route.objects.create(tenant=self.tenant, name="移除接口检查")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="mock-wayline-existing",
            is_published=True,
        )

        patch_response = self.client.patch(f"/api/v1/routes/{route.id}", {"name": "不允许 PATCH"}, format="json")
        download_response = self.client.get(f"/api/v1/routes/{route.id}/download")

        self.assertEqual(patch_response.status_code, 405, patch_response.data)
        self.assertEqual(download_response.status_code, 404, getattr(download_response, "data", download_response.content))

    def test_publish_should_convert_stored_xml_to_kmz_and_mark_published(self):
        route = Route.objects.create(tenant=self.tenant, name="发布 XML")
        TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", is_published=False)
        self._attach_xml_draft_or_fail(route, xml_bytes=self.VALID_XML_BYTES, filename="publish.xml")

        publish_response = self.client.post(f"/api/v1/routes/{route.id}/publish")

        self.assertEqual(publish_response.status_code, 200, publish_response.data)
        self.assertTrue(publish_response.data["data"]["is_published"])
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertTrue(route_index.is_published)
        self.assertTrue(route_index.dji_wayline_id.startswith("mock-wayline-"))
        uploaded_payload = mock_dji_state.waylines[route_index.dji_wayline_id]
        self.assertTrue(uploaded_payload["file_name"].endswith(".kmz"))

    def test_publish_should_return_400_when_stored_xml_is_invalid(self):
        route = Route.objects.create(tenant=self.tenant, name="坏草稿 XML")
        TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", is_published=False)
        self._attach_xml_draft_or_fail(route, xml_bytes=b"<kml><Document>", filename="broken.xml")

        response = self.client.post(f"/api/v1/routes/{route.id}/publish")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["msg"], "当前 XML 草稿无法转换为可发布 KMZ")

    def test_publish_should_reject_body_parameters(self):
        route = Route.objects.create(tenant=self.tenant, name="发布 body 校验")
        TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", is_published=False)
        self._attach_xml_draft_or_fail(route, xml_bytes=self.VALID_XML_BYTES, filename="body-check.xml")

        response = self.client.post(f"/api/v1/routes/{route.id}/publish", {"unexpected": True}, format="json")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "B0001")

    def test_publish_should_replace_old_upstream_wayline_after_success(self):
        route = Route.objects.create(tenant=self.tenant, name="替换上游 XML")
        old_wayline_id = mock_dji_state.create_wayline(name="legacy-upstream")["wayline_id"]
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id=old_wayline_id,
            is_published=True,
        )
        self._attach_xml_draft_or_fail(route, xml_bytes=self.VALID_XML_BYTES, filename="legacy.xml")

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(f"/api/v1/routes/{route.id}/publish")

        self.assertEqual(response.status_code, 200, response.data)
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertNotEqual(route_index.dji_wayline_id, old_wayline_id)
        self.assertTrue(route_index.is_published)
        self.assertNotIn(old_wayline_id, mock_dji_state.waylines)

    def test_publish_should_cleanup_new_upload_and_keep_old_wayline_when_post_upload_step_fails(self):
        route = Route.objects.create(tenant=self.tenant, name="发布补偿 XML")
        old_wayline_id = mock_dji_state.create_wayline(name="legacy-upstream")["wayline_id"]
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id=old_wayline_id,
            is_published=True,
        )
        self._attach_xml_draft_or_fail(route, xml_bytes=self.VALID_XML_BYTES, filename="compensate.xml")
        observed_wayline_ids = {}

        def _raise_after_upload(*args, **kwargs):
            observed_wayline_ids["during_log_action"] = set(mock_dji_state.waylines.keys())
            raise RuntimeError("log failed after upload")

        with patch("apps.route.views.log_action", side_effect=_raise_after_upload):
            response = self.client.post(f"/api/v1/routes/{route.id}/publish")

        self.assertEqual(response.status_code, 500, response.data)
        self.assertIn("during_log_action", observed_wayline_ids)
        self.assertIn(old_wayline_id, observed_wayline_ids["during_log_action"])
        self.assertEqual(len(observed_wayline_ids["during_log_action"]), 2)

        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertEqual(route_index.dji_wayline_id, old_wayline_id)
        self.assertTrue(route_index.is_published)
        self.assertIn(old_wayline_id, mock_dji_state.waylines)
        self.assertEqual(set(mock_dji_state.waylines.keys()), {old_wayline_id})

    def test_publish_should_succeed_when_old_wayline_cleanup_fails_after_commit(self):
        route = Route.objects.create(tenant=self.tenant, name="发布清理失败 XML")
        old_wayline_id = mock_dji_state.create_wayline(name="legacy-upstream")["wayline_id"]
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id=old_wayline_id,
            is_published=True,
        )
        self._attach_xml_draft_or_fail(route, xml_bytes=self.VALID_XML_BYTES, filename="cleanup-failure.xml")

        def _raise_delete_failure(*args, **kwargs):
            raise DjiGatewayUpstreamError("delete failed", status_code=500)

        with patch("apps.route.views.DjiGateway.delete_route", side_effect=_raise_delete_failure):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(f"/api/v1/routes/{route.id}/publish")

        self.assertEqual(response.status_code, 200, response.data)
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertNotEqual(route_index.dji_wayline_id, old_wayline_id)
        self.assertTrue(route_index.is_published)
        self.assertIn(route_index.dji_wayline_id, mock_dji_state.waylines)
        self.assertIn(old_wayline_id, mock_dji_state.waylines)

    def test_delete_should_remove_local_xml_file(self):
        route = Route.objects.create(tenant=self.tenant, name="删除 XML 航线")
        TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", is_published=False)
        route = self._attach_xml_draft_or_fail(route, xml_bytes=self.VALID_XML_BYTES, filename="delete.xml")
        xml_name = route.xml_file.name

        response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(default_storage.exists(xml_name))

    def test_delete_should_reject_route_referenced_by_paused_mission(self):
        route = Route.objects.create(tenant=self.tenant, name="暂停任务航线")
        TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", is_published=False)
        pilot_user = User.objects.create_user(username="route_pause_pilot", password="pass1234", status=1)
        ensure_staff_profile(pilot_user, name="暂停飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, pilot_member, _pilot_role = ensure_tenant_role_binding(
            pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="ROUTE-PAUSE-DRONE-001",
            name="暂停任务无人机",
            model="M30",
            device_sn="ROUTE-PAUSE-SN-001",
        )
        Mission.objects.create(
            tenant=self.tenant,
            name="暂停中的任务",
            route=route,
            route_name=route.name,
            drone=drone,
            drone_name=drone.name,
            pilot=pilot_member,
            pilot_name="暂停飞手",
            status=MissionStatus.PAUSED,
            dji_job_id="",
        )

        response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(Route.objects.filter(id=route.id).exists())
