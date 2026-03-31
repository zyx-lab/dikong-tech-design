# Route XML Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor route CRUD so uploaded XML is the only editable route source, store that XML through Django `FileField` + `default_storage`, expose XML readback, and publish by converting the stored XML into KMZ before uploading to DJI.

**Architecture:** Replace the current waypoint-backed public route contract with a file-backed contract centered on `Route.xml_file`. Keep `TenantRouteIndex` as the minimal publish-state bridge, stream XML back through a dedicated route action instead of raw media URLs, and isolate XML-to-KMZ packaging inside `apps/route/services.py` so the publish path has one clear responsibility. Route runtime code must stop importing `apps.waypoint`, but the legacy waypoint app can remain installed in this pass to avoid unnecessary migration churn beyond the accepted route contract change.

**Tech Stack:** Django 5.1.6, Django REST Framework 3.15.2, drf-spectacular, Django `FileField` / `default_storage`, Python `xml.etree.ElementTree`, Python `zipfile`, Mock DJI upstream, local `.venv`

---

## File Structure

**Create**
- `apps/route/migrations/0006_route_xml_source.py`

**Modify**
- `config/settings.py`
- `apps/route/models.py`
- `apps/route/serializers.py`
- `apps/route/services.py`
- `apps/route/views.py`
- `apps/route/tests.py`
- `apps/route/test_live_api.py`
- `apps/api_v1/tests.py`
- `apps/mission/tests.py`
- `apps/mission/test_live_api.py`
- `apps/flight_record/tests.py`
- `apps/flight_record/test_live_api.py`
- `apps/media_file/tests.py`
- `apps/media_file/test_live_api.py`
- `apps/dji_bff/tests.py`
- `README.md`
- `业务侧实现/route_impl_desc.md`
- `业务侧实现/route_data_dictionary.md`
- `业务侧实现/route_logical_model.md`
- `业务侧实现/waypoint_impl_desc.md`
- `业务侧实现/waypoint_data_dictionary.md`
- `业务侧实现/waypoint_logical_model.md`
- `项目总体概览/逻辑设计/overall_data_dictionary.md`
- `项目总体概览/逻辑设计/overall_logical_model.md`

**Test**
- `apps/route/tests.py`
- `apps/route/test_live_api.py`
- `apps/api_v1/tests.py`
- `apps/mission/tests.py`
- `apps/mission/test_live_api.py`
- `apps/flight_record/tests.py`
- `apps/flight_record/test_live_api.py`
- `apps/media_file/tests.py`
- `apps/media_file/test_live_api.py`
- `apps/dji_bff/tests.py`

### Why These Files

- `config/settings.py` needs a real `MEDIA_ROOT` / `MEDIA_URL` baseline for local default-storage behavior and must stop advertising removed Route enums to drf-spectacular.
- `apps/route/models.py`, `serializers.py`, `views.py`, and `services.py` are the entire active route runtime path today.
- `apps/route/tests.py` and `apps/route/test_live_api.py` are the contract tests that must be rewritten from waypoint JSON to XML upload / XML readback semantics.
- `apps/api_v1/tests.py` must reflect the schema path change from `/download` to `/xml` and the removal of `PATCH`.
- `apps/mission/*`, `apps/flight_record/*`, `apps/media_file/*`, and `apps/dji_bff/tests.py` still construct `Route` objects with removed fields like `creator_name`, so they must be aligned after the model shrink.
- The route and waypoint docs plus overall logical/data dictionary docs are canonical descriptions that would otherwise drift immediately after the refactor.

### Task 1: Lock the XML-Only Route Contract With Failing Tests

**Files:**
- Modify: `apps/route/tests.py`
- Modify: `apps/route/test_live_api.py`
- Modify: `apps/api_v1/tests.py`
- Test: `apps/route/tests.py`
- Test: `apps/route/test_live_api.py`
- Test: `apps/api_v1/tests.py`

- [ ] **Step 1: Replace the waypoint-oriented route tests with XML-oriented failing tests**

Update `apps/route/tests.py` imports and test setup to use uploaded XML files and isolated local media storage:

```python
import shutil
import tempfile
from io import BytesIO
from zipfile import ZipFile

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.models import EmploymentStatus, ScopeType, Tenant, TenantStatus
```

Add this helper pattern inside the route API test class:

```python
class RouteXmlSourceApiTests(MockDjiUpstreamTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.media_root = tempfile.mkdtemp(prefix="route-xml-tests-")
        self.media_override = override_settings(MEDIA_ROOT=self.media_root, MEDIA_URL="/media/")
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)

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

    def xml_upload(self, body=b"<route><mission /></route>", name="route.xml"):
        return SimpleUploadedFile(name, body, content_type="application/xml")
```

Add these contract tests in `apps/route/tests.py`:

