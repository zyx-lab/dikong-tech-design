# Mission Execution Window and Media Auto-Bind Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add mission execution status and execution time window tracking, expose a single mission advance action, and auto-bind synced DJI media to missions by `device_sn + captured_at`, while explicitly deferring the derived “飞行中” feature and ignoring `flight_record`/`jobId` in this phase.

**Architecture:** Keep `Mission` as the only business anchor for execution and media归档 in this phase. Persist mission state as `待执行 / 执行中 / 执行完成` plus `started_at / finished_at`. Add `POST /api/v1/missions/{id}/advance` for the only allowed state progression. Keep DJI media ingestion inside `apps/dji_bff/tasks.py`; after each media upsert, attempt a conservative unique mission match within the same tenant using `device_sn` and the mission execution window, while preserving manual mission bindings and never touching `flight_record`.

**Tech Stack:** Django 5.1, Django REST Framework, drf-spectacular, Django ORM transactions, existing DJI HTTP sync path.

**Status:** Implemented on branch `feat/mission-execution-window-media-bind`. `apps/mission/urls.py` did not need a direct edit because the existing router already exposes the `ViewSet @action(url_path="advance")` route.

---

## File map and responsibilities

- **Modify:** `apps/mission/models.py`
  - Replace the old bound/unbound status model with execution status and time window fields.
  - Add model-level transition/time consistency validation that does not depend on flight state.
- **Modify:** `apps/mission/serializers.py`
  - Enforce `route` + `drone` on create.
  - Allow editing only while mission is `待执行`.
  - Expose `started_at` / `finished_at` in read payloads.
- **Modify:** `apps/mission/views.py`
  - Add `advance` action.
  - Guard same-drone concurrent execution.
  - Document new schema surface.
- **Create:** `apps/mission/migrations/0008_mission_execution_window.py`
  - Add `started_at` / `finished_at`.
  - Remap legacy status values to the new execution status baseline.
- **Modify:** `apps/mission/tests.py`
  - Lock the new create/update/advance/state-window behavior.
- **Modify:** `apps/mission/test_live_api.py`
  - Update the live contract smoke test for the new status and `advance` action.
- **Modify:** `apps/dji_bff/tasks.py`
  - Add mission auto-match helper and call it during media sync.
- **Modify:** `apps/dji_bff/tests.py`
  - Lock auto-bind behavior, collision behavior, and “manual binding wins” behavior.
- **Modify:** `apps/media_file/serializers.py`
  - Remove the old `DRONE_BOUND` status assumption from manual bind validation.
- **Modify:** `apps/media_file/tests.py`
  - Update any assertions that still rely on the old mission status enum.
- **Modify:** `apps/api_v1/tests.py`
  - Add `/api/v1/missions/{id}/advance` to schema surface expectations.
- **Modify:** `apps/access/test_live_schema_api.py`
  - Keep removed mission action assertions and add the new `advance` path assertion.
- **Modify:** `README.md`
  - Document the new mission lifecycle and media auto-bind rule.

---

### Task 1: Lock the mission execution contract with failing tests

**Files:**
- Modify: `apps/mission/tests.py`
- Modify: `apps/mission/test_live_api.py`
- Test: `apps/mission/tests.py`
- Test: `apps/mission/test_live_api.py`

- [x] **Step 1: Write the failing mission API tests for the new execution lifecycle**

