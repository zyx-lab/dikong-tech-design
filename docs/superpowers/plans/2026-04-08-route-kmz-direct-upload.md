# Route KMZ Direct Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the route XML draft plus `/publish` model with immediate KMZ upload on `POST`/`PUT`, remove `/publish` and `/xml`, and serve route downloads by proxying the persisted DJI `download_url`.

**Architecture:** Keep `Route` as the minimal local business entity and keep `TenantRouteIndex` as the only DJI publish-state bridge. `POST /api/v1/routes` and `PUT /api/v1/routes/{id}` will validate and upload `kmz_file` directly to DJI, then persist `dji_wayline_id`, `download_url`, and `is_published=true` in one transaction, with compensating delete when the upstream upload succeeds but local persistence fails. `GET /api/v1/routes/{id}/kmz` will proxy the saved `download_url`; no local XML/KMZ file copy remains in the system.

**Tech Stack:** Django 5.1.6, Django REST Framework 3.15.2, drf-spectacular, Django migrations, Django test runner, Mock DJI upstream

---

## File Structure

**Create**
- `apps/route/migrations/0007_remove_route_xml_file.py`

**Modify**
- `apps/route/models.py`
- `apps/route/serializers.py`
- `apps/route/views.py`
- `apps/route/tests.py`
- `apps/route/test_live_api.py`
- `apps/dji_bff/gateway.py`
- `apps/dji_bff/tests.py`
- `apps/dji_mock/views.py`
- `apps/api_v1/tests.py`
- `apps/access/test_live_schema_api.py`
- `项目总体概览/DJI_backend_api.md`
- `项目总体概览/DJI适配接入边界设计.md`
- `项目总体概览/逻辑设计/overall_data_dictionary.md`
- `项目总体概览/逻辑设计/overall_logical_model.md`

**Do Not Modify**
- `apps/route/services.py`

**Test**
- `apps/route/tests.py`
- `apps/route/test_live_api.py`
- `apps/dji_bff/tests.py`
- `apps/api_v1/tests.py`
- `apps/access/test_live_schema_api.py`

### Why These Files

- `apps/route/models.py` still stores `xml_file`; this field must be removed so the public contract matches the new data model.
- `apps/route/serializers.py` still validates XML payloads; it must switch to `kmz_file` with only light ZIP/KMZ validation.
- `apps/route/views.py` still exposes `/publish` and `/xml`, marks routes unpublished on write, and deletes local XML files on update/delete. This file owns the main transactional refactor.
- `apps/dji_bff/gateway.py` already uploads via `files/upload`, but it still lacks a binary downloader that consumes the persisted `download_url`.
- `apps/dji_mock/views.py` already returns a real-looking upload payload; it must also support the exact download proxy path expected by the new `/routes/{id}/kmz` tests.
- `apps/route/tests.py`, `apps/route/test_live_api.py`, `apps/api_v1/tests.py`, and `apps/access/test_live_schema_api.py` still lock the XML/publish contract and need to be rewritten to the KMZ contract.
- `项目总体概览/*` documents still describe XML drafts, `/publish`, and `/xml`; they must be rewritten so runtime docs and code do not diverge.
- `apps/route/services.py` becomes dead once XML conversion is removed. Delete imports and usages first, then leave the file untouched in this plan; removing the file itself is unnecessary for this change.

### Task 1: Lock the New Public Contract With Failing Route and Schema Tests

**Files:**
- Modify: `apps/route/tests.py`
- Modify: `apps/route/test_live_api.py`
- Modify: `apps/api_v1/tests.py`
- Modify: `apps/access/test_live_schema_api.py`
- Test: `apps/route/tests.py`
- Test: `apps/route/test_live_api.py`
- Test: `apps/api_v1/tests.py`
- Test: `apps/access/test_live_schema_api.py`

- [ ] **Step 1: Replace XML fixture helpers in `apps/route/tests.py` with KMZ helpers**

Remove XML-specific helpers and add a reusable KMZ builder that creates a minimal valid ZIP payload.

```python
from io import BytesIO
from zipfile import ZipFile


def build_test_kmz(*, template_bytes: bytes = b"<kml/>", wpml_bytes: bytes = b"<wpml/>") -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("template.kml", template_bytes)
        archive.writestr("waylines.wpml", wpml_bytes)
    return buffer.getvalue()
```

Also replace the upload helper:

```python
    def _upload_kmz_route(self, *, name="KMZ 航线", kmz_bytes=None):
        return self.client.post(
            "/api/v1/routes",
            {
                "name": name,
                "kmz_file": SimpleUploadedFile(
                    "route.kmz",
                    kmz_bytes if kmz_bytes is not None else build_test_kmz(),
                    content_type="application/vnd.google-earth.kmz",
                ),
            },
            format="multipart",
        )
```

- [ ] **Step 2: Rewrite route API tests to the new KMZ behavior**

Rename `RouteXmlSourceApiTests` to `RouteKmzApiTests`, then replace the XML/publish test cases with these KMZ-contract tests:

```python
    def test_create_should_upload_kmz_and_persist_route_index(self):
        response = self._upload_kmz_route(name="城市巡检 KMZ")

        self.assertEqual(response.status_code, 201, response.data)
        data = response.data["data"]
        self.assertEqual(data["name"], "城市巡检 KMZ")
        self.assertTrue(data["is_published"])

        route = Route.objects.get(id=data["id"])
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertTrue(route_index.is_published)
        self.assertTrue(route_index.dji_wayline_id.startswith("mock-wayline-"))
        self.assertEqual(
            route_index.download_url,
            f"/api/v1/wayline/workspaces/mock-workspace-001/waylines/{route_index.dji_wayline_id}/url",
        )
        uploaded = mock_dji_state.waylines[route_index.dji_wayline_id]
        self.assertEqual(uploaded["file_name"], "route.kmz")
```

```python
    def test_create_should_reject_non_zip_kmz_payload(self):
        response = self._upload_kmz_route(name="坏 KMZ", kmz_bytes=b"not-a-zip")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["data"], {"kmz_file": ["上传文件必须是有效 KMZ/ZIP 文件"]})
```

```python
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

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.put(
                f"/api/v1/routes/{route.id}",
                {
                    "name": "更新后 KMZ",
                    "kmz_file": SimpleUploadedFile(
                        "route-updated.kmz",
                        build_test_kmz(template_bytes=b"<kml><name>updated</name></kml>"),
                        content_type="application/vnd.google-earth.kmz",
                    ),
                },
                format="multipart",
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["data"]["is_published"])
        route.refresh_from_db()
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertNotEqual(route_index.dji_wayline_id, old_wayline_id)
        self.assertNotIn(old_wayline_id, mock_dji_state.waylines)
```

```python
    def test_kmz_download_should_proxy_saved_download_url(self):
        route = Route.objects.create(tenant=self.tenant, name="下载 KMZ")
        wayline = mock_dji_state.create_wayline(name="downloadable-wayline", file_name="downloadable.kmz")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id=wayline["wayline_id"],
            download_url=f"/api/v1/wayline/workspaces/mock-workspace-001/waylines/{wayline['wayline_id']}/url",
            is_published=True,
        )

        response = self.client.get(f"/api/v1/routes/{route.id}/kmz")

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/vnd.google-earth.kmz", response["Content-Type"])
        self.assertEqual(b"".join(response.streaming_content), b"mock-kmz-binary")
```

```python
    def test_removed_publish_and_xml_endpoints_should_return_404(self):
        route = Route.objects.create(tenant=self.tenant, name="移除接口检查")
        TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", download_url="", is_published=False)

        publish_response = self.client.post(f"/api/v1/routes/{route.id}/publish")
        xml_response = self.client.get(f"/api/v1/routes/{route.id}/xml")

        self.assertEqual(publish_response.status_code, 404)
        self.assertEqual(xml_response.status_code, 404)
```

Keep the existing legacy write-contract rejection test, but replace `xml_file` with `kmz_file` so unknown-field behavior stays covered.

- [ ] **Step 3: Add the failure-path assertions that drive the transaction design**

Add two more route tests so create/update flow is fully locked before implementation:

```python
    def test_create_should_delete_new_upstream_wayline_when_local_persist_fails(self):
        with patch("apps.route.views.TenantRouteIndex.objects.create", side_effect=RuntimeError("db boom")):
            response = self._upload_kmz_route(name="补偿删除 KMZ")

        self.assertEqual(response.status_code, 500, response.data)
        self.assertEqual(mock_dji_state.waylines, {})
```

```python
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

        response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(Route.objects.filter(id=route.id).exists())
        self.assertNotIn(wayline["wayline_id"], mock_dji_state.waylines)
```

```python
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
        ):
            response = self.client.delete(f"/api/v1/routes/{route.id}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(Route.objects.filter(id=route.id).exists())
```

- [ ] **Step 4: Rewrite the live route contract tests to POST/PUT/GET `kmz`**

Replace `LiveRouteXmlSourceApiTests` with `LiveRouteKmzApiTests` and use the same inline KMZ builder pattern.

```python
def build_test_kmz(*, template_bytes: bytes = b"<kml/>", wpml_bytes: bytes = b"<wpml/>") -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("template.kml", template_bytes)
        archive.writestr("waylines.wpml", wpml_bytes)
    return buffer.getvalue()


class LiveRouteKmzApiTests(LiveDjiGatewayApiTestCase):
    VALID_KMZ_BYTES = build_test_kmz()
```

Use these assertions:

```python
    def test_route_kmz_upload_and_download_should_follow_http_contract(self):
        create_status, _, create_raw = self._post_multipart(
            "/api/v1/routes",
            fields={"name": "实时 KMZ 航线"},
            files={"kmz_file": ("live-route.kmz", self.VALID_KMZ_BYTES, "application/vnd.google-earth.kmz")},
        )

        self.assertEqual(create_status, 201, create_raw.decode("utf-8", errors="ignore"))
        create_data = json.loads(create_raw.decode("utf-8"))["data"]
        self.assertTrue(create_data["is_published"])
        route_id = create_data["id"]

        kmz_response = self.client.get(f"/api/v1/routes/{route_id}/kmz")
        self.assertEqual(kmz_response.status_code, 200)
        self.assertIn("application/vnd.google-earth.kmz", kmz_response.headers.get("Content-Type", ""))
```