```python
def test_create_should_store_xml_file_and_mark_route_unpublished(self):
    response = self.client.post(
        "/api/v1/routes",
        {"name": "城市巡检航线", "xml_file": self.xml_upload()},
        format="multipart",
    )

    self.assertEqual(response.status_code, 201)
    data = response.data["data"]
    self.assertEqual(sorted(data.keys()), ["created_at", "id", "is_published", "name", "updated_at"])

    route = Route.objects.get(id=data["id"])
    self.assertTrue(route.xml_file.name.endswith(".xml"))
    self.assertTrue(default_storage.exists(route.xml_file.name))
    self.assertFalse(TenantRouteIndex.objects.get(route=route).is_published)
```

```python
def test_create_should_reject_non_parseable_xml(self):
    response = self.client.post(
        "/api/v1/routes",
        {"name": "坏 XML 航线", "xml_file": self.xml_upload(body=b"<route>", name="broken.xml")},
        format="multipart",
    )

    self.assertEqual(response.status_code, 400)
    self.assertEqual(response.data["code"], "B0001")
    self.assertEqual(response.data["data"], {"xml_file": ["上传文件必须是可解析 XML"]})
```

```python
def test_detail_and_xml_readback_should_hide_waypoints_and_return_raw_xml(self):
    route = Route.objects.create(tenant=self.tenant, name="细节航线")
    route.xml_file.save("detail.xml", ContentFile(b"<route><name>detail</name></route>"), save=True)
    TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", is_published=False)

    detail_response = self.client.get(f"/api/v1/routes/{route.id}")
    xml_response = self.client.get(f"/api/v1/routes/{route.id}/xml")

    self.assertEqual(detail_response.status_code, 200)
    self.assertNotIn("waypoints", detail_response.data["data"])
    self.assertEqual(xml_response.status_code, 200)
    self.assertEqual(xml_response["Content-Type"], "application/xml")
    self.assertEqual(b"".join(xml_response.streaming_content), b"<route><name>detail</name></route>")
```

```python
def test_put_should_replace_full_xml_reset_publish_and_delete_old_local_file(self):
    route = Route.objects.create(tenant=self.tenant, name="待替换航线")
    route.xml_file.save("old.xml", ContentFile(b"<route><old /></route>"), save=True)
    route_index = TenantRouteIndex.objects.create(
        tenant=self.tenant,
        route=route,
        dji_wayline_id="mock-wayline-existing",
        is_published=True,
    )
    old_name = route.xml_file.name

    response = self.client.put(
        f"/api/v1/routes/{route.id}",
        {"name": "已替换航线", "xml_file": self.xml_upload(body=b"<route><new /></route>", name="new.xml")},
        format="multipart",
    )

    self.assertEqual(response.status_code, 200)
    route.refresh_from_db()
    route_index.refresh_from_db()
    self.assertEqual(route.name, "已替换航线")
    self.assertFalse(route_index.is_published)
    self.assertNotEqual(route.xml_file.name, old_name)
    self.assertFalse(default_storage.exists(old_name))
```

```python
def test_patch_and_download_should_be_removed(self):
    route = Route.objects.create(tenant=self.tenant, name="无 patch 航线")
    route.xml_file.save("route.xml", ContentFile(b"<route />"), save=True)
    TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", is_published=False)

    patch_response = self.client.patch(f"/api/v1/routes/{route.id}", {"name": "x"}, format="json")
    download_response = self.client.get(f"/api/v1/routes/{route.id}/download")

    self.assertEqual(patch_response.status_code, 405)
    self.assertEqual(download_response.status_code, 404)
```

Add these publish-facing tests now, still expecting red before implementation:

```python
def test_publish_should_convert_stored_xml_to_kmz_and_mark_published(self):
    route = Route.objects.create(tenant=self.tenant, name="待发布航线")
    route.xml_file.save("publish.xml", ContentFile(b"<route><mission /></route>"), save=True)
    TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", is_published=False)

    response = self.client.post(f"/api/v1/routes/{route.id}/publish")

    self.assertEqual(response.status_code, 200)
    route_index = TenantRouteIndex.objects.get(route=route)
    self.assertTrue(route_index.is_published)
    self.assertTrue(route_index.dji_wayline_id.startswith("mock-wayline-"))
    uploaded = mock_dji_state.waylines[route_index.dji_wayline_id]
    self.assertTrue(uploaded["file_name"].endswith(".kmz"))
```

```python
def test_publish_should_return_400_when_stored_xml_is_invalid(self):
    route = Route.objects.create(tenant=self.tenant, name="坏草稿")
    route.xml_file.save("broken.xml", ContentFile(b"<route>"), save=True)
    TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", is_published=False)

    response = self.client.post(f"/api/v1/routes/{route.id}/publish")

    self.assertEqual(response.status_code, 400)
    self.assertEqual(response.data["code"], "B0001")
    self.assertEqual(response.data["msg"], "当前 XML 草稿无法转换为可发布 KMZ")
```