```python
from datetime import timedelta
from django.utils import timezone
from apps.mission.models import MissionStatus


def test_create_should_require_route_and_drone(self):
    response = self.client.post(
        "/missions",
        {
            "name": "缺少无人机任务",
            "route": self.route.id,
            "pilot": self.pilot_member.id,
        },
        format="json",
    )

    self.assertEqual(response.status_code, 400)
    self.assertEqual(response.data["code"], "B0001")
    self.assertEqual(response.data["data"], {"drone": ["该字段是必填项。"]})


def test_put_should_allow_route_and_drone_change_only_when_pending(self):
    other_route = Route.objects.create(tenant=self.tenant, name="改绑航线")
    other_drone = Drone.objects.create(
        tenant=self.tenant,
        code="MISSION-DRONE-002",
        name="改绑无人机",
        model="M30",
        device_sn="MISSION-SN-002",
    )
    mission = Mission.objects.create(
        tenant=self.tenant,
        name="待执行任务",
        route=self.route,
        route_name=self.route.name,
        drone=self.drone,
        device_sn=self.drone.device_sn,
        drone_name=self.drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.PENDING,
    )

    response = self.client.put(
        f"/missions/{mission.id}",
        {"route": other_route.id, "drone": other_drone.id},
        format="json",
    )

    self.assertEqual(response.status_code, 200, response.data)
    mission.refresh_from_db()
    self.assertEqual(mission.route_id, other_route.id)
    self.assertEqual(mission.drone_id, other_drone.id)
    self.assertEqual(mission.device_sn, other_drone.device_sn)


def test_put_should_reject_any_update_when_status_is_running(self):
    mission = Mission.objects.create(
        tenant=self.tenant,
        name="执行中任务",
        route=self.route,
        route_name=self.route.name,
        drone=self.drone,
        device_sn=self.drone.device_sn,
        drone_name=self.drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.RUNNING,
        started_at=timezone.now(),
    )

    response = self.client.put(
        f"/missions/{mission.id}",
        {"remark": "不允许修改"},
        format="json",
    )

    self.assertEqual(response.status_code, 409)
    self.assertEqual(response.data["code"], "C0201")


def test_advance_should_move_pending_to_running_and_write_started_at(self):
    mission = Mission.objects.create(
        tenant=self.tenant,
        name="状态推进任务",
        route=self.route,
        route_name=self.route.name,
        drone=self.drone,
        device_sn=self.drone.device_sn,
        drone_name=self.drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.PENDING,
    )

    response = self.client.post(f"/missions/{mission.id}/advance")

    self.assertEqual(response.status_code, 200, response.data)
    mission.refresh_from_db()
    self.assertEqual(mission.status, MissionStatus.RUNNING)
    self.assertIsNotNone(mission.started_at)
    self.assertIsNone(mission.finished_at)


def test_advance_should_move_running_to_completed_and_write_finished_at(self):
    started_at = timezone.now() - timedelta(minutes=10)
    mission = Mission.objects.create(
        tenant=self.tenant,
        name="状态完成任务",
        route=self.route,
        route_name=self.route.name,
        drone=self.drone,
        device_sn=self.drone.device_sn,
        drone_name=self.drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.RUNNING,
        started_at=started_at,
    )

    response = self.client.post(f"/missions/{mission.id}/advance")

    self.assertEqual(response.status_code, 200, response.data)
    mission.refresh_from_db()
    self.assertEqual(mission.status, MissionStatus.COMPLETED)
    self.assertEqual(mission.started_at, started_at)
    self.assertIsNotNone(mission.finished_at)


def test_advance_should_reject_completed_mission(self):
    mission = Mission.objects.create(
        tenant=self.tenant,
        name="已完成任务",
        route=self.route,
        route_name=self.route.name,
        drone=self.drone,
        device_sn=self.drone.device_sn,
        drone_name=self.drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.COMPLETED,
        started_at=timezone.now() - timedelta(minutes=10),
        finished_at=timezone.now(),
    )

    response = self.client.post(f"/missions/{mission.id}/advance")

    self.assertEqual(response.status_code, 409)
    self.assertEqual(response.data["code"], "C0201")


def test_advance_should_reject_second_running_mission_for_same_drone(self):
    Mission.objects.create(
        tenant=self.tenant,
        name="占用无人机任务",
        route=self.route,
        route_name=self.route.name,
        drone=self.drone,
        device_sn=self.drone.device_sn,
        drone_name=self.drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.RUNNING,
        started_at=timezone.now() - timedelta(minutes=5),
    )
    waiting = Mission.objects.create(
        tenant=self.tenant,
        name="等待执行任务",
        route=self.route,
        route_name=self.route.name,
        drone=self.drone,
        device_sn=self.drone.device_sn,
        drone_name=self.drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.PENDING,
    )

    response = self.client.post(f"/missions/{waiting.id}/advance")

    self.assertEqual(response.status_code, 409)
    self.assertEqual(response.data["code"], "C0201")
```

- [x] **Step 2: Run the mission test subset to verify it fails for the expected reasons**

Run:
```bash
.venv/bin/python manage.py test \
  apps.mission.tests.MissionApiTests \
  apps.mission.test_live_api.LiveMissionApiTests \
  -v 2
```

Expected:
- FAIL because `MissionStatus.PENDING/RUNNING/COMPLETED` does not exist
- FAIL because `/missions/{id}/advance` is unmounted
- FAIL because `Mission` does not have `started_at` / `finished_at`

