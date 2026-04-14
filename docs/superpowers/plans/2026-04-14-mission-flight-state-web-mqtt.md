# Mission Flight State via In-Process MQTT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a derived `飞行中` mission status that is returned by mission APIs when an already-running mission's drone is observed airborne via DJI MQTT OSD, without persisting that transient state to the database.

**Architecture:** Keep `Mission.status` persisted as `待执行 / 执行中 / 执行完成`. Add a process-local flight state registry keyed by `device_sn`, plus a single workspace-level MQTT watcher embedded in the Django Web process. Mission read serialization resolves `status=执行中` into `status=飞行中` when the registry says the drone is airborne and the OSD is still fresh.

**Tech Stack:** Django 5.1, Django REST Framework, drf-spectacular, Django AppConfig startup hooks, Python threads, `paho-mqtt`, existing `DjiWorkspaceConfig` / `DjiGateway` login cache.

---

## File map and responsibilities

- **Modify:** `requirements.txt`
  - Add the MQTT client dependency required by the Web-process watcher.
- **Modify:** `config/settings.py`
  - Add small, explicit watcher settings for enable flag, refresh interval, and OSD freshness timeout.
- **Create:** `apps/mission/flight_state.py`
  - Define the derived mission display status enum (`0/1/2/3`), runtime registry, `mode_code` airborne mapping, timeout logic, and a helper that resolves mission display status from a `Mission` instance.
- **Modify:** `apps/mission/serializers.py`
  - Switch read-side `status` from model choices to computed display status and update field descriptions.
- **Modify:** `apps/mission/views.py`
  - Update OpenAPI descriptions so mission list/detail/advance explicitly document the derived `飞行中` behavior.
- **Modify:** `apps/mission/apps.py`
  - Start the DJI MQTT watcher only in real Web-process contexts, not in tests or offline management commands.
- **Modify:** `apps/dji_bff/gateway.py`
  - Add one small public helper that returns a validated, authenticated workspace config for MQTT usage.
- **Create:** `apps/dji_bff/mqtt_watcher.py`
  - Implement the singleton watcher manager, startup guard, MQTT connection loop, subscription refresh, OSD handling, and registry updates.
- **Modify:** `apps/mission/tests.py`
  - Add failing tests for derived `飞行中` mission status and timeout fallback.
- **Modify:** `apps/dji_bff/tests.py`
  - Add failing tests for watcher startup guards, subscription target resolution, and OSD-to-registry updates with fake MQTT clients.
- **Modify:** `apps/mission/test_live_api.py`
  - Extend live contract expectations for mission `status` values and verify that ordinary mission execution still returns persisted `0/1/2` in the absence of registry flight state.
- **Modify:** `apps/api_v1/tests.py`
  - Lock OpenAPI schema docs to mention `status=3` and the fact that it is derived, not persisted.

---

### Task 1: Lock the derived mission status contract with failing tests

**Files:**
- Modify: `apps/mission/tests.py`
- Modify: `apps/mission/test_live_api.py`
- Modify: `apps/api_v1/tests.py`
- Test: `apps/mission/tests.py`
- Test: `apps/mission/test_live_api.py`
- Test: `apps/api_v1/tests.py`

- [ ] **Step 1: Write failing mission tests for runtime `飞行中` status resolution**