Update `apps/route/test_live_api.py` to use multipart XML upload and XML readback:

```python
from django.core.files.uploadedfile import SimpleUploadedFile
```

```python
class LiveRouteXmlSourceApiTests(LiveDjiGatewayApiTestCase):
    def xml_upload(self, body=b"<route><mission /></route>", name="live.xml"):
        return SimpleUploadedFile(name, body, content_type="application/xml")

    def test_route_create_detail_and_xml_should_follow_http_contract(self):
        create_response = self.client.post(
            "/api/v1/routes",
            {"name": "实时航线", "xml_file": self.xml_upload()},
            format="multipart",
        )
        self.assertEqual(create_response.status_code, 201)
        route_id = create_response.json()["data"]["id"]

        detail_response = self.client.get(f"/api/v1/routes/{route_id}")
        self.assertEqual(detail_response.status_code, 200)
        self.assertNotIn("waypoints", detail_response.json()["data"])

        xml_response = self.client.get(f"/api/v1/routes/{route_id}/xml")
        self.assertEqual(xml_response.status_code, 200)
        self.assertEqual(b"".join(xml_response.streaming_content).decode(), "<route><mission /></route>")

        self.assertEqual(self.client.patch(f"/api/v1/routes/{route_id}", {"name": "x"}, format="json").status_code, 405)
        self.assertEqual(self.client.get(f"/api/v1/routes/{route_id}/download").status_code, 404)
```

Update `apps/api_v1/tests.py` route path assertions:

```python
self.assertIn("/api/v1/routes/{id}/xml", paths)
self.assertNotIn("/api/v1/routes/{id}/download", paths)
self.assertNotIn("patch", paths["/api/v1/routes/{id}"])
```

- [ ] **Step 2: Run the new contract tests and confirm they fail**

Run:

```bash
.venv/bin/python manage.py test \
  apps.route.tests.RouteXmlSourceApiTests \
  apps.route.test_live_api.LiveRouteXmlSourceApiTests \
  apps.api_v1.tests.OpenApiDocsTests \
  -v 2
```

Expected:
- `FAIL`
- `POST /api/v1/routes` still rejects `xml_file`
- `/api/v1/routes/{id}/xml` is missing
- `PATCH` and `/download` are still present
- publish tests in `apps/route/tests.py` still read waypoint rows instead of stored XML

- [ ] **Step 3: Commit the failing-test checkpoint**

Run:

```bash
git add apps/route/tests.py apps/route/test_live_api.py apps/api_v1/tests.py
git commit -m "test: lock route xml source contract"
```

### Task 2: Implement File-Backed Route CRUD and XML Readback

**Files:**
- Modify: `config/settings.py`
- Modify: `apps/route/models.py`
- Create: `apps/route/migrations/0006_route_xml_source.py`
- Modify: `apps/route/serializers.py`
- Modify: `apps/route/views.py`
- Test: `apps/route/tests.py`
- Test: `apps/route/test_live_api.py`

- [ ] **Step 1: Add local default-storage settings and shrink the `Route` model**

Update `config/settings.py`:

```python
STATIC_URL = "static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "access.User"
```

Remove the deleted Route enum overrides from `SPECTACULAR_SETTINGS`:

```python
    "ENUM_NAME_OVERRIDES": {
        "ActiveDisabledStatusEnum": "apps.access.models.DirectoryStatus",
        "DroneAssignmentStatusEnum": "apps.drone_assignment.models.DroneAssignmentStatus",
        "DroneStatusEnum": "apps.drone.models.DroneStatus",
        "FlightRecordStatusEnum": "apps.flight_record.models.FlightRecordStatus",
        "MediaTypeEnum": "apps.media_file.models.MediaType",
        "MissionStatusEnum": "apps.mission.models.MissionStatus",
    },
```

Replace `apps/route/models.py` with the XML-backed shape:

```python
from django.db import models


class Route(models.Model):
    tenant = models.ForeignKey(
        "access.Tenant",
        on_delete=models.CASCADE,
        related_name="routes",
        verbose_name="租户",
    )
    name = models.CharField("航线名称", max_length=100)
    xml_file = models.FileField("XML 文件", upload_to="routes/xml", blank=True, default="")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        db_table = "routes"
        ordering = ["-id"]
        default_permissions = ()
        permissions = [
            ("view_route", "可查看航线"),
            ("manage_route", "可新增与编辑航线"),
        ]

    def __str__(self):
        return f"{self.id}-{self.name}"
```

Create `apps/route/migrations/0006_route_xml_source.py`:

```python
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("route", "0005_alter_route_tenant"),
    ]

    operations = [
        migrations.RemoveField(model_name="route", name="creator_name"),
        migrations.RemoveField(model_name="route", name="drone_type_id"),
        migrations.RemoveField(model_name="route", name="estimated_duration"),
        migrations.RemoveField(model_name="route", name="route_type"),
        migrations.RemoveField(model_name="route", name="total_distance"),
        migrations.RemoveField(model_name="route", name="waypoint_count"),
        migrations.AddField(
            model_name="route",
            name="xml_file",
            field=models.FileField(blank=True, default="", upload_to="routes/xml", verbose_name="XML 文件"),
        ),
    ]
```

- [ ] **Step 2: Replace route serializers with `name + xml_file` only**

Replace `apps/route/serializers.py` with:

```python
import xml.etree.ElementTree as ET

from rest_framework import serializers

from apps.api_v1.serializers import RejectUnknownFieldsMixin
from apps.route.models import Route


class RouteReadSerializer(serializers.ModelSerializer):
    is_published = serializers.SerializerMethodField()

    class Meta:
        model = Route
        fields = ["id", "name", "is_published", "created_at", "updated_at"]
        read_only_fields = fields

    def get_is_published(self, obj) -> bool:
        return bool(getattr(getattr(obj, "dji_index", None), "is_published", False))


class RouteWriteSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    xml_file = serializers.FileField(required=True, allow_empty_file=False)

    class Meta:
        model = Route
        fields = ["name", "xml_file"]

    def validate_xml_file(self, value):
        raw = value.read()
        try:
            ET.fromstring(raw)
        except ET.ParseError as exc:
            raise serializers.ValidationError("上传文件必须是可解析 XML") from exc
        value.seek(0)
        return value


class RouteCreateSerializer(RouteWriteSerializer):
    pass


class RouteUpdateSerializer(RouteWriteSerializer):
    pass
```

- [ ] **Step 3: Refactor `RouteViewSet` to XML-only list/retrieve/create/update/delete/xml**

Update `apps/route/views.py` imports and class shape:

```python
from pathlib import Path

from django.core.files.storage import default_storage
from django.db import transaction
from django.http import FileResponse
from rest_framework import mixins, parsers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
```

Use the new filter surface and methods:

```python
ROUTE_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(name="name", type=str, location=OpenApiParameter.QUERY, description="按航线名称模糊匹配。"),
]
```

```python
class RouteViewSet(...):
    queryset = Route.objects.select_related("dji_index").all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    http_method_names = ["get", "post", "put", "delete", "head", "options"]

    permission_map = {
        "list": "route.view_route",
        "retrieve": "route.view_route",
        "xml": "route.view_route",
        "create": "route.manage_route",
        "update": "route.manage_route",
        "publish": "route.manage_route",
        "destroy": "route.manage_route",
    }
```

Create/update logic should no longer touch waypoint rows:

```python
    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset())
        if self.request.query_params.get("name"):
            queryset = queryset.filter(name__icontains=self.request.query_params["name"])
        return queryset

    @transaction.atomic
    def perform_create(self, serializer):
        route = serializer.save(tenant=self.get_current_tenant())
        route_index = TenantRouteIndex.objects.create(
            tenant=self.get_current_tenant(),
            route=route,
            dji_wayline_id="",
            is_published=False,
        )
        route.dji_index = route_index
        log_action(
            request=self.request,
            action="ROUTE_CREATE",
            target_type="route",
            target_id=route.id,
            after_data=self._payload(route),
        )
        return route

    @transaction.atomic
    def perform_update(self, serializer):
        route = self.get_object()
        before_data = self._payload(route)
        old_xml_name = route.xml_file.name
        route = serializer.save()
        route_index, _ = TenantRouteIndex.objects.get_or_create(
            tenant=self.get_current_tenant(),
            route=route,
            defaults={"is_published": False},
        )
        route_index.is_published = False
        route_index.save(update_fields=["is_published", "updated_at"])
        if old_xml_name and old_xml_name != route.xml_file.name:
            default_storage.delete(old_xml_name)
        log_action(
            request=self.request,
            action="ROUTE_UPDATE",
            target_type="route",
            target_id=route.id,
            before_data=before_data,
            after_data=self._payload(route),
        )
        return route
```

Add XML readback and local XML cleanup:

```python
    @action(detail=True, methods=["get"])
    def xml(self, request, *args, **kwargs):
        route = self.get_object()
        response = FileResponse(route.xml_file.open("rb"), content_type="application/xml")
        response["Content-Disposition"] = f'attachment; filename="{Path(route.xml_file.name).name}"'
        return response

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        error_response = _reject_request_body_if_present(request, message="DELETE 请求不支持提交 body 参数")
        if error_response is not None:
            return error_response

        route = self.get_object()
        if Mission.objects.filter(
            tenant=self.get_current_tenant(),
            route=route,
            status__in=[MissionStatus.PENDING, MissionStatus.RUNNING],
        ).exists():
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "航线正在被任务使用，无法删除",
                    {"route_id": route.id},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        before_data = snapshot(route)
        route_index = getattr(route, "dji_index", None)
        xml_name = route.xml_file.name
        route_id = route.id
        route.delete()
        if xml_name:
            default_storage.delete(xml_name)
        if route_index is not None and route_index.dji_wayline_id:
            try:
                DjiGateway().delete_route(route_index.dji_wayline_id)
            except DjiGatewayUpstreamError as exc:
                if exc.status_code != 404:
                    raise
        log_action(
            request=request,
            action="ROUTE_DELETE",
            target_type="route",
            target_id=route_id,
            before_data=before_data,
            after_data={"id": route_id, "deleted": True},
        )
        return Response({"id": route_id, "deleted": True}, status=status.HTTP_200_OK)
```

Do not implement `partial_update` or `download`.

- [ ] **Step 4: Run the CRUD / XML-readback contract tests and make sure they pass**

Run:

```bash
.venv/bin/python manage.py test \
  apps.route.tests.RouteXmlSourceApiTests.test_create_should_store_xml_file_and_mark_route_unpublished \
  apps.route.tests.RouteXmlSourceApiTests.test_create_should_reject_non_parseable_xml \
  apps.route.tests.RouteXmlSourceApiTests.test_detail_and_xml_readback_should_hide_waypoints_and_return_raw_xml \
  apps.route.tests.RouteXmlSourceApiTests.test_put_should_replace_full_xml_reset_publish_and_delete_old_local_file \
  apps.route.tests.RouteXmlSourceApiTests.test_patch_and_download_should_be_removed \
  apps.route.test_live_api.LiveRouteXmlSourceApiTests.test_route_create_detail_and_xml_should_follow_http_contract \
  -v 2
```

Expected:
- `OK`
- publish-related tests still fail until Task 3

- [ ] **Step 5: Commit the XML-backed CRUD refactor**

Run:

```bash
git add config/settings.py apps/route/models.py apps/route/migrations/0006_route_xml_source.py apps/route/serializers.py apps/route/views.py
git commit -m "refactor: store route drafts as xml files"
```

### Task 3: Publish Stored XML as KMZ and Remove Waypoint Runtime Dependence

**Files:**
- Modify: `apps/route/services.py`
- Modify: `apps/route/views.py`
- Modify: `apps/route/tests.py`
- Modify: `apps/route/test_live_api.py`
- Test: `apps/route/tests.py`
- Test: `apps/route/test_live_api.py`

- [ ] **Step 1: Add focused service and publish-branch tests**

Add a service-level packaging test to `apps/route/tests.py`:

```python
class RouteXmlPackagingTests(TestCase):
    def test_build_route_kmz_from_xml_should_wrap_xml_bytes_in_kmz_archive(self):
        tenant = Tenant.objects.create(code="route_xml_pkg_tenant", name="Route XML 包装租户", status=TenantStatus.ACTIVE)
        route = Route.objects.create(tenant=tenant, name="包装航线")
        route.xml_file.save("pack.xml", ContentFile(b"<route><node /></route>"), save=True)

        kmz_file = build_route_kmz_from_xml(route)

        self.assertTrue(kmz_file.name.endswith(".kmz"))
        archive = ZipFile(BytesIO(kmz_file.read()))
        self.assertEqual(archive.namelist(), ["route.xml"])
        self.assertEqual(archive.read("route.xml"), b"<route><node /></route>")
```

Add these route API tests:

```python
def test_publish_should_reject_body_parameters(self):
    route = Route.objects.create(tenant=self.tenant, name="禁止 body 航线")
    route.xml_file.save("publish.xml", ContentFile(b"<route />"), save=True)
    TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", is_published=False)

    response = self.client.post(f"/api/v1/routes/{route.id}/publish", {"unexpected": True}, format="json")

    self.assertEqual(response.status_code, 400)
    self.assertEqual(response.data["code"], "B0001")
```

```python
def test_publish_should_replace_old_upstream_wayline_after_success(self):
    route = Route.objects.create(tenant=self.tenant, name="替换上游航线")
    route.xml_file.save("replace.xml", ContentFile(b"<route><replace /></route>"), save=True)
    old_upstream = mock_dji_state.create_wayline(name="legacy-upstream")["wayline_id"]
    TenantRouteIndex.objects.create(
        tenant=self.tenant,
        route=route,
        dji_wayline_id=old_upstream,
        is_published=False,
    )

    response = self.client.post(f"/api/v1/routes/{route.id}/publish")

    self.assertEqual(response.status_code, 200)
    route_index = TenantRouteIndex.objects.get(route=route)
    self.assertNotEqual(route_index.dji_wayline_id, old_upstream)
    self.assertNotIn(old_upstream, mock_dji_state.waylines)
```