- [x] **Step 3: Add the live contract test for the new action surface**

```python
def test_create_advance_and_delete_should_follow_live_http_contract(self):
    create_response = self.client.post(
        "/api/v1/missions",
        {
            "name": "实时巡检任务",
            "route": self.route.id,
            "drone": self.drone.id,
            "pilot": self.pilot_member.id,
        },
        format="json",
    )
    self.assertEqual(create_response.status_code, 201)
    mission_id = create_response.json()["data"]["id"]
    self.assertEqual(create_response.json()["data"]["status"], 0)
    self.assertIsNone(create_response.json()["data"]["started_at"])
    self.assertIsNone(create_response.json()["data"]["finished_at"])

    advance_response = self.client.post(f"/api/v1/missions/{mission_id}/advance")
    self.assertEqual(advance_response.status_code, 200)
    self.assertEqual(advance_response.json()["data"]["status"], 1)
    self.assertIsNotNone(advance_response.json()["data"]["started_at"])

    finish_response = self.client.post(f"/api/v1/missions/{mission_id}/advance")
    self.assertEqual(finish_response.status_code, 200)
    self.assertEqual(finish_response.json()["data"]["status"], 2)
    self.assertIsNotNone(finish_response.json()["data"]["finished_at"])
```

- [x] **Step 4: Commit the failing contract tests only**

```bash
git add apps/mission/tests.py apps/mission/test_live_api.py
git commit -m "test: lock mission execution lifecycle contract"
```

### Task 2: Implement the mission model, migration, and serializer contract

**Files:**
- Modify: `apps/mission/models.py`
- Create: `apps/mission/migrations/0008_mission_execution_window.py`
- Modify: `apps/mission/serializers.py`
- Test: `apps/mission/tests.py`

- [x] **Step 1: Add the failing model-level validation tests for execution timestamps**

```python
def test_model_should_require_started_at_when_status_is_running(self):
    mission = Mission(
        tenant=self.tenant,
        name="缺少开始时间任务",
        route=self.route,
        route_name=self.route.name,
        drone=self.drone,
        device_sn=self.drone.device_sn,
        drone_name=self.drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.RUNNING,
    )

    with self.assertRaises(ValidationError):
        mission.save()


def test_model_should_require_finished_at_when_status_is_completed(self):
    mission = Mission(
        tenant=self.tenant,
        name="缺少结束时间任务",
        route=self.route,
        route_name=self.route.name,
        drone=self.drone,
        device_sn=self.drone.device_sn,
        drone_name=self.drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.COMPLETED,
        started_at=timezone.now() - timedelta(minutes=5),
    )

    with self.assertRaises(ValidationError):
        mission.save()
```

- [x] **Step 2: Run the narrow model test subset and verify it fails**

Run:
```bash
.venv/bin/python manage.py test \
  apps.mission.tests.MissionApiTests.test_model_should_require_started_at_when_status_is_running \
  apps.mission.tests.MissionApiTests.test_model_should_require_finished_at_when_status_is_completed \
  -v 2
```

Expected:
- FAIL because the new fields and validation do not exist yet

- [x] **Step 3: Implement the mission model and migration with the smallest backward-compatible shape**

```python
class MissionStatus(models.IntegerChoices):
    PENDING = 0, "待执行"
    RUNNING = 1, "执行中"
    COMPLETED = 2, "执行完成"


class Mission(models.Model):
    # existing fields...
    started_at = models.DateTimeField("开始执行时间", null=True, blank=True)
    finished_at = models.DateTimeField("执行完成时间", null=True, blank=True)

    def clean(self):
        # existing tenant / soft-delete checks...
        if self.status == MissionStatus.PENDING:
            if self.finished_at is not None:
                raise ValidationError({"finished_at": "待执行任务不允许写入 finished_at"})
        elif self.status == MissionStatus.RUNNING:
            if self.started_at is None:
                raise ValidationError({"started_at": "执行中任务必须提供 started_at"})
            if self.finished_at is not None:
                raise ValidationError({"finished_at": "执行中任务不允许写入 finished_at"})
        elif self.status == MissionStatus.COMPLETED:
            if self.started_at is None:
                raise ValidationError({"started_at": "执行完成任务必须提供 started_at"})
            if self.finished_at is None:
                raise ValidationError({"finished_at": "执行完成任务必须提供 finished_at"})
            if self.finished_at < self.started_at:
                raise ValidationError({"finished_at": "finished_at 不能早于 started_at"})
```

