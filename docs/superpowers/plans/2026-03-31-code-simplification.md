# Code Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove obsolete implementation shells and consolidate duplicated Django business-API code without changing accepted business semantics.

**Architecture:** Keep the current module boundaries and public API contracts intact. Delete code that only supported already-removed semantics first, then introduce one light shared serializer mixin plus small file-local view helpers to reduce repetition in active modules. Use focused Django test subsets after each task and finish with a full `manage.py test` run.

**Tech Stack:** Django 5.1.6, Django REST Framework 3.15.2, drf-spectacular, SQLite test database, local `.venv`

---

## File Structure

**Delete**
- `apps/waypoint/serializers.py`

**Create**
- `apps/api_v1/serializers.py`

**Modify**
- `apps/api_v1/tests.py`
- `apps/drone/serializers.py`
- `apps/drone/views.py`
- `apps/route/serializers.py`
- `apps/route/views.py`
- `apps/mission/serializers.py`
- `apps/mission/views.py`
- `apps/flight_record/serializers.py`
- `apps/flight_record/views.py`
- `apps/dji_bff/views.py`
- `apps/dji_bff/services.py`

**Test**
- `apps/route/tests.py`
- `apps/drone/tests.py`
- `apps/mission/tests.py`
- `apps/flight_record/tests.py`
- `apps/dji_bff/tests.py`
- `apps/api_v1/tests.py`

### Why These Files

- `apps/waypoint/serializers.py` still encodes the removed public waypoint CRUD semantics and references the deleted route-status model.
- `apps/api_v1/serializers.py` is the smallest shared location for business-side serializer helpers.
- `apps/drone/serializers.py`, `apps/mission/serializers.py`, `apps/route/serializers.py`, and `apps/flight_record/serializers.py` all repeat the same unknown-field validation pattern.
- `apps/drone/views.py`, `apps/route/views.py`, `apps/mission/views.py`, and `apps/flight_record/views.py` all contain duplicated response-building or action-validation logic.
- `apps/dji_bff/views.py` and `apps/dji_bff/services.py` contain small duplicated internal helper patterns that can be collapsed without changing runtime behavior.

### Task 1: Remove Public-Waypoint Leftovers

**Files:**
- Delete: `apps/waypoint/serializers.py`
- Test: `apps/route/tests.py`

- [ ] **Step 1: Run existing guard tests before deleting the dead waypoint shell**

Run:

```bash
.venv/bin/python manage.py test \
  apps.route.tests.RouteApiTests.test_old_waypoint_endpoints_should_be_removed \
  apps.route.tests.RouteApiTests.test_api_v1_should_not_mount_public_waypoint_urls \
  apps.route.tests.RouteApiTests.test_detail_should_return_nested_waypoints_in_sequence_order \
  apps.route.tests.RouteApiTests.test_update_with_waypoints_should_replace_full_waypoint_set \
  -v 2
```

Expected:
- `OK`
- This proves the public `/api/v1/waypoints` surface is already gone and route-owned nested waypoints are the live behavior.

- [ ] **Step 2: Delete the obsolete waypoint serializer module**

Apply this patch:

```diff
*** Begin Patch
*** Delete File: apps/waypoint/serializers.py
*** End Patch
```

- [ ] **Step 3: Verify no code still imports the removed waypoint serializers**

Run:

```bash
rg -n "apps\\.waypoint\\.serializers|WaypointCreateSerializer|WaypointPatchSerializer|WaypointReadSerializer" apps
```

Expected:
- no matches

- [ ] **Step 4: Re-run the route guard tests after deletion**

Run:

```bash
.venv/bin/python manage.py test \
  apps.route.tests.RouteApiTests.test_old_waypoint_endpoints_should_be_removed \
  apps.route.tests.RouteApiTests.test_api_v1_should_not_mount_public_waypoint_urls \
  apps.route.tests.RouteApiTests.test_detail_should_return_nested_waypoints_in_sequence_order \
  apps.route.tests.RouteApiTests.test_update_with_waypoints_should_replace_full_waypoint_set \
  -v 2
```