Add this live publish test to `apps/route/test_live_api.py`:

```python
def test_route_publish_should_follow_http_contract(self):
    create_response = self.client.post(
        "/api/v1/routes",
        {"name": "实时发布航线", "xml_file": self.xml_upload()},
        format="multipart",
    )
    route_id = create_response.json()["data"]["id"]

    publish_response = self.client.post(f"/api/v1/routes/{route_id}/publish")

    self.assertEqual(publish_response.status_code, 200)
    self.assertTrue(publish_response.json()["data"]["is_published"])
```

Add a delete cleanup assertion to the delete test:

```python
def test_delete_should_remove_local_xml_file(self):
    route = Route.objects.create(tenant=self.tenant, name="删除 XML 航线")
    route.xml_file.save("delete.xml", ContentFile(b"<route><delete /></route>"), save=True)
    TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", is_published=False)
    xml_name = route.xml_file.name

    response = self.client.delete(f"/api/v1/routes/{route.id}")

    self.assertEqual(response.status_code, 200)
    self.assertFalse(default_storage.exists(xml_name))
```

- [ ] **Step 2: Implement XML-to-KMZ packaging in `apps/route/services.py`**

Replace the current waypoint helpers in `apps/route/services.py` with:

```python
from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET

from django.core.files.uploadedfile import SimpleUploadedFile


def build_route_kmz_from_xml(route):
    if not route.xml_file:
        raise ValueError("route xml file missing")

    with route.xml_file.open("rb") as file_obj:
        raw = file_obj.read()

    try:
        ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("stored route xml is invalid") from exc

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("route.xml", raw)

    return SimpleUploadedFile(
        name=f"route-{route.id}.kmz",
        content=buffer.getvalue(),
        content_type="application/vnd.google-earth.kmz",
    )
```

- [ ] **Step 3: Refactor `publish` to read `Route.xml_file` instead of waypoint rows**

Update `apps/route/views.py` imports:

```python
from apps.route.services import build_route_kmz_from_xml
```

Replace the `publish` action body with:

```python
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def publish(self, request, *args, **kwargs):
        error_response = _reject_request_body_if_present(request, message="publish 请求不支持提交 body 参数")
        if error_response is not None:
            return error_response

        route = self.get_object()
        before_data = self._payload(route)
        route_index, _ = TenantRouteIndex.objects.get_or_create(
            tenant=self.get_current_tenant(),
            route=route,
            defaults={"is_published": False},
        )
        old_wayline_id = route_index.dji_wayline_id

        try:
            kmz_file = build_route_kmz_from_xml(route)
        except ValueError:
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "当前 XML 草稿无法转换为可发布 KMZ",
                    {"route_id": route.id},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        upstream_payload = DjiGateway().upload_route(
            route_name=f"route-{route.id}-{uuid.uuid4().hex}",
            file_obj=kmz_file,
        )
        route_index.dji_wayline_id = upstream_payload["dji_wayline_id"]
        route_index.is_published = True
        route_index.save(update_fields=["dji_wayline_id", "is_published", "updated_at"])
        route.dji_index = route_index

        if old_wayline_id and old_wayline_id != route_index.dji_wayline_id:
            try:
                DjiGateway().delete_route(old_wayline_id)
            except DjiGatewayUpstreamError as exc:
                if exc.status_code != 404:
                    raise

        log_action(
            request=request,
            action="ROUTE_PUBLISH",
            target_type="route",
            target_id=route.id,
            before_data=before_data,
            after_data=self._payload(route),
        )
        return _route_success_response(self, route, http_status=status.HTTP_200_OK)
```

Verify there are no runtime imports of `Waypoint` left under `apps/route`:

```bash
rg -n "\\bWaypoint\\b|waypoint_rows|replace_route_waypoints" apps/route
```

Expected:
- no matches

- [ ] **Step 4: Run publish-focused tests and make sure they pass**

Run:

```bash
.venv/bin/python manage.py test \
  apps.route.tests.RouteXmlPackagingTests \
  apps.route.tests.RouteXmlSourceApiTests.test_publish_should_convert_stored_xml_to_kmz_and_mark_published \
  apps.route.tests.RouteXmlSourceApiTests.test_publish_should_return_400_when_stored_xml_is_invalid \
  apps.route.tests.RouteXmlSourceApiTests.test_publish_should_reject_body_parameters \
  apps.route.tests.RouteXmlSourceApiTests.test_publish_should_replace_old_upstream_wayline_after_success \
  apps.route.tests.RouteXmlSourceApiTests.test_delete_should_remove_local_xml_file \
  apps.route.test_live_api.LiveRouteXmlSourceApiTests.test_route_publish_should_follow_http_contract \
  -v 2
```

