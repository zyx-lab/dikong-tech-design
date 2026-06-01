from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.access.exceptions import StandardConstraintConflict, StandardForbidden, StandardNotFound
from apps.access.models import DirectoryStatus
from apps.dji_bff.gateway import DjiGatewayError
from apps.iam_v2.models import FixedRole
from apps.iam_v2.services import is_platform_super_admin
from apps.resource_v2.audit import log_v2_action
from apps.resource_v2.gateway import DjiConnectionGateway
from apps.resource_v2.models import (
    BindingStatus,
    ResourceBinding,
    ResourceSharePermission,
    ResourceType,
)
from apps.resource_v2.services import get_resource, visible_bindings_queryset
from apps.workforce_v2.services import pilot_has_effective_qualification, visible_pilots_queryset
from apps.inspection_v2.models import (
    CloudMediaFile,
    CloudMediaType,
    FlightRecordStatus,
    FlightSession,
    FlightSessionStatus,
    FlightTelemetrySnapshot,
    InspectionFlightRecord,
    InspectionMission,
    MissionResourceAssignment,
    MissionStatus,
    WaypointRoute,
)


MEDIA_ASSOCIATION_GRACE_SECONDS = 0


def is_dispatcher(context) -> bool:
    return FixedRole.TASK_MONITOR_DISPATCHER in context.role_codes or is_platform_super_admin(context)


def is_department_admin(context) -> bool:
    return FixedRole.DEPARTMENT_ADMIN in context.role_codes or is_platform_super_admin(context)


def is_assigned_pilot(context, mission: InspectionMission) -> bool:
    return FixedRole.PILOT in context.role_codes and mission.pilot.account_profile.user_id == context.user.id


def is_plain_pilot(context) -> bool:
    return (
        FixedRole.PILOT in context.role_codes
        and not is_dispatcher(context)
        and not is_department_admin(context)
        and not is_platform_super_admin(context)
    )


def require_dispatcher(context) -> None:
    if not is_dispatcher(context):
        raise StandardForbidden()


def can_view_inspection(context) -> bool:
    return (
        is_dispatcher(context)
        or is_department_admin(context)
        or FixedRole.PILOT in context.role_codes
        or is_platform_super_admin(context)
    )


def require_inspection_viewer(context) -> None:
    if not can_view_inspection(context):
        raise StandardForbidden()


def _department_tree_q(field: str, context) -> Q:
    return Q(**{f"{field}__tenant": context.department.tenant, f"{field}__path__startswith": context.department.path})


def _shared_resource_permissions(context):
    return ResourceSharePermission.objects.filter(
        share_group__target_departments__department=context.department,
        share_group__status=DirectoryStatus.ACTIVE,
    )


def _shared_resource_q(context, prefix: str = "resource_assignments") -> Q:
    query = Q(pk__in=[])
    for share in _shared_resource_permissions(context):
        permissions = set(share.permissions or [])
        if not permissions.intersection({"view", "monitor", "dispatch_task", "use"}):
            continue
        query |= Q(
            **{
                f"{prefix}__resource_type": share.resource_type,
                f"{prefix}__resource_object_id": share.resource_object_id,
            }
        )
    return query


def visible_missions_queryset(context) -> QuerySet:
    require_inspection_viewer(context)
    queryset = (
        InspectionMission.objects.select_related(
            "creator_department",
            "primary_resource_owner_department",
            "route",
            "drone",
            "dock",
            "payload",
            "pilot",
            "pilot__account_profile",
            "pilot__account_profile__user",
            "pilot__account_profile__department",
        )
        .prefetch_related("resource_assignments")
        .filter(tenant=context.department.tenant)
        .order_by("-id")
    )
    if is_platform_super_admin(context):
        return queryset
    if is_plain_pilot(context):
        return queryset.filter(pilot__account_profile__user=context.user).distinct()
    return queryset.filter(
        _department_tree_q("creator_department", context)
        | _department_tree_q("primary_resource_owner_department", context)
        | _department_tree_q("resource_assignments__owner_department", context)
        | _shared_resource_q(context)
    ).distinct()