```python
from datetime import timedelta
from django.utils import timezone

from apps.mission.flight_state import flight_state_registry
from apps.mission.models import MissionStatus


def test_retrieve_should_return_flying_status_when_running_mission_drone_is_airborne(self):
    mission = _persist_mission_fixture(
        tenant=self.tenant,
        name="飞行中任务",
        route=self.route,
        route_name=self.route.name,
        drone=self.drone,
        device_sn=self.drone.device_sn,
        drone_name=self.drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.RUNNING,
        started_at=timezone.now() - timedelta(minutes=3),
    )
    flight_state_registry.update_from_mode_code(device_sn=self.drone.device_sn, mode_code=5)

    response = self.client.get(f"/missions/{mission.id}")

    self.assertEqual(response.status_code, 200)
    self.assertEqual(self._payload(response.data)["status"], 3)


def test_retrieve_should_fall_back_to_running_when_airborne_state_is_stale(self):
    mission = _persist_mission_fixture(
        tenant=self.tenant,
        name="超时回退任务",
        route=self.route,
        route_name=self.route.name,
        drone=self.drone,
        device_sn=self.drone.device_sn,
        drone_name=self.drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.RUNNING,
        started_at=timezone.now() - timedelta(minutes=3),
    )
    flight_state_registry.update_from_mode_code(
        device_sn=self.drone.device_sn,
        mode_code=5,
        observed_at=timezone.now() - timedelta(seconds=30),
    )

    response = self.client.get(f"/missions/{mission.id}")

    self.assertEqual(response.status_code, 200)
    self.assertEqual(self._payload(response.data)["status"], 1)


def test_retrieve_should_not_override_completed_status_with_airborne_registry(self):
    mission = _persist_mission_fixture(
        tenant=self.tenant,
        name="完成任务",
        route=self.route,
        route_name=self.route.name,
        drone=self.drone,
        device_sn=self.drone.device_sn,
        drone_name=self.drone.name,
        pilot=self.pilot_member,
        pilot_name="飞手",
        status=MissionStatus.COMPLETED,
        started_at=timezone.now() - timedelta(minutes=10),
        finished_at=timezone.now() - timedelta(minutes=1),
    )
    flight_state_registry.update_from_mode_code(device_sn=self.drone.device_sn, mode_code=5)

    response = self.client.get(f"/missions/{mission.id}")

    self.assertEqual(response.status_code, 200)
    self.assertEqual(self._payload(response.data)["status"], 2)
```

- [ ] **Step 2: Extend the live contract test so absence of runtime state still preserves persisted status**

```python
def test_create_advance_and_delete_should_follow_live_http_contract(self):
    # existing create omitted
    advance_response = self.client.post(f"/api/v1/missions/{mission_id}/advance")
    self.assertEqual(advance_response.status_code, 200)
    advance_data = advance_response.json()["data"]
    self.assertEqual(advance_data["status"], 1)
    self.assertIsNotNone(advance_data["started_at"])
    self.assertIsNone(advance_data["finished_at"])
```

- [ ] **Step 3: Add failing schema assertions for the new derived `status=3` documentation**

```python
def test_business_schema_should_describe_derived_mission_flying_status(self):
    response = self.client.get("/api/v1/docs/schema/")
    self.assertEqual(response.status_code, 200)
    schema = response.json()

    mission_props = schema["components"]["schemas"]["MissionRead"]["properties"]
    self.assertIn("3=飞行中", mission_props["status"]["description"])
    self.assertIn("实时派生", mission_props["status"]["description"])

    mission_retrieve = schema["paths"]["/api/v1/missions/{id}"]["get"]
    self.assertIn("飞行中", mission_retrieve["description"])
```

- [ ] **Step 4: Run the focused test subset and verify RED**

Run:
```bash
.venv/bin/python manage.py test \
  apps.mission.tests.MissionApiTests \
  apps.mission.test_live_api.LiveMissionApiTests \
  apps.api_v1.tests.OpenApiDocsTests \
  -v 1
```

Expected:
- FAIL because `apps.mission.flight_state` does not exist yet
- FAIL because mission read serializer only exposes persisted `0/1/2`
- FAIL because OpenAPI descriptions do not mention derived `status=3`

- [ ] **Step 5: Commit the failing tests**

```bash
git add apps/mission/tests.py apps/mission/test_live_api.py apps/api_v1/tests.py
git commit -m "test: lock derived mission flying status contract"
```

---

### Task 2: Implement runtime flight state resolution with minimal read-path changes

**Files:**
- Create: `apps/mission/flight_state.py`
- Modify: `apps/mission/serializers.py`
- Modify: `apps/mission/views.py`
- Test: `apps/mission/tests.py`
- Test: `apps/api_v1/tests.py`

- [ ] **Step 1: Create the runtime flight state module**

```python
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from django.conf import settings
from django.utils import timezone

from apps.mission.models import Mission, MissionStatus


class MissionDisplayStatus:
    PENDING = MissionStatus.PENDING
    RUNNING = MissionStatus.RUNNING
    COMPLETED = MissionStatus.COMPLETED
    FLYING = 3


AIRBORNE_MODE_CODES = {3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15, 16, 17, 18}


@dataclass
class FlightStateSnapshot:
    is_airborne: bool
    mode_code: int | None
    last_osd_at: object
    updated_at: object


class FlightStateRegistry:
    def __init__(self):
        self._lock = RLock()
        self._states: dict[str, FlightStateSnapshot] = {}

    def clear(self):
        with self._lock:
            self._states.clear()

    def update_from_mode_code(self, *, device_sn: str, mode_code: int | None, observed_at=None):
        if not device_sn:
            return
        now = observed_at or timezone.now()
        snapshot = FlightStateSnapshot(
            is_airborne=mode_code in AIRBORNE_MODE_CODES,
            mode_code=mode_code,
            last_osd_at=now,
            updated_at=timezone.now(),
        )
        with self._lock:
            self._states[device_sn] = snapshot

    def get(self, device_sn: str) -> FlightStateSnapshot | None:
        if not device_sn:
            return None
        with self._lock:
            return self._states.get(device_sn)
```

