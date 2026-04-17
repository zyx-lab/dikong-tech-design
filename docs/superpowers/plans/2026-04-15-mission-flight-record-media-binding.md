# Mission Flight Record Media Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically bind synced media to the unique `FlightRecord` of its resolved `Mission`, keep `FlightRecord.video_count` strongly consistent with current bound video media, and remove `video_count` from the writable `PUT /api/v1/flight-records/{id}` contract.

**Architecture:** Keep `flight_record` detail serialization unchanged and fill the missing relationship in the DJI media sync pipeline. Extend `apps/dji_bff/tasks.py` with two focused helpers: one resolves the active `FlightRecord` for a `Mission`, the other recalculates `video_count` from current bound video media. Update the flight-record write serializer and API tests so `video_count` stops being a human-editable summary field.

**Tech Stack:** Django ORM, Django TestCase, DRF serializers, existing `sync_media_indexes()` task flow, existing flight-record API tests.

---

## File Structure

- Modify: `apps/dji_bff/tasks.py`
  - Add one helper for `mission -> flight_record` resolution
  - Add one helper for strong-consistency `video_count` recalculation with row-level serialization
  - Extend media sync create/update paths to set `MediaFile.flight_record` and recalculate affected `FlightRecord.video_count`
- Modify: `apps/dji_bff/tests.py`
  - Add TDD coverage for new-media flight-record binding
  - Add TDD coverage for no-record behavior
  - Add TDD coverage for backfill on resync
  - Add TDD coverage for preserving existing explicit flight-record binding
  - Add TDD coverage for `video_count` recalculation
- Modify: `apps/flight_record/models.py`
  - Align `build_snapshot_defaults()` with the new strong-consistency `video_count` semantics
- Modify: `apps/mission/tests.py`
  - Update completed-mission snapshot expectations to the new initial `video_count` behavior
- Modify: `apps/flight_record/serializers.py`
  - Remove `video_count` from `FlightRecordWriteSerializer`
- Modify: `apps/flight_record/tests.py`
  - Replace the old happy-path write test that edited `video_count`
  - Add a failing test that sending `video_count` now returns invalid params
- Modify: `apps/flight_record/test_live_api.py`
  - Update the live HTTP contract test to stop sending `video_count`
  - Add/adjust a live assertion that `video_count` remains read-only
- Verify unchanged: `apps/flight_record/serializers.py`
  - `FlightRecordDetailSerializer.get_media_files()` must continue reading only `obj.media_files`

### Task 1: Bind New Synced Media to FlightRecord and Recalculate Video Count

**Files:**
- Modify: `apps/dji_bff/tests.py`
- Modify: `apps/dji_bff/tasks.py`

- [ ] **Step 1: Write the failing tests for create-path flight-record binding and video-count sync**

Add the missing model import near the top of `apps/dji_bff/tests.py`:

```python
from apps.flight_record.models import FlightRecord
```

Append these two tests immediately after `test_sync_media_indexes_should_auto_bind_when_media_is_within_finished_at_grace_window`:

```python
    def test_sync_media_indexes_should_auto_bind_flight_record_for_new_media_when_mission_has_record(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-FLIGHT-RECORD-NEW-DRONE",
            name="新媒体飞行记录无人机",
            model="M30",
            device_sn="MEDIA-FLIGHT-RECORD-NEW-SN-001",
        )
        route = Route.objects.create(tenant=self.tenant, name="新媒体飞行记录航线")
        captured_at = timezone.now() - timedelta(minutes=1)
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="新媒体飞行记录任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=captured_at - timedelta(minutes=2),
            finished_at=captured_at + timedelta(minutes=2),
        )
        flight_record = FlightRecord.create_from_completed_mission(mission=mission)
        gateway = SimpleNamespace(
            list_media_files=lambda: [
                {
                    "file_id": "media-flight-record-new-file",
                    "file_name": "MEDIA_FLIGHT_RECORD_NEW.MP4",
                    "drone": drone.device_sn,
                    "captured_at": captured_at.isoformat(),
                }
            ]
        )

        summary = sync_media_indexes(gateway=gateway)

        self.assertEqual(summary["created_count"], 1)
        media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="media-flight-record-new-file")
        self.assertEqual(media_index.mission_id, mission.id)
        self.assertEqual(media_index.media_file.flight_record_id, flight_record.id)
        flight_record.refresh_from_db()
        self.assertEqual(flight_record.video_count, 1)

    def test_sync_media_indexes_should_leave_flight_record_empty_when_mission_has_no_record(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-FLIGHT-RECORD-NONE-DRONE",
            name="无飞行记录无人机",
            model="M30",
            device_sn="MEDIA-FLIGHT-RECORD-NONE-SN-001",
        )
        route = Route.objects.create(tenant=self.tenant, name="无飞行记录航线")
        captured_at = timezone.now() - timedelta(minutes=1)
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="无飞行记录任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=captured_at - timedelta(minutes=2),
            finished_at=captured_at + timedelta(minutes=2),
        )
        gateway = SimpleNamespace(
            list_media_files=lambda: [
                {
                    "file_id": "media-flight-record-none-file",
                    "file_name": "MEDIA_FLIGHT_RECORD_NONE.MP4",
                    "drone": drone.device_sn,
                    "captured_at": captured_at.isoformat(),
                }
            ]
        )

        summary = sync_media_indexes(gateway=gateway)

        self.assertEqual(summary["created_count"], 1)
        media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="media-flight-record-none-file")
        self.assertEqual(media_index.mission_id, mission.id)
        self.assertIsNone(media_index.media_file.flight_record_id)
```

