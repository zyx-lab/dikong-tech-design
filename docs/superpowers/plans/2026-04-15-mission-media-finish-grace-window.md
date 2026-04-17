# Mission Media Finish Grace Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow DJI media auto-binding to match only completed missions and tolerate media captured up to 5 seconds after `finished_at`.

**Architecture:** Keep the change inside `apps.dji_bff.tasks._match_mission_for_media()` so the public API, persistence model, and sync orchestration stay unchanged. Add focused regression tests in `apps.dji_bff.tests` that lock the new finish-grace behavior and the new exclusion of missions without `finished_at`.

**Tech Stack:** Django, Django TestCase, existing `sync_media_indexes()` media sync path

---

### Task 1: Lock the finish-grace behavior with failing tests

**Files:**
- Modify: `apps/dji_bff/tests.py`
- Test: `apps/dji_bff/tests.py`

- [ ] **Step 1: Write the failing tests**

Add three tests near the existing `sync_media_indexes` mission-window coverage:

```python
    def test_sync_media_indexes_should_auto_bind_when_media_is_within_finished_at_grace_window(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-FINISH-GRACE-DRONE",
            name="结束宽限无人机",
            model="M30",
            device_sn="MEDIA-FINISH-GRACE-SN-001",
        )
        route = Route.objects.create(tenant=self.tenant, name="结束宽限航线")
        finished_at = timezone.now() - timedelta(minutes=1)
        captured_at = finished_at + timedelta(seconds=2)
        mission = Mission.objects.create(
            tenant=self.tenant,
            name="结束宽限任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=finished_at - timedelta(minutes=3),
            finished_at=finished_at,
        )
        gateway = SimpleNamespace(
            list_media_files=lambda: [
                {
                    "file_id": "media-finish-grace-file",
                    "file_name": "MEDIA_FINISH_GRACE.JPG",
                    "drone": drone.device_sn,
                    "captured_at": captured_at.isoformat(),
                }
            ]
        )

        summary = sync_media_indexes(gateway=gateway)

        self.assertEqual(summary["created_count"], 1)
        media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="media-finish-grace-file")
        self.assertEqual(media_index.mission_id, mission.id)

    def test_sync_media_indexes_should_not_auto_bind_when_media_is_past_finished_at_grace_window(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-FINISH-GRACE-PAST-DRONE",
            name="超出宽限无人机",
            model="M30",
            device_sn="MEDIA-FINISH-GRACE-SN-002",
        )
        route = Route.objects.create(tenant=self.tenant, name="超出宽限航线")
        finished_at = timezone.now() - timedelta(minutes=1)
        captured_at = finished_at + timedelta(seconds=6)
        Mission.objects.create(
            tenant=self.tenant,
            name="超出宽限任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.COMPLETED,
            started_at=finished_at - timedelta(minutes=3),
            finished_at=finished_at,
        )
        gateway = SimpleNamespace(
            list_media_files=lambda: [
                {
                    "file_id": "media-finish-grace-past-file",
                    "file_name": "MEDIA_FINISH_GRACE_PAST.JPG",
                    "drone": drone.device_sn,
                    "captured_at": captured_at.isoformat(),
                }
            ]
        )

        summary = sync_media_indexes(gateway=gateway)

        self.assertEqual(summary["created_count"], 1)
        media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="media-finish-grace-past-file")
        self.assertIsNone(media_index.mission_id)

    def test_sync_media_indexes_should_ignore_missions_without_finished_at_for_auto_bind(self):
        drone = Drone.objects.create(
            tenant=self.tenant,
            code="MEDIA-NO-FINISH-DRONE",
            name="无完成时间无人机",
            model="M30",
            device_sn="MEDIA-NO-FINISH-SN-001",
        )
        route = Route.objects.create(tenant=self.tenant, name="无完成时间航线")
        started_at = timezone.now() - timedelta(minutes=1)
        captured_at = started_at + timedelta(seconds=10)
        Mission.objects.create(
            tenant=self.tenant,
            name="无完成时间任务",
            route=route,
            route_name=route.name,
            drone=drone,
            device_sn=drone.device_sn,
            drone_name=drone.name,
            pilot=self.pilot_member,
            pilot_name="飞手",
            status=MissionStatus.RUNNING,
            started_at=started_at,
            finished_at=None,
        )
        gateway = SimpleNamespace(
            list_media_files=lambda: [
                {
                    "file_id": "media-no-finish-file",
                    "file_name": "MEDIA_NO_FINISH.JPG",
                    "drone": drone.device_sn,
                    "captured_at": captured_at.isoformat(),
                }
            ]
        )

        summary = sync_media_indexes(gateway=gateway)

        self.assertEqual(summary["created_count"], 1)
        media_index = TenantMediaIndex.objects.get(tenant=self.tenant, dji_file_id="media-no-finish-file")
        self.assertIsNone(media_index.mission_id)
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run:

```bash
.venv/bin/python manage.py test \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_auto_bind_when_media_is_within_finished_at_grace_window \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_not_auto_bind_when_media_is_past_finished_at_grace_window \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_ignore_missions_without_finished_at_for_auto_bind
```

Expected: at least the “within finished_at grace window” test fails under the old strict `finished_at` upper bound, and the “without finished_at” test fails if running missions still participate.

- [ ] **Step 3: Commit the red test change**

```bash
git add apps/dji_bff/tests.py
git commit -m "test(dji_bff): lock mission media finish grace behavior"
```

### Task 2: Implement the 5-second completed-mission grace window

**Files:**
- Modify: `apps/dji_bff/tasks.py`
- Test: `apps/dji_bff/tests.py`

- [ ] **Step 1: Write the minimal implementation**

Update `_match_mission_for_media()` to require completed mission windows and add a 5-second grace constant:

```python
MISSION_MEDIA_FINISH_GRACE_SECONDS = 5


def _match_mission_for_media(*, tenant, device_sn: str, captured_at):
    if not device_sn or captured_at is None:
        return None

    matched = []
    queryset = Mission.objects.filter(
        tenant=tenant,
        is_deleted=False,
        device_sn=device_sn,
        started_at__isnull=False,
        finished_at__isnull=False,
    ).exclude(status=MissionStatus.PENDING)

    for mission in queryset:
        window_end = mission.finished_at + timedelta(seconds=MISSION_MEDIA_FINISH_GRACE_SECONDS)
        if mission.started_at <= captured_at <= window_end:
            matched.append(mission)

    if len(matched) == 1:
        return matched[0]
    return None
```

- [ ] **Step 2: Run the focused tests to verify they pass**

Run:

```bash
.venv/bin/python manage.py test \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_auto_bind_when_media_is_within_finished_at_grace_window \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_not_auto_bind_when_media_is_past_finished_at_grace_window \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests.test_sync_media_indexes_should_ignore_missions_without_finished_at_for_auto_bind
```

Expected: all three tests PASS.

- [ ] **Step 3: Run the module regression suite**

Run:

```bash
.venv/bin/python manage.py test apps.dji_bff.tests
```

Expected: PASS with no regressions in existing media sync, device sync, and internal sync endpoint coverage.

- [ ] **Step 4: Commit the implementation**

```bash
git add apps/dji_bff/tasks.py apps/dji_bff/tests.py
git commit -m "fix(dji_bff): add mission media finish grace window"
```
