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


@dataclass(frozen=True)
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
        normalized_sn = str(device_sn or "").strip()
        if not normalized_sn:
            return
        observed_at = observed_at or timezone.now()
        snapshot = FlightStateSnapshot(
            is_airborne=mode_code in AIRBORNE_MODE_CODES,
            mode_code=mode_code,
            last_osd_at=observed_at,
            updated_at=timezone.now(),
        )
        with self._lock:
            self._states[normalized_sn] = snapshot

    def get(self, device_sn: str) -> FlightStateSnapshot | None:
        normalized_sn = str(device_sn or "").strip()
        if not normalized_sn:
            return None
        with self._lock:
            return self._states.get(normalized_sn)


def resolve_mission_display_status(mission: Mission) -> int:
    if mission.status != MissionStatus.RUNNING:
        return mission.status
    if not mission.device_sn:
        return MissionDisplayStatus.RUNNING

    snapshot = flight_state_registry.get(mission.device_sn)
    if snapshot is None or snapshot.last_osd_at is None:
        return MissionDisplayStatus.RUNNING

    freshness_seconds = max(0, int(getattr(settings, "DJI_MQTT_OSD_FRESHNESS_SECONDS", 10)))
    age_seconds = (timezone.now() - snapshot.last_osd_at).total_seconds()
    if age_seconds > freshness_seconds:
        return MissionDisplayStatus.RUNNING
    if snapshot.is_airborne:
        return MissionDisplayStatus.FLYING
    return MissionDisplayStatus.RUNNING


flight_state_registry = FlightStateRegistry()
