import re
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError, connection, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import EmploymentStatus, ScopeType
from apps.access.test_support import ensure_staff_profile, ensure_tenant_role_binding, grant_role_permissions
from apps.dji_bff.gateway import DjiGatewayUpstreamError, GatewayResponse
from apps.dji_bff.models import TenantRouteIndex
from apps.dji_mock.state import mock_dji_state
from apps.dji_mock.test_support import MockDjiUpstreamTestMixin
from apps.drone.models import Drone
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route
from apps.waypoint.models import Waypoint

User = get_user_model()



class RouteKmzApiTests(MockDjiUpstreamTestMixin, TestCase):
    VALID_TEMPLATE_BYTES = b'<?xml version="1.0" encoding="UTF-8"?><kml><Document><name>route</name></Document></kml>'
    UPDATED_TEMPLATE_BYTES = b'<?xml version="1.0" encoding="UTF-8"?><kml><Document><name>route-updated</name></Document></kml>'
    KMZ_CONTENT_TYPE = "application/vnd.google-earth.kmz"

    def setUp(self):
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

    @staticmethod
    def _build_test_kmz(*, template_bytes: bytes = VALID_TEMPLATE_BYTES, wpml_bytes: bytes = None) -> bytes:
        if wpml_bytes is None:
            wpml_bytes = template_bytes
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("template.kml", template_bytes)
            archive.writestr("waylines.wpml", wpml_bytes)
        return buffer.getvalue()

    def _upload_kmz_route(self, *, name="KMZ 航线", kmz_bytes=None):
        return self.client.post(
            "/api/v1/routes",
            {
                "name": name,
                "kmz_file": SimpleUploadedFile(
                    "route.kmz",
                    kmz_bytes if kmz_bytes is not None else self._build_test_kmz(),
                    content_type=self.KMZ_CONTENT_TYPE,
                ),
            },
            format="multipart",
        )

    def _response_body(self, response):
        return b"".join(response.streaming_content)

    def test_create_should_upload_kmz_and_persist_route_index(self):
        sentinel_wayline_id = "mock-wayline-kmz-create"
        sentinel_download_url = "https://upstream/download/create.kmz"
        kmz_bytes = self._build_test_kmz()

        with patch(
            "apps.route.views.DjiGateway.upload_route",
            return_value={"dji_wayline_id": sentinel_wayline_id, "download_url": sentinel_download_url},
        ) as upload_mock:
            response = self._upload_kmz_route(name="城市巡检 KMZ", kmz_bytes=kmz_bytes)

        self.assertEqual(response.status_code, 201, response.data)
        data = response.data["data"]
        self.assertEqual(data["name"], "城市巡检 KMZ")
        self.assertTrue(data["is_published"])
        route = Route.objects.get(id=data["id"])
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertTrue(route_index.is_published)
        self.assertEqual(route_index.dji_wayline_id, sentinel_wayline_id)
        self.assertEqual(route_index.download_url, sentinel_download_url)
        upload_mock.assert_called_once()
        uploaded_file = upload_mock.call_args.kwargs["file_obj"]
        uploaded_name = getattr(uploaded_file, "name", "")
        self.assertRegex(uploaded_name, rf"^{route.id}-城市巡检-KMZ-[0-9a-f]{{8}}\.kmz$")
        self.assertNotEqual(uploaded_name, "route.kmz")
        uploaded_file.seek(0)
        self.assertEqual(uploaded_file.read(), kmz_bytes)
        self.assertRegex(
            upload_mock.call_args.kwargs["route_name"],
            rf"^{route.id}-城市巡检-KMZ-[0-9a-f]{{8}}$",
        )

    def test_create_should_use_mock_upload_shape_and_traceable_upstream_name(self):
        response = self._upload_kmz_route(name="城市巡检 KMZ")

        self.assertEqual(response.status_code, 201, response.data)
        route = Route.objects.get(id=response.data["data"]["id"])
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertRegex(
            route_index.download_url,
            rf"^/api/v1/wayline/workspaces/mock-workspace-001/waylines/{re.escape(route_index.dji_wayline_id)}/url$",
        )

        created_wayline = mock_dji_state.waylines[route_index.dji_wayline_id]
        self.assertRegex(created_wayline["name"], rf"^{route.id}-城市巡检-KMZ-[0-9a-f]{{8}}$")

    def test_create_should_reject_non_zip_kmz_payload(self):
        response = self._upload_kmz_route(name="坏 KMZ", kmz_bytes=b"not-a-zip")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["data"], {"kmz_file": ["上传文件必须是有效 KMZ/ZIP 文件"]})

    def test_create_should_require_kmz_file(self):
        response = self.client.post(
            "/api/v1/routes",
            {"name": "缺少 KMZ"},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data.get("data"), {"kmz_file": ["未提交文件。"]})

    def test_put_should_replace_upstream_wayline_and_schedule_old_cleanup(self):
        route = Route.objects.create(tenant=self.tenant, name="更新前 KMZ")
        old_wayline_id = mock_dji_state.create_wayline(name="legacy-wayline")["wayline_id"]
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id=old_wayline_id,
            download_url=f"/api/v1/wayline/workspaces/mock-workspace-001/waylines/{old_wayline_id}/url",
            is_published=True,
        )
        new_wayline_id = "mock-wayline-kmz-update"
        new_download_url = "https://upstream/download/update.kmz"
        update_kmz_bytes = self._build_test_kmz(template_bytes=self.UPDATED_TEMPLATE_BYTES)

        with patch(
            "apps.route.views.DjiGateway.upload_route",
            return_value={"dji_wayline_id": new_wayline_id, "download_url": new_download_url},
        ) as upload_mock:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = self.client.put(
                    f"/api/v1/routes/{route.id}",
                    {
                        "name": "更新后 KMZ",
                        "kmz_file": SimpleUploadedFile(
                            "route-updated.kmz",
                            update_kmz_bytes,
                            content_type=self.KMZ_CONTENT_TYPE,
                        ),
                    },
                    format="multipart",
                )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["data"]["is_published"])
        route.refresh_from_db()
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertEqual(route_index.dji_wayline_id, new_wayline_id)
        self.assertEqual(route_index.download_url, new_download_url)
        self.assertTrue(route_index.is_published)
        upload_mock.assert_called_once()
        uploaded_file = upload_mock.call_args.kwargs["file_obj"]
        uploaded_name = getattr(uploaded_file, "name", "")
        self.assertRegex(uploaded_name, rf"^{route.id}-更新后-KMZ-[0-9a-f]{{8}}\.kmz$")
        self.assertNotEqual(uploaded_name, "route-updated.kmz")
        uploaded_file.seek(0)
        self.assertEqual(uploaded_file.read(), update_kmz_bytes)

        self.assertEqual(len(callbacks), 1)
        self.assertIn(old_wayline_id, mock_dji_state.waylines)
        for callback in callbacks:
            callback()
        self.assertNotIn(old_wayline_id, mock_dji_state.waylines)

    def test_put_should_reject_empty_kmz_file(self):
        route = Route.objects.create(tenant=self.tenant, name="空 KMZ")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="",
            download_url="",
            is_published=False,
        )

        response = self.client.put(
            f"/api/v1/routes/{route.id}",
            {
                "name": "空 KMZ 更新",
                "kmz_file": SimpleUploadedFile(
                    "empty.kmz",
                    b"",
                    content_type=self.KMZ_CONTENT_TYPE,
                ),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data.get("data"), {"kmz_file": ["提交的文件为空。"]})

    def test_kmz_download_should_proxy_saved_download_url(self):
        route = Route.objects.create(tenant=self.tenant, name="下载 KMZ")
        download_url = "https://upstream.example/downloads/downloadable.kmz"
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="mock-wayline-download",
            download_url=download_url,
            is_published=True,
        )

        with patch(
            "apps.route.views.DjiGateway.download_route_file",
            return_value=GatewayResponse(
                status_code=200,
                headers={"Content-Type": self.KMZ_CONTENT_TYPE},
                data=b"mock-kmz-binary",
            ),
        ) as download_mock:
            response = self.client.get(f"/api/v1/routes/{route.id}/kmz")

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.KMZ_CONTENT_TYPE, response["Content-Type"])
        self.assertIn(f"route-{route.id}.kmz", response["Content-Disposition"])
        self.assertEqual(self._response_body(response), b"mock-kmz-binary")
        download_mock.assert_called_once_with(download_url)

    def test_kmz_download_should_resolve_download_url_when_missing(self):
        route = Route.objects.create(tenant=self.tenant, name="缺少下载地址")
        resolved_download_url = "https://upstream.example/downloads/resolved.kmz"
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="mock-wayline-download-missing",
            download_url="",
            is_published=True,
        )

        with patch("apps.route.views.DjiGateway.get_route_download_url", return_value=resolved_download_url) as resolve_mock:
            with patch(
                "apps.route.views.DjiGateway.download_route_file",
                return_value=GatewayResponse(
                    status_code=200,
                    headers={"Content-Type": self.KMZ_CONTENT_TYPE},
                    data=b"resolved-kmz-binary",
                ),
            ) as download_mock:
                response = self.client.get(f"/api/v1/routes/{route.id}/kmz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._response_body(response), b"resolved-kmz-binary")
        resolve_mock.assert_called_once_with("mock-wayline-download-missing")
        download_mock.assert_called_once_with(resolved_download_url)

    def test_kmz_download_should_retry_with_resolved_download_url_after_upstream_404(self):
        route = Route.objects.create(tenant=self.tenant, name="上游丢失 KMZ")
        download_url = "https://upstream.example/downloads/stale.kmz"
        resolved_download_url = "https://upstream.example/downloads/fresh.kmz"
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="mock-wayline-download-missing-upstream",
            download_url=download_url,
            is_published=True,
        )

        with patch(
            "apps.route.views.DjiGateway.download_route_file",
            side_effect=[
                DjiGatewayUpstreamError("missing", status_code=404),
                GatewayResponse(
                    status_code=200,
                    headers={"Content-Type": self.KMZ_CONTENT_TYPE},
                    data=b"fresh-kmz-binary",
                ),
            ],
        ) as download_mock:
            with patch("apps.route.views.DjiGateway.get_route_download_url", return_value=resolved_download_url) as resolve_mock:
                response = self.client.get(f"/api/v1/routes/{route.id}/kmz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._response_body(response), b"fresh-kmz-binary")
        resolve_mock.assert_called_once_with("mock-wayline-download-missing-upstream")
        self.assertEqual(download_mock.call_count, 2)
        self.assertEqual(download_mock.call_args_list[0].args, (download_url,))
        self.assertEqual(download_mock.call_args_list[1].args, (resolved_download_url,))

    def test_removed_publish_and_xml_endpoints_should_return_404(self):
        route = Route.objects.create(tenant=self.tenant, name="移除接口检查")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="",
            download_url="",
            is_published=False,
        )

        publish_response = self.client.post(f"/api/v1/routes/{route.id}/publish")
        xml_response = self.client.get(f"/api/v1/routes/{route.id}/xml")

        self.assertEqual(publish_response.status_code, 404, getattr(publish_response, "data", publish_response.content))
        self.assertEqual(xml_response.status_code, 404, getattr(xml_response, "data", xml_response.content))

    def test_create_should_reject_legacy_waypoints_json_write_contract(self):
        response = self.client.post(
            "/api/v1/routes",
            {
                "name": "旧写入契约",
                "kmz_file": SimpleUploadedFile(
                    "legacy-create.kmz",
                    self._build_test_kmz(),
                    content_type=self.KMZ_CONTENT_TYPE,
                ),
                "route_type": 0,
                "drone_type_id": 2,
                "total_distance": "123.45",
                "estimated_duration": 60,
                "waypoints": "[]",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("waypoints", response.data["data"])
        self.assertIn("route_type", response.data["data"])
        self.assertIn("total_distance", response.data["data"])
        self.assertIn("estimated_duration", response.data["data"])
        self.assertIn("drone_type_id", response.data["data"])
        self.assertEqual(response.data["data"]["waypoints"], ["该字段在此接口不可写"])
        self.assertEqual(response.data["data"]["route_type"], ["该字段在此接口不可写"])
        self.assertEqual(response.data["data"]["total_distance"], ["该字段在此接口不可写"])
        self.assertEqual(response.data["data"]["estimated_duration"], ["该字段在此接口不可写"])
        self.assertEqual(response.data["data"]["drone_type_id"], ["该字段在此接口不可写"])

    def test_put_should_reject_legacy_waypoints_json_write_contract(self):
        route = Route.objects.create(tenant=self.tenant, name="旧 PUT 契约")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="",
            download_url="",
            is_published=False,
        )

        response = self.client.put(
            f"/api/v1/routes/{route.id}",
            {
                "name": "旧 PUT 契约更新",
                "kmz_file": SimpleUploadedFile(
                    "legacy-put.kmz",
                    self._build_test_kmz(template_bytes=self.UPDATED_TEMPLATE_BYTES),
                    content_type=self.KMZ_CONTENT_TYPE,
                ),
                "route_type": 0,
                "total_distance": "222.20",
                "estimated_duration": 120,
                "waypoints": "[]",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("waypoints", response.data["data"])
        self.assertIn("route_type", response.data["data"])
        self.assertIn("total_distance", response.data["data"])
        self.assertIn("estimated_duration", response.data["data"])
        self.assertEqual(response.data["data"]["waypoints"], ["该字段在此接口不可写"])
        self.assertEqual(response.data["data"]["route_type"], ["该字段在此接口不可写"])
        self.assertEqual(response.data["data"]["total_distance"], ["该字段在此接口不可写"])
        self.assertEqual(response.data["data"]["estimated_duration"], ["该字段在此接口不可写"])

    def test_create_should_delete_new_upstream_wayline_when_local_persist_fails(self):
        with patch("apps.route.views.TenantRouteIndex.objects.create", side_effect=RuntimeError("db boom")):
            response = self._upload_kmz_route(name="补偿删除 KMZ")

        self.assertEqual(response.status_code, 500, response.data)
        self.assertEqual(mock_dji_state.waylines, {})
        self.assertFalse(
            Route.objects.filter(tenant=self.tenant, name="补偿删除 KMZ").exists(),
            "Route should not persist when TenantRouteIndex creation fails",
        )

    def test_create_should_not_persist_route_when_upload_fails(self):
        kmz_bytes = self._build_test_kmz()
        with patch(
            "apps.route.views.DjiGateway.upload_route",
            side_effect=DjiGatewayUpstreamError("upload failed", status_code=502, data={"code": "E5000"}),
        ):
            response = self._upload_kmz_route(name="上传失败 KMZ", kmz_bytes=kmz_bytes)

        self.assertEqual(response.status_code, 500, response.data)
        self.assertFalse(
            Route.objects.filter(tenant=self.tenant, name="上传失败 KMZ").exists(),
            "Route should not remain when upstream upload fails",
        )

    def test_create_should_cleanup_upstream_and_not_persist_when_log_action_fails(self):
        kmz_bytes = self._build_test_kmz()

        with patch(
            "apps.route.views.log_action",
            side_effect=RuntimeError("log failure"),
        ):
            response = self._upload_kmz_route(name="日志失败 KMZ", kmz_bytes=kmz_bytes)

        self.assertEqual(response.status_code, 500, response.data)
        self.assertEqual(mock_dji_state.waylines, {})
        self.assertFalse(
            Route.objects.filter(tenant=self.tenant, name="日志失败 KMZ").exists(),
            "Route should not persist when log_action fails after upload",
        )

    def test_create_should_cleanup_upload_after_transaction_rollback_error(self):
        def _broken_sync(*args, **kwargs):
            transaction.set_rollback(True)
            raise OperationalError("database is locked")

        with patch("apps.route.views._sync_route_index", side_effect=_broken_sync):
            response = self._upload_kmz_route(name="回滚失败清理 KMZ")

        self.assertEqual(response.status_code, 500, response.data)
        self.assertEqual(mock_dji_state.waylines, {})
        self.assertFalse(
            Route.objects.filter(tenant=self.tenant, name="回滚失败清理 KMZ").exists(),
            "Route should not persist when database rollback is required after upload",
        )

    def test_create_should_upload_outside_database_transaction(self):
        baseline_atomic_depth = len(connection.atomic_blocks)
        seen_atomic_state: dict[str, int] = {}

        def _mock_upload(*, gateway, route_id, route_name, kmz_file):
            seen_atomic_state["atomic_depth"] = len(connection.atomic_blocks)
            return ("mock-wayline-id", "https://upstream/download/create.kmz")

        with patch("apps.route.views._upload_route_to_upstream", side_effect=_mock_upload):
            response = self._upload_kmz_route(name="事务外创建 KMZ")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(seen_atomic_state["atomic_depth"], baseline_atomic_depth)

    def test_put_should_keep_old_index_when_new_upload_fails(self):
        route = Route.objects.create(tenant=self.tenant, name="旧上游 KMZ")
        old_wayline = mock_dji_state.create_wayline(name="legacy-wayline")
        old_download_url = f"/api/v1/wayline/workspaces/mock-workspace-001/waylines/{old_wayline['wayline_id']}/url"
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id=old_wayline["wayline_id"],
            download_url=old_download_url,
            is_published=True,
        )
        update_kmz_bytes = self._build_test_kmz(template_bytes=self.UPDATED_TEMPLATE_BYTES)

        with patch(
            "apps.route.views.DjiGateway.upload_route",
            side_effect=DjiGatewayUpstreamError("upload failed", status_code=502, data={"code": "E5000"}),
        ) as upload_mock:
            response = self.client.put(
                f"/api/v1/routes/{route.id}",
                {
                    "name": "上传失败保留",
                    "kmz_file": SimpleUploadedFile(
                        "route-updated.kmz",
                        update_kmz_bytes,
                        content_type=self.KMZ_CONTENT_TYPE,
                    ),
                },
                format="multipart",
            )

        self.assertEqual(response.status_code, 500, response.data)
        upload_mock.assert_called_once()
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertEqual(route_index.dji_wayline_id, old_wayline["wayline_id"])
        self.assertEqual(route_index.download_url, old_download_url)
        self.assertTrue(route_index.is_published)
        self.assertIn(old_wayline["wayline_id"], mock_dji_state.waylines)

    def test_put_should_delete_new_upstream_wayline_when_index_persist_fails(self):
        route = Route.objects.create(tenant=self.tenant, name="局部失败 KMZ")
        old_wayline = mock_dji_state.create_wayline(name="legacy-wayline")
        old_download_url = f"/api/v1/wayline/workspaces/mock-workspace-001/waylines/{old_wayline['wayline_id']}/url"
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id=old_wayline["wayline_id"],
            download_url=old_download_url,
            is_published=True,
        )
        new_kmz_bytes = self._build_test_kmz(template_bytes=self.UPDATED_TEMPLATE_BYTES)

        upload_state: dict[str, str] = {}

        def _mock_upload(*, route_name, file_obj):
            wayline = mock_dji_state.create_wayline(name=route_name, file_name=getattr(file_obj, "name", "route.kmz"))
            download_url = f"/api/v1/wayline/workspaces/mock-workspace-001/waylines/{wayline['wayline_id']}/url"
            upload_state["wayline_id"] = wayline["wayline_id"]
            upload_state["download_url"] = download_url
            return {"dji_wayline_id": wayline["wayline_id"], "download_url": download_url}

        with patch("apps.route.views.DjiGateway.upload_route", side_effect=_mock_upload) as upload_mock:
            with patch("apps.route.views.TenantRouteIndex.save", side_effect=RuntimeError("db boom")):
                response = self.client.put(
                    f"/api/v1/routes/{route.id}",
                    {
                        "name": "持久化失败",
                        "kmz_file": SimpleUploadedFile(
                            "route-updated.kmz",
                            new_kmz_bytes,
                            content_type=self.KMZ_CONTENT_TYPE,
                        ),
                    },
                    format="multipart",
                )

        self.assertEqual(response.status_code, 500, response.data)
        self.assertEqual(upload_mock.call_count, 1)
        self.assertNotIn(upload_state.get("wayline_id"), mock_dji_state.waylines)
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertEqual(route_index.dji_wayline_id, old_wayline["wayline_id"])
        self.assertEqual(route_index.download_url, old_download_url)
        self.assertTrue(route_index.is_published)

    def test_put_should_cleanup_upload_and_keep_old_index_when_log_action_fails(self):
        route = Route.objects.create(tenant=self.tenant, name="旧上游 KMZ")
        old_wayline = mock_dji_state.create_wayline(name="legacy-wayline")
        old_download_url = f"/api/v1/wayline/workspaces/mock-workspace-001/waylines/{old_wayline['wayline_id']}/url"
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id=old_wayline["wayline_id"],
            download_url=old_download_url,
            is_published=True,
        )
        kmz_bytes = self._build_test_kmz(template_bytes=self.UPDATED_TEMPLATE_BYTES)

        upload_state: dict[str, str] = {}

        def _mock_upload(*, route_name, file_obj):
            wayline = mock_dji_state.create_wayline(name=route_name, file_name=getattr(file_obj, "name", "route.kmz"))
            download_url = f"/api/v1/wayline/workspaces/mock-workspace-001/waylines/{wayline['wayline_id']}/url"
            upload_state["wayline_id"] = wayline["wayline_id"]
            upload_state["download_url"] = download_url
            return {"dji_wayline_id": wayline["wayline_id"], "download_url": download_url}

        with patch("apps.route.views.DjiGateway.upload_route", side_effect=_mock_upload):
            with patch("apps.route.views.log_action", side_effect=RuntimeError("log failure")):
                response = self.client.put(
                    f"/api/v1/routes/{route.id}",
                    {
                        "name": "日志失败更新",
                        "kmz_file": SimpleUploadedFile(
                            "route-updated.kmz",
                            kmz_bytes,
                            content_type=self.KMZ_CONTENT_TYPE,
                        ),
                    },
                    format="multipart",
                )

        self.assertEqual(response.status_code, 500, response.data)
        self.assertIn("wayline_id", upload_state)
        self.assertNotIn(upload_state["wayline_id"], mock_dji_state.waylines)
        route.refresh_from_db()
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertEqual(route_index.dji_wayline_id, old_wayline["wayline_id"])
        self.assertEqual(route_index.download_url, old_download_url)
        self.assertTrue(route_index.is_published)
        self.assertIn(old_wayline["wayline_id"], mock_dji_state.waylines)

    def test_put_should_cleanup_upload_after_transaction_rollback_error(self):
        route = Route.objects.create(tenant=self.tenant, name="旧上游 KMZ")
        old_wayline = mock_dji_state.create_wayline(name="legacy-wayline")
        old_download_url = f"/api/v1/wayline/workspaces/mock-workspace-001/waylines/{old_wayline['wayline_id']}/url"
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id=old_wayline["wayline_id"],
            download_url=old_download_url,
            is_published=True,
        )
        new_kmz_bytes = self._build_test_kmz(template_bytes=self.UPDATED_TEMPLATE_BYTES)

        upload_state: dict[str, str] = {}

        def _mock_upload(*, route_name, file_obj):
            wayline = mock_dji_state.create_wayline(name=route_name, file_name=getattr(file_obj, "name", "route.kmz"))
            download_url = f"/api/v1/wayline/workspaces/mock-workspace-001/waylines/{wayline['wayline_id']}/url"
            upload_state["wayline_id"] = wayline["wayline_id"]
            upload_state["download_url"] = download_url
            return {"dji_wayline_id": wayline["wayline_id"], "download_url": download_url}

        def _broken_update(*args, **kwargs):
            transaction.set_rollback(True)
            raise OperationalError("database is locked")

        with patch("apps.route.views.DjiGateway.upload_route", side_effect=_mock_upload) as upload_mock:
            with patch("apps.route.serializers.RouteWriteSerializer.update", side_effect=_broken_update):
                response = self.client.put(
                    f"/api/v1/routes/{route.id}",
                    {
                        "name": "回滚失败更新",
                        "kmz_file": SimpleUploadedFile(
                            "route-updated.kmz",
                            new_kmz_bytes,
                            content_type=self.KMZ_CONTENT_TYPE,
                        ),
                    },
                    format="multipart",
                )

        self.assertEqual(response.status_code, 500, response.data)
        self.assertEqual(upload_mock.call_count, 1)
        self.assertNotIn(upload_state.get("wayline_id"), mock_dji_state.waylines)
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertEqual(route_index.dji_wayline_id, old_wayline["wayline_id"])
        self.assertEqual(route_index.download_url, old_download_url)
        self.assertTrue(route_index.is_published)

    def test_put_should_upload_outside_database_transaction(self):
        route = Route.objects.create(tenant=self.tenant, name="旧上游 KMZ")
        TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", download_url="", is_published=False)
        baseline_atomic_depth = len(connection.atomic_blocks)
        seen_atomic_state: dict[str, int] = {}

        def _mock_upload(*, gateway, route_id, route_name, kmz_file):
            seen_atomic_state["atomic_depth"] = len(connection.atomic_blocks)
            return ("mock-wayline-id", "https://upstream/download/update.kmz")

        with patch("apps.route.views._upload_route_to_upstream", side_effect=_mock_upload):
            response = self.client.put(
                f"/api/v1/routes/{route.id}",
                {
                    "name": "事务外更新 KMZ",
                    "kmz_file": SimpleUploadedFile(
                        "route-updated.kmz",
                        self._build_test_kmz(template_bytes=self.UPDATED_TEMPLATE_BYTES),
                        content_type=self.KMZ_CONTENT_TYPE,
                    ),
                },
                format="multipart",
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(seen_atomic_state["atomic_depth"], baseline_atomic_depth)

    def test_delete_should_best_effort_remove_current_upstream_wayline(self):
        route = Route.objects.create(tenant=self.tenant, name="删除 KMZ")
        wayline = mock_dji_state.create_wayline(name="delete-me")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id=wayline["wayline_id"],
            download_url=f"/api/v1/wayline/workspaces/mock-workspace-001/waylines/{wayline['wayline_id']}/url",
            is_published=True,
        )

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(callbacks), 2)
        self.assertFalse(Route.objects.filter(id=route.id).exists())
        self.assertIn(wayline["wayline_id"], mock_dji_state.waylines)
        for callback in callbacks:
            callback()
        self.assertNotIn(wayline["wayline_id"], mock_dji_state.waylines)

    def test_delete_should_ignore_upstream_delete_error_and_still_remove_route(self):
        route = Route.objects.create(tenant=self.tenant, name="忽略删除失败")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="mock-wayline-existing",
            download_url="/api/v1/wayline/workspaces/mock-workspace-001/waylines/mock-wayline-existing/url",
            is_published=True,
        )

        with patch(
            "apps.route.views.DjiGateway.delete_route",
            side_effect=DjiGatewayUpstreamError("delete failed", status_code=502, data={"code": "E5000"}),
        ) as delete_mock:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(Route.objects.filter(id=route.id).exists())
        delete_mock.assert_called_once_with("mock-wayline-existing")

    def test_delete_should_ignore_log_action_failure_and_still_remove_route(self):
        route = Route.objects.create(tenant=self.tenant, name="删除日志失败")
        wayline = mock_dji_state.create_wayline(name="delete-log-fail")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id=wayline["wayline_id"],
            download_url=f"/api/v1/wayline/workspaces/mock-workspace-001/waylines/{wayline['wayline_id']}/url",
            is_published=True,
        )

        with patch("apps.route.views.log_action", side_effect=RuntimeError("log failure")):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(Route.objects.filter(id=route.id).exists())
        self.assertNotIn(wayline["wayline_id"], mock_dji_state.waylines)

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

    def test_put_should_reject_when_bound_mission_uses_route(self):
        route = Route.objects.create(tenant=self.tenant, name="被占用航线")
        TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", download_url="", is_published=False)
        pilot_user = User.objects.create_user(username="route_bound_pilot", password="pass1234", status=1)
        ensure_staff_profile(pilot_user, name="绑定飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, pilot_member, _pilot_role = ensure_tenant_role_binding(
            pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="ROUTE-BOUND-DRONE-001",
            name="已绑定任务无人机",
            model="M30",
            device_sn="ROUTE-BOUND-SN-001",
        )
        Mission.objects.create(
            tenant=self.tenant,
            name="占用航线任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=pilot_member,
            pilot_name="绑定飞手",
            status=MissionStatus.PENDING,
        )

        response = self.client.put(
            f"/api/v1/routes/{route.id}",
            {
                "name": "尝试更新",
                "kmz_file": SimpleUploadedFile(
                    "route-updated.kmz",
                    self._build_test_kmz(template_bytes=self.UPDATED_TEMPLATE_BYTES),
                    content_type=self.KMZ_CONTENT_TYPE,
                ),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "B0001")

    def test_delete_should_reject_when_bound_mission_uses_route(self):
        route = Route.objects.create(tenant=self.tenant, name="已绑定任务航线")
        TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", is_published=False)
        pilot_user = User.objects.create_user(username="route_bound_delete_pilot", password="pass1234", status=1)
        ensure_staff_profile(pilot_user, name="绑定飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, pilot_member, _pilot_role = ensure_tenant_role_binding(
            pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="ROUTE-BOUND-DELETE-DRONE-001",
            name="删除阻断任务无人机",
            model="M30",
            device_sn="ROUTE-BOUND-DELETE-SN-001",
        )
        Mission.objects.create(
            tenant=self.tenant,
            name="已绑定任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=pilot_member,
            pilot_name="绑定飞手",
            status=MissionStatus.PENDING,
        )

        response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(Route.objects.filter(id=route.id).exists())

    def test_delete_should_ignore_unbound_mission_blocker(self):
        route = Route.objects.create(tenant=self.tenant, name="未绑定任务航线")
        TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", download_url="", is_published=False)
        pilot_user = User.objects.create_user(username="route_unbound_pilot", password="pass1234", status=1)
        ensure_staff_profile(pilot_user, name="未绑定飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, pilot_member, _pilot_role = ensure_tenant_role_binding(
            pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        Mission.objects.create(
            tenant=self.tenant,
            name="未绑定无人机任务",
            route=route,
            route_name=route.name,
            drone=None,
            pilot=pilot_member,
            pilot_name="未绑定飞手",
            status=MissionStatus.PENDING,
        )

        response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 200, response.data)

    def test_delete_should_ignore_soft_deleted_mission_blocker(self):
        route = Route.objects.create(tenant=self.tenant, name="已删除任务航线")
        TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", is_published=False)
        pilot_user = User.objects.create_user(username="route_deleted_pilot", password="pass1234", status=1)
        ensure_staff_profile(pilot_user, name="已删除任务飞手", employment_status=EmploymentStatus.ACTIVE)
        _tenant, pilot_member, _pilot_role = ensure_tenant_role_binding(
            pilot_user,
            tenant=self.tenant,
            role_code="pilot_operator",
            role_name="飞手",
        )
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="ROUTE-DELETED-DRONE-001",
            name="已删除任务无人机",
            model="M30",
            device_sn="ROUTE-DELETED-SN-001",
        )
        Mission.objects.create(
            tenant=self.tenant,
            name="已删除的绑定任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=pilot_member,
            pilot_name="已删除任务飞手",
            status=MissionStatus.PENDING,
            is_deleted=True,
            deleted_at=timezone.now(),
        )

        response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(Route.objects.filter(id=route.id).exists())

    def test_delete_should_cleanup_legacy_waypoint_rows(self):
        route = Route.objects.create(tenant=self.tenant, name="历史航点航线")
        TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", is_published=False)
        Waypoint.objects.create(
            route=route,
            sequence=1,
            latitude="22.54309600",
            longitude="114.05786500",
            altitude="80.00",
        )

        response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(Route.objects.filter(id=route.id).exists())
        self.assertFalse(Waypoint.objects.filter(route_id=route.id).exists())
class RouteScopeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner_user = User.objects.create_user(username="route_scope_owner", password="pass1234", status=1)
        ensure_staff_profile(self.owner_user, name="航线所有者", employment_status=EmploymentStatus.ACTIVE)
        self.tenant, self.owner_member, self.owner_role = ensure_tenant_role_binding(
            self.owner_user,
            tenant_code="route_scope_tenant",
            role_code="route_scope_owner_role",
            role_name="航线所有者角色",
        )
        grant_role_permissions(
            self.owner_role,
            {
                "route.view_route": ScopeType.ALL,
                "route.manage_route": ScopeType.ALL,
            },
        )

        self.route = Route.objects.create(tenant=self.tenant, name="范围收敛航线")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=self.route,
            dji_wayline_id="scope-wayline-001",
            is_published=True,
        )

        self.assigned_user = User.objects.create_user(username="route_scope_assigned", password="pass1234", status=1)
        ensure_staff_profile(self.assigned_user, name="范围成员", employment_status=EmploymentStatus.ACTIVE)
        _tenant, self.assigned_member, self.assigned_role = ensure_tenant_role_binding(
            self.assigned_user,
            tenant=self.tenant,
            role_code="route_scope_assigned_role",
            role_name="范围成员角色",
        )
        grant_role_permissions(self.assigned_role, {"route.view_route": ScopeType.ASSIGNED})

        self.client.force_authenticate(self.assigned_user)
        self.client.credentials(HTTP_X_TENANT_CODE=self.tenant.code)

    def test_assigned_scope_should_not_list_routes_without_assignment_semantics(self):
        response = self.client.get("/api/v1/routes")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["total"], 0)
        self.assertEqual(response.data["data"]["list"], [])

    def test_assigned_scope_should_not_retrieve_routes_without_assignment_semantics(self):
        response = self.client.get(f"/api/v1/routes/{self.route.id}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "C0404")