def visible_routes_queryset(context) -> QuerySet:
    require_inspection_viewer(context)
    queryset = WaypointRoute.objects.prefetch_related("waypoints").filter(tenant=context.department.tenant).order_by("-id")
    if is_platform_super_admin(context):
        return queryset
    visible_mission_route_ids = visible_missions_queryset(context).values("route_id")
    if is_plain_pilot(context):
        return queryset.filter(pk__in=visible_mission_route_ids).distinct()
    return queryset.filter(_department_tree_q("owner_department", context) | Q(pk__in=visible_mission_route_ids)).distinct()


def editable_routes_queryset(context) -> QuerySet:
    queryset = WaypointRoute.objects.prefetch_related("waypoints").filter(
        tenant=context.department.tenant,
        status=DirectoryStatus.ACTIVE,
    )
    if is_platform_super_admin(context):
        return queryset
    return queryset.filter(owner_department=context.department)


def assignable_routes_queryset(context) -> QuerySet:
    return visible_routes_queryset(context).filter(status=DirectoryStatus.ACTIVE)


def visible_records_queryset(context) -> QuerySet:
    require_inspection_viewer(context)
    queryset = (
        InspectionFlightRecord.objects.select_related(
            "mission",
            "session",
            "creator_department",
            "primary_resource_owner_department",
        )
        .filter(tenant=context.department.tenant)
        .order_by("-end_time", "-id")
    )
    if is_platform_super_admin(context):
        return queryset
    visible_mission_ids = visible_missions_queryset(context).values("id")
    if is_plain_pilot(context):
        return queryset.filter(mission_id__in=visible_mission_ids).distinct()
    return queryset.filter(
        _department_tree_q("creator_department", context)
        | _department_tree_q("primary_resource_owner_department", context)
        | Q(mission_id__in=visible_mission_ids)
    ).distinct()


def visible_media_queryset(context) -> QuerySet:
    require_inspection_viewer(context)
    queryset = CloudMediaFile.objects.select_related("flight_record", "mission").filter(
        tenant=context.department.tenant
    )
    if is_platform_super_admin(context):
        return queryset.order_by("-captured_at", "-id")
    visible_record_ids = visible_records_queryset(context).values("id")
    visible_mission_ids = visible_missions_queryset(context).values("id")
    return queryset.filter(Q(flight_record_id__in=visible_record_ids) | Q(mission_id__in=visible_mission_ids)).distinct()


def visible_sessions_queryset(context) -> QuerySet:
    if not (is_dispatcher(context) or FixedRole.PILOT in context.role_codes or is_platform_super_admin(context)):
        raise StandardForbidden()
    queryset = (
        FlightSession.objects.select_related(
            "mission",
            "mission__pilot",
            "mission__pilot__account_profile",
            "mission__pilot__account_profile__user",
            "drone",
            "dock",
            "payload",
        )
        .filter(status=FlightSessionStatus.RUNNING, mission__tenant=context.department.tenant)
        .order_by("-started_at", "-id")
    )
    if is_platform_super_admin(context):
        return queryset
    queryset = queryset.filter(mission_id__in=visible_missions_queryset(context).values("id"))
    if is_plain_pilot(context):
        queryset = queryset.filter(mission__pilot__account_profile__user=context.user)
    return queryset.distinct()


def get_visible_route_or_404(context, route_id: int) -> WaypointRoute:
    route = visible_routes_queryset(context).filter(pk=route_id).first()
    if route is None:
        raise StandardNotFound()
    return route


def get_editable_route_or_404(context, route_id: int) -> WaypointRoute:
    route = editable_routes_queryset(context).filter(pk=route_id).first()
    if route is None:
        raise StandardNotFound()
    return route


def get_visible_mission_or_404(context, mission_id: int) -> InspectionMission:
    mission = visible_missions_queryset(context).filter(pk=mission_id).first()
    if mission is None:
        raise StandardNotFound()
    return mission


