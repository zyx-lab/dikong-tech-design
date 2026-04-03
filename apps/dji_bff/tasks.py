from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.access.services import log_action
from apps.dji_bff.gateway import DjiGateway
from apps.dji_bff.models import DjiDeviceIndex, SyncStatus, TenantMediaIndex, TenantMissionIndex
from apps.drone.models import Drone
from apps.flight_record.models import FlightRecord
from apps.media_file.models import MediaFile, MediaType
from apps.mission.models import MissionStatus


DJI_JOB_STATUS_MAP = {
    "READY": MissionStatus.PENDING,
    "PENDING": MissionStatus.PENDING,
    "QUEUED": MissionStatus.PENDING,
    "RUNNING": MissionStatus.RUNNING,
    "IN_PROGRESS": MissionStatus.RUNNING,
    "EXECUTING": MissionStatus.RUNNING,
    "PAUSED": MissionStatus.PAUSED,
    "FINISHED": MissionStatus.COMPLETED,
    "COMPLETED": MissionStatus.COMPLETED,
    "SUCCESS": MissionStatus.COMPLETED,
    "DONE": MissionStatus.COMPLETED,
    "CANCELED": MissionStatus.CANCELED,
    "CANCELLED": MissionStatus.CANCELED,
    "STOPPED": MissionStatus.CANCELED,
    "ABORTED": MissionStatus.CANCELED,
    "FAILED": MissionStatus.FAILED,
    "ERROR": MissionStatus.FAILED,
}


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
                return parsed
    return None


def _device_sn_from_payload(payload: dict) -> str:
    # 直接取外层 device_sn（无人机 SN）
    # children 是摄像头/负载，不需要取其 SN
    return _string(payload, "device_sn", "deviceSn", "sn")


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


def _mission_status(raw_status: str):
    return DJI_JOB_STATUS_MAP.get(raw_status.strip().upper()) if raw_status else None


def sync_device_indexes(*, gateway: DjiGateway | None = None) -> dict[str, int]:
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
            "last_seen_at": _datetime_value(payload, "last_seen_at", "lastSeenAt", "updated_at", "updatedAt") or now,
            "firmware_version": _string(payload, "firmware_version", "firmwareVersion"),
            "firmware_status": _string(payload, "firmware_status", "firmwareStatus"),
        }
        _, created = DjiDeviceIndex.objects.update_or_create(device_sn=device_sn, defaults=defaults)
        seen_device_sns.add(device_sn)
        summary.synced_count += 1
        if created:
            summary.created_count += 1
        else:
            summary.updated_count += 1

    DjiDeviceIndex.objects.exclude(device_sn__in=seen_device_sns).delete()
    Drone.objects.filter(device_sn__in=seen_device_sns).update(dji_online=True)
    Drone.objects.exclude(device_sn__in=seen_device_sns).update(dji_online=False)

    log_action(action="DJI_DEVICE_SYNC", target_type="dji_device_index", after_data=summary.asdict())
    return summary.asdict()


def sync_mission_indexes(*, gateway: DjiGateway | None = None) -> dict[str, int]:
    gateway = gateway or DjiGateway()
    summary = SyncSummary()
    now = timezone.now()
    jobs_by_id: dict[str, dict] = {}

    for payload in gateway.list_jobs():
        if not isinstance(payload, dict):
            continue
        job_id = _string(payload, "job_id", "jobId", "id")
        if job_id:
            jobs_by_id[job_id] = payload

    for mission_index in TenantMissionIndex.objects.select_related("mission").order_by("id"):
        payload = jobs_by_id.get(mission_index.dji_job_id)
        if payload is None:
            mission_index.sync_status = SyncStatus.ERROR
            mission_index.error_msg = "上游任务不存在"
            mission_index.last_sync_at = now
            mission_index.save(update_fields=["sync_status", "error_msg", "last_sync_at", "updated_at"])
            summary.error_count += 1
            continue

        execution_status = _string(payload, "status", "execution_status", "executionStatus")
        mission_index.execution_status = execution_status
        mission_index.sync_status = SyncStatus.SYNCED
        mission_index.error_msg = ""
        mission_index.last_sync_at = _datetime_value(payload, "updated_at", "updatedAt", "created_at", "createdAt") or now
        mission_index.save(update_fields=["execution_status", "sync_status", "error_msg", "last_sync_at", "updated_at"])

        local_status = _mission_status(execution_status)
        if local_status is not None and mission_index.mission.status != local_status:
            mission_index.mission.status = local_status
            mission_index.mission.save(update_fields=["status", "updated_at"])

        summary.synced_count += 1
        summary.updated_count += 1

    log_action(action="DJI_MISSION_SYNC", target_type="tenant_mission_index", after_data=summary.asdict())
    return summary.asdict()


