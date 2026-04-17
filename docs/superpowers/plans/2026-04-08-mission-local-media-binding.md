# Mission Local-Only and Media Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `Mission` into a pure local task record, remove DJI job coupling, keep route/live behavior stable, and add explicit batch binding from `MediaFile` to `Mission`.

**Architecture:** Keep the existing Django app boundaries. Rework the mission domain first so the data model and API stop depending on DJI jobs, then update route blockers to use the new mission state semantics, simplify media sync into device-based ingest only, and finally add one focused media batch-binding endpoint. Preserve the existing route KMZ and drone live gateway flows.

**Tech Stack:** Django 5.1.6, Django REST Framework 3.15.2, drf-spectacular, SQLite test database, local `.venv`

---

## File Structure

**Create**
- `apps/mission/migrations/0007_localize_mission_status_and_drop_dji_job.py`
- `apps/dji_bff/migrations/0003_remove_tenantmissionindex.py`

**Modify**
- `apps/mission/models.py`
- `apps/mission/serializers.py`
- `apps/mission/views.py`
- `apps/mission/tests.py`
- `apps/mission/test_live_api.py`
- `apps/route/views.py`
- `apps/route/tests.py`
- `apps/media_file/serializers.py`
- `apps/media_file/views.py`
- `apps/media_file/urls.py`
- `apps/media_file/tests.py`
- `apps/media_file/test_live_api.py`
- `apps/dji_bff/models.py`
- `apps/dji_bff/tasks.py`
- `apps/dji_bff/views.py`
- `apps/dji_bff/urls.py`
- `apps/dji_bff/management/commands/run_dji_sync_scheduler.py`
- `apps/dji_bff/tests.py`
- `apps/api_v1/tests.py`
- `项目总体概览/逻辑设计/overall_logical_model.md`
- `项目总体概览/逻辑设计/overall_data_dictionary.md`
- `项目总体概览/逻辑设计/overall_schema.dbml`

**Why These Files**
- `apps/mission/*` is where the current DJI-coupled mission model, serializers, and API live.
- `apps/dji_bff/*` owns the soon-to-be-removed mission sync table, internal sync endpoint, and media ingest behavior.
- `apps/route/*` needs the new “bound mission blocks route mutation” rule.
- `apps/media_file/*` is the right place for the explicit batch bind endpoint and its request validation.
- `apps/api_v1/tests.py` locks the OpenAPI surface so removing `/missions/{id}/cancel` and adding `/media-files/bind-mission` does not drift silently.
- `项目总体概览/逻辑设计/*` is the canonical schema and business-model documentation that must match the refactored runtime behavior.

### Task 1: Localize Mission Model and Write Paths

**Files:**
- Create: `apps/mission/migrations/0007_localize_mission_status_and_drop_dji_job.py`
- Modify: `apps/mission/models.py`
- Modify: `apps/mission/serializers.py`
- Modify: `apps/mission/views.py`
- Modify: `apps/mission/tests.py`
- Test: `apps/mission/tests.py`

- [ ] **Step 1: Write failing mission tests for local-only creation and drone-driven status**

Add these tests to `apps/mission/tests.py` near the current create/update/delete coverage:

```python
def test_create_should_allow_missing_drone_and_mark_unbound(self):
    response = self.client.post(
        "/api/v1/missions",
        {
            "name": "未绑定无人机任务",
            "route": self.route.id,
            "pilot": self.pilot_member.id,
        },
        format="json",
    )

    self.assertEqual(response.status_code, 201, response.data)
    mission = Mission.objects.get(name="未绑定无人机任务")
    self.assertIsNone(mission.drone_id)
    self.assertEqual(mission.status, MissionStatus.DRONE_UNBOUND)
    self.assertEqual(mission.device_sn, "")
    self.assertEqual(mission.drone_name, "")
    self.assertEqual(mock_dji_state.jobs, {})


def test_create_should_mark_bound_when_drone_is_present_without_creating_dji_job(self):
    response = self.client.post(
        "/api/v1/missions",
        {
            "name": "已绑定无人机任务",
            "route": self.route.id,
            "drone": self.drone.id,
            "pilot": self.pilot_member.id,
        },
        format="json",
    )

    self.assertEqual(response.status_code, 201, response.data)
    mission = Mission.objects.get(name="已绑定无人机任务")
    self.assertEqual(mission.status, MissionStatus.DRONE_BOUND)
    self.assertEqual(mission.device_sn, self.drone.device_sn)
    self.assertEqual(mission.drone_name, self.drone.name)
    self.assertEqual(mock_dji_state.jobs, {})


def test_create_should_allow_unpublished_route_because_mission_is_now_local_only(self):
    self.route_index.is_published = False
    self.route_index.save(update_fields=["is_published", "updated_at"])

    response = self.client.post(
        "/api/v1/missions",
        {
            "name": "本地任务不再要求已发布航线",
            "route": self.route.id,
            "pilot": self.pilot_member.id,
        },
        format="json",
    )

    self.assertEqual(response.status_code, 201, response.data)
    self.assertTrue(Mission.objects.filter(name="本地任务不再要求已发布航线").exists())


def test_patch_should_unbind_drone_and_reset_status_snapshot_fields(self):
    mission = Mission.objects.create(
        tenant=self.tenant,
        name="待解绑任务",
        route=self.route,
        route_name=self.route.name,
        drone=self.drone,
        device_sn=self.drone.device_sn,
        drone_name=self.drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.DRONE_BOUND,
    )

    response = self.client.patch(
        f"/api/v1/missions/{mission.id}",
        {"drone": None},
        format="json",
    )

    self.assertEqual(response.status_code, 200, response.data)
    mission.refresh_from_db()
    self.assertIsNone(mission.drone_id)
    self.assertEqual(mission.status, MissionStatus.DRONE_UNBOUND)
    self.assertEqual(mission.device_sn, "")
    self.assertEqual(mission.drone_name, "")
```

- [ ] **Step 2: Run the new mission tests and confirm the current DJI-coupled code fails**

Run:

```bash
.venv/bin/python manage.py test \
  apps.mission.tests.MissionApiTests.test_create_should_allow_missing_drone_and_mark_unbound \
  apps.mission.tests.MissionApiTests.test_create_should_mark_bound_when_drone_is_present_without_creating_dji_job \
  apps.mission.tests.MissionApiTests.test_create_should_allow_unpublished_route_because_mission_is_now_local_only \
  apps.mission.tests.MissionApiTests.test_patch_should_unbind_drone_and_reset_status_snapshot_fields \
  -v 2
```

Expected:
- `FAIL`
- One failure should show `dock_sn` is still required.
- One failure should show `Mission.drone` cannot be null.
- One failure should show mission update still rejects the `drone` field.

- [ ] **Step 3: Implement the mission model, serializer, view, and migration changes**

Update `apps/mission/models.py`:

```python
from django.core.exceptions import ValidationError
from django.db import models

from apps.access.models import DirectoryStatus, EmploymentStatus, TenantMemberRoleStatus, TenantMemberStatus


class MissionStatus(models.IntegerChoices):
    DRONE_UNBOUND = 0, "未绑定无人机"
    DRONE_BOUND = 1, "已绑定无人机"


class Mission(models.Model):
    tenant = models.ForeignKey("access.Tenant", on_delete=models.CASCADE, related_name="missions", verbose_name="租户")
    name = models.CharField("任务名称", max_length=100)
    route = models.ForeignKey(
        "route.Route",
        on_delete=models.SET_NULL,
        related_name="missions",
        verbose_name="航线",
        null=True,
        blank=True,
    )
    route_name = models.CharField("航线名称（冗余）", max_length=100, blank=True, default="")
    drone = models.ForeignKey(
        "drone.Drone",
        on_delete=models.PROTECT,
        related_name="missions",
        verbose_name="无人机",
        null=True,
        blank=True,
    )
    device_sn = models.CharField("设备序列号（冗余）", max_length=128, blank=True, default="")
    drone_name = models.CharField("无人机名称（冗余）", max_length=100, blank=True, default="")
    pilot = models.ForeignKey("access.TenantMember", on_delete=models.PROTECT, related_name="missions", verbose_name="飞手成员")
    pilot_name = models.CharField("飞手姓名（冗余）", max_length=50, blank=True, default="")
    scheduled_at = models.DateTimeField("计划执行时间", null=True, blank=True)
    remark = models.CharField("任务备注", max_length=500, blank=True, default="")
    status = models.PositiveSmallIntegerField("任务状态", choices=MissionStatus.choices, default=MissionStatus.DRONE_UNBOUND)
    is_deleted = models.BooleanField("是否已删除", default=False)
    deleted_at = models.DateTimeField("删除时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    def clean(self):
        if self.pk:
            was_deleted = Mission.objects.filter(pk=self.pk).values_list("is_deleted", flat=True).first()
            if was_deleted and not self.is_deleted:
                raise ValidationError({"is_deleted": "任务软删除后不可恢复"})
        if self.is_deleted and self.deleted_at is None:
            raise ValidationError({"deleted_at": "逻辑删除记录必须提供 deleted_at"})
        if not self.is_deleted and self.deleted_at is not None:
            raise ValidationError({"deleted_at": "未删除记录不允许写入 deleted_at"})
        if self.tenant_id and self.route_id and self.route.tenant_id != self.tenant_id:
            raise ValidationError({"route": "route 必须属于当前 tenant"})
        if self.tenant_id and self.drone_id and self.drone.tenant_id != self.tenant_id:
            raise ValidationError({"drone": "drone 必须属于当前 tenant"})
        if self.tenant_id and self.pilot_id and self.pilot.tenant_id != self.tenant_id:
            raise ValidationError({"pilot": "pilot 必须属于当前 tenant"})
        if self.pilot_id and self.pilot.status != TenantMemberStatus.ACTIVE:
            raise ValidationError({"pilot": "仅允许分配给 ACTIVE 成员"})
        if self.pilot_id:
            staff = getattr(self.pilot.user, "staff_profile", None)
            if staff is None:
                raise ValidationError({"pilot": "pilot 对应账号必须存在 staff_profile"})
            if staff.employment_status != EmploymentStatus.ACTIVE:
                raise ValidationError({"pilot": "仅允许分配给在职飞手"})
            if not self.pilot.role_bindings.filter(
                system_role__code="pilot_operator",
                system_role__status=DirectoryStatus.ACTIVE,
                status=TenantMemberRoleStatus.GRANTED,
            ).exists():
                raise ValidationError({"pilot": "仅允许分配给飞手类型（pilot_operator）"})
        if self.drone_id:
            self.status = MissionStatus.DRONE_BOUND
            self.device_sn = self.drone.device_sn
            self.drone_name = self.drone.name
        else:
            self.status = MissionStatus.DRONE_UNBOUND
            self.device_sn = ""
            self.drone_name = ""
```

Update `apps/mission/serializers.py`:

```python
from apps.drone.models import Drone


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
            "remark",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class MissionCreateSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    drone = serializers.PrimaryKeyRelatedField(queryset=Drone.objects.all(), required=False, allow_null=True)

    def create(self, validated_data):
        route = validated_data["route"]
        pilot = validated_data["pilot"]
        drone = validated_data.get("drone")
        validated_data["route_name"] = route.name
        validated_data["pilot_name"] = _pilot_display_name(pilot)
        validated_data["status"] = MissionStatus.DRONE_BOUND if drone else MissionStatus.DRONE_UNBOUND
        return super().create(validated_data)

    class Meta:
        model = Mission
        fields = ["name", "route", "drone", "pilot", "scheduled_at", "remark"]


class MissionUpdateSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    drone = serializers.PrimaryKeyRelatedField(queryset=Drone.objects.all(), required=False, allow_null=True)

    def update(self, instance, validated_data):
        mission = super().update(instance, validated_data)
        if mission.drone_id is None:
            mission.device_sn = ""
            mission.drone_name = ""
            mission.status = MissionStatus.DRONE_UNBOUND
        else:
            mission.device_sn = mission.drone.device_sn
            mission.drone_name = mission.drone.name
            mission.status = MissionStatus.DRONE_BOUND
        mission.save(update_fields=["device_sn", "drone_name", "status", "updated_at"])
        return mission

    class Meta:
        model = Mission
        fields = ["name", "drone", "scheduled_at", "remark"]
```

Update `apps/mission/views.py`:

```python
from django.db import transaction
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from apps.mission.models import Mission
from apps.mission.serializers import MissionCreateSerializer, MissionReadSerializer, MissionUpdateSerializer


class MissionViewSet(
    BusinessApiResponseMixin,
    TenantScopedBusinessMixin,
    PermissionMapMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Mission.objects.select_related("route", "drone", "pilot__user__staff_profile").all().order_by("-id")

    permission_map = {
        "list": "mission.view_mission",
        "retrieve": "mission.view_mission",
        "create": "mission.manage_mission",
        "update": "mission.manage_mission",
        "partial_update": "mission.manage_mission",
        "destroy": "mission.manage_mission",
    }

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=False)
        if serializer.errors:
            return Response(validation_error_payload(serializer.errors), status=status.HTTP_400_BAD_REQUEST)
        mission = self.perform_create(serializer)
        return _mission_success_response(self, mission, http_status=status.HTTP_201_CREATED, include_headers=True)

    @transaction.atomic
    def perform_create(self, serializer):
        tenant = self.get_current_tenant()
        mission = serializer.save(tenant=tenant)
        log_action(
            request=self.request,
            action="MISSION_CREATE",
            target_type="mission",
            target_id=mission.id,
            after_data=self._payload(mission),
        )
        return mission

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        error_response = _reject_request_body_if_present(request, message="DELETE 请求不支持请求体")
        if error_response is not None:
            return error_response
        mission = self.get_object()
        before_data = snapshot(mission)
        mission.is_deleted = True
        mission.deleted_at = timezone.now()
        mission.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
        deleted_payload = {"id": mission.id, "deleted": True}
        log_action(
            request=request,
            action="MISSION_DELETE",
            target_type="mission",
            target_id=mission.id,
            before_data=before_data,
            after_data=deleted_payload,
        )
        return Response(deleted_payload, status=status.HTTP_200_OK)
```

Create `apps/mission/migrations/0007_localize_mission_status_and_drop_dji_job.py`:

```python
from django.db import migrations, models


def normalize_mission_status(apps, schema_editor):
    Mission = apps.get_model("mission", "Mission")
    Mission.objects.filter(drone__isnull=True).update(status=0, device_sn="", drone_name="")
    Mission.objects.filter(drone__isnull=False).update(status=1)


class Migration(migrations.Migration):
    dependencies = [("mission", "0006_mission_deleted_at_mission_device_sn_and_more")]

    operations = [
        migrations.RemoveField(model_name="mission", name="dji_job_id"),
        migrations.AlterField(
            model_name="mission",
            name="drone",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.PROTECT,
                related_name="missions",
                to="drone.drone",
                verbose_name="无人机",
            ),
        ),
        migrations.AlterField(
            model_name="mission",
            name="status",
            field=models.PositiveSmallIntegerField(
                choices=[(0, "未绑定无人机"), (1, "已绑定无人机")],
                default=0,
                verbose_name="任务状态",
            ),
        ),
        migrations.RunPython(normalize_mission_status, migrations.RunPython.noop),
    ]
```

Also clean `apps/mission/tests.py` so it stops importing and asserting DJI-only behavior:

```python
-from apps.dji_bff.models import SyncStatus, TenantMissionIndex, TenantRouteIndex
+from apps.dji_bff.models import TenantRouteIndex
```

Delete these obsolete test methods entirely because the behavior no longer exists:
- `test_create_should_sync_job_and_persist_index`
- `test_create_should_require_dock_sn`
- `test_create_should_reject_unpublished_route` (rewrite it into `test_create_should_allow_unpublished_route_because_mission_is_now_local_only`)
- `test_create_should_cancel_upstream_job_when_local_finalize_fails`
- `test_cancel_should_call_gateway_and_mark_mission_canceled`
- `test_cancel_should_reject_request_body`
- `test_delete_should_cancel_active_mission_before_soft_delete`

Rewrite `test_delete_should_soft_delete_mission_and_hide_it_from_api` and `test_delete_should_reject_request_body` so they no longer create `TenantMissionIndex` rows or reference `dji_job_id`.

- [ ] **Step 4: Re-run the focused mission tests and confirm the new local-only behavior passes**

Run:

```bash
.venv/bin/python manage.py test \
  apps.mission.tests.MissionApiTests.test_create_should_allow_missing_drone_and_mark_unbound \
  apps.mission.tests.MissionApiTests.test_create_should_mark_bound_when_drone_is_present_without_creating_dji_job \
  apps.mission.tests.MissionApiTests.test_create_should_allow_unpublished_route_because_mission_is_now_local_only \
  apps.mission.tests.MissionApiTests.test_patch_should_unbind_drone_and_reset_status_snapshot_fields \
  apps.mission.tests.MissionApiTests.test_mission_soft_delete_should_be_irreversible \
  apps.mission.tests.MissionApiTests.test_delete_should_soft_delete_mission_and_hide_it_from_api \
  -v 2
```

Expected:
- `OK`
- The response payload should no longer contain `dji_job_id`, `sync_status`, `execution_status`, or `last_sync_at`.

- [ ] **Step 5: Commit the mission local-only refactor slice**

Run:

```bash
git add \
  apps/mission/models.py \
  apps/mission/serializers.py \
  apps/mission/views.py \
  apps/mission/tests.py \
  apps/mission/migrations/0007_localize_mission_status_and_drop_dji_job.py

git commit -m "refactor: localize mission state and remove dji job coupling"
```

### Task 2: Remove Mission Sync Surfaces and Public Cancel Contract

**Files:**
- Create: `apps/dji_bff/migrations/0003_remove_tenantmissionindex.py`
- Modify: `apps/dji_bff/models.py`
- Modify: `apps/dji_bff/tasks.py`
- Modify: `apps/dji_bff/views.py`
- Modify: `apps/dji_bff/urls.py`
- Modify: `apps/dji_bff/management/commands/run_dji_sync_scheduler.py`
- Modify: `apps/dji_bff/tests.py`
- Modify: `apps/mission/test_live_api.py`
- Modify: `apps/api_v1/tests.py`
- Test: `apps/dji_bff/tests.py`
- Test: `apps/mission/test_live_api.py`
- Test: `apps/api_v1/tests.py`

- [ ] **Step 1: Add failing tests for the removed mission sync and cancel surface**

Update `apps/api_v1/tests.py`:

```python
def test_business_schema_should_expose_refactored_paths(self):
    response = self.client.get("/api/v1/docs/schema/")
    schema = response.json()
    paths = schema["paths"]

    self.assertNotIn("/api/v1/missions/{id}/cancel", paths)
```

Update `apps/dji_bff/tests.py`:

```python
def test_internal_sync_missions_endpoint_should_be_removed(self):
    response = self.client.post(
        "/api/v1/__internal__/dji/sync/missions",
        HTTP_X_DJI_INTERNAL_TOKEN="internal-sync-token",
    )

    self.assertEqual(response.status_code, 404)


def test_scheduler_command_should_only_report_devices_and_media(self):
    stdout = StringIO()

    call_command("run_dji_sync_scheduler", "--once", stdout=stdout)

    output = stdout.getvalue()
    self.assertIn("devices=", output)
    self.assertIn("media=", output)
    self.assertNotIn("missions=", output)
```

Update `apps/mission/test_live_api.py`:

```python
def test_create_and_delete_should_follow_live_http_contract(self):
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
    self.assertEqual(create_response.json()["data"]["status"], 1)

    cancel_response = self.client.post(f"/api/v1/missions/{mission_id}/cancel")
    self.assertEqual(cancel_response.status_code, 404)

    delete_response = self.client.delete(f"/api/v1/missions/{mission_id}")
    self.assertEqual(delete_response.status_code, 200)
    self.assertTrue(delete_response.json()["data"]["deleted"])
```

- [ ] **Step 2: Run the failing schema/internal/live tests**

Run:

```bash
.venv/bin/python manage.py test \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_expose_refactored_paths \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_internal_sync_missions_endpoint_should_be_removed \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_scheduler_command_should_only_report_devices_and_media \
  apps.mission.test_live_api.LiveMissionApiTests.test_create_and_delete_should_follow_live_http_contract \
  -v 2
```

Expected:
- `FAIL`
- The OpenAPI schema should still expose `/api/v1/missions/{id}/cancel`.
- The internal mission sync endpoint should still resolve.
- The scheduler output should still include `missions=`.

- [ ] **Step 3: Remove the mission sync table, endpoint, scheduler branch, and cancel contract**

Update `apps/dji_bff/models.py` by deleting the `TenantMissionIndex` model block entirely.

Create `apps/dji_bff/migrations/0003_remove_tenantmissionindex.py`:

```python
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("dji_bff", "0002_tenantrouteindex_download_url")]

    operations = [
        migrations.DeleteModel(name="TenantMissionIndex"),
    ]
```

Update `apps/dji_bff/tasks.py` imports and remove `sync_mission_indexes` completely:

```python
from apps.dji_bff.models import DjiDeviceIndex, SyncStatus, TenantMediaIndex

# keep `sync_device_indexes` unchanged
# delete `sync_mission_indexes` entirely
# keep `sync_media_indexes` as the only remaining data-sync function beside devices
```

Update `apps/dji_bff/tests.py` imports and remove the obsolete mission-sync test:

```python
-from apps.dji_bff.models import DjiDeviceIndex, DjiWorkspaceConfig, SyncStatus, TenantMediaIndex, TenantMissionIndex
-from apps.dji_bff.tasks import sync_device_indexes, sync_media_indexes, sync_mission_indexes
+from apps.dji_bff.models import DjiDeviceIndex, DjiWorkspaceConfig, SyncStatus, TenantMediaIndex
+from apps.dji_bff.tasks import sync_device_indexes, sync_media_indexes
```

Delete `test_sync_mission_indexes_should_update_execution_status_and_local_status` from `apps/dji_bff/tests.py`.

Update `apps/dji_bff/views.py`:

```python
from apps.dji_bff.tasks import sync_device_indexes, sync_media_indexes


@csrf_exempt
def sync_devices(request):
    return _run_sync(request, sync_device_indexes)


@csrf_exempt
def sync_media(request):
    return _run_sync(request, sync_media_indexes)
```

Update `apps/dji_bff/urls.py`:

```python
urlpatterns = [
    path("__internal__/dji/sync/devices", views.sync_devices),
    path("__internal__/dji/sync/media", views.sync_media),
    path("__internal__/dji/callbacks/media-upload", views.media_upload_callback),
    path("__internal__/dji/callbacks/media-group-upload", views.media_group_upload_callback),
]
```

Update `apps/dji_bff/management/commands/run_dji_sync_scheduler.py`:

```python
from apps.dji_bff.tasks import sync_device_indexes, sync_media_indexes


class Command(BaseCommand):
    help = "执行 DJI 资源同步调度。默认循环执行；可用 --once 只跑一轮。"

    @staticmethod
    def run_sync_cycle():
        return {
            "devices": sync_device_indexes(),
            "media": sync_media_indexes(),
        }
```

Update `apps/api_v1/tests.py` expected path/method/response sets so they:
- remove `/api/v1/missions/{id}/cancel`

Use this shape in the path/method assertions:

```python
expected_methods = {
    "/api/v1/missions": {"get", "post"},
    "/api/v1/missions/{id}": {"get", "put", "patch", "delete"},
    "/api/v1/media-files": {"get"},
    "/api/v1/media-files/{id}": {"get", "delete"},
}
```

Update `apps/mission/test_live_api.py` to keep only create/delete behavior; do not call `cancel` except to assert `404`.

- [ ] **Step 4: Re-run the schema/internal/live tests and confirm the surface is gone**

Run:

```bash
.venv/bin/python manage.py test \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_expose_refactored_paths \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_lock_current_operation_surface_and_bodyless_actions \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_describe_current_business_error_responses \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_internal_sync_missions_endpoint_should_be_removed \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_scheduler_command_should_only_report_devices_and_media \
  apps.mission.test_live_api.LiveMissionApiTests.test_create_and_delete_should_follow_live_http_contract \
  -v 2
```

Expected:
- `OK`
- `/api/v1/missions/{id}/cancel` should be absent from the schema.
- `/api/v1/__internal__/dji/sync/missions` should return `404`.
- The scheduler output should only report `devices=` and `media=`.

- [ ] **Step 5: Commit the removed mission sync surface**

Run:

```bash
git add \
  apps/dji_bff/models.py \
  apps/dji_bff/tasks.py \
  apps/dji_bff/views.py \
  apps/dji_bff/urls.py \
  apps/dji_bff/management/commands/run_dji_sync_scheduler.py \
  apps/dji_bff/tests.py \
  apps/dji_bff/migrations/0003_remove_tenantmissionindex.py \
  apps/mission/test_live_api.py \
  apps/api_v1/tests.py

git commit -m "refactor: remove mission sync surfaces and cancel contract"
```

### Task 3: Block Route Update and Delete When a Mission Is Drone-Bound

**Files:**
- Modify: `apps/route/views.py`
- Modify: `apps/route/tests.py`
- Test: `apps/route/tests.py`

- [ ] **Step 1: Add failing route blocker tests for `DRONE_BOUND` missions**

Add these tests to `apps/route/tests.py` near the current delete blocker coverage:

```python
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
        status=MissionStatus.DRONE_BOUND,
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
        status=MissionStatus.DRONE_UNBOUND,
    )

    response = self.client.delete(f"/api/v1/routes/{route.id}")

    self.assertEqual(response.status_code, 200, response.data)
```

Also rewrite the existing delete blocker test so the mission status is `MissionStatus.DRONE_BOUND` instead of `MissionStatus.PAUSED`.

- [ ] **Step 2: Run the focused route blocker tests and confirm current logic still keys off the old statuses**

Run:

```bash
.venv/bin/python manage.py test \
  apps.route.tests.RouteKmzApiTests.test_put_should_reject_when_bound_mission_uses_route \
  apps.route.tests.RouteKmzApiTests.test_delete_should_ignore_unbound_mission_blocker \
  apps.route.tests.RouteKmzApiTests.test_delete_should_reject_when_bound_mission_uses_route \
  -v 2
```

Expected:
- `FAIL`
- The update path should still allow the request.
- The delete path should still be using the old `PENDING/RUNNING/PAUSED` blocker set.

- [ ] **Step 3: Implement the new bound-mission route guard**

Update `apps/route/views.py`:

```python
class RouteViewSet(
    BusinessApiResponseMixin,
    TenantScopedBusinessMixin,
    PermissionMapMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    def _has_bound_mission_blocker(self, route: Route) -> bool:
        return Mission.objects.filter(
            tenant=self.get_current_tenant(),
            route=route,
            is_deleted=False,
            status=MissionStatus.DRONE_BOUND,
        ).exists()

    def update(self, request, *args, **kwargs):
        route = self.get_object()
        if self._has_bound_mission_blocker(route):
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "航线已被已绑定无人机的任务占用，无法更新",
                    {"route_id": route.id},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )
        before_data = self._payload(route)
        route_index = getattr(route, "dji_index", None)
        gateway = DjiGateway()
        route_id = route.id
        wayline_id = route_index.dji_wayline_id if route_index is not None else ""
        route.waypoint_rows.all().delete()
        route.delete()
        if wayline_id:
            transaction.on_commit(
                lambda current_wayline_id=wayline_id: self._delete_upstream_wayline_if_exists(
                    gateway=gateway,
                    wayline_id=current_wayline_id,
                    best_effort=True,
                ),
                robust=True,
            )
        transaction.on_commit(
            lambda: log_action(
                request=request,
                action="ROUTE_DELETE",
                target_type="route",
                target_id=route_id,
                before_data=before_data,
                after_data={"id": route_id, "deleted": True},
            ),
            robust=True,
        )
        return Response({"id": route_id, "deleted": True}, status=status.HTTP_200_OK)
        serializer = self.get_serializer(route, data=request.data)
        serializer.is_valid(raise_exception=True)
        route = self.perform_update(serializer)
        return _route_success_response(self, route, http_status=status.HTTP_200_OK)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        error_response = _reject_request_body_if_present(request, message="DELETE 请求不支持提交 body 参数")
        if error_response is not None:
            return error_response
        route = self.get_object()
        if self._has_bound_mission_blocker(route):
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "航线已被已绑定无人机的任务占用，无法删除",
                    {"route_id": route.id},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )
        before_data = self._payload(route)
        route_index = getattr(route, "dji_index", None)
        gateway = DjiGateway()
        route_id = route.id
        wayline_id = route_index.dji_wayline_id if route_index is not None else ""
        route.waypoint_rows.all().delete()
        route.delete()
        if wayline_id:
            transaction.on_commit(
                lambda current_wayline_id=wayline_id: self._delete_upstream_wayline_if_exists(
                    gateway=gateway,
                    wayline_id=current_wayline_id,
                    best_effort=True,
                ),
                robust=True,
            )
        transaction.on_commit(
            lambda: log_action(
                request=request,
                action="ROUTE_DELETE",
                target_type="route",
                target_id=route_id,
                before_data=before_data,
                after_data={"id": route_id, "deleted": True},
            ),
            robust=True,
        )
        return Response({"id": route_id, "deleted": True}, status=status.HTTP_200_OK)
```

Update `apps/route/tests.py` so every route blocker fixture uses `MissionStatus.DRONE_BOUND` for the blocking case and `MissionStatus.DRONE_UNBOUND` for the non-blocking case.

- [ ] **Step 4: Re-run the route blocker tests and confirm update/delete now follow the new semantics**

Run:

```bash
.venv/bin/python manage.py test \
  apps.route.tests.RouteKmzApiTests.test_put_should_reject_when_bound_mission_uses_route \
  apps.route.tests.RouteKmzApiTests.test_delete_should_ignore_unbound_mission_blocker \
  apps.route.tests.RouteKmzApiTests.test_delete_should_reject_when_bound_mission_uses_route \
  apps.route.tests.RouteKmzApiTests.test_delete_should_ignore_soft_deleted_mission_blocker \
  -v 2
```

Expected:
- `OK`
- `DRONE_BOUND` missions block route update/delete.
- `DRONE_UNBOUND` and soft-deleted missions do not block.

- [ ] **Step 5: Commit the route blocker change**

Run:

```bash
git add apps/route/views.py apps/route/tests.py
git commit -m "fix(route): block mutation when bound missions occupy route"
```