```python
# apps/mission/migrations/0008_mission_execution_window.py
from django.db import migrations, models


def remap_legacy_statuses(apps, schema_editor):
    Mission = apps.get_model("mission", "Mission")
    Mission.objects.filter(status__in=[0, 1]).update(status=0)


class Migration(migrations.Migration):
    dependencies = [("mission", "0007_localize_mission_status_and_drop_dji_job")]

    operations = [
        migrations.AddField(
            model_name="mission",
            name="started_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="开始执行时间"),
        ),
        migrations.AddField(
            model_name="mission",
            name="finished_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="执行完成时间"),
        ),
        migrations.RunPython(remap_legacy_statuses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="mission",
            name="status",
            field=models.PositiveSmallIntegerField(
                choices=[(0, "待执行"), (1, "执行中"), (2, "执行完成")],
                default=0,
                verbose_name="任务状态",
            ),
        ),
    ]
```

- [x] **Step 4: Update serializers so the new contract matches the feature scope**

```python
class MissionReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Mission
        fields = [
            "id",
            "name",
            "route",
            "route_name",
            "drone",
            "device_sn",
            "drone_name",
            "pilot",
            "pilot_name",
            "scheduled_at",
            "started_at",
            "finished_at",
            "remark",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class MissionCreateSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Mission
        fields = ["name", "route", "drone", "pilot", "scheduled_at", "remark"]
        extra_kwargs = {
            "route": {"required": True, "allow_null": False},
            "drone": {"required": True, "allow_null": False},
        }


class MissionUpdateSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    route = serializers.PrimaryKeyRelatedField(queryset=Route.objects.all(), required=False)

    class Meta:
        model = Mission
        fields = ["name", "route", "drone", "scheduled_at", "remark"]
        extra_kwargs = {
            "drone": {"required": False, "allow_null": False},
        }

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if getattr(self.instance, "status", None) != MissionStatus.PENDING:
            raise serializers.ValidationError("仅允许修改待执行任务")
        return attrs
```

- [x] **Step 5: Run the mission test subset to verify it now passes**

Run:
```bash
.venv/bin/python manage.py test apps.mission.tests.MissionApiTests -v 2
```

Expected:
- PASS for create/update/model timestamp tests
- Remaining FAILs only around the unimplemented `advance` action

- [x] **Step 6: Commit the model and serializer contract change**

```bash
git add apps/mission/models.py apps/mission/serializers.py apps/mission/migrations/0008_mission_execution_window.py apps/mission/tests.py
git commit -m "feat: add mission execution status and time window"
```

### Task 3: Add the mission advance action and same-drone concurrency guard

**Files:**
- Modify: `apps/mission/views.py`
- Note: `apps/mission/urls.py` remained unchanged because the existing router auto-exposes `@action(url_path="advance")`
- Modify: `apps/mission/tests.py`
- Modify: `apps/mission/test_live_api.py`
- Modify: `apps/api_v1/tests.py`
- Modify: `apps/access/test_live_schema_api.py`

- [x] **Step 1: Add the failing schema tests for the new mission action**

```python
def test_business_schema_should_expose_mission_advance_path(self):
    response = self.client.get("/api/v1/docs/schema/")
    self.assertEqual(response.status_code, 200)
    schema = response.json()

    self.assertIn("/api/v1/missions/{id}/advance", schema["paths"])
    self.assertEqual(set(schema["paths"]["/api/v1/missions/{id}/advance"].keys()), {"post"})
    self.assertNotIn("requestBody", schema["paths"]["/api/v1/missions/{id}/advance"]["post"])
```

- [x] **Step 2: Run the schema tests to verify the new path is missing**

Run:
```bash
.venv/bin/python manage.py test \
  apps.api_v1.tests.OpenApiDocsTests \
  apps.access.test_live_schema_api.LiveIamSchemaTests \
  -v 2
```

Expected:
- FAIL because `/api/v1/missions/{id}/advance` is not in the schema

- [x] **Step 3: Implement the advance action with a transactional same-drone guard**