- [ ] **Step 2: Run the targeted tests to confirm RED**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_auto_bind_flight_record_for_new_media_when_mission_has_record \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_leave_flight_record_empty_when_mission_has_no_record
```

Expected:

- The first test fails because the create path does not currently write `flight_record`
- The first test may also fail on `video_count` remaining `0`
- The second test can already pass or fail, but the overall command must be red

- [ ] **Step 3: Write the minimal create-path implementation**

Add the missing import at the top of `apps/dji_bff/tasks.py`:

```python
from apps.flight_record.models import FlightRecord
```

Add these helpers below `_match_mission_for_media()`:

```python
def _flight_record_for_mission(*, mission: Mission | None):
    if mission is None:
        return None
    return FlightRecord.objects.filter(mission=mission, is_deleted=False).first()


def _sync_video_count_for_flight_record(*, flight_record: FlightRecord | None):
    if flight_record is None:
        return
    flight_record.video_count = MediaFile.objects.filter(
        flight_record=flight_record,
        is_deleted=False,
        media_type=MediaType.VIDEO,
        dji_index__isnull=False,
    ).count()
    flight_record.save(update_fields=["video_count", "updated_at"])
```

In `sync_media_indexes()`, resolve `resolved_flight_record` before the `with transaction.atomic()` block:

```python
        resolved_flight_record = _flight_record_for_mission(mission=matched_mission)