### Task 4: Simplify Media Sync Into Device-Based Local Ingest Only

**Files:**
- Modify: `apps/dji_bff/tasks.py`
- Modify: `apps/dji_bff/tests.py`
- Test: `apps/dji_bff/tests.py`

- [ ] **Step 1: Add failing sync tests for local-only media ingest and preserved manual bindings**

Replace the old job-based mission association test in `apps/dji_bff/tests.py` with these checks:

```python
def test_sync_media_indexes_should_leave_mission_and_flight_record_empty_even_when_job_id_exists(self):
    Drone.objects.create(
        tenant=self.tenant,
        code="MEDIA-SYNC-DRONE-BOUND",
        name="媒体同步无人机",
        model="M30",
        device_sn="MEDIA-CROSS-SN-001",
    )
    route = Route.objects.create(tenant=self.tenant, name="媒体同步航线")
    mission = Mission.objects.create(
        tenant=self.tenant,
        name="不应自动匹配的任务",
        route=route,
        route_name=route.name,
        drone=Drone.objects.get(device_sn="MEDIA-CROSS-SN-001"),
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.DRONE_BOUND,
    )
    mock_dji_state.seed_media_file(
        file_id="media-cross-mission-file",
        name="MEDIA_CROSS_MISSION.JPG",
        device_sn="MEDIA-CROSS-SN-001",
        job_id="legacy-job-id-that-should-be-ignored",
    )

    summary = sync_media_indexes()

    self.assertGreaterEqual(summary["created_count"], 1)
    media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="media-cross-mission-file")
    media_file = media_index.media_file
    self.assertIsNone(media_file.mission_id)
    self.assertIsNone(media_file.flight_record_id)
    self.assertIsNone(media_index.mission_id)


def test_sync_media_indexes_should_preserve_existing_manual_mission_binding(self):
    drone = Drone.objects.create(
        tenant=self.tenant,
        code="MEDIA-MANUAL-DRONE",
        name="人工绑定无人机",
        model="M30",
        device_sn="MEDIA-MANUAL-SN-001",
    )
    route = Route.objects.create(tenant=self.tenant, name="人工绑定航线")
    mission = Mission.objects.create(
        tenant=self.tenant,
        name="人工绑定任务",
        route=route,
        route_name=route.name,
        drone=drone,
        device_sn=drone.device_sn,
        drone_name=drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.DRONE_BOUND,
    )
    media_file = MediaFile.objects.create(
        tenant=self.tenant,
        mission=mission,
        device_sn=drone.device_sn,
        media_type=1,
        file_name="MEDIA_MANUAL.JPG",
        file_url="dji://media-manual-file",
    )
    TenantMediaIndex.objects.create(
        tenant=self.tenant,
        media_file=media_file,
        dji_file_id="media-manual-file",
        device_sn=drone.device_sn,
        mission=mission,
        sync_status=SyncStatus.SYNCED,
    )
    mock_dji_state.seed_media_file(
        file_id="media-manual-file",
        name="MEDIA_MANUAL.JPG",
        device_sn=drone.device_sn,
        job_id="ignored-job-id",
    )

    summary = sync_media_indexes()

    self.assertGreaterEqual(summary["updated_count"], 1)
    media_file.refresh_from_db()
    self.assertEqual(media_file.mission_id, mission.id)
    self.assertEqual(media_file.dji_index.mission_id, mission.id)
```

- [ ] **Step 2: Run the focused media sync tests and confirm the current code still auto-fills mission data**

Run:

```bash
.venv/bin/python manage.py test \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_leave_mission_and_flight_record_empty_even_when_job_id_exists \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_preserve_existing_manual_mission_binding \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_not_restore_soft_deleted_media_file \
  -v 2
```

Expected:
- `FAIL`
- The current sync code should still fill `mission` from `job_id`.
- The current sync code may overwrite manual binding state on update.

- [ ] **Step 3: Remove automatic mission/flight-record matching from media sync**

Update `apps/dji_bff/tasks.py` imports and `sync_media_indexes`:

```python
from apps.dji_bff.models import DjiDeviceIndex, SyncStatus, TenantMediaIndex
from apps.drone.models import Drone
from apps.media_file.models import MediaFile, MediaType


def sync_media_indexes(*, gateway: DjiGateway | None = None) -> dict[str, int]:
    gateway = gateway or DjiGateway()
    summary = SyncSummary()
    now = timezone.now()

    for payload in gateway.list_media_files():
        if not isinstance(payload, dict):
            summary.ignored_count += 1
            continue

        dji_file_id = _string(payload, "file_id", "fileId", "id")
        if not dji_file_id:
            summary.ignored_count += 1
            continue

        device_sn = _string(payload, "device_sn", "deviceSn", "sn")
        if not device_sn:
            summary.ignored_count += 1
            continue

        claimed_drone = Drone.objects.select_related("tenant").filter(device_sn=device_sn).first()
        if claimed_drone is None:
            summary.ignored_count += 1
            continue
        tenant = claimed_drone.tenant

        media_fields = {
            "tenant": tenant,
            "device_sn": device_sn,
            "media_type": _media_type(payload),
            "file_name": _file_name(payload),
            "file_url": f"dji://{dji_file_id}",
            "thumbnail_url": _string(payload, "thumbnail_url", "thumbnailUrl"),
            "file_size": _int(payload, "file_size", "fileSize"),
            "latitude": payload.get("latitude"),
            "longitude": payload.get("longitude"),
            "captured_at": _datetime_value(payload, "captured_at", "capturedAt", "create_time", "createTime"),
        }

        with transaction.atomic():
            media_index = TenantMediaIndex.objects.select_related("media_file").filter(
                tenant=tenant,
                dji_file_id=dji_file_id,
            ).first()
            if media_index is None:
                media_file = MediaFile.objects.create(
                    **media_fields,
                    mission=None,
                    flight_record=None,
                )
                TenantMediaIndex.objects.create(
                    tenant=tenant,
                    media_file=media_file,
                    dji_file_id=dji_file_id,
                    device_sn=device_sn,
                    mission=None,
                    sync_status=SyncStatus.SYNCED,
                    last_sync_at=media_fields["captured_at"] or now,
                    error_msg="",
                )
                summary.created_count += 1
            else:
                media_file = media_index.media_file
                for field, value in media_fields.items():
                    setattr(media_file, field, value)
                media_file.save(
                    update_fields=[
                        "tenant",
                        "device_sn",
                        "media_type",
                        "file_name",
                        "file_url",
                        "thumbnail_url",
                        "file_size",
                        "latitude",
                        "longitude",
                        "captured_at",
                    ]
                )
                media_index.device_sn = device_sn
                media_index.sync_status = SyncStatus.SYNCED
                media_index.last_sync_at = media_fields["captured_at"] or now
                media_index.error_msg = ""
                media_index.save(update_fields=["device_sn", "sync_status", "last_sync_at", "error_msg", "updated_at"])
                summary.updated_count += 1

        summary.synced_count += 1
```

Important: do **not** write `mission=None` back into an existing `MediaFile` or `TenantMediaIndex`, otherwise a later manual binding will be erased on the next sync.

Also rewrite `apps/dji_bff/tests.py::test_sync_media_indexes_should_not_restore_soft_deleted_media_file` so it no longer creates `TenantMissionIndex` or uses `dji_job_id`; the fixture only needs a claimed drone, one soft-deleted `MediaFile`, and one `TenantMediaIndex`.

- [ ] **Step 4: Re-run the media sync tests and confirm sync now only does reliable local ingest**

Run:

```bash
.venv/bin/python manage.py test \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_create_local_read_model_from_upstream_media \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_leave_mission_and_flight_record_empty_even_when_job_id_exists \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_preserve_existing_manual_mission_binding \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_not_restore_soft_deleted_media_file \
  -v 2
```