def sync_media_indexes(*, gateway: DjiGateway | None = None) -> dict[str, int]:
    gateway = gateway or DjiGateway()
    summary = SyncSummary()
    now = timezone.now()
    mission_indexes_by_job_id = {
        index.dji_job_id: index
        for index in TenantMissionIndex.objects.select_related("tenant", "mission").all()
        if index.dji_job_id
    }

    for payload in gateway.list_media_files():
        if not isinstance(payload, dict):
            summary.ignored_count += 1
            continue

        dji_file_id = _string(payload, "file_id", "fileId", "id")
        if not dji_file_id:
            summary.ignored_count += 1
            continue

        device_sn = _string(payload, "device_sn", "deviceSn", "sn")
        job_id = _string(payload, "job_id", "jobId")

        mission_index = mission_indexes_by_job_id.get(job_id) if job_id else None
        mission = mission_index.mission if mission_index is not None else None
        tenant = mission_index.tenant if mission_index is not None else None

        if tenant is None and device_sn:
            claimed_drone = Drone.objects.select_related("tenant").filter(device_sn=device_sn).first()
            if claimed_drone is not None:
                tenant = claimed_drone.tenant

        if tenant is None:
            summary.ignored_count += 1
            continue

        flight_record = None
        if mission is not None:
            flight_record = FlightRecord.objects.filter(tenant=tenant, mission=mission).order_by("-id").first()
        if flight_record is None and device_sn:
            flight_record = (
                FlightRecord.objects.filter(tenant=tenant, drone__device_sn=device_sn)
                .order_by("-id")
                .first()
            )

        media_defaults = {
            "tenant": tenant,
            "flight_record": flight_record,
            "media_type": _media_type(payload),
            "file_name": _file_name(payload),
            "file_url": f"dji://{dji_file_id}",
            "thumbnail_url": _string(payload, "thumbnail_url", "thumbnailUrl"),
            "file_size": _int(payload, "file_size", "fileSize"),
            "latitude": payload.get("latitude"),
            "longitude": payload.get("longitude"),
            "captured_at": _datetime_value(payload, "captured_at", "capturedAt", "create_time", "createTime"),
            "is_deleted": False,
            "deleted_at": None,
        }

        with transaction.atomic():
            media_index = (
                TenantMediaIndex.objects.select_related("media_file")
                .filter(tenant=tenant, dji_file_id=dji_file_id)
                .first()
            )
            if media_index is None:
                media_file = MediaFile.objects.create(**media_defaults)
                TenantMediaIndex.objects.create(
                    tenant=tenant,
                    media_file=media_file,
                    dji_file_id=dji_file_id,
                    device_sn=device_sn,
                    mission=mission,
                    sync_status=SyncStatus.SYNCED,
                    last_sync_at=media_defaults["captured_at"] or now,
                    error_msg="",
                )
                summary.created_count += 1
            else:
                media_file = media_index.media_file
                for field, value in media_defaults.items():
                    setattr(media_file, field, value)
                media_file.save()
                media_index.device_sn = device_sn
                media_index.mission = mission
                media_index.sync_status = SyncStatus.SYNCED
                media_index.last_sync_at = media_defaults["captured_at"] or now
                media_index.error_msg = ""
                media_index.save(update_fields=["device_sn", "mission", "sync_status", "last_sync_at", "error_msg", "updated_at"])
                summary.updated_count += 1

        summary.synced_count += 1

    log_action(action="DJI_MEDIA_SYNC", target_type="tenant_media_index", after_data=summary.asdict())
    return summary.asdict()