Expected:
- `OK`

- [ ] **Step 5: Commit the waypoint-shell deletion**

Run:

```bash
git add apps/waypoint/serializers.py
git commit -m "refactor: remove obsolete waypoint serializer shell"
```

### Task 2: Add a Shared Business Serializer Mixin

**Files:**
- Create: `apps/api_v1/serializers.py`
- Modify: `apps/api_v1/tests.py`
- Modify: `apps/route/serializers.py`
- Modify: `apps/drone/serializers.py`
- Modify: `apps/mission/serializers.py`
- Modify: `apps/flight_record/serializers.py`
- Test: `apps/api_v1/tests.py`
- Test: `apps/drone/tests.py`
- Test: `apps/mission/tests.py`
- Test: `apps/route/tests.py`
- Test: `apps/flight_record/tests.py`

- [ ] **Step 1: Write a failing unit test for the shared mixin**

Add this test block to `apps/api_v1/tests.py` near the existing API v1 tests:

```python
from django.test import SimpleTestCase
from rest_framework import serializers

from apps.api_v1.serializers import RejectUnknownFieldsMixin


class RejectUnknownFieldsMixinTests(SimpleTestCase):
    class DemoSerializer(RejectUnknownFieldsMixin, serializers.Serializer):
        name = serializers.CharField()

    def test_rejects_unknown_fields(self):
        serializer = self.DemoSerializer(data={"name": "demo", "unexpected": "x"})

        self.assertFalse(serializer.is_valid())
        self.assertEqual(serializer.errors, {"unexpected": ["该字段在此接口不可写"]})

    def test_accepts_known_fields(self):
        serializer = self.DemoSerializer(data={"name": "demo"})

        self.assertTrue(serializer.is_valid(), serializer.errors)
```

- [ ] **Step 2: Run the new unit test and confirm it fails because the helper does not exist yet**

Run:

```bash
.venv/bin/python manage.py test apps.api_v1.tests.RejectUnknownFieldsMixinTests -v 2
```

Expected:
- `FAIL`
- import error for `apps.api_v1.serializers` or missing `RejectUnknownFieldsMixin`

- [ ] **Step 3: Implement the shared mixin and adopt it in active business serializers**

Create `apps/api_v1/serializers.py` with:

```python
from rest_framework import serializers


class RejectUnknownFieldsMixin:
    unknown_field_error = "该字段在此接口不可写"

    def validate(self, attrs):
        initial_data = getattr(self, "initial_data", None)
        if isinstance(initial_data, dict):
            unknown_fields = sorted(set(initial_data.keys()) - set(self.fields.keys()))
            if unknown_fields:
                raise serializers.ValidationError(
                    {field: self.unknown_field_error for field in unknown_fields}
                )
        return super().validate(attrs)
```

Update `apps/route/serializers.py`:

```python
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from apps.api_v1.serializers import RejectUnknownFieldsMixin
from apps.route.models import Route


class RouteWaypointSerializer(RejectUnknownFieldsMixin, serializers.Serializer):
    sequence = serializers.IntegerField(min_value=1)
    latitude = serializers.DecimalField(max_digits=12, decimal_places=8)
    longitude = serializers.DecimalField(max_digits=12, decimal_places=8)
    altitude = serializers.DecimalField(max_digits=10, decimal_places=2)


class RouteWriteSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    waypoints = RouteWaypointSerializer(many=True, required=False)
```

Update `apps/drone/serializers.py` so both write serializers inherit the mixin and call `super().validate(attrs)` first:

```python
from apps.api_v1.serializers import RejectUnknownFieldsMixin


class DroneClaimSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        current_tenant = require_request_tenant(self.context)
        code = attrs.get("code")
        device_sn = attrs.get("device_sn")
        device_index = _device_index_for_sn(device_sn)

        if device_index is None:
            raise serializers.ValidationError({"device_sn": "共享设备池中不存在该 device_sn"})

        if code and Drone.objects.filter(tenant=current_tenant, code=code).exists():
            raise serializers.ValidationError({"code": "当前租户下已存在相同业务编码"})

        claimed_drone = Drone.objects.filter(device_sn=device_sn).first()
        if claimed_drone is not None:
            if claimed_drone.tenant_id == current_tenant.id:
                raise serializers.ValidationError({"device_sn": "当前租户下已认领该设备"})
            raise serializers.ValidationError({"device_sn": "该设备已被其他租户认领"})

        self._device_index = device_index
        return attrs


class DroneUpdateSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        current_tenant = require_request_tenant(self.context)
        instance = getattr(self, "instance", None)
        code = attrs.get("code", instance.code if instance is not None else None)

        if code and Drone.objects.filter(tenant=current_tenant, code=code).exclude(
            pk=getattr(instance, "pk", None)
        ).exists():
            raise serializers.ValidationError({"code": "当前租户下已存在相同业务编码"})
        return attrs
```

Update `apps/mission/serializers.py`:

```python
from apps.api_v1.serializers import RejectUnknownFieldsMixin


class MissionCreateSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        current_tenant = require_request_tenant(self.context)
        route = attrs.get("route")
        drone = attrs.get("drone")
        pilot = attrs.get("pilot")

        if route is not None and route.tenant_id != current_tenant.id:
            raise serializers.ValidationError({"route": "仅允许绑定当前租户下的航线"})
        if drone is not None and drone.tenant_id != current_tenant.id:
            raise serializers.ValidationError({"drone": "仅允许绑定当前租户下的无人机"})
        if pilot is not None and pilot.tenant_id != current_tenant.id:
            raise serializers.ValidationError({"pilot": "仅允许绑定当前租户下的成员"})
        if pilot is not None and pilot.status != TenantMemberStatus.ACTIVE:
            raise serializers.ValidationError({"pilot": "仅允许分配给 ACTIVE 成员"})
        if pilot is not None:
            staff = getattr(pilot.user, "staff_profile", None)
            if staff is None:
                raise serializers.ValidationError({"pilot": "pilot 对应账号必须存在 staff_profile"})
            if staff.employment_status != EmploymentStatus.ACTIVE:
                raise serializers.ValidationError({"pilot": "仅允许分配给在职飞手"})
            if not pilot.role_bindings.filter(
                system_role__code="pilot_operator",
                system_role__status=DirectoryStatus.ACTIVE,
                status=TenantMemberRoleStatus.GRANTED,
            ).exists():
                raise serializers.ValidationError({"pilot": "仅允许分配给飞手类型（pilot_operator）"})
        return attrs


class MissionUpdateSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        return attrs
```

Update `apps/flight_record/serializers.py`:

```python
from apps.api_v1.serializers import RejectUnknownFieldsMixin


class FlightRecordWriteSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        current_tenant = require_request_tenant(self.context)
        instance = getattr(self, "instance", None)
        if instance is not None and "status" in self.initial_data:
            raise serializers.ValidationError({"status": "status 不可通过 PATCH 直接修改，请使用状态动作接口"})

        flight_no = attrs.get("flight_no", instance.flight_no if instance is not None else None)
        start_time = attrs.get("start_time", instance.start_time if instance is not None else None)
        end_time = attrs.get("end_time", instance.end_time if instance is not None else None)
        mission = attrs.get("mission", instance.mission if instance is not None else None)
        drone = attrs.get("drone", instance.drone if instance is not None else None)
        pilot = attrs.get("pilot", instance.pilot if instance is not None else None)

        if flight_no and FlightRecord.objects.filter(tenant=current_tenant, flight_no=flight_no).exclude(
            pk=getattr(instance, "pk", None)
        ).exists():
            raise serializers.ValidationError({"flight_no": "当前租户下已存在相同架次编号"})

        if start_time and end_time and end_time < start_time:
            raise serializers.ValidationError({"end_time": "结束时间不能早于开始时间"})

        if mission is not None and mission.tenant_id != current_tenant.id:
            raise serializers.ValidationError({"mission": "仅允许绑定当前租户下的任务"})
        if drone is not None and drone.tenant_id != current_tenant.id:
            raise serializers.ValidationError({"drone": "仅允许绑定当前租户下的无人机"})
        if pilot is not None and pilot.tenant_id != current_tenant.id:
            raise serializers.ValidationError({"pilot": "仅允许绑定当前租户下的成员"})
        return attrs
```