Expected:
- `OK`
- New synced media should have `mission_id is None` and `flight_record_id is None`.
- Existing manual mission bindings should remain intact.
- Soft-deleted media should remain soft-deleted after sync.

- [ ] **Step 5: Commit the simplified media sync behavior**

Run:

```bash
git add apps/dji_bff/tasks.py apps/dji_bff/tests.py
git commit -m "refactor(dji_bff): stop auto-binding media to missions"
```

### Task 5: Add Explicit Batch Media-to-Mission Binding API

**Files:**
- Modify: `apps/media_file/serializers.py`
- Modify: `apps/media_file/views.py`
- Modify: `apps/media_file/urls.py`
- Modify: `apps/media_file/tests.py`
- Modify: `apps/media_file/test_live_api.py`
- Modify: `apps/api_v1/tests.py`
- Test: `apps/media_file/tests.py`
- Test: `apps/media_file/test_live_api.py`
- Test: `apps/api_v1/tests.py`

- [ ] **Step 1: Add failing tests for batch bind success and validation failures**

Add these tests to `apps/media_file/tests.py`:

```python
def test_bind_mission_should_assign_multiple_media_to_bound_mission(self):
    grant_role_permissions(self.role, {"media_file.manage_media_file": ScopeType.ALL})
    media_a = self._create_mission_only_media(mission=self.mission, file_name="IMG_BIND_A.JPG")
    media_b = self._create_mission_only_media(mission=self.mission, file_name="IMG_BIND_B.JPG")
    media_a.mission = None
    media_a.save(update_fields=["mission"])
    media_a.dji_index.mission = None
    media_a.dji_index.save(update_fields=["mission", "updated_at"])
    media_b.mission = None
    media_b.save(update_fields=["mission"])
    media_b.dji_index.mission = None
    media_b.dji_index.save(update_fields=["mission", "updated_at"])

    response = self.client.post(
        "/api/v1/media-files/bind-mission",
        {"mission_id": self.mission.id, "media_file_ids": [media_a.id, media_b.id]},
        format="json",
    )

    self.assertEqual(response.status_code, 200, response.data)
    media_a.refresh_from_db()
    media_b.refresh_from_db()
    self.assertEqual(media_a.mission_id, self.mission.id)
    self.assertEqual(media_b.mission_id, self.mission.id)
    self.assertEqual(media_a.dji_index.mission_id, self.mission.id)
    self.assertEqual(media_b.dji_index.mission_id, self.mission.id)


def test_bind_mission_should_reject_unbound_mission(self):
    grant_role_permissions(self.role, {"media_file.manage_media_file": ScopeType.ALL})
    self.mission.drone = None
    self.mission.status = MissionStatus.DRONE_UNBOUND
    self.mission.device_sn = ""
    self.mission.drone_name = ""
    self.mission.save(update_fields=["drone", "status", "device_sn", "drone_name", "updated_at"])
    media_file = self._create_mission_only_media(mission=self.mission, file_name="IMG_UNBOUND.JPG")
    media_file.mission = None
    media_file.save(update_fields=["mission"])
    media_file.dji_index.mission = None
    media_file.dji_index.save(update_fields=["mission", "updated_at"])

    response = self.client.post(
        "/api/v1/media-files/bind-mission",
        {"mission_id": self.mission.id, "media_file_ids": [media_file.id]},
        format="json",
    )

    self.assertEqual(response.status_code, 400)
    self.assertEqual(response.data["code"], "B0001")
    self.assertIn("mission_id", response.data["data"])


def test_bind_mission_should_reject_device_sn_mismatch(self):
    grant_role_permissions(self.role, {"media_file.manage_media_file": ScopeType.ALL})
    media_file = self._create_media(file_name="IMG_MISMATCH.JPG", device_sn="OTHER-SN-001")

    response = self.client.post(
        "/api/v1/media-files/bind-mission",
        {"mission_id": self.mission.id, "media_file_ids": [media_file.id]},
        format="json",
    )

    self.assertEqual(response.status_code, 400)
    self.assertEqual(response.data["code"], "B0001")
    self.assertIn("media_file_ids", response.data["data"])


def test_bind_mission_should_reject_cross_tenant_mission(self):
    grant_role_permissions(self.role, {"media_file.manage_media_file": ScopeType.ALL})
    other_user = User.objects.create_user(username="media_bind_other_tenant", password="pass1234", status=1)
    ensure_staff_profile(other_user, name="其他租户用户", employment_status=EmploymentStatus.ACTIVE)
    other_tenant, other_member, _other_role = ensure_tenant_role_binding(
        other_user,
        tenant_code="media_bind_other_tenant",
        role_code="pilot_operator",
        role_name="飞手",
    )
    ensure_tenant_member_position(other_member, code="pilot_operator", name="飞手")
    other_route = Route.objects.create(tenant=other_tenant, name="跨租户航线")
    other_drone = Drone.objects.create(
        tenant=other_tenant,
        code="OTHER-TENANT-DRONE-001",
        name="跨租户无人机",
        model="M30",
        device_sn="OTHER-TENANT-SN-001",
    )
    other_mission = Mission.objects.create(
        tenant=other_tenant,
        name="跨租户任务",
        route=other_route,
        route_name=other_route.name,
        drone=other_drone,
        device_sn=other_drone.device_sn,
        drone_name=other_drone.name,
        pilot=other_member,
        pilot_name="其他租户飞手",
        status=MissionStatus.DRONE_BOUND,
    )
    media_file = self._create_media(file_name="IMG_CROSS_TENANT.JPG", device_sn=self.drone.device_sn)

    response = self.client.post(
        "/api/v1/media-files/bind-mission",
        {"mission_id": other_mission.id, "media_file_ids": [media_file.id]},
        format="json",
    )

    self.assertEqual(response.status_code, 400)
    self.assertEqual(response.data["code"], "B0001")
    self.assertIn("mission_id", response.data["data"])


def test_bind_mission_should_overwrite_existing_mission_binding(self):
    grant_role_permissions(self.role, {"media_file.manage_media_file": ScopeType.ALL})
    other_mission = Mission.objects.create(
        tenant=self.tenant,
        name="同机改绑任务",
        route=self.route,
        route_name=self.route.name,
        drone=self.drone,
        device_sn=self.drone.device_sn,
        drone_name=self.drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.DRONE_BOUND,
    )
    media_file = self._create_mission_only_media(mission=self.mission, file_name="IMG_OVERWRITE.JPG")

    response = self.client.post(
        "/api/v1/media-files/bind-mission",
        {"mission_id": other_mission.id, "media_file_ids": [media_file.id]},
        format="json",
    )

    self.assertEqual(response.status_code, 200, response.data)
    media_file.refresh_from_db()
    self.assertEqual(media_file.mission_id, other_mission.id)
    self.assertEqual(media_file.dji_index.mission_id, other_mission.id)
```

Add one schema assertion to `apps/api_v1/tests.py`:

```python
self.assertIn("/api/v1/media-files/bind-mission", paths)
self.assertEqual(schema["paths"]["/api/v1/media-files/bind-mission"].keys(), {"post"})
```

Add one live HTTP smoke assertion to `apps/media_file/test_live_api.py`:

```python
def test_bind_mission_should_follow_live_http_contract(self):
    bind_response = self.client.post(
        "/api/v1/media-files/bind-mission",
        {"mission_id": self.mission.id, "media_file_ids": [self.media_file.id]},
        format="json",
    )
    self.assertEqual(bind_response.status_code, 200)
    self.assertEqual(bind_response.json()["data"]["updated_count"], 1)
```

- [ ] **Step 2: Run the failing batch-bind tests and confirm the endpoint does not exist yet**

Run:

```bash
.venv/bin/python manage.py test \
  apps.media_file.tests.MediaFileApiTests.test_bind_mission_should_assign_multiple_media_to_bound_mission \
  apps.media_file.tests.MediaFileApiTests.test_bind_mission_should_reject_unbound_mission \
  apps.media_file.tests.MediaFileApiTests.test_bind_mission_should_reject_device_sn_mismatch \
  apps.media_file.tests.MediaFileApiTests.test_bind_mission_should_reject_cross_tenant_mission \
  apps.media_file.tests.MediaFileApiTests.test_bind_mission_should_overwrite_existing_mission_binding \
  apps.media_file.test_live_api.LiveMediaFileApiTests.test_bind_mission_should_follow_live_http_contract \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_expose_refactored_paths \
  -v 2
```

Expected:
- `FAIL`
- `/api/v1/media-files/bind-mission` should return `404` or `405`.
- The schema should not expose the new path yet.

- [ ] **Step 3: Implement the request serializer, collection action, and URL wiring**

Update `apps/media_file/serializers.py`:

```python
from rest_framework import serializers

from apps.api_v1.serializers import RejectUnknownFieldsMixin
from apps.api_v1.tenant_scope import require_request_tenant
from apps.media_file.models import MediaFile
from apps.mission.models import Mission, MissionStatus


class MediaFileBindMissionSerializer(RejectUnknownFieldsMixin, serializers.Serializer):
    mission_id = serializers.IntegerField(min_value=1)
    media_file_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        tenant = require_request_tenant(self.context)
        mission = Mission.objects.filter(
            tenant=tenant,
            id=attrs["mission_id"],
            is_deleted=False,
        ).first()
        if mission is None:
            raise serializers.ValidationError({"mission_id": ["任务不存在或已删除"]})
        if mission.status != MissionStatus.DRONE_BOUND:
            raise serializers.ValidationError({"mission_id": ["仅允许绑定到已绑定无人机的任务"]})
        if not mission.device_sn:
            raise serializers.ValidationError({"mission_id": ["任务缺少 device_sn，无法绑定媒体"]})
        attrs["mission"] = mission
        attrs["media_file_ids"] = list(dict.fromkeys(attrs["media_file_ids"]))
        return attrs


class MediaFileBindMissionResultSerializer(serializers.Serializer):
    mission_id = serializers.IntegerField()
    media_file_ids = serializers.ListField(child=serializers.IntegerField())
    updated_count = serializers.IntegerField()
```

Update `apps/media_file/views.py`:

```python
from django.db import transaction
from rest_framework.decorators import action

from apps.media_file.serializers import (
    MediaFileBindMissionResultSerializer,
    MediaFileBindMissionSerializer,
    MediaFileReadSerializer,
)


MEDIA_FILE_BIND_MISSION_RESPONSE = object_envelope_serializer(
    "MediaFileBindMissionResponse",
    MediaFileBindMissionResultSerializer,
)


class MediaFileViewSet(
    BusinessApiResponseMixin,
    TenantScopedBusinessMixin,
    PermissionMapMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    http_method_names = ["get", "post", "delete", "head", "options"]
    permission_map = {
        "list": "media_file.view_media_file",
        "retrieve": "media_file.view_media_file",
        "download": "media_file.view_media_file",
        "destroy": "media_file.manage_media_file",
        "bind_mission": "media_file.manage_media_file",
    }

    def get_serializer_class(self):
        if self.action == "bind_mission":
            return MediaFileBindMissionSerializer
        return MediaFileReadSerializer

    @extend_schema(
        summary="批量绑定媒体到任务",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=MediaFileBindMissionSerializer,
        responses={
            200: OpenApiResponse(response=MEDIA_FILE_BIND_MISSION_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Media File"],
    )
    @action(detail=False, methods=["post"], url_path="bind-mission")
    @transaction.atomic
    def bind_mission(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mission = serializer.validated_data["mission"]
        media_ids = serializer.validated_data["media_file_ids"]

        queryset = self.apply_scope(
            self.scope_queryset_to_tenant(
                MediaFile.objects.select_related("mission", "flight_record", "dji_index")
            ).filter(is_deleted=False, id__in=media_ids)
        )
        media_files = list(queryset.select_for_update().order_by("id"))
        if len(media_files) != len(media_ids):
            return Response(
                validation_error_payload({"media_file_ids": ["存在不存在、已删除或无权限的媒体记录"]}),
                status=status.HTTP_400_BAD_REQUEST,
            )

        mismatched_ids = [item.id for item in media_files if item.device_sn != mission.device_sn]
        if mismatched_ids:
            return Response(
                validation_error_payload({"media_file_ids": [f"以下媒体 device_sn 不匹配: {mismatched_ids}"]}),
                status=status.HTTP_400_BAD_REQUEST,
            )
        conflicting_ids = [
            item.id
            for item in media_files
            if item.flight_record_id and item.flight_record and item.flight_record.mission_id and item.flight_record.mission_id != mission.id
        ]
        if conflicting_ids:
            return Response(
                validation_error_payload({"media_file_ids": [f"以下媒体已被 flight_record 锁定到其他任务: {conflicting_ids}"]}),
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated_ids = []
        for media_file in media_files:
            media_file.mission = mission
            media_file.save(update_fields=["mission"])
            if getattr(media_file, "dji_index", None) is not None:
                media_file.dji_index.mission = mission
                media_file.dji_index.save(update_fields=["mission", "updated_at"])
            updated_ids.append(media_file.id)

        payload = {
            "mission_id": mission.id,
            "media_file_ids": updated_ids,
            "updated_count": len(updated_ids),
        }
        log_action(
            request=request,
            action="MEDIA_FILE_BIND_MISSION",
            target_type="mission",
            target_id=mission.id,
            after_data=payload,
        )
        return Response(payload, status=status.HTTP_200_OK)
```

Update `apps/media_file/urls.py`:

```python
media_file_bind_mission = MediaFileViewSet.as_view({"post": "bind_mission"})

urlpatterns = [
    path("media-files", media_file_list, name="media-file-list"),
    path("media-files/bind-mission", media_file_bind_mission, name="media-file-bind-mission"),
    path("media-files/<int:pk>", media_file_detail, name="media-file-detail"),
    path("media-files/<int:pk>/download", media_file_download, name="media-file-download"),
]
```

Update `apps/api_v1/tests.py` so the OpenAPI contract includes the new collection action:

```python
expected_methods = {
    "/api/v1/media-files": {"get"},
    "/api/v1/media-files/{id}": {"get", "delete"},
    "/api/v1/media-files/bind-mission": {"post"},
}

expected_responses = {
    ("/api/v1/media-files/bind-mission", "post"): {"200", "400", "401", "403", "500"},
}
```

Normalize the existing media test fixtures to the new mission state model:

```python
-status=MissionStatus.RUNNING,
-dji_job_id="media-job-001",
+status=MissionStatus.DRONE_BOUND,

-status=MissionStatus.RUNNING,
-dji_job_id="media-live-job",
+status=MissionStatus.DRONE_BOUND,
```

Apply those replacements in:
- `apps/media_file/tests.py`
- `apps/media_file/test_live_api.py`

- [ ] **Step 4: Re-run the batch-bind and schema tests and confirm the endpoint works end-to-end**

Run:

```bash
.venv/bin/python manage.py test \
  apps.media_file.tests.MediaFileApiTests.test_bind_mission_should_assign_multiple_media_to_bound_mission \
  apps.media_file.tests.MediaFileApiTests.test_bind_mission_should_reject_unbound_mission \
  apps.media_file.tests.MediaFileApiTests.test_bind_mission_should_reject_device_sn_mismatch \
  apps.media_file.tests.MediaFileApiTests.test_bind_mission_should_reject_cross_tenant_mission \
  apps.media_file.tests.MediaFileApiTests.test_bind_mission_should_overwrite_existing_mission_binding \
  apps.media_file.test_live_api.LiveMediaFileApiTests.test_bind_mission_should_follow_live_http_contract \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_expose_refactored_paths \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_lock_current_operation_surface_and_bodyless_actions \
  apps.api_v1.tests.OpenApiDocsTests.test_business_schema_should_describe_current_business_error_responses \
  -v 2
```

