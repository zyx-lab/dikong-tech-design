from __future__ import annotations

from django.utils import timezone

from apps.access.services import log_action
from apps.dji_bff.models import SyncStatus, TenantMediaIndex, TenantRouteIndex


def _counts(*, resolved_count: int, ignored_count: int) -> dict[str, int]:
    return {"resolved_count": resolved_count, "ignored_count": ignored_count}


def _mark_route_index_published(route_index: TenantRouteIndex) -> None:
    route_index.is_published = True
    route_index.save(update_fields=["is_published", "updated_at"])


def _mark_media_index_synced(media_index: TenantMediaIndex, *, now) -> None:
    media_index.sync_status = SyncStatus.SYNCED
    media_index.last_sync_at = now
    media_index.error_msg = ""
    media_index.save(update_fields=["sync_status", "last_sync_at", "error_msg", "updated_at"])


def handle_wayline_upload_callback(payload: dict, *, request=None) -> dict[str, int]:
    name = str(payload.get("name") or "").strip()
    metadata = payload.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    dji_wayline_id = str(
        metadata.get("dji_wayline_id")
        or metadata.get("wayline_id")
        or metadata.get("file_id")
        or ""
    ).strip()
    resolved_count = 0
    ignored_count = 0

    if dji_wayline_id:
        route_index = TenantRouteIndex.objects.filter(dji_wayline_id=dji_wayline_id).first()
        if route_index is not None:
            _mark_route_index_published(route_index)
            resolved_count = 1
        else:
            ignored_count = 1
    elif name:
        route_indexes = list(TenantRouteIndex.objects.select_related("route").filter(route__name=name))
        if len(route_indexes) == 1:
            route_index = route_indexes[0]
            _mark_route_index_published(route_index)
            resolved_count = 1
        else:
            ignored_count = 1
    else:
        ignored_count = 1

    result = _counts(resolved_count=resolved_count, ignored_count=ignored_count)
    log_action(
        action="DJI_WAYLINE_CALLBACK",
        target_type="tenant_route_index",
        request=request,
        after_data={"payload": payload, **result},
    )
    return result


def handle_media_upload_callback(payload: dict, *, request=None) -> dict[str, int]:
    ext = payload.get("ext")
    ext = ext if isinstance(ext, dict) else {}
    dji_file_id = str(
        ext.get("dji_file_id")
        or ext.get("file_id")
        or payload.get("dji_file_id")
        or payload.get("file_id")
        or ""
    ).strip()
    now = timezone.now()
    resolved_count = 0
    ignored_count = 0

    if dji_file_id:
        media_index = TenantMediaIndex.objects.filter(dji_file_id=dji_file_id).first()
        if media_index is not None:
            _mark_media_index_synced(media_index, now=now)
            resolved_count = 1
        else:
            ignored_count = 1
    else:
        ignored_count = 1

    result = _counts(resolved_count=resolved_count, ignored_count=ignored_count)
    log_action(
        action="DJI_MEDIA_UPLOAD_CALLBACK",
        target_type="tenant_media_index",
        request=request,
        after_data={"payload": payload, **result},
    )
    return result


def handle_media_group_upload_callback(payload: dict, *, request=None) -> dict[str, int]:
    result = {
        "file_group_id": str(payload.get("file_group_id") or ""),
        "file_count": int(payload.get("file_count") or 0),
        "file_uploaded_count": int(payload.get("file_uploaded_count") or 0),
    }
    log_action(
        action="DJI_MEDIA_GROUP_CALLBACK",
        target_type="tenant_media_index",
        request=request,
        after_data={"payload": payload, **result},
    )
    return result