```python
from rest_framework.decorators import action


def _mission_state_conflict(message: str, *, mission_id: int, status_value: int):
    return Response(
        standard_error_payload(
            StandardCode.STATE_CONFLICT,
            message,
            {"mission_id": mission_id, "status": status_value},
        ),
        status=status.HTTP_409_CONFLICT,
    )


@action(detail=True, methods=["post"], url_path="advance")
@transaction.atomic
def advance(self, request, *args, **kwargs):
    if request.data:
        return Response(
            standard_error_payload(
                StandardCode.INVALID_PARAMS,
                "advance 请求不支持请求体",
                {"body": "不支持请求体，请移除 body 后重试"},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    mission = self.get_queryset().select_for_update().get(pk=kwargs["pk"])
    before_data = snapshot(mission)

    if mission.status == MissionStatus.PENDING:
        occupied = (
            Mission.objects.select_for_update()
            .filter(
                tenant_id=mission.tenant_id,
                drone_id=mission.drone_id,
                is_deleted=False,
                status=MissionStatus.RUNNING,
            )
            .exclude(pk=mission.pk)
            .exists()
        )
        if occupied:
            return _mission_state_conflict(
                "当前无人机已有执行中的任务",
                mission_id=mission.id,
                status_value=mission.status,
            )
        mission.status = MissionStatus.RUNNING
        mission.started_at = timezone.now()
        mission.finished_at = None
        mission.save(update_fields=["status", "started_at", "finished_at", "updated_at"])
    elif mission.status == MissionStatus.RUNNING:
        mission.status = MissionStatus.COMPLETED
        mission.finished_at = timezone.now()
        mission.save(update_fields=["status", "finished_at", "updated_at"])
    else:
        return _mission_state_conflict(
            "当前任务状态不允许继续推进",
            mission_id=mission.id,
            status_value=mission.status,
        )

    after_data = snapshot(mission)
    log_action(
        request=request,
        action="MISSION_ADVANCE",
        target_type="mission",
        target_id=mission.id,
        before_data=before_data,
        after_data=after_data,
    )
    return Response(after_data, status=status.HTTP_200_OK)
```

```python
permission_map = {
    "list": "mission.view_mission",
    "retrieve": "mission.view_mission",
    "create": "mission.manage_mission",
    "update": "mission.manage_mission",
    "advance": "mission.manage_mission",
    "destroy": "mission.manage_mission",
}
```

- [x] **Step 4: Add the new action to the documented mission surface**

```python
@extend_schema(
    summary="推进任务状态",
    parameters=[TENANT_CODE_HEADER_PARAMETER],
    request=None,
    responses={
        200: OpenApiResponse(response=MISSION_DETAIL_RESPONSE),
        400: BUSINESS_INVALID_PARAMS_RESPONSE,
        401: BUSINESS_PERMISSION_DENIED_RESPONSE,
        403: BUSINESS_PERMISSION_DENIED_RESPONSE,
        404: BUSINESS_NOT_FOUND_RESPONSE,
        409: OpenApiResponse(description="任务当前状态不允许推进，或同一无人机已有执行中的任务。"),
        500: BUSINESS_INTERNAL_ERROR_RESPONSE,
    },
    tags=["Business API - Mission"],
)
```

- [x] **Step 5: Run mission + schema tests and verify they pass**

Run:
```bash
.venv/bin/python manage.py test \
  apps.mission.tests.MissionApiTests \
  apps.mission.test_live_api.LiveMissionApiTests \
  apps.api_v1.tests.OpenApiDocsTests \
  apps.access.test_live_schema_api.LiveIamSchemaTests \
  -v 2
```

Expected:
- PASS for mission action tests
- PASS for schema path and bodyless action assertions

- [x] **Step 6: Commit the action layer**

```bash
git add apps/mission/views.py apps/mission/tests.py apps/mission/test_live_api.py apps/api_v1/tests.py apps/access/test_live_schema_api.py
git commit -m "feat: add mission advance action"
```

### Task 4: Auto-bind synced DJI media to missions by device_sn and execution window

**Files:**
- Modify: `apps/dji_bff/tasks.py`
- Modify: `apps/dji_bff/tests.py`
- Modify: `apps/media_file/serializers.py`
- Modify: `apps/media_file/tests.py`

- [x] **Step 1: Add the failing media sync tests for mission auto-bind**