```python
    def test_route_publish_endpoint_should_be_unmounted(self):
        create_status, _, create_raw = self._post_multipart(
            "/api/v1/routes",
            fields={"name": "实时无 publish"},
            files={"kmz_file": ("live-route.kmz", self.VALID_KMZ_BYTES, "application/vnd.google-earth.kmz")},
        )
        self.assertEqual(create_status, 201, create_raw.decode("utf-8", errors="ignore"))
        route_id = json.loads(create_raw.decode("utf-8"))["data"]["id"]

        publish_response = self.client.post(f"/api/v1/routes/{route_id}/publish")
        self.assertEqual(publish_response.status_code, 404)
```

- [ ] **Step 5: Rewrite the schema tests to the KMZ contract**

In `apps/api_v1/tests.py`, replace every `/routes/{id}/xml`, `/publish`, and `xml_file` assertion with `/routes/{id}/kmz`, no `/publish`, and `kmz_file`.

Use these concrete replacements:

```python
        self.assertIn("/api/v1/routes/{id}/kmz", paths)
        self.assertNotIn("/api/v1/routes/{id}/xml", paths)
        self.assertNotIn("/api/v1/routes/{id}/publish", paths)
```

```python
        self.assertEqual(set(create_schema.get("properties", {}).keys()), {"name", "kmz_file"})
        self.assertEqual(set(update_schema.get("properties", {}).keys()), {"name", "kmz_file"})
```

```python
            ("/api/v1/routes/{id}/kmz", "get"),
```

In `apps/access/test_live_schema_api.py`, update the live schema smoke assertions the same way:

```python
        self.assertIn("/api/v1/routes/{id}/kmz", paths)
        self.assertNotIn("/api/v1/routes/{id}/xml", paths)
        self.assertNotIn("/api/v1/routes/{id}/publish", paths)
```

- [ ] **Step 6: Run the route and schema tests to capture the expected failures**

Run:

```bash
.venv/bin/python manage.py test \
  apps.route.tests \
  apps.api_v1.tests.OpenApiDocsTests \
  apps.access.test_live_schema_api.LiveIamSchemaTests \
  -v 2
```

Expected:

```text
FAIL because the API still requires xml_file, still exposes /publish and /xml, and does not expose /routes/{id}/kmz
```

- [ ] **Step 7: Commit the test contract lock**

```bash
git add apps/route/tests.py apps/route/test_live_api.py apps/api_v1/tests.py apps/access/test_live_schema_api.py
git commit -m "test(route): lock kmz direct upload contract"
```

### Task 2: Remove `Route.xml_file` and Switch Serializer Validation to KMZ

**Files:**
- Create: `apps/route/migrations/0007_remove_route_xml_file.py`
- Modify: `apps/route/models.py`
- Modify: `apps/route/serializers.py`
- Test: `apps/route/tests.py`

- [ ] **Step 1: Add the failing serializer expectations for KMZ validation**

In `apps/route/tests.py`, add or update assertions so serializer messages are locked before implementation:

```python
    def test_create_should_require_kmz_file(self):
        response = self.client.post("/api/v1/routes", {"name": "缺文件"}, format="multipart")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["data"], {"kmz_file": ["未提交文件。"]})
```

```python
    def test_put_should_reject_empty_kmz_file(self):
        route = Route.objects.create(tenant=self.tenant, name="空 KMZ")
        TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", download_url="", is_published=False)

        response = self.client.put(
            f"/api/v1/routes/{route.id}",
            {"name": "空 KMZ", "kmz_file": SimpleUploadedFile("empty.kmz", b"", content_type="application/vnd.google-earth.kmz")},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["data"], {"kmz_file": ["提交的文件为空。"]})
```

- [ ] **Step 2: Run the two focused tests and confirm they fail**

Run:

```bash
.venv/bin/python manage.py test \
  apps.route.tests.RouteKmzApiTests.test_create_should_require_kmz_file \
  apps.route.tests.RouteKmzApiTests.test_put_should_reject_empty_kmz_file \
  -v 2
```

Expected:

```text
FAIL because the serializers still define xml_file instead of kmz_file
```

- [ ] **Step 3: Remove the model field and replace XML parsing with ZIP validation**

In `apps/route/models.py`, reduce `Route` to the minimal local entity:

```python
class Route(models.Model):
    tenant = models.ForeignKey(
        "access.Tenant",
        on_delete=models.CASCADE,
        related_name="routes",
        verbose_name="租户",
    )
    name = models.CharField("航线名称", max_length=100)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)
```

Create `apps/route/migrations/0007_remove_route_xml_file.py`:

```python
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("route", "0006_route_xml_source"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="route",
            name="xml_file",
        ),
    ]
```

In `apps/route/serializers.py`, replace XML parsing with ZIP validation:

```python
import zipfile

from rest_framework import serializers

from apps.api_v1.serializers import RejectUnknownFieldsMixin
from apps.route.models import Route
```

