from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.access.request_logging import sync_log_context
from apps.access.services import log_action
from apps.dji_bff.gateway import DjiGateway
from apps.dji_bff.models import DjiDeviceIndex, SyncStatus, TenantMediaIndex
from apps.drone.models import Drone, DroneStatus
from apps.flight_record.models import FlightRecord
from apps.media_file.models import MediaFile, MediaType
from apps.mission.models import Mission, MissionStatus

MISSION_MEDIA_FINISH_GRACE_SECONDS = 60


@dataclass
class SyncSummary:
    synced_count: int = 0
    created_count: int = 0
    updated_count: int = 0
    ignored_count: int = 0
    error_count: int = 0

    def asdict(self) -> dict[str, int]:
        return {
            "synced_count": self.synced_count,
            "created_count": self.created_count,
            "updated_count": self.updated_count,
            "ignored_count": self.ignored_count,
            "error_count": self.error_count,
        }


def _string(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _int(payload: dict, *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return int(value.strip())
            except ValueError:
                continue
    return None


def _datetime_value(payload: dict, *keys: str):
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            parsed = parse_datetime(value.strip())
            if parsed is not None:
                if timezone.is_naive(parsed):
                    return timezone.make_aware(parsed, timezone.get_current_timezone())
                return parsed
    return None


def _device_sn_from_payload(payload: dict) -> str:
    # 上游设备列表常用 device_sn；媒体列表在真实环境里也可能用 drone 表示无人机 SN。
    return _string(payload, "device_sn", "deviceSn", "sn", "drone")


def _file_name(payload: dict) -> str:
    return _string(payload, "name", "file_name", "fileName") or "unknown"


def _media_type(payload: dict) -> int:
    explicit = _int(payload, "media_type", "mediaType", "type")
    if explicit in {MediaType.PHOTO, MediaType.VIDEO}:
        return explicit
    suffix = Path(_file_name(payload)).suffix.lower()
    if suffix in {".mp4", ".mov", ".avi", ".mkv"}:
        return MediaType.VIDEO
    return MediaType.PHOTO


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
        window_end = mission.finished_at + timedelta(
            seconds=MISSION_MEDIA_FINISH_GRACE_SECONDS
        )
        if mission.started_at <= captured_at <= window_end:
            matched.append(mission)

    if len(matched) == 1:
        return matched[0]
    return None


def _flight_record_for_mission(*, mission: Mission | None):
    if mission is None:
        return None
    return FlightRecord.objects.filter(mission=mission, is_deleted=False).first()


def _sync_video_count_for_flight_record(*, flight_record: FlightRecord | None):
    FlightRecord.sync_video_count_from_media(flight_record=flight_record)


def sync_device_indexes(
    *, gateway: DjiGateway | None = None, sync_run_id: str | None = None
) -> dict[str, int]:
    with sync_log_context(sync_run_id):
        gateway = gateway or DjiGateway()
        summary = SyncSummary()
        seen_device_sns: set[str] = set()
        now = timezone.now()

        for payload in gateway.list_devices():
            if not isinstance(payload, dict):
                summary.ignored_count += 1
                continue

            domain = _int(payload, "domain")
            if domain not in (None, 0):
                summary.ignored_count += 1
                continue

            device_sn = _device_sn_from_payload(payload)
            if not device_sn:
                summary.ignored_count += 1
                continue

            defaults = {
                "last_payload": payload,
                "last_seen_at": _datetime_value(
                    payload, "last_seen_at", "lastSeenAt", "updated_at", "updatedAt"
                )
                or now,
                "firmware_version": _string(
                    payload, "firmware_version", "firmwareVersion"
                ),
                "firmware_status": _string(
                    payload, "firmware_status", "firmwareStatus"
                ),
            }
            _, created = DjiDeviceIndex.objects.update_or_create(
                device_sn=device_sn, defaults=defaults
            )
            seen_device_sns.add(device_sn)
            summary.synced_count += 1
            if created:
                summary.created_count += 1
            else:
                summary.updated_count += 1

        DjiDeviceIndex.objects.exclude(device_sn__in=seen_device_sns).delete()
        Drone.objects.filter(device_sn__in=seen_device_sns).update(dji_online=True)
        Drone.objects.exclude(device_sn__in=seen_device_sns).update(dji_online=False)

        log_action(
            action="DJI_DEVICE_SYNC",
            target_type="dji_device_index",
            after_data=summary.asdict(),
        )
        return summary.asdict()


def sync_media_indexes(
    *, gateway: DjiGateway | None = None, sync_run_id: str | None = None
) -> dict[str, int]:
    with sync_log_context(sync_run_id):
        gateway = gateway or DjiGateway()
        summary = SyncSummary()
        now = timezone.now()
        workspace_id = gateway._workspace_id()

        for payload in gateway.list_media_files():
            if not isinstance(payload, dict):
                summary.ignored_count += 1
                continue

            dji_file_id = _string(payload, "file_id", "fileId", "id")
            if not dji_file_id:
                summary.ignored_count += 1
                continue

            device_sn = _device_sn_from_payload(payload)
            if not device_sn:
                summary.ignored_count += 1
                continue

            claimed_drone = (
                Drone.objects.select_related("tenant")
                .filter(device_sn=device_sn, status=DroneStatus.CLAIMED)
                .first()
            )
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
                "captured_at": _datetime_value(
                    payload, "captured_at", "capturedAt", "create_time", "createTime"
                ),
            }
            matched_mission = _match_mission_for_media(
                tenant=tenant,
                device_sn=device_sn,
                captured_at=media_fields["captured_at"],
            )
            with transaction.atomic():
                media_index = (
                    TenantMediaIndex.objects.select_related("media_file")
                    .filter(tenant=tenant, dji_file_id=dji_file_id)
                    .first()
                )
                if media_index is None:
                    resolved_flight_record = _flight_record_for_mission(
                        mission=matched_mission
                    )
                    media_file = MediaFile.objects.create(
                        **media_fields,
                        mission=matched_mission,
                        flight_record=resolved_flight_record,
                    )
                    TenantMediaIndex.objects.create(
                        tenant=tenant,
                        media_file=media_file,
                        workspace_id=workspace_id,
                        dji_file_id=dji_file_id,
                        device_sn=device_sn,
                        mission=matched_mission,
                        sync_status=SyncStatus.SYNCED,
                        last_sync_at=media_fields["captured_at"] or now,
                        error_msg="",
                    )
                    _sync_video_count_for_flight_record(
                        flight_record=resolved_flight_record
                    )
                    summary.created_count += 1
                else:
                    media_file = media_index.media_file
                    previous_flight_record = media_file.flight_record
                    preserved_mission = (
                        media_index.mission
                        or media_file.mission
                        or (
                            previous_flight_record.mission
                            if previous_flight_record is not None
                            else None
                        )
                    )
                    resolved_mission = preserved_mission or matched_mission
                    resolved_flight_record = (
                        previous_flight_record
                        or _flight_record_for_mission(mission=resolved_mission)
                    )
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
                    media_index.workspace_id = workspace_id
                    media_index.mission = resolved_mission
                    media_index.device_sn = device_sn
                    media_index.sync_status = SyncStatus.SYNCED
                    media_index.last_sync_at = media_fields["captured_at"] or now
                    media_index.error_msg = ""
                    media_index.save(
                        update_fields=[
                            "workspace_id",
                            "mission",
                            "device_sn",
                            "sync_status",
                            "last_sync_at",
                            "error_msg",
                            "updated_at",
                        ]
                    )
                    _sync_video_count_for_flight_record(
                        flight_record=previous_flight_record
                    )
                    if resolved_flight_record != previous_flight_record:
                        _sync_video_count_for_flight_record(
                            flight_record=resolved_flight_record
                        )
                    summary.updated_count += 1

            summary.synced_count += 1

        log_action(
            action="DJI_MEDIA_SYNC",
            target_type="tenant_media_index",
            after_data=summary.asdict(),
        )
        return summary.asdict()