def get_visible_record_or_404(context, record_id: int) -> InspectionFlightRecord:
    record = visible_records_queryset(context).filter(pk=record_id).first()
    if record is None:
        raise StandardNotFound()
    return record


def _active_binding(resource_type: str, resource_id: int) -> ResourceBinding:
    binding = (
        ResourceBinding.objects.select_related("owner_department", "dji_connection")
        .filter(resource_type=resource_type, resource_object_id=resource_id, status=BindingStatus.ACTIVE)
        .first()
    )
    if binding is None:
        raise StandardNotFound()
    return binding


def _shared_can_use(context, binding: ResourceBinding) -> bool:
    shares = ResourceSharePermission.objects.filter(
        share_group__target_departments__department=context.department,
        share_group__status=DirectoryStatus.ACTIVE,
        resource_type=binding.resource_type,
        resource_object_id=binding.resource_object_id,
    )
    return any(set(share.permissions or []).intersection({"use", "dispatch_task"}) for share in shares)


def usable_resource_binding(context, resource_type: str, resource_id: int) -> ResourceBinding:
    binding = _active_binding(resource_type, resource_id)
    hierarchy_visible = (
        binding.owner_department.tenant_id == context.department.tenant_id
        and binding.owner_department.path.startswith(context.department.path)
    )
    if is_platform_super_admin(context) or hierarchy_visible or _shared_can_use(context, binding):
        return binding
    raise StandardForbidden()


def monitorable_resource_binding(context, resource_type: str, resource_id: int) -> ResourceBinding:
    binding = _active_binding(resource_type, resource_id)
    visible_ids = visible_bindings_queryset(context, resource_type=resource_type).values_list("id", flat=True)
    if binding.id not in set(visible_ids):
        raise StandardForbidden()
    return binding


def route_snapshot(route: WaypointRoute) -> dict:
    return {
        "id": route.id,
        "name": route.name,
        "defaultAltitude": str(route.default_altitude) if route.default_altitude is not None else None,
        "defaultSpeed": str(route.default_speed) if route.default_speed is not None else None,
        "waypoints": [
            {
                "sequence": waypoint.sequence,
                "latitude": str(waypoint.latitude),
                "longitude": str(waypoint.longitude),
                "altitude": str(waypoint.altitude),
                "speed": str(waypoint.speed) if waypoint.speed is not None else None,
                "heading": str(waypoint.heading) if waypoint.heading is not None else None,
                "hoverSeconds": waypoint.hover_seconds,
            }
            for waypoint in route.waypoints.order_by("sequence")
        ],
    }


def _resource_occupancy_conflict(resource_type: str, resource_id: int, *, exclude_mission_id: int | None = None) -> bool:
    filters = {"status": FlightSessionStatus.RUNNING}
    if resource_type == ResourceType.DRONE:
        filters["drone_id"] = resource_id
    elif resource_type == ResourceType.DOCK:
        filters["dock_id"] = resource_id
    elif resource_type == ResourceType.PAYLOAD:
        filters["payload_id"] = resource_id
    else:
        return False
    queryset = FlightSession.objects.filter(**filters)
    if exclude_mission_id is not None:
        queryset = queryset.exclude(mission_id=exclude_mission_id)
    return queryset.exists()


def ensure_resources_available(*, drone_id: int, dock_id: int | None = None, payload_id: int | None = None, exclude_mission_id=None):
    conflicts = []
    checks = [
        (ResourceType.DRONE, drone_id),
        (ResourceType.DOCK, dock_id),
        (ResourceType.PAYLOAD, payload_id),
    ]
    for resource_type, resource_id in checks:
        if resource_id and _resource_occupancy_conflict(resource_type, resource_id, exclude_mission_id=exclude_mission_id):
            conflicts.append({"resourceType": resource_type, "resourceId": resource_id})
    if conflicts:
        raise StandardConstraintConflict(msg="资源正在执行其他任务", data={"conflicts": conflicts})