```python
class RouteWriteSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    kmz_file = serializers.FileField(required=True, allow_empty_file=False)

    class Meta:
        model = Route
        fields = ["name", "kmz_file"]
        extra_kwargs = {
            "name": {"help_text": "航线名称。"},
            "kmz_file": {"help_text": "航线 KMZ 文件。"},
        }

    def validate_kmz_file(self, value):
        try:
            value.seek(0)
            with zipfile.ZipFile(value) as archive:
                archive.namelist()
        except (zipfile.BadZipFile, OSError, ValueError, TypeError):
            raise serializers.ValidationError("上传文件必须是有效 KMZ/ZIP 文件")
        finally:
            value.seek(0)
        return value
```

Do not add WPML semantic validation here. The spec explicitly leaves detailed format checks to DJI.

- [ ] **Step 4: Re-run the focused serializer tests**

Run:

```bash
.venv/bin/python manage.py test \
  apps.route.tests.RouteKmzApiTests.test_create_should_require_kmz_file \
  apps.route.tests.RouteKmzApiTests.test_put_should_reject_empty_kmz_file \
  -v 2
```

Expected:

```text
PASS for the two serializer tests; broader route tests still FAIL because views and schema still reference XML endpoints
```

- [ ] **Step 5: Commit the model and serializer baseline**

```bash
git add apps/route/models.py apps/route/migrations/0007_remove_route_xml_file.py apps/route/serializers.py apps/route/tests.py
git commit -m "refactor(route): replace xml contract with kmz input"
```

### Task 3: Refactor Route Create/Update/Delete to Immediate DJI Upload

**Files:**
- Modify: `apps/route/views.py`
- Modify: `apps/route/tests.py`
- Test: `apps/route/tests.py`

- [ ] **Step 1: Add failing tests for create/update transaction semantics**

Add two focused tests that specifically require immediate publish and compensation:

```python
    def test_create_should_not_leave_local_route_when_upload_fails(self):
        with patch(
            "apps.route.views.DjiGateway.upload_route",
            side_effect=DjiGatewayUpstreamError("upload failed", status_code=502, data={"code": "E5000"}),
        ):
            response = self._upload_kmz_route(name="上传失败")

        self.assertEqual(response.status_code, 502, response.data)
        self.assertFalse(Route.objects.filter(name="上传失败").exists())
```

```python
    def test_put_should_keep_old_index_when_new_upload_fails(self):
        route = Route.objects.create(tenant=self.tenant, name="保留旧索引")
        TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id="mock-wayline-existing",
            download_url="/api/v1/wayline/workspaces/mock-workspace-001/waylines/mock-wayline-existing/url",
            is_published=True,
        )

        with patch(
            "apps.route.views.DjiGateway.upload_route",
            side_effect=DjiGatewayUpstreamError("upload failed", status_code=502, data={"code": "E5000"}),
        ):
            response = self.client.put(
                f"/api/v1/routes/{route.id}",
                {
                    "name": "保留旧索引",
                    "kmz_file": SimpleUploadedFile(
                        "failed-update.kmz",
                        build_test_kmz(),
                        content_type="application/vnd.google-earth.kmz",
                    ),
                },
                format="multipart",
            )

        self.assertEqual(response.status_code, 502, response.data)
        route_index = TenantRouteIndex.objects.get(route=route)
        self.assertEqual(route_index.dji_wayline_id, "mock-wayline-existing")
        self.assertEqual(
            route_index.download_url,
            "/api/v1/wayline/workspaces/mock-workspace-001/waylines/mock-wayline-existing/url",
        )
        self.assertTrue(route_index.is_published)
```

```python
    def test_put_should_delete_new_upstream_wayline_when_index_persist_fails(self):
        route = Route.objects.create(tenant=self.tenant, name="更新补偿删除")
        old_wayline = mock_dji_state.create_wayline(name="old-wayline")
        route_index = TenantRouteIndex.objects.create(
            tenant=self.tenant,
            route=route,
            dji_wayline_id=old_wayline["wayline_id"],
            download_url=f"/api/v1/wayline/workspaces/mock-workspace-001/waylines/{old_wayline['wayline_id']}/url",
            is_published=True,
        )

        with patch.object(route_index, "save", side_effect=RuntimeError("db boom")):
            with patch("apps.route.views.TenantRouteIndex.objects.get_or_create", return_value=(route_index, False)):
                response = self.client.put(
                    f"/api/v1/routes/{route.id}",
                    {
                        "name": "更新补偿删除",
                        "kmz_file": SimpleUploadedFile(
                            "replace.kmz",
                            build_test_kmz(),
                            content_type="application/vnd.google-earth.kmz",
                        ),
                    },
                    format="multipart",
                )

        self.assertEqual(response.status_code, 500, response.data)
        self.assertEqual(set(mock_dji_state.waylines.keys()), {old_wayline["wayline_id"]})
```

- [ ] **Step 2: Run the focused transaction tests and confirm failure**

Run:

```bash
.venv/bin/python manage.py test \
  apps.route.tests.RouteKmzApiTests.test_create_should_not_leave_local_route_when_upload_fails \
  apps.route.tests.RouteKmzApiTests.test_put_should_keep_old_index_when_new_upload_fails \
  apps.route.tests.RouteKmzApiTests.test_put_should_delete_new_upstream_wayline_when_index_persist_fails \
  -v 2
```

Expected:

```text
FAIL because create currently saves the local route before upload and update currently resets publication state instead of immediate republish
```

- [ ] **Step 3: Replace XML draft lifecycle in `apps/route/views.py` with immediate upload flows**

Make these structural changes:

1. Remove imports that are only needed for XML storage and conversion:

```python
import logging
import uuid

from django.db import transaction
from django.http import FileResponse
```

becomes:

```python
from io import BytesIO

import logging
import re
import uuid

from django.db import transaction
from django.http import FileResponse, Http404
```

2. Add route-name sanitization and upload name generation near the top of `apps/route/views.py`:

```python
_ROUTE_NAME_UNSAFE_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


def _build_upstream_route_name(*, route: Route) -> str:
    normalized_name = _ROUTE_NAME_UNSAFE_RE.sub("-", route.name.strip())
    normalized_name = re.sub(r"-+", "-", normalized_name).strip("-._") or "route"
    normalized_name = normalized_name[:40]
    return f"{route.id}-{normalized_name}-{uuid.uuid4().hex[:8]}"
```

3. Add a helper that converts the incoming serializer file into an uploadable file object without storing it locally:

```python
from django.core.files.uploadedfile import SimpleUploadedFile
```

```python
def _clone_kmz_upload(uploaded_file):
    uploaded_file.seek(0)
    content = uploaded_file.read()
    uploaded_file.seek(0)
    return SimpleUploadedFile(
        name=getattr(uploaded_file, "name", "route.kmz"),
        content=content,
        content_type=getattr(uploaded_file, "content_type", "application/vnd.google-earth.kmz"),
    )
```

4. Rewrite `perform_create()` into “create local route, upload KMZ, persist index, compensate on failure”:

```python
    @transaction.atomic
    def perform_create(self, serializer):
        tenant = self.get_current_tenant()
        route = serializer.save(tenant=tenant)
        kmz_file = _clone_kmz_upload(serializer.validated_data["kmz_file"])
        gateway = DjiGateway()
        created_wayline_id = ""

        try:
            upstream_payload = gateway.upload_route(
                route_name=_build_upstream_route_name(route=route),
                file_obj=kmz_file,
            )
            created_wayline_id = str(upstream_payload["dji_wayline_id"])
            route_index = TenantRouteIndex.objects.create(
                tenant=tenant,
                route=route,
                dji_wayline_id=created_wayline_id,
                download_url=str(upstream_payload["download_url"]),
                is_published=True,
            )
        except Exception:
            if created_wayline_id:
                self._delete_upstream_wayline_if_exists(
                    gateway=gateway,
                    wayline_id=created_wayline_id,
                    log_stage=lambda *args, **kwargs: None,
                )
            raise

        route.dji_index = route_index
        log_action(
            request=self.request,
            action="ROUTE_CREATE",
            target_type="route",
            target_id=route.id,
            after_data=self._payload(route),
        )
        return route
```

5. Rewrite `perform_update()` into “capture `old_wayline_id`, upload new KMZ, then persist route and index together, with compensation on local failure”:

```python
    @transaction.atomic
    def perform_update(self, serializer):
        route = serializer.instance
        before_data = self._payload(route)
        next_name = serializer.validated_data["name"]
        kmz_file = _clone_kmz_upload(serializer.validated_data["kmz_file"])
        gateway = DjiGateway()
        route_index, _ = TenantRouteIndex.objects.get_or_create(
            tenant=self.get_current_tenant(),
            route=route,
            defaults={"dji_wayline_id": "", "download_url": "", "is_published": False},
        )
        old_wayline_id = route_index.dji_wayline_id
        new_wayline_id = ""
        route.name = next_name
        try:
            upstream_payload = gateway.upload_route(
                route_name=_build_upstream_route_name(route=route),
                file_obj=kmz_file,
            )
            new_wayline_id = str(upstream_payload["dji_wayline_id"])
            serializer.save()
            route_index.dji_wayline_id = new_wayline_id
            route_index.download_url = str(upstream_payload["download_url"])
            route_index.is_published = True
            route_index.save(update_fields=["dji_wayline_id", "download_url", "is_published", "updated_at"])
        except Exception:
            if new_wayline_id:
                self._delete_upstream_wayline_if_exists(
                    gateway=gateway,
                    wayline_id=new_wayline_id,
                    log_stage=lambda *args, **kwargs: None,
                )
            raise

        if old_wayline_id and old_wayline_id != new_wayline_id:
            transaction.on_commit(lambda: self._delete_upstream_wayline_if_exists(
                gateway=gateway,
                wayline_id=old_wayline_id,
                log_stage=lambda *args, **kwargs: None,
                best_effort=True,
            ))

        route.dji_index = route_index
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

6. Delete the `publish()` action, the `xml()` action, `_mark_route_unpublished()`, `_publish_route_to_upstream()`, and `_persist_published_route_or_raise()`. These helpers exist only for the old two-stage XML model.

7. Update `destroy()` so it no longer touches local XML storage:

```python
        route_id = route.id
        route.waypoint_rows.all().delete()
        route.delete()
