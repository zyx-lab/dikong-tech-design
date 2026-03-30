# Route / Waypoints Aggregation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor route editing so `Route` is the only public business resource, `waypoints[]` becomes route-owned data, DJI upload happens only via explicit route publish, and public `/api/v1/waypoints*` is removed.

**Architecture:** Keep the current `waypoints` table in phase 1 as internal storage, but move all public read/write semantics into `Route` APIs. Simplify route publish state to `TenantRouteIndex.is_published`, remove route status and waypoint permissions, and add a single explicit `POST /api/v1/routes/{id}/publish` action that generates KMZ and uploads the full route to DJI.

**Tech Stack:** Django 5, Django REST Framework, drf-spectacular, existing `DjiGateway` HTTP client, Django test runner, Mock DJI upstream.

---

## File Structure

### Files to Modify

- `apps/route/models.py`
  - Remove `Route.status`
  - Keep `Route` as aggregate root
- `apps/route/serializers.py`
  - Add nested route-owned `waypoints[]` request/response structure
  - Remove KMZ file upload contract from create/update serializers
- `apps/route/views.py`
  - Stop uploading DJI route on create/update
  - Add explicit `publish` action
  - Return nested `waypoints[]` on detail
  - Enforce `download` only for published routes
- `apps/route/urls.py`
  - Keep router, expose new publish action through the viewset
- `apps/dji_bff/models.py`
  - Simplify `TenantRouteIndex` fields to `dji_wayline_id` + `is_published`
- `apps/waypoint/models.py`
  - Keep internal table, remove public-facing custom permissions and route-status validation
- `apps/api_v1/urls.py`
  - Remove public inclusion of `apps.waypoint.urls`
- `apps/access/management/commands/seed_role_permissions.py`
  - Remove `waypoint.*` grants and fold everything into `route.*`
- `apps/route/tests.py`
  - Replace old route upload expectations with route aggregate expectations
  - Add publish tests and endpoint-removal assertions
- `apps/route/test_live_api.py`
  - Switch live contract tests to route-owned `waypoints[]` and publish/download flow
- `apps/waypoint/tests.py`
  - Delete old public waypoint API tests
- `apps/waypoint/test_live_api.py`
  - Delete old live waypoint API tests
- `apps/route/migrations/0001_initial.py`
  - Align reset-db schema for `Route`
- `apps/dji_bff/migrations/0001_initial.py`
  - Align reset-db schema for `TenantRouteIndex`
- `业务侧实现/route_impl_desc.md`
  - Update route API contract
- `业务侧实现/route_logical_model.md`
  - Update route aggregate semantics
- `业务侧实现/waypoint_impl_desc.md`
  - Retire standalone waypoint resource description
- `业务侧实现/waypoint_data_dictionary.md`
  - Mark waypoint table as internal storage only
- `权限管理侧实现/角色权限矩阵设计.md`
  - Remove standalone waypoint permissions

### Files to Create

- `apps/route/services.py`
  - Own route aggregate persistence helpers
  - Replace internal waypoint rows from `waypoints[]`
  - Generate publish payload / KMZ file object
  - Publish new wayline, then delete prior wayline on success

### Files to Delete

- `apps/waypoint/views.py`
- `apps/waypoint/urls.py`

Deleting these two files makes the public API boundary match the design. The `waypoint` app still exists for its model/table in phase 1.

---

### Task 1: Lock the New Public Contract With Failing Tests

**Files:**
- Modify: `apps/route/tests.py`
- Modify: `apps/route/test_live_api.py`
- Test: `apps/route/tests.py`
- Test: `apps/route/test_live_api.py`

- [ ] **Step 1: Write the failing route aggregate tests**

Add unit tests in `apps/route/tests.py` for:

```python
def test_create_should_accept_nested_waypoints_and_mark_route_unpublished(self):
    response = self.client.post(
        "/api/v1/routes",
        {
            "name": "城市巡检航线",
            "route_type": 0,
            "waypoints": [
                {
                    "sequence": 1,
                    "latitude": "22.28612345",
                    "longitude": "113.56781234",
                    "altitude": "120.50",
                },
                {
                    "sequence": 2,
                    "latitude": "22.28622345",
                    "longitude": "113.56791234",
                    "altitude": "121.50",
                },
            ],
        },
        format="json",
    )

    self.assertEqual(response.status_code, 201)
    route = Route.objects.get(name="城市巡检航线")
    route_index = TenantRouteIndex.objects.get(route=route)
    self.assertEqual(route.waypoint_count, 2)
    self.assertFalse(route_index.is_published)
```

```python
def test_detail_should_return_nested_waypoints_in_sequence_order(self):
    route = Route.objects.create(tenant=self.tenant, name="顺序航线", creator_name="管理员")
    TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", is_published=False)
    Waypoint.objects.create(route=route, sequence=2, latitude="22.2", longitude="113.2", altitude="120.00")
    Waypoint.objects.create(route=route, sequence=1, latitude="22.1", longitude="113.1", altitude="110.00")

    response = self.client.get(f"/api/v1/routes/{route.id}")

    self.assertEqual(response.status_code, 200)
    self.assertEqual(
        [item["sequence"] for item in response.data["data"]["waypoints"]],
        [1, 2],
    )
```

```python
def test_publish_should_upload_current_route_and_mark_published(self):
    route = Route.objects.create(tenant=self.tenant, name="待发布航线", creator_name="管理员", waypoint_count=1)
    Waypoint.objects.create(route=route, sequence=1, latitude="22.1", longitude="113.1", altitude="110.00")
    TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", is_published=False)

    response = self.client.post(f"/api/v1/routes/{route.id}/publish", format="json")

    self.assertEqual(response.status_code, 200)
    route_index = TenantRouteIndex.objects.get(route=route)
    self.assertTrue(route_index.is_published)
    self.assertTrue(route_index.dji_wayline_id.startswith("mock-wayline-"))
```

```python
def test_old_waypoint_endpoints_should_be_removed(self):
    response = self.client.get("/api/v1/waypoints")
    self.assertEqual(response.status_code, 404)
```

Add live-contract tests in `apps/route/test_live_api.py` for:

```python
def test_route_detail_publish_download_should_follow_http_contract(self):
    create_response = self.client.post(
        "/api/v1/routes",
        {
            "name": "实时航线",
            "waypoints": [
                {
                    "sequence": 1,
                    "latitude": "22.28612345",
                    "longitude": "113.56781234",
                    "altitude": "120.50",
                }
            ],
        },
        format="json",
    )
    self.assertEqual(create_response.status_code, 201)
    route_id = create_response.json()["data"]["id"]
    self.assertFalse(create_response.json()["data"]["is_published"])

    detail_response = self.client.get(f"/api/v1/routes/{route_id}")
    self.assertEqual(detail_response.status_code, 200)
    self.assertEqual(detail_response.json()["data"]["waypoints"][0]["sequence"], 1)

    publish_response = self.client.post(f"/api/v1/routes/{route_id}/publish")
    self.assertEqual(publish_response.status_code, 200)
    self.assertTrue(publish_response.json()["data"]["is_published"])

    download_response = self.client.get(f"/api/v1/routes/{route_id}/download")
    self.assertEqual(download_response.status_code, 200)
    self.assertIn("mock wayline binary", download_response.text)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
./.venv/bin/python manage.py test apps.route.tests apps.route.test_live_api -v 2
```

Expected:

- FAIL because `POST /api/v1/routes` still expects `file`
- FAIL because route detail does not return `waypoints`
- FAIL because publish action does not exist
- FAIL because `/api/v1/waypoints` still resolves

- [ ] **Step 3: Commit the failing-test checkpoint**

```bash
git add apps/route/tests.py apps/route/test_live_api.py
git commit -m "test: define route aggregate contract"
```

---

### Task 2: Simplify Models and Reset-DB Migrations

**Files:**
- Modify: `apps/route/models.py`
- Modify: `apps/dji_bff/models.py`
- Modify: `apps/waypoint/models.py`
- Modify: `apps/route/migrations/0001_initial.py`
- Modify: `apps/dji_bff/migrations/0001_initial.py`
- Test: `apps/route/tests.py`