def create_assignments(mission: InspectionMission, bindings: list[ResourceBinding]):
    MissionResourceAssignment.objects.bulk_create(
        [
            MissionResourceAssignment(
                mission=mission,
                resource_type=binding.resource_type,
                resource_object_id=binding.resource_object_id,
                owner_department=binding.owner_department,
            )
            for binding in bindings
        ]
    )


def can_safety_abort(context, mission: InspectionMission) -> bool:
    if is_platform_super_admin(context):
        return True
    if not (is_department_admin(context) or is_dispatcher(context)):
        return False
    return mission.resource_assignments.filter(
        owner_department__tenant=context.department.tenant,
        owner_department__path__startswith=context.department.path,
    ).exists()


def can_transition_as_operator(context, mission: InspectionMission) -> bool:
    return is_dispatcher(context) or is_assigned_pilot(context, mission)


def require_transition_operator(context, mission: InspectionMission) -> None:
    if not can_transition_as_operator(context, mission):
        raise StandardForbidden()


def start_mission(*, mission: InspectionMission, context, request) -> FlightSession:
    require_transition_operator(context, mission)
    if mission.status != MissionStatus.PENDING:
        raise StandardConstraintConflict(msg="只有待执行任务可以开始")
    ensure_resources_available(
        drone_id=mission.drone_id,
        dock_id=mission.dock_id,
        payload_id=mission.payload_id,
        exclude_mission_id=mission.id,
    )
    now = timezone.now()
    mission.status = MissionStatus.RUNNING
    mission.started_at = now
    mission.save(update_fields=["status", "started_at", "updated_at"])
    session = FlightSession.objects.create(
        mission=mission,
        drone=mission.drone,
        dock=mission.dock,
        payload=mission.payload,
        status=FlightSessionStatus.RUNNING,
        started_at=now,
        started_by_user=context.user,
    )
    log_v2_action(
        request=request,
        context=context,
        action="start_inspection_mission",
        target_type="inspection_mission",
        target_id=mission.id,
        resource_owner_department=mission.primary_resource_owner_department,
        resource_type=ResourceType.DRONE,
        resource_object_id=mission.drone_id,
        after_data={"sessionId": session.id, "status": mission.status},
    )
    return session


def _flight_no(mission: InspectionMission) -> str:
    return f"V2FR-{timezone.now().strftime('%Y%m%d%H%M%S')}-{mission.id}"


def _duration_seconds(start, end) -> int:
    return max(0, int((end - start).total_seconds()))


def complete_mission(*, mission: InspectionMission, context, request) -> InspectionFlightRecord:
    require_transition_operator(context, mission)
    if mission.status != MissionStatus.RUNNING:
        raise StandardConstraintConflict(msg="只有执行中任务可以完成")
    session = getattr(mission, "flight_session", None)
    if session is None or session.status != FlightSessionStatus.RUNNING:
        raise StandardConstraintConflict(msg="任务缺少执行中的飞行会话")
    now = timezone.now()
    mission.status = MissionStatus.COMPLETED
    mission.finished_at = now
    mission.save(update_fields=["status", "finished_at", "updated_at"])
    session.status = FlightSessionStatus.COMPLETED
    session.ended_at = now
    session.ended_by_user = context.user
    session.save(update_fields=["status", "ended_at", "ended_by_user", "updated_at"])
    record, _created = InspectionFlightRecord.objects.get_or_create(
        mission=mission,
        defaults={
            "tenant": mission.tenant,
            "session": session,
            "flight_no": _flight_no(mission),
            "creator_department": mission.creator_department,
            "primary_resource_owner_department": mission.primary_resource_owner_department,
            "mission_name": mission.name,
            "route_name": mission.route.name,
            "drone_device_sn": mission.drone.device_sn,
            "drone_name": mission.drone.name,
            "pilot_name": mission.pilot.display_name,
            "start_time": session.started_at,
            "end_time": now,
            "flight_duration": _duration_seconds(session.started_at, now),
            "status": FlightRecordStatus.COMPLETED,
        },
    )
    try:
        sync_media_for_record(record=record)
    except DjiGatewayError:
        pass
    log_v2_action(
        request=request,
        context=context,
        action="complete_inspection_mission",
        target_type="inspection_mission",
        target_id=mission.id,
        resource_owner_department=mission.primary_resource_owner_department,
        resource_type=ResourceType.DRONE,
        resource_object_id=mission.drone_id,
        after_data={"flightRecordId": record.id, "status": mission.status},
    )
    return record