```

Keep the mission guard, but treat upstream delete as true best-effort: log and ignore every `DjiGatewayUpstreamError`, including non-`404`, so a DJI delete failure never blocks local route deletion.

- [ ] **Step 4: Re-run the full route test module**

Run:

```bash
.venv/bin/python manage.py test apps.route.tests -v 2
```

Expected:

```text
PASS for route API tests except the new /kmz proxy test, which still fails until the gateway downloader exists
```

- [ ] **Step 5: Commit the route transaction refactor**

```bash
git add apps/route/views.py apps/route/tests.py
git commit -m "refactor(route): upload kmz on create and update"
```

### Task 4: Add Download-by-`download_url` Gateway Support and Expose `/routes/{id}/kmz`

**Files:**
- Modify: `apps/dji_bff/gateway.py`
- Modify: `apps/dji_bff/tests.py`
- Modify: `apps/route/views.py`
- Modify: `apps/route/tests.py`
- Test: `apps/dji_bff/tests.py`
- Test: `apps/route/tests.py`

- [ ] **Step 1: Add failing gateway tests for persisted download URLs**

In `apps/dji_bff/tests.py`, add these tests:

```python
    def test_download_route_file_should_expand_relative_download_url(self):
        gateway = DjiGateway(base_url="http://mock-dji")
        response = GatewayResponse(status_code=200, headers={"Content-Type": "application/vnd.google-earth.kmz"}, data=b"kmz-bytes")

        with patch.object(gateway, "_request_binary", return_value=response, create=True) as request_mock:
            payload = gateway.download_route_file(
                "/api/v1/wayline/workspaces/mock-workspace-001/waylines/mock-wayline-001/url"
            )

        request_mock.assert_called_once()
        self.assertEqual(payload.status_code, 200)
        self.assertEqual(payload.data, b"kmz-bytes")
```

```python
    def test_download_route_file_should_reject_blank_url(self):
        gateway = DjiGateway(base_url="http://mock-dji")

        with self.assertRaises(DjiGatewayUpstreamError):
            gateway.download_route_file("")
```

- [ ] **Step 2: Run the gateway tests and confirm they fail**

Run:

```bash
.venv/bin/python manage.py test apps.dji_bff.tests -v 2
```

Expected:

```text
FAIL because DjiGateway has no binary download helper yet
```

- [ ] **Step 3: Implement raw binary request support and `download_route_file()` in `apps/dji_bff/gateway.py`**

Add a raw binary request helper alongside `_request()` so KMZ bytes are not passed through `_parse_body()`:

```python
    def _request_binary(
        self,
        method: str,
        path: str,
        *,
        authenticate: bool = True,
        follow_redirects: bool = True,
    ) -> GatewayResponse:
        auth_token = self._ensure_authenticated().access_token if authenticate else None
        headers = self._headers(auth_token=auth_token)
        try:
            return self._request_raw(method, path, data=None, headers=headers, follow_redirects=follow_redirects)
        except DjiGatewayUpstreamError as exc:
            if not authenticate or not self._is_auth_error(exc):
                raise
            headers = self._headers(auth_token=self._reauthenticate().access_token)
            return self._request_raw(method, path, data=None, headers=headers, follow_redirects=follow_redirects)
```

```python
    def _request_raw(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None,
        headers: dict[str, str],
        follow_redirects: bool,
    ) -> GatewayResponse:
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = f"{self.base_url}{path}"
        request = Request(url=url, data=data, headers=headers, method=method)
        opener = None if follow_redirects else build_opener(_NoRedirectHandler())
        try:
            open_fn = urlopen if opener is None else opener.open
            with open_fn(request, timeout=self.timeout) as response:
                return GatewayResponse(
                    status_code=response.status,
                    headers=dict(response.headers.items()),
                    data=response.read(),
                )
        except HTTPError as exc:
            payload = self._parse_body(exc.read())
            raise DjiGatewayUpstreamError("DJI upstream request failed", status_code=exc.code, data=payload) from exc
        except URLError as exc:
            raise DjiGatewayUpstreamError("DJI upstream unreachable", status_code=502) from exc
```

Then add the business helper next to `get_route_download_url()`:

```python
    def download_route_file(self, download_url: str) -> GatewayResponse:
        normalized_url = str(download_url or "").strip()
        if not normalized_url:
            raise DjiGatewayUpstreamError("未获取到航线下载地址", status_code=404)

        if normalized_url.startswith("http://") or normalized_url.startswith("https://"):
            request_path = normalized_url
        else:
            request_path = normalized_url if normalized_url.startswith("/") else f"/{normalized_url}"

        return self._request_binary("GET", request_path, follow_redirects=True)