- [ ] **Step 1: Write the failing model assertions**

Append focused tests in `apps/route/tests.py`:

```python
def test_route_model_should_not_expose_status_field(self):
    self.assertNotIn("status", [field.name for field in Route._meta.fields])
```

```python
def test_route_index_should_use_boolean_publish_flag(self):
    route = Route.objects.create(tenant=self.tenant, name="索引航线", creator_name="管理员")
    route_index = TenantRouteIndex.objects.create(
        tenant=self.tenant,
        route=route,
        dji_wayline_id="",
        is_published=False,
    )
    self.assertFalse(route_index.is_published)
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
./.venv/bin/python manage.py test apps.route.tests.RouteApiTests.test_route_model_should_not_expose_status_field apps.route.tests.RouteApiTests.test_route_index_should_use_boolean_publish_flag -v 2
```

Expected:

- FAIL because `Route.status` still exists
- FAIL because `TenantRouteIndex.is_published` does not exist

- [ ] **Step 3: Implement the model changes**

Update `apps/route/models.py` to remove `RouteStatus` and the `status` field:

```python
class Route(models.Model):
    tenant = models.ForeignKey(
        "access.Tenant",
        on_delete=models.CASCADE,
        related_name="routes",
        verbose_name="租户",
    )
    name = models.CharField("航线名称", max_length=100)
    route_type = models.PositiveSmallIntegerField(
        "航线类型扩展位",
        choices=RouteType.choices,
        default=RouteType.PENDING_EXTENSION,
    )
    drone_type_id = models.BigIntegerField("适用无人机类型 ID", null=True, blank=True)
    total_distance = models.DecimalField("航线总长度(米)", max_digits=12, decimal_places=2, null=True, blank=True)
    estimated_duration = models.PositiveIntegerField("预计飞行时长(秒)", null=True, blank=True)
    waypoint_count = models.PositiveIntegerField("航点数量", default=0)
    creator_name = models.CharField("创建人姓名", max_length=50, blank=True, default="")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)
```

Update `apps/dji_bff/models.py` route index only:

```python
class TenantRouteIndex(models.Model):
    tenant = models.ForeignKey("access.Tenant", on_delete=models.CASCADE, related_name="dji_route_indexes")
    route = models.OneToOneField("route.Route", on_delete=models.CASCADE, related_name="dji_index")
    dji_wayline_id = models.CharField("DJI 航线 ID", max_length=128, blank=True, default="")
    is_published = models.BooleanField("是否已发布", default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

Update `apps/waypoint/models.py` to remove public-facing permissions and route-status check:

```python
class Waypoint(models.Model):
    route = models.ForeignKey(
        "route.Route",
        on_delete=models.PROTECT,
        related_name="waypoint_rows",
        verbose_name="所属航线",
    )
    sequence = models.PositiveIntegerField("航点序号")
    latitude = models.DecimalField("纬度", max_digits=12, decimal_places=8)
    longitude = models.DecimalField("经度", max_digits=12, decimal_places=8)
    altitude = models.DecimalField("飞行高度（米）", max_digits=10, decimal_places=2)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        db_table = "waypoints"
        ordering = ["route_id", "sequence", "id"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["route", "sequence"], name="waypoints_route_seq_unique"),
        ]