```python
from datetime import timedelta
from django.utils import timezone
from apps.mission.models import MissionStatus


def test_sync_media_indexes_should_auto_bind_to_unique_completed_mission_window(self):
    captured_at = timezone.now() - timedelta(minutes=2)
    mission = Mission.objects.create(
        tenant=self.tenant,
        name="自动归档任务",
        route=self.route,
        route_name=self.route.name,
        drone=self.drone,
        device_sn=self.drone.device_sn,
        drone_name=self.drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.COMPLETED,
        started_at=captured_at - timedelta(minutes=3),
        finished_at=captured_at + timedelta(minutes=3),
    )
    mock_dji_state.add_media_file(
        file_id="media-bind-001",
        device_sn=self.drone.device_sn,
        file_name="DJI_0001.MP4",
        captured_at=captured_at.isoformat(),
    )

    summary = sync_media_indexes()

    self.assertEqual(summary["created_count"], 1)
    media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="media-bind-001")
    self.assertEqual(media_index.mission_id, mission.id)
    self.assertEqual(media_index.media_file.mission_id, mission.id)


def test_sync_media_indexes_should_not_override_existing_manual_mission_binding(self):
    current_mission = Mission.objects.create(
        tenant=self.tenant,
        name="当前命中任务",
        route=self.route,
        route_name=self.route.name,
        drone=self.drone,
        device_sn=self.drone.device_sn,
        drone_name=self.drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.COMPLETED,
        started_at=timezone.now() - timedelta(minutes=5),
        finished_at=timezone.now() + timedelta(minutes=5),
    )
    manual_mission = Mission.objects.create(
        tenant=self.tenant,
        name="人工绑定任务",
        route=self.route,
        route_name=self.route.name,
        drone=self.drone,
        device_sn=self.drone.device_sn,
        drone_name=self.drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.COMPLETED,
        started_at=timezone.now() - timedelta(minutes=30),
        finished_at=timezone.now() - timedelta(minutes=20),
    )
    media_file = MediaFile.objects.create(
        tenant=self.tenant,
        mission=manual_mission,
        device_sn=self.drone.device_sn,
        media_type=MediaType.VIDEO,
        file_name="DJI_0002.MP4",
        file_url="dji://media-bind-002",
        captured_at=timezone.now(),
    )
    TenantMediaIndex.objects.create(
        tenant=self.tenant,
        media_file=media_file,
        dji_file_id="media-bind-002",
        device_sn=self.drone.device_sn,
        mission=manual_mission,
        sync_status=SyncStatus.SYNCED,
        last_sync_at=timezone.now(),
    )
    mock_dji_state.add_media_file(
        file_id="media-bind-002",
        device_sn=self.drone.device_sn,
        file_name="DJI_0002.MP4",
        captured_at=timezone.now().isoformat(),
    )

    sync_media_indexes()

    media_file.refresh_from_db()
    self.assertEqual(media_file.mission_id, manual_mission.id)
    self.assertNotEqual(media_file.mission_id, current_mission.id)


def test_sync_media_indexes_should_leave_mission_empty_when_multiple_windows_match(self):
    captured_at = timezone.now() - timedelta(minutes=1)
    for idx in (1, 2):
        Mission.objects.create(
            tenant=self.tenant,
            name=f"冲突窗口任务{idx}",
            route=self.route,
            route_name=self.route.name,
            drone=self.drone,
            device_sn=self.drone.device_sn,
            drone_name=self.drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=captured_at - timedelta(minutes=5),
            finished_at=captured_at + timedelta(minutes=5),
        )
    mock_dji_state.add_media_file(
        file_id="media-bind-003",
        device_sn=self.drone.device_sn,
        file_name="DJI_0003.JPG",
        captured_at=captured_at.isoformat(),
    )

    sync_media_indexes()

    media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="media-bind-003")
    self.assertIsNone(media_index.mission_id)
    self.assertIsNone(media_index.media_file.mission_id)
```

- [x] **Step 2: Run the DJI/media test subset and verify it fails**

Run:
```bash
.venv/bin/python manage.py test \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests \
  apps.media_file.tests.MediaFileApiTests \
  -v 2
```

Expected:
- FAIL because mission auto-match helper does not exist
- FAIL because manual bind validation still checks the removed `DRONE_BOUND` status

- [x] **Step 3: Implement a conservative mission matcher inside the media sync flow**

```python
def _match_mission_for_media(*, tenant, device_sn: str, captured_at):
    if not device_sn or captured_at is None:
        return None

    queryset = Mission.objects.filter(
        tenant=tenant,
        is_deleted=False,
        device_sn=device_sn,
        started_at__isnull=False,
    ).exclude(status=MissionStatus.PENDING)

    matched = []
    for mission in queryset:
        window_end = mission.finished_at or timezone.now()
        if mission.started_at <= captured_at <= window_end:
            matched.append(mission)

    if len(matched) == 1:
        return matched[0]
    return None
```