- [ ] **Step 4: Run focused serializer and API tests**

Run:

```bash
.venv/bin/python manage.py test \
  apps.api_v1.tests.RejectUnknownFieldsMixinTests \
  apps.route.tests.RouteApiTests \
  apps.drone.tests.DroneApiTests \
  apps.mission.tests.MissionApiTests \
  apps.flight_record.tests.FlightRecordApiTests \
  -v 2
```

Expected:
- `OK`

- [ ] **Step 5: Commit the shared serializer cleanup**

Run:

```bash
git add \
  apps/api_v1/serializers.py \
  apps/api_v1/tests.py \
  apps/route/serializers.py \
  apps/drone/serializers.py \
  apps/mission/serializers.py \
  apps/flight_record/serializers.py
git commit -m "refactor: share business serializer unknown-field validation"
```

### Task 3: Consolidate Drone, Route, and Mission View Repetition

**Files:**
- Modify: `apps/drone/views.py:155-259`
- Modify: `apps/route/views.py:126-358`
- Modify: `apps/mission/views.py:127-266`
- Test: `apps/drone/tests.py`
- Test: `apps/route/tests.py`
- Test: `apps/mission/tests.py`

- [ ] **Step 1: Run focused guard tests for the three active viewsets**

Run:

```bash
.venv/bin/python manage.py test \
  apps.drone.tests.DroneApiTests \
  apps.route.tests.RouteApiTests \
  apps.mission.tests.MissionApiTests \
  -v 2
```

Expected:
- `OK`

- [ ] **Step 2: Simplify `DroneViewSet` with local write-response helpers**

Refactor `apps/drone/views.py` by extracting the duplicated serializer-error and success-response flow:

```python
class DroneViewSet(
    BusinessApiResponseMixin,
    TenantScopedBusinessMixin,
    PermissionMapMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    def _write_error_response(self, errors):
        if self._contains_duplicate_error(errors):
            return Response(
                standard_error_payload(StandardCode.DUPLICATE, "资源已存在", errors),
                status=status.HTTP_409_CONFLICT,
            )
        return Response(validation_error_payload(errors), status=status.HTTP_400_BAD_REQUEST)

    def _response_for_drone(self, drone: Drone, *, status_code: int):
        payload = self._payload(drone)
        headers = self.get_success_headers(payload) if status_code == status.HTTP_201_CREATED else {}
        return Response(payload, status=status_code, headers=headers)

    def _validated_serializer_or_response(self, serializer):
        serializer.is_valid(raise_exception=False)
        if serializer.errors:
            return None, self._write_error_response(serializer.errors)
        return serializer, None
```

Use those helpers in `create`, `update`, and `partial_update` so each method becomes a short orchestration wrapper instead of repeating the same branches three times.

- [ ] **Step 3: Simplify `RouteViewSet` and `MissionViewSet` with local payload and body-check helpers**

Refactor `apps/route/views.py`:

```python
class RouteViewSet(
    BusinessApiResponseMixin,
    TenantScopedBusinessMixin,
    PermissionMapMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    def _route_response(self, route: Route, *, status_code: int):
        payload = self._payload(route)
        headers = self.get_success_headers(payload) if status_code == status.HTTP_201_CREATED else {}
        return Response(payload, status=status_code, headers=headers)

    def _reject_body(self, request, *, action_name: str):
        if request.data:
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    f"{action_name} 请求不支持提交 body 参数",
                    {"body": "不支持请求体，请移除 body 后重试"},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None
```

Use `_route_response()` in `create`, `update`, `partial_update`, and `publish`. Use `_reject_body()` in `publish` and `destroy`.

Refactor `apps/mission/views.py`:

```python
class MissionViewSet(
    BusinessApiResponseMixin,
    TenantScopedBusinessMixin,
    PermissionMapMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    def _mission_response(self, mission: Mission, *, status_code: int):
        payload = self._payload(mission)
        headers = self.get_success_headers(payload) if status_code == status.HTTP_201_CREATED else {}
        return Response(payload, status=status_code, headers=headers)

    def _patch_body_required(self, request):
        if request.data:
            return None
        return Response(
            standard_error_payload(
                StandardCode.INVALID_PARAMS,
                "PATCH 请求至少包含一个可写字段",
                {"body": "请至少提交一个可写字段"},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )
```

Keep the route-published precondition and the DJI cancel semantics exactly as they are now; only reduce repeated response assembly and repeated request-body checks.

- [ ] **Step 4: Re-run the focused viewset tests**

Run:

```bash
.venv/bin/python manage.py test \
  apps.drone.tests.DroneApiTests \
  apps.route.tests.RouteApiTests \
  apps.mission.tests.MissionApiTests \
  -v 2
```

Expected:
- `OK`

- [ ] **Step 5: Commit the active-viewset cleanup**

Run:

```bash
git add apps/drone/views.py apps/route/views.py apps/mission/views.py
git commit -m "refactor: simplify drone route and mission view flows"
```

### Task 4: Shrink `FlightRecordViewSet` Without Changing Behavior

**Files:**
- Modify: `apps/flight_record/views.py:309-572`
- Test: `apps/flight_record/tests.py`

- [ ] **Step 1: Run the focused flight-record test subset first**

Run:

```bash
.venv/bin/python manage.py test \
  apps.flight_record.tests.FlightRecordApiTests.test_create_flight_record_should_return_success \
  apps.flight_record.tests.FlightRecordApiTests.test_complete_flight_record_should_return_success \
  apps.flight_record.tests.FlightRecordApiTests.test_complete_completed_flight_record_should_be_idempotent_success \
  apps.flight_record.tests.FlightRecordApiTests.test_complete_flight_record_with_body_should_return_invalid_params \
  apps.flight_record.tests.FlightRecordApiTests.test_abort_flight_record_should_return_success \
  apps.flight_record.tests.FlightRecordApiTests.test_abort_aborted_flight_record_should_be_idempotent_success \
  apps.flight_record.tests.FlightRecordApiTests.test_abort_flight_record_with_body_should_return_invalid_params \
  -v 2
```

Expected:
- `OK`

- [ ] **Step 2: Refactor `FlightRecordViewSet` into smaller helpers and remove low-signal comments**

Refactor `apps/flight_record/views.py` with helpers like:

```python
class FlightRecordViewSet(
    BusinessApiResponseMixin,
    TenantScopedBusinessMixin,
    PermissionMapMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    def _record_response(self, record: FlightRecord, *, status_code: int):
        payload = self._record_payload(record)
        headers = self.get_success_headers(payload) if status_code == status.HTTP_201_CREATED else {}
        return Response(payload, status=status_code, headers=headers)

    def _nonempty_patch_error(self):
        return Response(
            standard_error_payload(
                StandardCode.INVALID_PARAMS,
                "PATCH 请求至少包含一个可写字段",
                {"body": "请至少提交一个可写字段"},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    def _body_not_allowed(self, *, action_name: str):
        return Response(
            standard_error_payload(
                StandardCode.INVALID_PARAMS,
                f"{action_name} 请求不支持提交 body 参数",
                {"body": "不支持请求体，请移除 body 后重试"},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    def _transition_record(self, request, *, target_status, action_name, conflict_status, conflict_message):
        record = self.get_object()
        before_payload = self._record_payload(record)
        if record.status == target_status:
            log_action(
                request=request,
                action=action_name,
                target_type="flight_record",
                target_id=record.id,
                before_data=before_payload,
                after_data=before_payload,
            )
            return Response(before_payload, status=status.HTTP_200_OK)
        if record.status == conflict_status:
            return Response(
                standard_error_payload(
                    StandardCode.STATE_CONFLICT,
                    conflict_message,
                    {"flight_record_id": record.id, "status": record.status},
                ),
                status=status.HTTP_409_CONFLICT,
            )
        record.status = target_status
        record.save(update_fields=["status", "updated_at"])
        after_payload = self._record_payload(record)
        log_action(
            request=request,
            action=action_name,
            target_type="flight_record",
            target_id=record.id,
            before_data=before_payload,
            after_data=after_payload,
        )
        return Response(after_payload, status=status.HTTP_200_OK)
```