```

Align reset-db migrations:

```python
# apps/route/migrations/0001_initial.py
("waypoint_count", models.PositiveIntegerField(default=0, verbose_name="航点数量")),
# remove Route.status from the initial schema
```

```python
# apps/dji_bff/migrations/0001_initial.py
("dji_wayline_id", models.CharField(blank=True, default="", max_length=128, verbose_name="DJI 航线 ID")),
("is_published", models.BooleanField(default=False, verbose_name="是否已发布")),
# remove sync_status / last_sync_at / error_msg from TenantRouteIndex only
```

- [ ] **Step 4: Run the focused tests again**

Run:

```bash
./.venv/bin/python manage.py test apps.route.tests.RouteApiTests.test_route_model_should_not_expose_status_field apps.route.tests.RouteApiTests.test_route_index_should_use_boolean_publish_flag -v 2
```

Expected:

- PASS

- [ ] **Step 5: Validate migration drift**

Run:

```bash
./.venv/bin/python manage.py makemigrations --check --dry-run
```

Expected:

- `No changes detected`

- [ ] **Step 6: Commit**

```bash
git add apps/route/models.py apps/dji_bff/models.py apps/waypoint/models.py apps/route/migrations/0001_initial.py apps/dji_bff/migrations/0001_initial.py
git commit -m "refactor: simplify route publish state"
```

---

### Task 3: Move Waypoint Request/Response Logic Into Route Serializers

**Files:**
- Modify: `apps/route/serializers.py`
- Create: `apps/route/services.py`
- Test: `apps/route/tests.py`

- [ ] **Step 1: Write the failing serializer-level route aggregate tests**

Add tests in `apps/route/tests.py`:

```python
def test_update_with_waypoints_should_replace_full_waypoint_set(self):
    route = Route.objects.create(tenant=self.tenant, name="替换航线", creator_name="管理员", waypoint_count=2)
    TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="old-wayline", is_published=True)
    Waypoint.objects.create(route=route, sequence=1, latitude="22.1", longitude="113.1", altitude="110.00")
    Waypoint.objects.create(route=route, sequence=2, latitude="22.2", longitude="113.2", altitude="120.00")

    response = self.client.patch(
        f"/api/v1/routes/{route.id}",
        {
            "waypoints": [
                {"sequence": 10, "latitude": "22.9", "longitude": "113.9", "altitude": "150.00"}
            ]
        },
        format="json",
    )

    self.assertEqual(response.status_code, 200)
    self.assertEqual(route.waypoint_rows.count(), 1)
    self.assertEqual(route.waypoint_rows.first().sequence, 10)
    self.assertFalse(TenantRouteIndex.objects.get(route=route).is_published)
```

```python
def test_patch_without_waypoints_should_keep_existing_waypoint_rows(self):
    route = Route.objects.create(tenant=self.tenant, name="元数据修改", creator_name="管理员", waypoint_count=1)
    TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="old-wayline", is_published=True)
    Waypoint.objects.create(route=route, sequence=1, latitude="22.1", longitude="113.1", altitude="110.00")

    response = self.client.patch(
        f"/api/v1/routes/{route.id}",
        {"estimated_duration": 900},
        format="json",
    )

    self.assertEqual(response.status_code, 200)
    self.assertEqual(route.waypoint_rows.count(), 1)
    self.assertFalse(TenantRouteIndex.objects.get(route=route).is_published)
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
./.venv/bin/python manage.py test apps.route.tests.RouteApiTests.test_update_with_waypoints_should_replace_full_waypoint_set apps.route.tests.RouteApiTests.test_patch_without_waypoints_should_keep_existing_waypoint_rows -v 2
```

Expected:

- FAIL because route serializers do not accept nested `waypoints`

- [ ] **Step 3: Implement nested route-owned waypoint serialization**

Update `apps/route/serializers.py`:

```python
class RouteWaypointSerializer(serializers.Serializer):
    sequence = serializers.IntegerField(min_value=1)
    latitude = serializers.DecimalField(max_digits=12, decimal_places=8)
    longitude = serializers.DecimalField(max_digits=12, decimal_places=8)
    altitude = serializers.DecimalField(max_digits=10, decimal_places=2)


class RouteReadSerializer(serializers.ModelSerializer):
    is_published = serializers.SerializerMethodField()
    waypoints = serializers.SerializerMethodField()

    class Meta:
        model = Route
        fields = [
            "id",
            "name",
            "route_type",
            "drone_type_id",
            "total_distance",
            "estimated_duration",
            "waypoint_count",
            "creator_name",
            "is_published",
            "waypoints",
            "created_at",
            "updated_at",
        ]

    def get_is_published(self, obj) -> bool:
        return bool(getattr(getattr(obj, "dji_index", None), "is_published", False))

    def get_waypoints(self, obj):
        if self.context.get("include_waypoints", False) or getattr(self.context.get("request"), "parser_context", None):
            rows = getattr(obj, "waypoint_rows", obj.waypoint_rows.all()).all()
            rows = rows.order_by("sequence", "id")
            return [
                {
                    "sequence": row.sequence,
                    "latitude": row.latitude,
                    "longitude": row.longitude,
                    "altitude": row.altitude,
                }
                for row in rows
            ]
        return []