Expected:
- `OK`

- [ ] **Step 5: Commit the publish-path refactor**

Run:

```bash
git add apps/route/services.py apps/route/views.py apps/route/tests.py apps/route/test_live_api.py
git commit -m "refactor: publish routes from stored xml"
```

### Task 4: Align Schema, Cross-App Tests, and Canonical Documentation

**Files:**
- Modify: `apps/api_v1/tests.py`
- Modify: `apps/mission/tests.py`
- Modify: `apps/mission/test_live_api.py`
- Modify: `apps/flight_record/tests.py`
- Modify: `apps/flight_record/test_live_api.py`
- Modify: `apps/media_file/tests.py`
- Modify: `apps/media_file/test_live_api.py`
- Modify: `apps/dji_bff/tests.py`
- Modify: `README.md`
- Modify: `业务侧实现/route_impl_desc.md`
- Modify: `业务侧实现/route_data_dictionary.md`
- Modify: `业务侧实现/route_logical_model.md`
- Modify: `业务侧实现/waypoint_impl_desc.md`
- Modify: `业务侧实现/waypoint_data_dictionary.md`
- Modify: `业务侧实现/waypoint_logical_model.md`
- Modify: `项目总体概览/逻辑设计/overall_data_dictionary.md`
- Modify: `项目总体概览/逻辑设计/overall_logical_model.md`
- Test: `apps/api_v1/tests.py`
- Test: `apps/mission/tests.py`
- Test: `apps/mission/test_live_api.py`
- Test: `apps/flight_record/tests.py`
- Test: `apps/flight_record/test_live_api.py`
- Test: `apps/media_file/tests.py`
- Test: `apps/media_file/test_live_api.py`
- Test: `apps/dji_bff/tests.py`

- [ ] **Step 1: Remove old `Route(...)` field usage from non-route tests**

Replace old route construction patterns like:

```python
self.route = Route.objects.create(tenant=self.tenant, name="任务航线", creator_name="管理员")
```

with:

```python
self.route = Route.objects.create(tenant=self.tenant, name="任务航线")
```

Apply that exact simplification in:

- `apps/mission/tests.py`
- `apps/mission/test_live_api.py`
- `apps/flight_record/tests.py`
- `apps/flight_record/test_live_api.py`
- `apps/media_file/tests.py`
- `apps/media_file/test_live_api.py`
- `apps/dji_bff/tests.py`

Verify the old field is gone from runtime code and tests:

```bash
rg -n "creator_name|route_type|waypoint_count|drone_type_id|estimated_duration|total_distance" apps
```

Expected:
- matches only inside migrations and historical docs, not active runtime code or active tests

- [ ] **Step 2: Finish schema assertions for the new route surface**

Update `apps/api_v1/tests.py`:

```python
    def test_business_schema_should_expose_refactored_paths(self):
        response = self.client.get("/api/v1/docs/schema/")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        paths = schema["paths"]

        self.assertIn("/api/v1/drones", paths)
        self.assertIn("/api/v1/drones/available", paths)
        self.assertIn("/api/v1/drones/{id}/live/capacity", paths)
        self.assertIn("/api/v1/drones/{id}/live/start", paths)
        self.assertIn("/api/v1/routes/{id}/xml", paths)
        self.assertIn("/api/v1/missions/{id}/cancel", paths)
        self.assertIn("/api/v1/media-files/{id}/download", paths)
        self.assertIn("/api/v1/drone-assignments/{id}/cancel", paths)
```

```python
    def test_business_schema_should_not_expose_removed_paths(self):
        response = self.client.get("/api/v1/docs/schema/")
        self.assertEqual(response.status_code, 200)
        paths = response.json()["paths"]

        self.assertNotIn("/api/v1/routes/{id}/download", paths)
        self.assertNotIn("patch", paths["/api/v1/routes/{id}"])
        self.assertNotIn("/api/v1/drones/{id}/enable", paths)
        self.assertNotIn("/api/v1/drones/{id}/disable", paths)
        self.assertNotIn("/api/v1/drones/{id}/maintenance", paths)
        self.assertNotIn("/api/v1/drones/{id}/retire", paths)
        self.assertNotIn("/api/v1/routes/{id}/enable", paths)
        self.assertNotIn("/api/v1/routes/{id}/disable", paths)
        self.assertNotIn("/api/v1/missions/{id}/start", paths)
        self.assertNotIn("/api/v1/missions/{id}/pause", paths)
        self.assertNotIn("/api/v1/missions/{id}/resume", paths)
        self.assertNotIn("/api/v1/missions/{id}/complete", paths)
        self.assertNotIn("/api/v1/missions/{id}/fail", paths)
        self.assertNotIn("/api/v1/drone-assignments/{id}/reactivate", paths)
        self.assertNotIn("post", paths["/api/v1/media-files"])
        self.assertNotIn("put", paths["/api/v1/media-files/{id}"])
        self.assertNotIn("patch", paths["/api/v1/media-files/{id}"])
        self.assertNotIn("delete", paths["/api/v1/media-files/{id}"])
```