```

Do not re-query wayline metadata by `dji_wayline_id`. The source of truth is the persisted `download_url`.

- [ ] **Step 4: Expose the new `/kmz` action in `apps/route/views.py`**

Add the schema declaration and the action body:

```python
    @extend_schema(
        summary="下载航线 KMZ",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=OpenApiTypes.BINARY),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    )
    @action(detail=True, methods=["get"])
    def kmz(self, request, *args, **kwargs):
        route = self.get_object()
        route_index = getattr(route, "dji_index", None)
        download_url = getattr(route_index, "download_url", "") if route_index is not None else ""
        if not download_url:
            raise Http404("航线 KMZ 不存在")

        try:
            upstream_response = DjiGateway().download_route_file(download_url)
        except DjiGatewayUpstreamError as exc:
            if exc.status_code == 404:
                raise Http404("航线 KMZ 不存在") from exc
            raise
        content_type = upstream_response.headers.get("Content-Type", "application/vnd.google-earth.kmz")
        filename = f"route-{route.id}.kmz"
        return FileResponse(BytesIO(upstream_response.data), content_type=content_type, filename=filename)
```

Update the imports at the top:

```python
from io import BytesIO
```

Update `permission_map` and `get_queryset()` to replace `xml` with `kmz`.

- [ ] **Step 5: Re-run the focused gateway and route download tests**

Run:

```bash
.venv/bin/python manage.py test \
  apps.dji_bff.tests \
  apps.route.tests.RouteKmzApiTests.test_kmz_download_should_proxy_saved_download_url \
  -v 2
```

Expected:

```text
PASS
```

- [ ] **Step 6: Commit the download proxy support**

```bash
git add apps/dji_bff/gateway.py apps/dji_bff/tests.py apps/route/views.py apps/route/tests.py
git commit -m "feat(route): proxy kmz downloads via saved dji url"
```

### Task 5: Update Mock DJI and Live Tests to the New Upload and Download Shape

**Files:**
- Modify: `apps/dji_mock/views.py`
- Modify: `apps/route/test_live_api.py`
- Modify: `apps/route/tests.py`
- Test: `apps/route/test_live_api.py`
- Test: `apps/route/tests.py`

- [ ] **Step 1: Add the failing mock and live assertions for the real return shape**

In `apps/route/tests.py`, extend the create and update tests to assert the mock upload payload is still the real shape:

```python
        uploaded = mock_dji_state.waylines[route_index.dji_wayline_id]
        self.assertIn(str(route.id), uploaded["name"])
        self.assertIn("城市巡检-KMZ", uploaded["name"])
```

In `apps/route/test_live_api.py`, assert that the download proxy is the only supported readback path:

```python
        xml_response = self.client.get(f"/api/v1/routes/{route_id}/xml")
        self.assertEqual(xml_response.status_code, 404)
```

- [ ] **Step 2: Run the live and route tests and confirm any remaining failures**

Run:

```bash
.venv/bin/python manage.py test \
  apps.route.test_live_api \
  apps.route.tests.RouteKmzApiTests \
  -v 2
```

Expected:

```text
FAIL if mock download returns the wrong content type or if upload naming still uses the old route-{id}-{uuid} format
```

- [ ] **Step 3: Adjust `apps/dji_mock/views.py` only where needed for the final KMZ contract**

Keep the current real upload payload:

```python
    return _success(
        {
            "name": created["name"],
            "wayline_id": created["wayline_id"],
            "workspace_id": workspace_id,
            "download_url": f"/api/v1/wayline/workspaces/{workspace_id}/waylines/{created['wayline_id']}/url",
        }
    )
```

Ensure the binary download endpoint returns KMZ content type:

```python
def download_wayline_binary(request, filename: str):
    if request.method != "GET":
        raise Http404
    return HttpResponse(b"mock-kmz-binary", content_type="application/vnd.google-earth.kmz")
```

Do not add any publish-specific mock endpoint. `/publish` must stay unmounted.

- [ ] **Step 4: Update the route upload naming assertions to the spec format**

In `apps/route/views.py`, make sure the upstream route name comes from `_build_upstream_route_name(route=route)` and verify the test expectation matches the sanitizer behavior:

```python
        self.assertRegex(uploaded["name"], rf"^{route.id}-城市巡检-KMZ-[0-9a-f]{{8}}$")
```

- [ ] **Step 5: Re-run live and route tests**

Run:

```bash
.venv/bin/python manage.py test \
  apps.route.test_live_api \
  apps.route.tests \
  -v 2