```

```python
class RouteCreateSerializer(serializers.ModelSerializer):
    waypoints = RouteWaypointSerializer(many=True, required=False)
```

```python
class RouteUpdateSerializer(serializers.ModelSerializer):
    waypoints = RouteWaypointSerializer(many=True, required=False)
```

Create `apps/route/services.py`:

```python
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from django.core.files.base import ContentFile

from apps.waypoint.models import Waypoint


def replace_route_waypoints(*, route, waypoints: list[dict] | None) -> None:
    if waypoints is None:
        return
    Waypoint.objects.filter(route=route).delete()
    for item in sorted(waypoints, key=lambda row: row["sequence"]):
        Waypoint.objects.create(
            route=route,
            sequence=item["sequence"],
            latitude=item["latitude"],
            longitude=item["longitude"],
            altitude=item["altitude"],
        )
    route.waypoint_count = len(waypoints)
    route.save(update_fields=["waypoint_count", "updated_at"])


def build_route_kmz(*, route, waypoints: list[dict]) -> ContentFile:
    kml = "\n".join(
        [
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
            "<kml><Document>",
            f"<name>{route.name}</name>",
            *[
                (
                    f\"<Placemark><name>{item['sequence']}</name>\"
                    f\"<Point><coordinates>{item['longitude']},{item['latitude']},{item['altitude']}</coordinates></Point>\"
                    \"</Placemark>\"
                )
                for item in sorted(waypoints, key=lambda row: row["sequence"])
            ],
            "</Document></kml>",
        ]
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as zf:
        zf.writestr("doc.kml", kml)
    buffer.seek(0)
    return ContentFile(buffer.getvalue(), name=f"route-{route.id}.kmz")
```

- [ ] **Step 4: Run the focused tests again**

Run:

```bash
./.venv/bin/python manage.py test apps.route.tests.RouteApiTests.test_update_with_waypoints_should_replace_full_waypoint_set apps.route.tests.RouteApiTests.test_patch_without_waypoints_should_keep_existing_waypoint_rows -v 2
```

Expected:

- PASS

- [ ] **Step 5: Commit**

```bash
git add apps/route/serializers.py apps/route/services.py apps/route/tests.py
git commit -m "feat: make waypoints route-owned data"
```

---

### Task 4: Refactor Route Views and Add Explicit Publish

**Files:**
- Modify: `apps/route/views.py`
- Modify: `apps/route/test_live_api.py`
- Test: `apps/route/tests.py`
- Test: `apps/route/test_live_api.py`

- [ ] **Step 1: Write the failing publish/download behavior tests**

Add tests in `apps/route/tests.py`:

```python
def test_download_should_reject_unpublished_route(self):
    route = Route.objects.create(tenant=self.tenant, name="未发布航线", creator_name="管理员")
    TenantRouteIndex.objects.create(tenant=self.tenant, route=route, dji_wayline_id="", is_published=False)

    response = self.client.get(f"/api/v1/routes/{route.id}/download")

    self.assertEqual(response.status_code, 409)
    self.assertEqual(response.data["code"], "C0201")
```

```python
def test_publish_should_replace_old_upstream_wayline_after_success(self):
    old_wayline = mock_dji_state.create_wayline(name="legacy-wayline")
    route = Route.objects.create(tenant=self.tenant, name="重发布航线", creator_name="管理员", waypoint_count=1)
    Waypoint.objects.create(route=route, sequence=1, latitude="22.1", longitude="113.1", altitude="110.00")
    TenantRouteIndex.objects.create(
        tenant=self.tenant,
        route=route,
        dji_wayline_id=old_wayline["wayline_id"],
        is_published=False,
    )

    response = self.client.post(f"/api/v1/routes/{route.id}/publish")

    self.assertEqual(response.status_code, 200)
    route_index = TenantRouteIndex.objects.get(route=route)
    self.assertTrue(route_index.is_published)
    self.assertNotEqual(route_index.dji_wayline_id, old_wayline["wayline_id"])
    self.assertNotIn(old_wayline["wayline_id"], mock_dji_state.waylines)
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
./.venv/bin/python manage.py test apps.route.tests.RouteApiTests.test_download_should_reject_unpublished_route apps.route.tests.RouteApiTests.test_publish_should_replace_old_upstream_wayline_after_success -v 2
```

Expected:

- FAIL because download still assumes any route index is downloadable
- FAIL because publish action does not exist

- [ ] **Step 3: Implement route create/update/publish/download behavior**

Update `apps/route/views.py`:

```python
from uuid import uuid4

from apps.route.services import build_route_kmz, replace_route_waypoints
from apps.waypoint.models import Waypoint
```

```python
permission_map = {
    "list": "route.view_route",
    "retrieve": "route.view_route",
    "download": "route.view_route",
    "create": "route.manage_route",
    "update": "route.manage_route",
    "partial_update": "route.manage_route",
    "destroy": "route.manage_route",
    "publish": "route.manage_route",
}
```

```python
@transaction.atomic
def perform_create(self, serializer):
    waypoints = serializer.validated_data.pop("waypoints", [])
    staff = IdentityService.get_staff(self.request.user)
    route = serializer.save(
        tenant=self.get_current_tenant(),
        creator_name=staff.name if staff else "",
        waypoint_count=0,
    )
    replace_route_waypoints(route=route, waypoints=waypoints)
    TenantRouteIndex.objects.create(
        tenant=self.get_current_tenant(),
        route=route,
        dji_wayline_id="",
        is_published=False,
    )
    log_action(
        request=self.request,
        action="ROUTE_CREATE",
        target_type="route",
        target_id=route.id,
        after_data=self._payload(route),
    )
    return route
```

```python
@transaction.atomic
def perform_update(self, serializer):
    route = self.get_object()
    before_data = self._payload(route)
    waypoints = serializer.validated_data.pop("waypoints", None)
    route = serializer.save()
    replace_route_waypoints(route=route, waypoints=waypoints)
    TenantRouteIndex.objects.update_or_create(
        tenant=self.get_current_tenant(),
        route=route,
        defaults={
            "dji_wayline_id": getattr(getattr(route, "dji_index", None), "dji_wayline_id", ""),
            "is_published": False,
        },
    )
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

```python
@action(detail=True, methods=["post"])
@transaction.atomic
def publish(self, request, *args, **kwargs):
    route = self.get_object()
    rows = list(route.waypoint_rows.order_by("sequence", "id"))
    if not rows:
        return Response(
            validation_error_payload({"waypoints": ["至少需要一个航点后才能发布"]}),
            status=status.HTTP_400_BAD_REQUEST,
        )

    waypoints = [
        {
            "sequence": row.sequence,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "altitude": row.altitude,
        }
        for row in rows
    ]
    route_index = TenantRouteIndex.objects.get(route=route)
    old_wayline_id = route_index.dji_wayline_id
    upstream_name = f"route-{route.id}-{uuid4().hex[:12]}"
    kmz_file = build_route_kmz(route=route, waypoints=waypoints)
    uploaded = DjiGateway().upload_route(route_name=upstream_name, file_obj=kmz_file)
    route_index.dji_wayline_id = uploaded["dji_wayline_id"]
    route_index.is_published = True
    route_index.save(update_fields=["dji_wayline_id", "is_published", "updated_at"])
    if old_wayline_id and old_wayline_id != route_index.dji_wayline_id:
        DjiGateway().delete_route(old_wayline_id)
    log_action(
        request=request,
        action="ROUTE_PUBLISH",
        target_type="route",
        target_id=route.id,
        after_data=self._payload(route),
    )
    return Response(self._payload(route), status=status.HTTP_200_OK)
```

```python
@action(detail=True, methods=["get"])
def download(self, request, *args, **kwargs):
    route = self.get_object()
    route_index = getattr(route, "dji_index", None)
    if route_index is None or not route_index.is_published or not route_index.dji_wayline_id:
        return Response(
            standard_error_payload(StandardCode.STATE_CONFLICT, "航线尚未发布，无法下载", {"route_id": route.id}),
            status=status.HTTP_409_CONFLICT,
        )
    download_url = DjiGateway().get_route_download_url(route_index.dji_wayline_id)
    return HttpResponseRedirect(download_url)
```

- [ ] **Step 4: Run route tests and live tests**

Run:

```bash
./.venv/bin/python manage.py test apps.route.tests apps.route.test_live_api -v 2
```

Expected:

- PASS

- [ ] **Step 5: Commit**

```bash
git add apps/route/views.py apps/route/test_live_api.py apps/route/tests.py
git commit -m "feat: add explicit route publish flow"
```

---

### Task 5: Remove Public Waypoint API and Fold Permissions Into Route

**Files:**
- Modify: `apps/api_v1/urls.py`
- Delete: `apps/waypoint/views.py`
- Delete: `apps/waypoint/urls.py`
- Modify: `apps/access/management/commands/seed_role_permissions.py`
- Test: `apps/route/tests.py`

- [ ] **Step 1: Write the failing boundary tests**

Add tests in `apps/route/tests.py`:

```python
def test_waypoint_permissions_should_no_longer_be_seeded(self):
    from apps.access.management.commands.seed_role_permissions import ROLE_PERMISSION_MATRIX

    for mapping in ROLE_PERMISSION_MATRIX.values():
        self.assertFalse(any(code.startswith("waypoint.") for code in mapping.keys()))
```

```python
def test_api_v1_should_not_mount_public_waypoint_urls(self):
    response = self.client.get("/api/v1/waypoints")
    self.assertEqual(response.status_code, 404)
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
./.venv/bin/python manage.py test apps.route.tests.RouteApiTests.test_waypoint_permissions_should_no_longer_be_seeded apps.route.tests.RouteApiTests.test_api_v1_should_not_mount_public_waypoint_urls -v 2
```

Expected:

- FAIL because waypoint permissions still exist in the seed matrix
- FAIL because `/api/v1/waypoints` is still mounted

- [ ] **Step 3: Remove the public boundary**

Update `apps/api_v1/urls.py`:

```python
urlpatterns = [
    path("", views.ApiV1RootView.as_view(), name="api-v1-root"),
    path("health", views.ApiV1HealthView.as_view(), name="api-v1-health"),
    path("iam/", include("apps.access.api_v1.urls")),
    path("", include("apps.dji_bff.urls")),
    path("", include("apps.drone.urls")),
    path("", include("apps.drone_assignment.urls")),
    path("", include("apps.route.urls")),
    path("", include("apps.mission.urls")),
    path("", include("apps.flight_record.urls")),
    path("", include("apps.media_file.urls")),
]
```

Delete:

```text
apps/waypoint/views.py
apps/waypoint/urls.py
```

Update `apps/access/management/commands/seed_role_permissions.py` by removing all `waypoint.view_waypoint` and `waypoint.manage_waypoint` grants.

- [ ] **Step 4: Re-run the focused tests**

Run:

```bash
./.venv/bin/python manage.py test apps.route.tests.RouteApiTests.test_waypoint_permissions_should_no_longer_be_seeded apps.route.tests.RouteApiTests.test_api_v1_should_not_mount_public_waypoint_urls -v 2
```

Expected:

- PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api_v1/urls.py apps/access/management/commands/seed_role_permissions.py
git rm apps/waypoint/views.py apps/waypoint/urls.py
git commit -m "refactor: remove public waypoint resource"
```

---

### Task 6: Retire Old Waypoint Tests and Replace Them With Route Aggregate Coverage

**Files:**
- Delete: `apps/waypoint/tests.py`
- Delete: `apps/waypoint/test_live_api.py`
- Modify: `apps/route/tests.py`
- Modify: `apps/route/test_live_api.py`
- Test: `apps/route/tests.py`
- Test: `apps/route/test_live_api.py`

- [ ] **Step 1: Remove obsolete standalone waypoint test modules**

Delete:

```text
apps/waypoint/tests.py
apps/waypoint/test_live_api.py
```

These tests are no longer valid because the public resource they exercise no longer exists.

- [ ] **Step 2: Add route aggregate replacements for the deleted coverage**

Ensure `apps/route/tests.py` and `apps/route/test_live_api.py` contain route-owned coverage for:

```python
def test_route_detail_should_return_waypoint_count_and_waypoints(self):
    ...
```

```python
def test_route_patch_should_mark_route_unpublished(self):
    ...
```

```python
def test_route_publish_should_require_at_least_one_waypoint(self):
    ...
```

```python
def test_route_delete_should_remove_internal_waypoint_rows(self):
    ...
```

- [ ] **Step 3: Run the route suite**

Run:

```bash
./.venv/bin/python manage.py test apps.route.tests apps.route.test_live_api -v 2
```

Expected:

- PASS

- [ ] **Step 4: Commit**

```bash
git add apps/route/tests.py apps/route/test_live_api.py
git rm apps/waypoint/tests.py apps/waypoint/test_live_api.py
git commit -m "test: replace waypoint api coverage with route aggregate coverage"
```

---

### Task 7: Update Canonical Docs and Run Full Verification

**Files:**
- Modify: `业务侧实现/route_impl_desc.md`
- Modify: `业务侧实现/route_logical_model.md`
- Modify: `业务侧实现/waypoint_impl_desc.md`
- Modify: `业务侧实现/waypoint_data_dictionary.md`
- Modify: `权限管理侧实现/角色权限矩阵设计.md`
- Test: `apps.route.tests`
- Test: `apps.route.test_live_api`
- Test: full suite

- [ ] **Step 1: Update canonical docs**

Document the new boundary:

```md
- `Waypoint` 不再作为独立业务资源对外暴露
- 对外只保留 `routes` 业务入口
- `waypoints[]` 作为 `Route` 的内部编辑结构出现在 route detail/create/update 中
- `POST /api/v1/routes/{id}/publish` 是唯一会触达 DJI 航线接口的动作
- `route.view_route` / `route.manage_route` 完全吸收旧 waypoint 权限
```

- [ ] **Step 2: Run focused verification**

Run:

```bash
./.venv/bin/python manage.py test apps.route.tests apps.route.test_live_api -v 2
```

Expected:

- PASS

- [ ] **Step 3: Run full regression suite**

Run:

```bash
./.venv/bin/python manage.py test
```

Expected:

- `OK`

- [ ] **Step 4: Confirm migration cleanliness**

Run:

```bash
./.venv/bin/python manage.py makemigrations --check --dry-run
```

Expected:

- `No changes detected`

- [ ] **Step 5: Commit**

```bash
git add 业务侧实现/route_impl_desc.md 业务侧实现/route_logical_model.md 业务侧实现/waypoint_impl_desc.md 业务侧实现/waypoint_data_dictionary.md 权限管理侧实现/角色权限矩阵设计.md
git commit -m "docs: align route aggregate and waypoint retirement"
```

---

## Self-Review Notes

### Spec coverage

Covered by tasks:

1. remove public waypoint resource
   - Tasks 1, 5, 6
2. route-owned `waypoints[]`
   - Tasks 1, 3, 4
3. explicit publish endpoint
   - Task 4
4. `TenantRouteIndex.is_published`
   - Task 2
5. remove route status and waypoint permissions
   - Tasks 2, 5
6. update canonical docs
   - Task 7

No spec gaps remain.

### Placeholder scan

No `TODO` / `TBD` placeholders remain.

### Type consistency

Consistent names used throughout:

- `is_published`
- `waypoints`
- `publish`
- `dji_wayline_id`
- `RouteWaypointSerializer`
- `replace_route_waypoints`
- `build_route_kmz`