Use `_record_response()` in `create`, `update`, and `partial_update`. Replace the two duplicated transition methods with `_transition_record()` while preserving:

1. idempotent `complete`
2. idempotent `abort`
3. the same `409` conflict payloads
4. the same audit action names

Delete the long repetitive inline comments around `list`, `create`, `retrieve`, `partial_update`, `complete`, and `abort`.

- [ ] **Step 3: Run the full flight-record test module**

Run:

```bash
.venv/bin/python manage.py test apps.flight_record.tests.FlightRecordApiTests -v 2
```

Expected:
- `OK`

- [ ] **Step 4: Run `git diff --stat` and confirm the file materially shrank**

Run:

```bash
git diff --stat -- apps/flight_record/views.py
```

Expected:
- the diff shows a net reduction in `apps/flight_record/views.py`

- [ ] **Step 5: Commit the flight-record simplification**

Run:

```bash
git add apps/flight_record/views.py
git commit -m "refactor: simplify flight record viewset flow"
```

### Task 5: Simplify `dji_bff` Internals and Run Full Verification

**Files:**
- Modify: `apps/dji_bff/views.py:18-113`
- Modify: `apps/dji_bff/services.py:9-101`
- Test: `apps/dji_bff/tests.py`
- Test: full suite via `manage.py test`

- [ ] **Step 1: Run focused `dji_bff` tests before changing helpers**

Run:

```bash
.venv/bin/python manage.py test apps.dji_bff.tests.DjiBffSyncAndInternalApiTests -v 2
```

Expected:
- `OK`

- [ ] **Step 2: Collapse duplicated internal response and callback helper code**

Refactor `apps/dji_bff/views.py` to share the internal POST precondition:

```python
def _require_internal_post(request):
    auth_error = _require_internal_token(request)
    if auth_error is not None:
        return auth_error
    if request.method != "POST":
        raise Http404
    return None


def _gateway_error_response(exc: DjiGatewayError):
    return _error(
        "DJI 同步失败",
        status=exc.status_code if exc.status_code >= 400 else 502,
        code="E0001",
        data={"detail": str(exc), "upstream": exc.data},
    )
```

Use those helpers inside `_run_sync()` and `_handle_callback()` so the token + method gate only lives in one place.

Refactor `apps/dji_bff/services.py` to share the duplicated “resolve one row, mutate, log result” code:

```python
def _set_route_published(route_index):
    route_index.is_published = True
    route_index.save(update_fields=["is_published", "updated_at"])


def _mark_media_synced(media_index, *, now):
    media_index.sync_status = SyncStatus.SYNCED
    media_index.last_sync_at = now
    media_index.error_msg = ""
    media_index.save(update_fields=["sync_status", "last_sync_at", "error_msg", "updated_at"])


def _callback_result(*, resolved_count: int, ignored_count: int) -> dict[str, int]:
    return {"resolved_count": resolved_count, "ignored_count": ignored_count}
```

Use these helpers in `handle_wayline_upload_callback()` and `handle_media_upload_callback()` without changing:

1. callback input matching rules
2. audit action names
3. persisted fields
4. returned `resolved_count` / `ignored_count` contract

- [ ] **Step 3: Re-run the focused `dji_bff` tests**

Run:

```bash
.venv/bin/python manage.py test apps.dji_bff.tests.DjiBffSyncAndInternalApiTests -v 2
```

Expected:
- `OK`

- [ ] **Step 4: Run the full test suite**

Run:

```bash
.venv/bin/python manage.py test
```

Expected:
- `OK`
- no failing tests

- [ ] **Step 5: Commit the final cleanup pass**

Run:

```bash
git add apps/dji_bff/views.py apps/dji_bff/services.py
git commit -m "refactor: simplify dji bff helpers"
```