- [ ] **Step 3: Sync canonical route and waypoint documentation**

Update `README.md` route section so it lists:

```md
7. Business API - 航线（route）
- `GET/POST /api/v1/routes`
- `GET/PUT /api/v1/routes/{id}`
- `DELETE /api/v1/routes/{id}`
- `GET /api/v1/routes/{id}/xml`
- `POST /api/v1/routes/{id}/publish`
```

Update `业务侧实现/route_impl_desc.md` so the route table is documented as:

```md
### Route 表 (routes)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| tenant | ForeignKey | 租户 |
| name | CharField(100) | 航线名称 |
| xml_file | FileField | 当前本地 XML 草稿文件 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |
```

And the API section explicitly says:

```md
- `POST /api/v1/routes` 和 `PUT /api/v1/routes/{id}` 使用 `multipart/form-data`
- 唯一可写字段是 `name`、`xml_file`
- `GET /api/v1/routes/{id}/xml` 返回原始 XML 文件流
- 不再公开 `waypoints[]`
- 不再保留 `PATCH /api/v1/routes/{id}`
- 不再保留 `GET /api/v1/routes/{id}/download`
```

Update the waypoint docs to say the waypoint table is no longer part of the active route runtime chain:

```md
- `Waypoint` 已不再参与当前 route 编辑、读取、发布链路。
- 当前 route 真相源是 `Route.xml_file`。
- waypoint 文档只保留为历史/内部说明，不代表存在公开 API 或运行依赖。
```

Update `项目总体概览/逻辑设计/overall_data_dictionary.md` and `overall_logical_model.md` so the route section matches the XML-backed model and `/xml` endpoint.

- [ ] **Step 4: Run the affected cross-app tests, then the full suite**

Run:

```bash
.venv/bin/python manage.py test \
  apps.api_v1.tests \
  apps.mission.tests apps.mission.test_live_api \
  apps.flight_record.tests apps.flight_record.test_live_api \
  apps.media_file.tests apps.media_file.test_live_api \
  apps.dji_bff.tests \
  -v 2
```

Expected:
- `OK`

Then run:

```bash
.venv/bin/python manage.py test -v 2
```

Expected:
- `OK`
- no route-related schema failures
- no remaining tests constructing `Route` with removed fields

- [ ] **Step 5: Commit the schema, fallout, and docs sync**

Run:

```bash
git add \
  apps/api_v1/tests.py \
  apps/mission/tests.py apps/mission/test_live_api.py \
  apps/flight_record/tests.py apps/flight_record/test_live_api.py \
  apps/media_file/tests.py apps/media_file/test_live_api.py \
  apps/dji_bff/tests.py \
  README.md \
  业务侧实现/route_impl_desc.md 业务侧实现/route_data_dictionary.md 业务侧实现/route_logical_model.md \
  业务侧实现/waypoint_impl_desc.md 业务侧实现/waypoint_data_dictionary.md 业务侧实现/waypoint_logical_model.md \
  项目总体概览/逻辑设计/overall_data_dictionary.md 项目总体概览/逻辑设计/overall_logical_model.md
git commit -m "docs: sync route xml source contract"
```

## Self-Review Checklist

### Spec Coverage

- XML-only route write contract: covered by Task 1 and Task 2.
- `GET /api/v1/routes/{id}/xml`: covered by Task 1 and Task 2.
- `PATCH` removal and `/download` removal: covered by Task 1, Task 2, and Task 4.
- XML-to-KMZ publish pipeline: covered by Task 3.
- `is_published=false` after `PUT`: covered by Task 1 and Task 2.
- Route runtime no longer depends on waypoint rows: covered by Task 3.
- Canonical docs sync: covered by Task 4.

### Placeholder Scan

- No placeholder markers or deferred “implement later” text remain in task steps.
- Every code-touching step includes concrete code or a concrete grep/command target.
- Every test step has an exact command and an expected result.

### Type / Name Consistency

- `xml_file` is the only write field used consistently throughout the plan.
- `build_route_kmz_from_xml(route)` is the single publish packaging helper name throughout the plan.
- The new XML readback route is consistently `/api/v1/routes/{id}/xml`.
- The removed actions are consistently `PATCH /api/v1/routes/{id}` and `GET /api/v1/routes/{id}/download`.