- [ ] **Step 2: Add timeout-aware mission display status resolution**

```python
def resolve_mission_display_status(mission: Mission) -> int:
    if mission.status != MissionStatus.RUNNING:
        return mission.status
    if not mission.device_sn:
        return MissionDisplayStatus.RUNNING

    snapshot = flight_state_registry.get(mission.device_sn)
    if snapshot is None or snapshot.last_osd_at is None:
        return MissionDisplayStatus.RUNNING

    freshness_seconds = int(getattr(settings, "DJI_MQTT_OSD_FRESHNESS_SECONDS", 10))
    if (timezone.now() - snapshot.last_osd_at).total_seconds() > freshness_seconds:
        return MissionDisplayStatus.RUNNING
    if snapshot.is_airborne:
        return MissionDisplayStatus.FLYING
    return MissionDisplayStatus.RUNNING
```

- [ ] **Step 3: Switch mission read serializer to use computed display status and updated help text**

```python
class MissionReadSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField(
        help_text=(
            "任务当前状态。通过任务列表返回项或任务详情的 `status` 字段读取："
            "0=待执行，1=执行中，2=执行完成，3=飞行中。"
            "其中 3=飞行中 为实时派生状态，不写入数据库。"
        )
    )

    def get_status(self, obj) -> int:
        return resolve_mission_display_status(obj)
```

- [ ] **Step 4: Update mission view OpenAPI descriptions to mention derived `飞行中`**

```python
description="任务当前状态通过响应 `data.status` 字段读取；状态值为 0=待执行、1=执行中、2=执行完成、3=飞行中。`3=飞行中` 为实时派生状态，仅在任务已处于执行中且对应无人机被实时判定为在飞时返回。"
```

- [ ] **Step 5: Re-run the focused tests and verify GREEN**

Run:
```bash
.venv/bin/python manage.py test \
  apps.mission.tests.MissionApiTests \
  apps.mission.test_live_api.LiveMissionApiTests \
  apps.api_v1.tests.OpenApiDocsTests \
  -v 1
```

Expected:
- PASS for the newly added mission status/read-path tests
- PASS for the schema description assertions

- [ ] **Step 6: Commit the runtime mission status implementation**

```bash
git add apps/mission/flight_state.py apps/mission/serializers.py apps/mission/views.py apps/api_v1/tests.py apps/mission/tests.py apps/mission/test_live_api.py
git commit -m "feat: derive mission flying status at read time"
```

---

### Task 3: Add the in-process DJI MQTT watcher with startup guards

**Files:**
- Modify: `requirements.txt`
- Modify: `config/settings.py`
- Modify: `apps/dji_bff/gateway.py`
- Create: `apps/dji_bff/mqtt_watcher.py`
- Modify: `apps/mission/apps.py`
- Modify: `apps/dji_bff/tests.py`
- Test: `apps/dji_bff/tests.py`

- [ ] **Step 1: Add the MQTT dependency and watcher settings**

```text
# requirements.txt
paho-mqtt==2.1.0
```

```python
# config/settings.py
DJI_MQTT_WATCHER_ENABLED = os.getenv("DJI_MQTT_WATCHER_ENABLED", "true").lower() == "true"
DJI_MQTT_WATCHER_REFRESH_SECONDS = int(os.getenv("DJI_MQTT_WATCHER_REFRESH_SECONDS", "5"))
DJI_MQTT_OSD_FRESHNESS_SECONDS = int(os.getenv("DJI_MQTT_OSD_FRESHNESS_SECONDS", "10"))
```

- [ ] **Step 2: Add a public gateway helper for authenticated workspace config retrieval**

```python
def get_workspace_config(self) -> DjiWorkspaceConfig:
    return self._ensure_authenticated()
```

- [ ] **Step 3: Write failing watcher tests with a fake MQTT client**