def close_mission(
    *,
    mission: InspectionMission,
    context,
    request,
    status_value: str,
    reason: str = "",
    allow_safety: bool = False,
) -> InspectionMission:
    if status_value == MissionStatus.CANCELED and not allow_safety and not is_dispatcher(context):
        raise StandardForbidden()
    if status_value == MissionStatus.FAILED and not allow_safety and not is_dispatcher(context):
        raise StandardForbidden()
    if mission.status not in {MissionStatus.PENDING, MissionStatus.RUNNING}:
        raise StandardConstraintConflict(msg="当前任务状态不允许关闭")
    now = timezone.now()
    session = getattr(mission, "flight_session", None)
    if session and session.status == FlightSessionStatus.RUNNING:
        session.status = FlightSessionStatus.CANCELED if status_value == MissionStatus.CANCELED else FlightSessionStatus.FAILED
        session.ended_at = now
        session.ended_by_user = context.user
        session.save(update_fields=["status", "ended_at", "ended_by_user", "updated_at"])
    mission.status = status_value
    if status_value == MissionStatus.CANCELED:
        mission.canceled_at = now
        mission.cancel_reason = reason
        update_fields = ["status", "canceled_at", "cancel_reason", "updated_at"]
    else:
        mission.finished_at = now
        mission.failure_reason = reason
        update_fields = ["status", "finished_at", "failure_reason", "updated_at"]
    mission.save(update_fields=update_fields)
    log_v2_action(
        request=request,
        context=context,
        action="close_inspection_mission",
        target_type="inspection_mission",
        target_id=mission.id,
        resource_owner_department=mission.primary_resource_owner_department,
        after_data={"status": mission.status, "reason": reason},
    )
    return mission


def safety_abort_mission(*, mission: InspectionMission, context, request, reason: str = "") -> InspectionMission:
    if not can_safety_abort(context, mission):
        raise StandardForbidden()
    return close_mission(
        mission=mission,
        context=context,
        request=request,
        status_value=MissionStatus.FAILED,
        reason=reason,
        allow_safety=True,
    )


def update_telemetry_snapshot(*, session: FlightSession, payload: dict) -> FlightTelemetrySnapshot:
    reported_at = payload.get("reportedAt") or payload.get("reported_at")
    if isinstance(reported_at, str):
        parsed = parse_datetime(reported_at)
        reported_at = parsed or timezone.now()
        if timezone.is_naive(reported_at):
            reported_at = timezone.make_aware(reported_at, timezone.get_current_timezone())
    if reported_at is None:
        reported_at = timezone.now()

    raw_payload = {key: (str(value) if isinstance(value, Decimal) else value) for key, value in payload.items()}
    defaults = {
        "latitude": payload.get("latitude"),
        "longitude": payload.get("longitude"),
        "altitude": payload.get("altitude"),
        "speed": payload.get("speed"),
        "heading": payload.get("heading"),
        "battery_percent": payload.get("batteryPercent", payload.get("battery_percent")),
        "reported_at": reported_at,
        "raw_payload": raw_payload,
    }
    snapshot, _created = FlightTelemetrySnapshot.objects.update_or_create(session=session, defaults=defaults)
    return snapshot


def _parse_media_type(payload: dict) -> str:
    value = str(payload.get("media_type") or payload.get("mediaType") or payload.get("type") or "").strip().lower()
    name = str(payload.get("file_name") or payload.get("fileName") or payload.get("name") or "").lower()
    if value in {"photo", "image", "jpg", "jpeg", "0"} or name.endswith((".jpg", ".jpeg", ".png")):
        return CloudMediaType.PHOTO
    if value in {"video", "mp4", "1"} or name.endswith((".mp4", ".mov")):
        return CloudMediaType.VIDEO
    return CloudMediaType.OTHER