```

Expected:

```text
PASS
```

- [ ] **Step 6: Commit the mock and live-contract alignment**

```bash
git add apps/dji_mock/views.py apps/route/test_live_api.py apps/route/tests.py apps/route/views.py
git commit -m "test(route): align live and mock kmz contract"
```

### Task 6: Regenerate OpenAPI Surface and Rewrite Canonical Docs

**Files:**
- Modify: `apps/api_v1/tests.py`
- Modify: `apps/access/test_live_schema_api.py`
- Modify: `项目总体概览/DJI_backend_api.md`
- Modify: `项目总体概览/DJI适配接入边界设计.md`
- Modify: `项目总体概览/逻辑设计/overall_data_dictionary.md`
- Modify: `项目总体概览/逻辑设计/overall_logical_model.md`
- Test: `apps/api_v1/tests.py`
- Test: `apps/access/test_live_schema_api.py`

- [ ] **Step 1: Replace the docs text that still describes XML drafts or `/publish`**

Apply these concrete wording changes:

In `项目总体概览/DJI适配接入边界设计.md`, replace the old route API table rows:

```md
| `POST /api/v1/routes`          | POST   | 上传 KMZ 并立即发布到 DJI，创建本地 route 与最新发布索引 |
| `PUT /api/v1/routes/{id}`      | PUT    | 上传新的 KMZ 并替换当前 route 绑定的 DJI 航线           |
| `GET /api/v1/routes/{id}/kmz`  | GET    | 根据已保存 `download_url` 代理下载当前 KMZ             |
| `DELETE /api/v1/routes/{id}`   | DELETE | 删除 route，并 best-effort 删除当前 `dji_wayline_id`   |
```

Replace the old create/publish flow text with:

```md
1. 前端调用 `POST /api/v1/routes` 并提交 `kmz_file`。
2. 服务端先创建本地 `Route` 以获得 `route.id`。
3. 服务端以 `{route.id}-{sanitized_route_name}-{uuid8}` 作为 DJI 名称，调用 `POST /api/v1/wayline/workspaces/{workspace_id}/waylines/files/upload`。
4. 上传成功后写入 `TenantRouteIndex(dji_wayline_id, download_url, is_published=true)`。
5. 不保留本地 XML/KMZ 草稿文件，也不再暴露 `/publish` 与 `/xml`。
```

In `项目总体概览/逻辑设计/overall_data_dictionary.md` and `overall_logical_model.md`, remove every mention of `Route.xml_file`, change `is_published` wording from “当前本地草稿是否已发布” to “当前 route 是否已绑定最近一次成功上传的 DJI 航线”, and add `download_url` wherever `TenantRouteIndex` is listed.

In `项目总体概览/DJI_backend_api.md`, keep the upstream `files/upload` response example and add one sentence under `download_url` saying the business side stores this value and proxies it via `/api/v1/routes/{id}/kmz`.

- [ ] **Step 2: Run the schema tests once the docs and routes are aligned**

Run:

```bash
.venv/bin/python manage.py test \
  apps.api_v1.tests.OpenApiDocsTests \
  apps.access.test_live_schema_api.LiveIamSchemaTests \
  -v 2
```

Expected:

```text
PASS
```

- [ ] **Step 3: Commit the schema and docs sync**

```bash
git add \
  apps/api_v1/tests.py \
  apps/access/test_live_schema_api.py \
  项目总体概览/DJI_backend_api.md \
  项目总体概览/DJI适配接入边界设计.md \
  项目总体概览/逻辑设计/overall_data_dictionary.md \
  项目总体概览/逻辑设计/overall_logical_model.md
git commit -m "docs(route): document kmz direct upload contract"
```

### Task 7: Final Verification and Cleanup

**Files:**
- Modify: `apps/route/views.py`
- Modify: `apps/route/serializers.py`
- Modify: `apps/route/tests.py`
- Modify: `apps/dji_bff/gateway.py`
- Modify: `apps/dji_mock/views.py`
- Test: `apps/route/tests.py`
- Test: `apps/route/test_live_api.py`
- Test: `apps/dji_bff/tests.py`
- Test: `apps/api_v1/tests.py`
- Test: `apps/access/test_live_schema_api.py`

- [ ] **Step 1: Remove leftover XML-only imports and dead code references**

Verify these lines no longer exist:

```bash
rg -n "xml_file|build_route_kmz_from_xml|/publish|/xml|读取航线 XML 草稿|当前 XML 草稿" \
  apps/route/models.py apps/route/serializers.py apps/route/views.py apps/route/tests.py apps/route/test_live_api.py \
  apps/api_v1/tests.py apps/access/test_live_schema_api.py 项目总体概览
```

Expected after cleanup:

```text
Only historical notes that are explicitly marked superseded may remain; runtime code and canonical docs should have no active XML draft references
```

If `apps/route/views.py` still imports `build_route_kmz_from_xml`, remove it. If `apps/route/serializers.py` still imports `xml.etree.ElementTree`, remove it. `apps/route/services.py` may remain as dead legacy code, but no runtime path may import or call it after this feature lands.

- [ ] **Step 2: Run the full verification suite for this feature**

Run:

```bash
.venv/bin/python manage.py test \
  apps.route.tests \
  apps.route.test_live_api \
  apps.dji_bff.tests \
  apps.api_v1.tests.OpenApiDocsTests \
  apps.access.test_live_schema_api.LiveIamSchemaTests \
  -v 2
```

Expected:

```text
PASS
```

- [ ] **Step 3: Inspect the diff before handoff**

Run:

```bash
git status --short
```

Expected:

```text
Only the route/gateway/mock/schema/doc files from this plan should appear; unrelated dirty mission/media files must remain untouched
```

- [ ] **Step 4: Commit the verified feature branch state**

```bash
git add apps/route apps/dji_bff apps/dji_mock apps/api_v1/tests.py apps/access/test_live_schema_api.py 项目总体概览
git commit -m "feat(route): switch to kmz direct upload workflow"
```