```python
@override_settings(DJI_MQTT_WATCHER_ENABLED=True)
def test_watcher_should_update_registry_from_osd_mode_code(self):
    watcher = DjiMqttWatcher()
    watcher._handle_osd_message(
        topic="thing/product/MISSION-SN-001/osd",
        payload={"data": {"mode_code": 5}},
    )

    snapshot = flight_state_registry.get("MISSION-SN-001")
    self.assertIsNotNone(snapshot)
    self.assertTrue(snapshot.is_airborne)
    self.assertEqual(snapshot.mode_code, 5)


def test_watcher_should_collect_running_mission_device_sns_only(self):
    Mission.objects.create(... status=MissionStatus.RUNNING, device_sn="RUNNING-SN")
    Mission.objects.create(... status=MissionStatus.COMPLETED, device_sn="DONE-SN")

    watcher = DjiMqttWatcher()
    self.assertEqual(watcher._target_device_sns(), {"RUNNING-SN"})


def test_should_start_in_process_watcher_should_reject_test_command(self):
    with patch("apps.dji_bff.mqtt_watcher.sys.argv", ["manage.py", "test"]):
        self.assertFalse(should_start_in_process_watcher())
```

- [ ] **Step 4: Implement the watcher with connection loop, topic refresh, and OSD handling**

```python
class DjiMqttWatcher:
    def _target_device_sns(self) -> set[str]:
        close_old_connections()
        try:
            return set(
                Mission.objects.filter(status=MissionStatus.RUNNING)
                .exclude(device_sn="")
                .values_list("device_sn", flat=True)
            )
        finally:
            close_old_connections()

    def _handle_osd_message(self, *, topic: str, payload: dict):
        device_sn = self._device_sn_from_topic(topic, suffix="/osd")
        mode_code = self._mode_code(payload)
        if device_sn and mode_code is not None:
            flight_state_registry.update_from_mode_code(device_sn=device_sn, mode_code=mode_code)
```

- [ ] **Step 5: Start the watcher from `MissionConfig.ready()` behind startup guards**

```python
class MissionConfig(AppConfig):
    ...
    def ready(self):
        from apps.dji_bff.mqtt_watcher import ensure_dji_mqtt_watcher_started

        ensure_dji_mqtt_watcher_started()
```

- [ ] **Step 6: Install the dependency locally and run the watcher test subset**

Run:
```bash
.venv/bin/pip install paho-mqtt==2.1.0
.venv/bin/python manage.py test apps.dji_bff.tests -v 1
```

Expected:
- PASS for the new watcher tests
- PASS for existing DJI BFF tests

- [ ] **Step 7: Commit the watcher implementation**

```bash
git add requirements.txt config/settings.py apps/dji_bff/gateway.py apps/dji_bff/mqtt_watcher.py apps/mission/apps.py apps/dji_bff/tests.py
git commit -m "feat: watch mission flight state via web mqtt"
```

---

### Task 4: Run regression verification and sync API docs wording

**Files:**
- Modify: `apps/mission/views.py` if schema wording still needs tightening
- Modify: `apps/api_v1/tests.py` if schema expectations need final alignment
- Test: `apps.mission.tests.MissionApiTests`
- Test: `apps.mission.test_live_api.LiveMissionApiTests`
- Test: `apps.dji_bff.tests.DjiBffSyncAndInternalApiTests`
- Test: `apps.api_v1.tests.OpenApiDocsTests`

- [ ] **Step 1: Run the full regression subset for affected domains**

Run:
```bash
.venv/bin/python manage.py test \
  apps.mission.tests.MissionApiTests \
  apps.mission.test_live_api.LiveMissionApiTests \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests \
  apps.api_v1.tests.OpenApiDocsTests \
  -v 1
```

Expected:
- All tests pass
- No drf-spectacular warning regressions

- [ ] **Step 2: If needed, tighten the final mission docs wording**

```python
# apps/mission/views.py description fragments
"3=飞行中为实时派生状态，不写入数据库；当任务已是执行中且对应无人机被 MQTT OSD 判定为在飞时返回。"
```

- [ ] **Step 3: Re-run the same regression command and verify GREEN**

Run:
```bash
.venv/bin/python manage.py test \
  apps.mission.tests.MissionApiTests \
  apps.mission.test_live_api.LiveMissionApiTests \
  apps.dji_bff.tests.DjiBffSyncAndInternalApiTests \
  apps.api_v1.tests.OpenApiDocsTests \
  -v 1
```

Expected:
- PASS again after any final wording adjustments

- [ ] **Step 4: Commit the doc/schema alignment if there was any final change**

```bash
git add apps/mission/views.py apps/api_v1/tests.py
if ! git diff --cached --quiet; then git commit -m "docs: clarify derived mission flying status"; fi
```