def _parse_captured_at(payload: dict):
    value = payload.get("captured_at") or payload.get("capturedAt") or payload.get("create_time") or payload.get("createTime")
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return timezone.datetime.fromtimestamp(value / 1000 if value > 10_000_000_000 else value, tz=timezone.utc)
    if isinstance(value, str):
        parsed = parse_datetime(value)
        if parsed is None:
            return None
        if timezone.is_naive(parsed):
            return timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed
    return value


def _media_identifier(payload: dict) -> str:
    return str(
        payload.get("cloud_file_id")
        or payload.get("dji_file_id")
        or payload.get("file_id")
        or payload.get("fileId")
        or payload.get("id")
        or ""
    ).strip()


def _media_string(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _media_matches_record(record: InspectionFlightRecord, payload: dict) -> bool:
    device_sn = _media_string(payload, "device_sn", "deviceSn", "drone_sn", "droneSn")
    if device_sn and device_sn != record.drone_device_sn:
        return False
    captured_at = _parse_captured_at(payload)
    if captured_at is None:
        return device_sn == record.drone_device_sn
    start = record.start_time - timedelta(seconds=MEDIA_ASSOCIATION_GRACE_SECONDS)
    end = record.end_time + timedelta(seconds=MEDIA_ASSOCIATION_GRACE_SECONDS)
    return start <= captured_at <= end


def _record_connection(record: InspectionFlightRecord):
    binding = _active_binding(ResourceType.DRONE, record.mission.drone_id)
    return binding.dji_connection


def sync_media_for_record(*, record: InspectionFlightRecord) -> dict:
    connection = _record_connection(record)
    gateway = DjiConnectionGateway(connection)
    cloud_items = gateway.list_media_files()
    synced = 0
    for payload in cloud_items:
        if not isinstance(payload, dict) or not _media_matches_record(record, payload):
            continue
        cloud_file_id = _media_identifier(payload)
        if not cloud_file_id:
            continue
        media_type = _parse_media_type(payload)
        captured_at = _parse_captured_at(payload)
        CloudMediaFile.objects.update_or_create(
            tenant=record.tenant,
            cloud_file_id=cloud_file_id,
            defaults={
                "flight_record": record,
                "mission": record.mission,
                "device_sn": record.drone_device_sn,
                "media_type": media_type,
                "file_name": _media_string(payload, "file_name", "fileName", "name"),
                "thumbnail_url": _media_string(payload, "thumbnail_url", "thumbnailUrl", "thumb_url", "thumbUrl"),
                "preview_url": _media_string(payload, "preview_url", "previewUrl"),
                "download_url": _media_string(payload, "download_url", "downloadUrl", "file_url", "fileUrl"),
                "playback_url": _media_string(payload, "playback_url", "playbackUrl"),
                "file_size": payload.get("file_size") or payload.get("fileSize"),
                "captured_at": captured_at,
                "metadata": payload,
            },
        )
        synced += 1
    counts = record.media_files.values_list("media_type", flat=True)
    photo_count = sum(1 for item in counts if item == CloudMediaType.PHOTO)
    video_count = sum(1 for item in record.media_files.values_list("media_type", flat=True) if item == CloudMediaType.VIDEO)
    record.photo_count = photo_count
    record.video_count = video_count
    record.save(update_fields=["photo_count", "video_count", "updated_at"])
    return {"synced": synced, "photoCount": photo_count, "videoCount": video_count}


def visible_resource_for_live(context, drone_id: int) -> ResourceBinding:
    binding = monitorable_resource_binding(context, ResourceType.DRONE, drone_id)
    from apps.resource_v2.services import effective_permissions_for_binding

    effective = set(effective_permissions_for_binding(context, binding))
    if "monitor" not in effective and not is_platform_super_admin(context):
        raise StandardForbidden()
    return binding