Expected:
- `OK`
- The new endpoint should return `200` with `mission_id`, `media_file_ids`, and `updated_count`.
- The OpenAPI schema should expose `/api/v1/media-files/bind-mission` with `POST` only.

- [ ] **Step 5: Commit the explicit media batch-binding API**

Run:

```bash
git add \
  apps/media_file/serializers.py \
  apps/media_file/views.py \
  apps/media_file/urls.py \
  apps/media_file/tests.py \
  apps/media_file/test_live_api.py \
  apps/api_v1/tests.py

git commit -m "feat(media_file): add explicit mission batch binding"
```

### Task 6: Update Canonical Schema Docs and Run Full Regression

**Files:**
- Modify: `项目总体概览/逻辑设计/overall_logical_model.md`
- Modify: `项目总体概览/逻辑设计/overall_data_dictionary.md`
- Modify: `项目总体概览/逻辑设计/overall_schema.dbml`
- Test: `apps/mission/tests.py`
- Test: `apps/mission/test_live_api.py`
- Test: `apps/route/tests.py`
- Test: `apps/media_file/tests.py`
- Test: `apps/media_file/test_live_api.py`
- Test: `apps/dji_bff/tests.py`
- Test: `apps/api_v1/tests.py`
- Test: `apps/drone/tests.py`
- Test: `apps/drone/test_live_api.py`

- [ ] **Step 1: Update the canonical docs to match the new schema and behavior**

Before editing the docs, sweep the remaining test fixtures for removed mission status values and `dji_job_id` writes:

Run:

```bash
rg -n "MissionStatus\\.(PENDING|RUNNING|PAUSED|COMPLETED|CANCELED|FAILED)|dji_job_id\\s*=|TenantMissionIndex" \
  apps/dji_bff/tests.py \
  apps/media_file/tests.py \
  apps/media_file/test_live_api.py \
  apps/drone/tests.py \
  apps/route/tests.py
```

Then apply the concrete replacements:

```python
-status=MissionStatus.PENDING,
+status=MissionStatus.DRONE_BOUND,

-status=MissionStatus.RUNNING,
+status=MissionStatus.DRONE_BOUND,

-status=MissionStatus.PAUSED,
+status=MissionStatus.DRONE_BOUND,

-status=MissionStatus.COMPLETED,
+status=MissionStatus.DRONE_BOUND,

-dji_job_id="some-legacy-id",
```

For any fixture that intentionally models “mission without bound drone”, use:

```python
status=MissionStatus.DRONE_UNBOUND,
drone=None,
device_sn="",
drone_name="",
```

After that sweep, update the docs themselves.

Update the mission section in `项目总体概览/逻辑设计/overall_logical_model.md` to this shape:

```markdown
### 2.16 missions（任务表）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| route_id | bigint | FK -> routes.id, NULLABLE | 任务航线 |
| drone_id | bigint | FK -> drones.id, NULLABLE | 绑定无人机；为空表示未绑定 |
| device_sn | varchar(128) | NOT NULL, DEFAULT '' | 无人机 SN 冗余，用于追溯 |
| status | smallint | NOT NULL, DEFAULT 0 | 仅表示无人机绑定状态：0=DRONE_UNBOUND, 1=DRONE_BOUND |
| is_deleted | boolean | NOT NULL, DEFAULT false | 软删除标记 |
| deleted_at | timestamp | NULLABLE | 删除时间 |

说明：
1. Mission 只表示 Django 本地任务单，不再映射 DJI job。
2. Mission 软删除不可恢复，不提供恢复 API。
3. `drone_id` 为空时，`status` 必须为 `DRONE_UNBOUND`。
4. `drone_id` 非空时，`status` 必须为 `DRONE_BOUND`。
```

Update the DJI index section in `项目总体概览/逻辑设计/overall_data_dictionary.md` by removing the entire `tenant_mission_indexes` table entry and rewriting the media section notes to say:

```markdown
1. media 同步链路只按 `device_sn -> tenant` 做可靠落库，不再按 `job_id` 自动关联 mission。
2. `media_files.mission_id` 为可空外键，仅通过业务 API 显式绑定。
3. Mission 与 Media 的软删除均不可恢复。
```

Update `项目总体概览/逻辑设计/overall_schema.dbml` so the `missions` table and the DJI index tables match the new runtime shape:

```dbml
Table missions {
  id bigint [pk, increment]
  tenant_id bigint [not null, ref: > tenants.id]
  name varchar(100) [not null]
  route_id bigint [ref: > routes.id]
  route_name varchar(100) [not null, default: '']
  drone_id bigint [ref: > drones.id]
  device_sn varchar(128) [not null, default: '']
  drone_name varchar(100) [not null, default: '']
  pilot_id bigint [not null, ref: > tenant_members.id]
  pilot_name varchar(50) [not null, default: '']
  scheduled_at timestamp
  remark varchar(500) [not null, default: '']
  status smallint [not null, default: 0, note: '0=DRONE_UNBOUND,1=DRONE_BOUND']
  is_deleted boolean [not null, default: false]
  deleted_at timestamp
  created_at timestamp [not null]
  updated_at timestamp [not null]
}

Table tenant_media_indexes {
  id bigint [pk, increment]
  tenant_id bigint [not null, ref: > tenants.id]
  media_file_id bigint [not null, unique, ref: > media_files.id]
  dji_file_id varchar(128) [not null]
  device_sn varchar(128) [not null, default: '']
  mission_id bigint [ref: > missions.id]
  sync_status varchar(32) [not null, default: 'PENDING']
  last_sync_at timestamp
  error_msg varchar(255) [not null, default: '']
  created_at timestamp [not null]
  updated_at timestamp [not null]
}
```

- [ ] **Step 2: Grep the canonical docs for removed mission-sync concepts**

Run:

```bash
rg -n "tenant_mission_indexes|dji_job_id|任务同步|flight-tasks|/missions/\{id\}/cancel" \
  项目总体概览/逻辑设计/overall_logical_model.md \
  项目总体概览/逻辑设计/overall_data_dictionary.md \
  项目总体概览/逻辑设计/overall_schema.dbml
```

Expected:
- no matches

- [ ] **Step 3: Run the cross-module regression suite**

Run:

```bash
.venv/bin/python manage.py test \
  apps.mission.tests \
  apps.mission.test_live_api \
  apps.route.tests \
  apps.media_file.tests \
  apps.media_file.test_live_api \
  apps.dji_bff.tests \
  apps.api_v1.tests \
  apps.drone.tests \
  apps.drone.test_live_api \
  -v 2
```

Expected:
- `OK`
- No references to `TenantMissionIndex`, `dji_job_id`, or `/api/v1/missions/{id}/cancel` remain in the passing contract tests.

- [ ] **Step 4: Inspect the final diff and make sure only the planned files changed**

Run:

```bash
git status --short
```

Expected:
- only the files listed in this plan are modified
- no accidental `.codex/`, temp files, or unrelated user changes are staged

- [ ] **Step 5: Commit the documentation sync and final integration pass**

Run:

```bash
git add \
  项目总体概览/逻辑设计/overall_logical_model.md \
  项目总体概览/逻辑设计/overall_data_dictionary.md \
  项目总体概览/逻辑设计/overall_schema.dbml

git commit -m "docs: align canonical mission and media schema docs"
```