```python
media_fields = {
    # existing fields...
    "captured_at": _datetime_value(payload, "captured_at", "capturedAt", "create_time", "createTime"),
}
matched_mission = _match_mission_for_media(
    tenant=tenant,
    device_sn=device_sn,
    captured_at=media_fields["captured_at"],
)

if media_index is None:
    media_file = MediaFile.objects.create(
        **media_fields,
        mission=matched_mission,
        flight_record=None,
    )
    TenantMediaIndex.objects.create(
        tenant=tenant,
        media_file=media_file,
        dji_file_id=dji_file_id,
        device_sn=device_sn,
        mission=matched_mission,
        sync_status=SyncStatus.SYNCED,
        last_sync_at=media_fields["captured_at"] or now,
        error_msg="",
    )
else:
    preserve_manual_mission = media_index.mission or media_index.media_file.mission
    resolved_mission = preserve_manual_mission or matched_mission
    media_file.mission = resolved_mission
    media_index.mission = resolved_mission
```

- [x] **Step 4: Relax manual bind validation so it follows device binding, not the removed old status enum**

```python
if not mission.device_sn:
    raise serializers.ValidationError({"mission_id": ["任务缺少 device_sn，无法绑定媒体"]})
```

Delete this old gate entirely:

```python
if mission.status != MissionStatus.DRONE_BOUND:
    raise serializers.ValidationError({"mission_id": ["仅允许绑定到已绑定无人机的任务"]})
```

- [x] **Step 5: Run the media sync and media API tests to verify they pass**

Run:
```bash
.venv/bin/python manage.py test \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests \
  apps.media_file.tests.MediaFileApiTests \
  -v 2
```

Expected:
- PASS for unique-window auto-bind
- PASS for conflict no-bind
- PASS for manual binding preservation

- [x] **Step 6: Commit the media auto-bind behavior**

```bash
git add apps/dji_bff/tasks.py apps/dji_bff/tests.py apps/media_file/serializers.py apps/media_file/tests.py
git commit -m "feat: auto-bind synced media to mission windows"
```

### Task 5: Update docs and run the final regression suite

**Files:**
- Modify: `README.md`
- Modify: `apps/api_v1/tests.py`
- Modify: `apps/access/test_live_schema_api.py`

- [x] **Step 1: Update the README mission/media behavior summary**

```markdown
- Mission 执行流转改为 `待执行 -> 执行中 -> 执行完成`，通过 `POST /api/v1/missions/{id}/advance` 推进。
- `Mission.started_at` 在任务进入执行中时写入，`Mission.finished_at` 在任务执行完成时写入。
- DJI 媒体同步当前不依赖 `flight_record` 或 `jobId`；系统按 `device_sn + captured_at` 命中唯一 mission 时间窗时自动回填 mission。
- 同一无人机同一时刻只允许一个 mission 进入执行中。
- “飞行中”读取态和 MQTT/OSD 判定不在本次范围内。
```

- [x] **Step 2: Run the focused final regression suite**

Run:
```bash
.venv/bin/python manage.py test \
  apps.mission.tests.MissionApiTests \
  apps.mission.test_live_api.LiveMissionApiTests \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests \
  apps.media_file.tests.MediaFileApiTests \
  apps.api_v1.tests.OpenApiDocsTests \
  apps.access.test_live_schema_api.LiveIamSchemaTests \
  -v 2
```

Expected:
- PASS with no mission serializer/schema regressions
- PASS with no media auto-bind regressions
- PASS with `/api/v1/missions/{id}/advance` documented and bodyless

- [x] **Step 3: Commit the docs and schema expectation updates**

```bash
git add README.md apps/api_v1/tests.py apps/access/test_live_schema_api.py
git commit -m "docs: describe mission execution and media auto-bind"
```

---

## Self-review checklist

- The plan explicitly excludes the derived “飞行中” feature from this phase.
- The plan does not introduce `flight_record` into the media binding path.
- The plan preserves manual mission binding and avoids ambiguous auto-binding.
- The plan keeps route/drone fields nullable at the database level for legacy rows, while enforcing the new contract in create/update/advance paths.
- Every new surface change is covered by at least one API, schema, or sync test.