```

Update the create branch:

```python
            if media_index is None:
                media_file = MediaFile.objects.create(
                    **media_fields,
                    mission=matched_mission,
                    flight_record=resolved_flight_record,
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
                _sync_video_count_for_flight_record(flight_record=resolved_flight_record)
                summary.created_count += 1
```

Do not touch the update path yet.

- [ ] **Step 4: Run the targeted tests again to confirm GREEN**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_auto_bind_flight_record_for_new_media_when_mission_has_record \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_leave_flight_record_empty_when_mission_has_no_record
```

Expected:

- Both tests pass
- Django reports `Ran 2 tests` and `OK`

- [ ] **Step 5: Commit the create-path change**

Run:

```bash
git add apps/dji_bff/tasks.py apps/dji_bff/tests.py
git commit -m "fix(dji_bff): bind new synced media to flight records"
```

Expected:

- One commit containing the helper import, helpers, and the two new tests

### Task 2: Backfill FlightRecord on Resync, Serialize Recounts, and Align Initial Video Count Semantics

**Files:**
- Modify: `apps/dji_bff/tests.py`
- Modify: `apps/dji_bff/tasks.py`
- Modify: `apps/flight_record/models.py`
- Modify: `apps/mission/tests.py`

- [ ] **Step 1: Write the failing tests for resync backfill, preservation, and initial count semantics**

Append these two tests after the Task 1 tests:

```python
    def test_sync_media_indexes_should_backfill_flight_record_for_existing_mission_bound_media(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-FLIGHT-RECORD-BACKFILL-DRONE",
            name="回填飞行记录无人机",
            model="M30",
            device_sn="MEDIA-FLIGHT-RECORD-BACKFILL-SN-001",
        )
        route = Route.objects.create(tenant=self.tenant, name="回填飞行记录航线")
        captured_at = timezone.now() - timedelta(minutes=1)
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="回填飞行记录任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=captured_at - timedelta(minutes=2),
            finished_at=captured_at + timedelta(minutes=2),
        )
        flight_record = FlightRecord.create_from_completed_mission(mission=mission)
        media_file = MediaFile.objects.create(
            tenant=self.tenant,
            mission=mission,
            flight_record=None,
            device_sn=drone.device_sn,
            media_type=MediaType.VIDEO,
            file_name="MEDIA_FLIGHT_RECORD_BACKFILL.MP4",
            file_url="dji://media-flight-record-backfill-file",
            captured_at=captured_at,
        )
        TenantMediaIndex.objects.create(
            tenant=self.tenant,
            media_file=media_file,
            dji_file_id="media-flight-record-backfill-file",
            device_sn=drone.device_sn,
            mission=mission,
            sync_status=SyncStatus.SYNCED,
            last_sync_at=captured_at,
        )
        gateway = SimpleNamespace(
            list_media_files=lambda: [
                {
                    "file_id": "media-flight-record-backfill-file",
                    "file_name": "MEDIA_FLIGHT_RECORD_BACKFILL.MP4",
                    "drone": drone.device_sn,
                    "captured_at": captured_at.isoformat(),
                }
            ]
        )

        summary = sync_media_indexes(gateway=gateway)

        self.assertEqual(summary["updated_count"], 1)
        media_file.refresh_from_db()
        self.assertEqual(media_file.flight_record_id, flight_record.id)
        flight_record.refresh_from_db()
        self.assertEqual(flight_record.video_count, 1)

    def test_sync_media_indexes_should_preserve_existing_flight_record_binding_on_resync(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-FLIGHT-RECORD-PRESERVE-DRONE",
            name="保留飞行记录无人机",
            model="M30",
            device_sn="MEDIA-FLIGHT-RECORD-PRESERVE-SN-001",
        )
        route = Route.objects.create(tenant=self.tenant, name="保留飞行记录航线")
        preserved_captured_at = timezone.now() - timedelta(minutes=10)
        preserved_mission = Mission.objects.create(
            tenant=self.tenant,
            name="保留飞行记录任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=preserved_captured_at - timedelta(minutes=2),
            finished_at=preserved_captured_at + timedelta(minutes=2),
        )
        preserved_flight_record = FlightRecord.create_from_completed_mission(mission=preserved_mission)
        media_file = MediaFile.objects.create(
            tenant=self.tenant,
            mission=None,
            flight_record=preserved_flight_record,
            device_sn=drone.device_sn,
            media_type=MediaType.VIDEO,
            file_name="MEDIA_FLIGHT_RECORD_PRESERVE.MP4",
            file_url="dji://media-flight-record-preserve-file",
            captured_at=preserved_captured_at,
        )
        TenantMediaIndex.objects.create(
            tenant=self.tenant,
            media_file=media_file,
            dji_file_id="media-flight-record-preserve-file",
            device_sn=drone.device_sn,
            mission=None,
            sync_status=SyncStatus.SYNCED,
            last_sync_at=preserved_captured_at,
        )
        matched_captured_at = timezone.now() - timedelta(minutes=1)
        Mission.objects.create(
            tenant=self.tenant,
            name="新的自动匹配任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=matched_captured_at - timedelta(minutes=2),
            finished_at=matched_captured_at + timedelta(minutes=2),
        )
        gateway = SimpleNamespace(
            list_media_files=lambda: [
                {
                    "file_id": "media-flight-record-preserve-file",
                    "file_name": "MEDIA_FLIGHT_RECORD_PRESERVE.MP4",
                    "drone": drone.device_sn,
                    "captured_at": matched_captured_at.isoformat(),
                }
            ]
        )

        summary = sync_media_indexes(gateway=gateway)

        self.assertEqual(summary["updated_count"], 1)
        media_file.refresh_from_db()
        self.assertEqual(media_file.flight_record_id, preserved_flight_record.id)
        self.assertEqual(media_file.mission_id, preserved_mission.id)
        preserved_flight_record.refresh_from_db()
        self.assertEqual(preserved_flight_record.video_count, 1)
```

In `apps/mission/tests.py`, update the assertion in `test_advance_should_complete_mission_and_create_flight_record_snapshot` to the new strong-consistency expectation:

```python
        self.assertEqual(record.video_count, 0)
```

- [ ] **Step 2: Run the targeted tests to confirm RED**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_backfill_flight_record_for_existing_mission_bound_media \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_preserve_existing_flight_record_binding_on_resync

./.venv/bin/python manage.py test \
  apps.mission.tests.MissionApiTests.test_advance_should_complete_mission_and_create_flight_record_snapshot
```

Expected:

- The backfill test fails because the update path does not write `flight_record`
- The preserve test fails because resync does not currently derive `mission` from an existing `flight_record` binding
- The mission snapshot test fails because `build_snapshot_defaults()` still initializes `video_count` from mission-bound media instead of the new strong-consistency definition

- [ ] **Step 3: Extend the update path and recalculate counts**

First, replace `_sync_video_count_for_flight_record()` in `apps/dji_bff/tasks.py` with this serialized version:

```python
def _sync_video_count_for_flight_record(*, flight_record: FlightRecord | None):
    if flight_record is None:
        return
    locked_flight_record = FlightRecord.objects.select_for_update().filter(
        pk=flight_record.pk,
        is_deleted=False,
    ).first()
    if locked_flight_record is None:
        return
    locked_flight_record.video_count = MediaFile.objects.filter(
        flight_record=locked_flight_record,
        is_deleted=False,
        media_type=MediaType.VIDEO,
        dji_index__isnull=False,
    ).count()
    locked_flight_record.save(update_fields=["video_count", "updated_at"])
```

Replace the current update-branch mission resolution inside `sync_media_indexes()` with this logic:

```python
            else:
                media_file = media_index.media_file
                previous_flight_record = media_file.flight_record
                preserved_mission = (
                    media_index.mission
                    or media_file.mission
                    or (previous_flight_record.mission if previous_flight_record is not None else None)
                )
                resolved_mission = preserved_mission or matched_mission
                resolved_flight_record = previous_flight_record or _flight_record_for_mission(mission=resolved_mission)
                for field, value in media_fields.items():
                    setattr(media_file, field, value)
                media_file.mission = resolved_mission
                media_file.flight_record = resolved_flight_record
                media_file.save(
                    update_fields=[
                        "tenant",
                        "mission",
                        "flight_record",
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
                media_index.mission = resolved_mission
                media_index.device_sn = device_sn
                media_index.sync_status = SyncStatus.SYNCED
                media_index.last_sync_at = media_fields["captured_at"] or now
                media_index.error_msg = ""
                media_index.save(
                    update_fields=["mission", "device_sn", "sync_status", "last_sync_at", "error_msg", "updated_at"]
                )
                _sync_video_count_for_flight_record(flight_record=previous_flight_record)
                if resolved_flight_record != previous_flight_record:
                    _sync_video_count_for_flight_record(flight_record=resolved_flight_record)
                summary.updated_count += 1
```

Important points:

- Preserve an existing explicit `MediaFile.flight_record`
- When a media row already has `flight_record` but no `mission`, backfill `mission` from `flight_record.mission`
- Recalculate the old and new `FlightRecord.video_count` values whenever the bound `flight_record` may have changed
- Keep the recount inside the existing transaction block so the row lock actually serializes concurrent updates

In `apps/flight_record/models.py`, align the initial snapshot value by changing only the `video_count` line inside `build_snapshot_defaults()` to:

```python
            "video_count": 0,
```

- [ ] **Step 4: Run the targeted tests and both affected suites to confirm GREEN**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_backfill_flight_record_for_existing_mission_bound_media \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_preserve_existing_flight_record_binding_on_resync

./.venv/bin/python manage.py test \
  apps.mission.tests.MissionApiTests.test_advance_should_complete_mission_and_create_flight_record_snapshot

./.venv/bin/python manage.py test apps.dji_bff.tests
./.venv/bin/python manage.py test apps.mission.tests
./.venv/bin/python manage.py test apps.flight_record.tests
```

Expected:

- The two targeted tests pass
- The mission snapshot test now passes with `video_count == 0`
- `apps.dji_bff.tests` passes in full
- `apps.mission.tests` passes in full
- `apps.flight_record.tests` still passes because the detail serializer contract remains unchanged

- [ ] **Step 5: Commit the resync/backfill change**

Run:

```bash
git add apps/dji_bff/tasks.py apps/dji_bff/tests.py
git commit -m "fix(dji_bff): backfill flight records for synced media"
```

Expected:

- One commit containing the update-path change and the resync/video-count tests

### Task 3: Remove video_count From FlightRecord PUT and Update API Contracts

**Files:**
- Modify: `apps/flight_record/tests.py`
- Modify: `apps/flight_record/test_live_api.py`
- Modify: `apps/flight_record/serializers.py`

- [ ] **Step 1: Write the failing tests for read-only video_count**

In `apps/flight_record/tests.py`, replace the current `test_put_flight_record_should_only_update_summary_fields` body with a request that updates only writable fields and asserts `video_count` is unchanged:

```python
    def test_put_flight_record_should_only_update_summary_fields(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record(status=FlightRecordStatus.COMPLETED)
        original_video_count = record.video_count

        response = self.client.put(
            f"/api/v1/flight-records/{record.id}",
            {
                "mission_name": "修正后的任务名",
                "airport_name": "深圳宝安机场",
                "photo_count": 0,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["mission_name"], "修正后的任务名")
        self.assertEqual(response.data["data"]["airport_name"], "深圳宝安机场")
        self.assertEqual(response.data["data"]["video_count"], original_video_count)
        record.refresh_from_db()
        self.assertEqual(record.mission_name, "修正后的任务名")
        self.assertEqual(record.airport_name, "深圳宝安机场")
        self.assertEqual(record.video_count, original_video_count)
```

Add a new invalid-param test right after it:

```python
    def test_put_flight_record_with_video_count_should_return_invalid_params(self):
        self._grant_permission("flight_record.manage_flight_record")
        self.client.force_authenticate(self.viewer_user)
        record = self._create_flight_record()

        response = self.client.put(
            f"/api/v1/flight-records/{record.id}",
            {"video_count": 8},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "B0001")
        self.assertEqual(response.data["data"], {"video_count": ["该字段在此接口不可写"]})
```

In `apps/flight_record/test_live_api.py`, update `test_list_retrieve_update_delete_should_follow_live_http_contract` so the `PUT` payload no longer sends `video_count`, and add an explicit invalid request:

```python
        put_response = self.client.put(
            f"/api/v1/flight-records/{record.id}",
            {
                "mission_name": "修正后的任务名称",
                "airport_name": "深圳宝安机场",
            },
            format="json",
        )
        self.assertEqual(put_response.status_code, 200)
        put_data = put_response.json()["data"]
        self.assertEqual(put_data["mission_name"], "修正后的任务名称")
        self.assertEqual(put_data["airport_name"], "深圳宝安机场")
        self.assertEqual(put_data["video_count"], record.video_count)

        invalid_put_response = self.client.put(
            f"/api/v1/flight-records/{record.id}",
            {"video_count": 5},
            format="json",
        )
        self.assertEqual(invalid_put_response.status_code, 400)
```

- [ ] **Step 2: Run the targeted tests to confirm RED**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.flight_record.tests.FlightRecordApiTests.test_put_flight_record_should_only_update_summary_fields \
  apps.flight_record.tests.FlightRecordApiTests.test_put_flight_record_with_video_count_should_return_invalid_params \
  apps.flight_record.test_live_api.LiveFlightRecordApiTests.test_list_retrieve_update_delete_should_follow_live_http_contract
```

Expected:

- The new invalid-param test fails because `video_count` is still writable
- The live test fails because the invalid request currently succeeds

- [ ] **Step 3: Remove video_count from the write serializer**

In `apps/flight_record/serializers.py`, change `FlightRecordWriteSerializer.Meta` to this exact structure:

```python
class FlightRecordWriteSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = FlightRecord
        fields = [
            "mission_name",
            "route_name",
            "airport_name",
            "drone_name",
            "pilot_name",
            "flight_duration",
            "photo_count",
        ]
        extra_kwargs = {
            "mission_name": {"required": False, "help_text": "任务名称展示文案，可人工修正。"},
            "route_name": {"required": False, "help_text": "航线名称展示文案，可人工修正。"},
            "airport_name": {"required": False, "help_text": "执行机场名称，当前阶段允许人工补录。"},
            "drone_name": {"required": False, "help_text": "无人机名称展示文案，可人工修正。"},
            "pilot_name": {"required": False, "help_text": "飞手姓名展示文案，可人工修正。"},
            "flight_duration": {"required": False, "help_text": "飞行时长快照，单位秒。"},
            "photo_count": {"required": False, "help_text": "图片数量；当前阶段固定由人工修正，默认 0。"},
        }
```

Do not change `FlightRecordSummarySerializer` or `FlightRecordDetailSerializer`; `video_count` must remain readable.

- [ ] **Step 4: Run the targeted tests and the flight-record suites to confirm GREEN**

Run:

```bash
./.venv/bin/python manage.py test \
  apps.flight_record.tests.FlightRecordApiTests.test_put_flight_record_should_only_update_summary_fields \
  apps.flight_record.tests.FlightRecordApiTests.test_put_flight_record_with_video_count_should_return_invalid_params \
  apps.flight_record.test_live_api.LiveFlightRecordApiTests.test_list_retrieve_update_delete_should_follow_live_http_contract

./.venv/bin/python manage.py test apps.flight_record.tests
./.venv/bin/python manage.py test apps.flight_record.test_live_api
```

Expected:

- The targeted tests pass
- Both flight-record test modules pass
- `video_count` is still returned in GET responses but rejected in PUT requests

- [ ] **Step 5: Commit the read-only video_count contract**

Run:

```bash
git add apps/flight_record/serializers.py apps/flight_record/tests.py apps/flight_record/test_live_api.py
git commit -m "fix(flight_record): make video count read only"
```

Expected:

- One commit containing the serializer change and the API contract tests

### Task 4: Final Verification Against the Approved Design

**Files:**
- Verify changed: `apps/dji_bff/tasks.py`
- Verify changed: `apps/dji_bff/tests.py`
- Verify changed: `apps/flight_record/serializers.py`
- Verify changed: `apps/flight_record/tests.py`
- Verify changed: `apps/flight_record/test_live_api.py`

- [ ] **Step 1: Confirm no mission fallback was introduced in the detail serializer**

Run:

```bash
git diff -- apps/flight_record/serializers.py
```

Expected:

- The diff only shows `FlightRecordWriteSerializer` changes
- No changes to `FlightRecordDetailSerializer.get_media_files()`

- [ ] **Step 2: Re-run the exact verification commands for the full feature**

Run:

```bash
./.venv/bin/python manage.py test apps.dji_bff.tests
./.venv/bin/python manage.py test apps.flight_record.tests
./.venv/bin/python manage.py test apps.flight_record.test_live_api
```

Expected:

- All three commands report `OK`
- No failing tests in any suite

- [ ] **Step 3: Capture the deploy-time smoke checks for the human operator**

After deploying and restarting the sync worker, run:

```bash
curl -sS "$BASE_URL/api/v1/media-files" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Code: $TENANT_CODE" | jq '.data.list[] | {id, mission_id, flight_record}'

curl -sS "$BASE_URL/api/v1/flight-records/20" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Code: $TENANT_CODE" | jq '.data | {id, mission, video_count, media_files}'

curl -sS -X PUT "$BASE_URL/api/v1/flight-records/20" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Code: $TENANT_CODE" \
  -H "Content-Type: application/json" \
  -d '{"video_count": 5}' | jq '{code, data}'
```

Expected:

- The previously mission-bound media rows now also show non-null `flight_record`
- `GET /api/v1/flight-records/{id}` returns non-empty `media_files`
- `GET /api/v1/flight-records/{id}` returns a `video_count` that matches the currently bound video media rows
- The `PUT` request with `video_count` now returns `400`

## Self-Review

- Spec coverage:
  - Auto-bind `flight_record` on mission match: covered by Task 1 and Task 2
  - Preserve existing explicit binding: covered by Task 2
  - Strong-consistency `video_count`: covered by Task 1 and Task 2
  - Remove writable `video_count`: covered by Task 3
  - Keep GET detail semantics without fallback: covered by Task 4
- Placeholder scan:
  - No `TODO`, `TBD`, or “similar to” placeholders remain
  - Every code-edit step includes concrete code
- Type consistency:
  - Uses the existing `Mission`, `FlightRecord`, `MediaFile`, `TenantMediaIndex`, `MediaType`, and `sync_media_indexes()` names from the current codebase
  - Helper names `_flight_record_for_mission()` and `_sync_video_count_for_flight_record()` are defined once and reused consistently
