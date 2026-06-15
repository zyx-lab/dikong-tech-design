from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.access.exceptions import StandardConstraintConflict, StandardForbidden, StandardNotFound
from apps.access.models import DirectoryStatus
from apps.dji_cloud.gateway import DjiGatewayError
from apps.iam_v2.models import FixedRole
from apps.iam_v2.services import is_platform_super_admin
from apps.resource_v2.audit import log_v2_action
from apps.resource_v2.gateway import DjiConnectionGateway
from apps.resource_v2.models import (
    BindingStatus,
    DjiConnection,
    DockResource,
    DroneResource,
    GatewayResource,
    ResourceBinding,
    ResourceSharePermission,
    ResourceType,
)
from apps.resource_v2.mqtt import upsert_drone_telemetry_from_osd
from apps.resource_v2.services import get_resource, visible_bindings_queryset
from apps.inspection_v2.models import (
    CloudMediaFile,
    CloudMediaType,
    CloudExecutionStatus,
    FlightRecordStatus,
    FlightSession,
    FlightSessionStatus,
    FlightTelemetrySnapshot,
    InspectionFlightRecord,
    InspectionMission,
    LiveStreamStatus,
    MissionCloudExecution,
    MissionExecutionMode,
    MissionResourceAssignment,
    MissionStatus,
    WaypointRoute,
    WaypointRouteCloudFile,
    route_cover_image_url,
)


LIVE_REPLAY_TIMESTAMP_PATTERN = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})_"
    r"(?P<hour>\d{2})-(?P<minute>\d{2})-(?P<second>\d{2})"
    r"(?:-(?P<microsecond>\d{1,6}))?",
    re.IGNORECASE,
)
MEDIA_PREVIEW_URL_REFRESH_MARGIN = timedelta(minutes=5)
_AMZ_DATE_FORMAT = "%Y%m%dT%H%M%SZ"


def is_dispatcher(context) -> bool:
    return FixedRole.TASK_MONITOR_DISPATCHER in context.role_codes or is_platform_super_admin(context)


def is_department_admin(context) -> bool:
    return FixedRole.DEPARTMENT_ADMIN in context.role_codes or is_platform_super_admin(context)


def is_assigned_pilot(context, mission: InspectionMission) -> bool:
    return FixedRole.PILOT in context.role_codes and mission.pilot_account_profile.user_id == context.user.id


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
    return Q(**{f"{field}__path__startswith": context.department.path})


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
            "pilot_account_profile",
            "pilot_account_profile__user",
            "pilot_account_profile__department",
        )
        .prefetch_related("resource_assignments")
        .order_by("-id")
    )
    if is_platform_super_admin(context):
        return queryset
    if is_plain_pilot(context):
        return queryset.filter(pilot_account_profile__user=context.user).distinct()
    return queryset.filter(
        _department_tree_q("creator_department", context)
        | _department_tree_q("primary_resource_owner_department", context)
        | _department_tree_q("resource_assignments__owner_department", context)
        | _shared_resource_q(context)
    ).distinct()


def visible_routes_queryset(context) -> QuerySet:
    require_inspection_viewer(context)
    queryset = WaypointRoute.objects.prefetch_related("waypoints").order_by("-id")
    if is_platform_super_admin(context):
        return queryset
    visible_mission_route_ids = visible_missions_queryset(context).values("route_id")
    if is_plain_pilot(context):
        return queryset.filter(pk__in=visible_mission_route_ids).distinct()
    return queryset.filter(_department_tree_q("owner_department", context) | Q(pk__in=visible_mission_route_ids)).distinct()


def editable_routes_queryset(context) -> QuerySet:
    queryset = WaypointRoute.objects.prefetch_related("waypoints").filter(
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
    queryset = CloudMediaFile.objects.select_related("flight_record", "mission")
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
            "mission__pilot_account_profile",
            "mission__pilot_account_profile__user",
            "mission__pilot_account_profile__department",
            "mission__cloud_execution",
            "drone",
            "dock",
            "payload",
        )
        .filter(status=FlightSessionStatus.RUNNING)
        .order_by("-started_at", "-id")
    )
    if is_platform_super_admin(context):
        return queryset
    queryset = queryset.filter(mission_id__in=visible_missions_queryset(context).values("id"))
    if is_plain_pilot(context):
        queryset = queryset.filter(mission__pilot_account_profile__user=context.user)
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


def _route_cloud_file_for_mission(mission: InspectionMission) -> WaypointRouteCloudFile:
    try:
        return mission.route.cloud_file
    except WaypointRouteCloudFile.DoesNotExist as exc:
        raise StandardConstraintConflict(msg="任务航线尚未同步到当前 DJI 连接，请重新上传/更新航线") from exc


def _execution_binding_for_mission(mission: InspectionMission, execution_mode: str) -> ResourceBinding:
    if execution_mode == MissionExecutionMode.DOCK_AUTO:
        if mission.dock_id is None:
            raise StandardConstraintConflict(msg="机场自动执行任务缺少 dockId")
        return _active_binding(ResourceType.DOCK, mission.dock_id)
    if execution_mode == MissionExecutionMode.PILOT2_MANUAL:
        if mission.executor_id is None:
            raise StandardConstraintConflict(msg="Pilot2 手动执行任务缺少 executorId")
        return _active_binding(ResourceType.GATEWAY, mission.executor_id)
    raise StandardConstraintConflict(msg="任务执行模式无效")


def _ensure_same_dji_connection_for_mission(
    *,
    route_cloud_file: WaypointRouteCloudFile,
    drone_binding: ResourceBinding,
    execution_binding: ResourceBinding,
    execution_label: str,
) -> None:
    if route_cloud_file.dji_connection_id != drone_binding.dji_connection_id:
        raise StandardConstraintConflict(msg="任务航线尚未同步到当前 DJI 连接，请重新上传/更新航线")
    if execution_binding.dji_connection_id != drone_binding.dji_connection_id:
        raise StandardConstraintConflict(msg=f"{execution_label}必须与无人机属于同一个 DJI 连接")


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
    hierarchy_visible = binding.owner_department.path.startswith(context.department.path)
    if is_platform_super_admin(context) or hierarchy_visible or _shared_can_use(context, binding):
        return binding
    raise StandardForbidden()


def monitorable_resource_binding(context, resource_type: str, resource_id: int) -> ResourceBinding:
    binding = _active_binding(resource_type, resource_id)
    visible_ids = visible_bindings_queryset(context, resource_type=resource_type).values_list("id", flat=True)
    if binding.id not in set(visible_ids):
        raise StandardForbidden()
    return binding


def route_cloud_file_snapshot(cloud_file: WaypointRouteCloudFile | None) -> dict | None:
    if cloud_file is None:
        return None
    return {
        "routeId": cloud_file.route_id,
        "djiConnectionId": cloud_file.dji_connection_id,
        "workspaceId": cloud_file.workspace_id,
        "djiFileId": cloud_file.dji_file_id,
        "waylineType": cloud_file.wayline_type,
        "downloadUrl": cloud_file.download_url,
        "downloadUrlExpiresAt": cloud_file.download_url_expires_at.isoformat() if cloud_file.download_url_expires_at else None,
        "uploadedAt": cloud_file.uploaded_at.isoformat() if cloud_file.uploaded_at else None,
    }


def route_snapshot(route: WaypointRoute) -> dict:
    try:
        cloud_file = route.cloud_file
    except WaypointRouteCloudFile.DoesNotExist:
        cloud_file = None
    return {
        "id": route.id,
        "name": route.name,
        "defaultAltitude": str(route.default_altitude) if route.default_altitude is not None else None,
        "defaultSpeed": str(route.default_speed) if route.default_speed is not None else None,
        "coverImageUrl": route_cover_image_url(route),
        "djiFile": route_cloud_file_snapshot(cloud_file),
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


def mission_execution_mode(mission: InspectionMission) -> str:
    if mission.dock_id and not mission.executor_id:
        return MissionExecutionMode.DOCK_AUTO
    if mission.executor_id and not mission.dock_id:
        return MissionExecutionMode.PILOT2_MANUAL
    raise StandardConstraintConflict(msg="任务必须且只能绑定 dockId 或 executorId")


def _first_list(payload: dict, *keys: str) -> list:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _select_live_video_id(capacity: dict, *, device_sn: str) -> str:
    if not isinstance(capacity, dict):
        return ""
    cameras = _first_list(capacity, "cameras_list", "camerasList", "cameras")
    for camera in cameras:
        if not isinstance(camera, dict):
            continue
        camera_index = str(camera.get("index") or camera.get("camera_index") or camera.get("cameraIndex") or "").strip()
        if not camera_index:
            continue
        videos = _first_list(camera, "videos_list", "videosList", "videos")
        for video in videos:
            if not isinstance(video, dict):
                continue
            video_index = str(video.get("index") or video.get("video_index") or video.get("videoIndex") or "").strip()
            if video_index:
                return f"{device_sn}/{camera_index}/{video_index}"
    return ""


def _live_urls(payload) -> dict:
    return payload if isinstance(payload, dict) else {"raw": payload}


def _stop_live_for_execution(execution: MissionCloudExecution | None) -> bool:
    if execution is None or not execution.live_video_id:
        return False
    if execution.live_status == LiveStreamStatus.STOPPED:
        return True

    now = timezone.now()
    execution.live_status = LiveStreamStatus.STOPPING
    execution.save(update_fields=["live_status", "updated_at"])
    try:
        DjiConnectionGateway(execution.dji_connection).stop_live(execution.drone_sn, video_id=execution.live_video_id)
    except DjiGatewayError as exc:
        execution.live_status = LiveStreamStatus.FAILED
        execution.live_error_message = str(exc)
        execution.save(update_fields=["live_status", "live_error_message", "updated_at"])
        return False

    execution.live_status = LiveStreamStatus.STOPPED
    execution.live_stopped_at = now
    execution.live_error_message = ""
    execution.save(update_fields=["live_status", "live_stopped_at", "live_error_message", "updated_at"])
    return True


def _resource_occupancy_conflict(resource_type: str, resource_id: int, *, exclude_mission_id: int | None = None) -> bool:
    filters = {"status": FlightSessionStatus.RUNNING}
    if resource_type == ResourceType.DRONE:
        filters["drone_id"] = resource_id
    elif resource_type == ResourceType.DOCK:
        filters["dock_id"] = resource_id
    elif resource_type == ResourceType.GATEWAY:
        filters["executor_id"] = resource_id
    elif resource_type == ResourceType.PAYLOAD:
        filters["payload_id"] = resource_id
    else:
        return False
    queryset = FlightSession.objects.filter(**filters)
    if exclude_mission_id is not None:
        queryset = queryset.exclude(mission_id=exclude_mission_id)
    return queryset.exists()


def ensure_resources_available(
    *,
    drone_id: int,
    dock_id: int | None = None,
    executor_id: int | None = None,
    payload_id: int | None = None,
    exclude_mission_id=None,
):
    conflicts = []
    checks = [
        (ResourceType.DRONE, drone_id),
        (ResourceType.DOCK, dock_id),
        (ResourceType.GATEWAY, executor_id),
        (ResourceType.PAYLOAD, payload_id),
    ]
    for resource_type, resource_id in checks:
        if resource_id and _resource_occupancy_conflict(resource_type, resource_id, exclude_mission_id=exclude_mission_id):
            conflicts.append({"resourceType": resource_type, "resourceId": resource_id})
    if conflicts:
        raise StandardConstraintConflict(msg="资源正在执行其他任务", data={"conflicts": conflicts})


def _resource_occupancy_conflicts_for_mission(mission: InspectionMission) -> list[dict]:
    conflicts = []
    checks = [
        (ResourceType.DRONE, mission.drone_id),
        (ResourceType.DOCK, mission.dock_id),
        (ResourceType.GATEWAY, mission.executor_id),
        (ResourceType.PAYLOAD, mission.payload_id),
    ]
    for resource_type, resource_id in checks:
        if resource_id and _resource_occupancy_conflict(resource_type, resource_id, exclude_mission_id=mission.id):
            conflicts.append({"resourceType": resource_type, "resourceId": resource_id})
    return conflicts


def _preflight_item(code: str, label: str, status: str, message: str, detail: dict | None = None) -> dict:
    item = {
        "code": code,
        "label": label,
        "status": status,
        "message": message,
    }
    if detail is not None:
        item["detail"] = detail
    return item


def _preflight_fail(code: str, label: str, message: str, detail: dict | None = None) -> dict:
    return _preflight_item(code, label, "FAIL", message, detail)


def _preflight_pass(code: str, label: str, message: str, detail: dict | None = None) -> dict:
    return _preflight_item(code, label, "PASS", message, detail)


def _preflight_warning(code: str, label: str, message: str, detail: dict | None = None) -> dict:
    return _preflight_item(code, label, "WARNING", message, detail)


def _preflight_skipped(code: str, label: str, message: str, detail: dict | None = None) -> dict:
    return _preflight_item(code, label, "SKIPPED", message, detail)


def _preflight_active_binding(resource_type: str, resource_id: int | None) -> ResourceBinding | None:
    if not resource_id:
        return None
    return (
        ResourceBinding.objects.select_related("owner_department", "dji_connection")
        .filter(resource_type=resource_type, resource_object_id=resource_id, status=BindingStatus.ACTIVE)
        .first()
    )


def _preflight_blocking_reasons(checks: list[dict]) -> list[dict]:
    return [
        {"code": check["code"], "message": check["message"], "detail": check.get("detail", {})}
        for check in checks
        if check.get("status") == "FAIL"
    ]


def _preflight_warnings(checks: list[dict]) -> list[dict]:
    return [
        {"code": check["code"], "message": check["message"], "detail": check.get("detail", {})}
        for check in checks
        if check.get("status") == "WARNING"
    ]


def build_mission_preflight_check(*, mission: InspectionMission, context) -> dict:
    require_transition_operator(context, mission)
    checks = []
    route_cloud_file = None
    drone_binding = None
    executor_binding = None
    dock_binding = None
    execution_binding = None
    execution_mode = ""
    execution_label = "执行资源"
    selected_live_video_id = ""
    dji_connection_id = None
    workspace_id = ""
    route_dji_file_id = ""

    if mission.status == MissionStatus.PENDING:
        checks.append(_preflight_pass("MISSION_PENDING", "任务状态", "任务处于待执行状态"))
    else:
        checks.append(
            _preflight_fail(
                "MISSION_PENDING",
                "任务状态",
                "只有待执行任务可以启动",
                {"currentStatus": mission.status},
            )
        )

    if mission.dock_id and mission.executor_id:
        checks.append(_preflight_fail("EXECUTION_RESOURCE_EXCLUSIVE", "执行资源", "dockId 与 executorId 必须二选一"))
    elif mission.dock_id:
        execution_mode = MissionExecutionMode.DOCK_AUTO
        execution_label = "机场"
        checks.append(_preflight_pass("DOCK_PRESENT", "机场", "任务已绑定机场 dockId"))
    elif mission.executor_id:
        execution_mode = MissionExecutionMode.PILOT2_MANUAL
        execution_label = "执行端/遥控器"
        checks.append(_preflight_pass("EXECUTOR_PRESENT", "执行端/遥控器", "任务已绑定 Pilot2 执行端/遥控器 executorId"))
    else:
        checks.append(_preflight_fail("EXECUTION_RESOURCE_PRESENT", "执行资源", "任务必须绑定 dockId 或 executorId"))

    try:
        route_cloud_file = mission.route.cloud_file
    except WaypointRouteCloudFile.DoesNotExist:
        checks.append(_preflight_fail("ROUTE_DJI_FILE_PRESENT", "DJI 航线文件", "任务航线尚未同步到当前 DJI 连接，请重新上传/更新航线"))
    else:
        route_dji_file_id = route_cloud_file.dji_file_id
        workspace_id = route_cloud_file.workspace_id
        dji_connection_id = route_cloud_file.dji_connection_id
        checks.append(
            _preflight_pass(
                "ROUTE_DJI_FILE_PRESENT",
                "DJI 航线文件",
                "任务航线已上传 DJI",
                {"djiFileId": route_cloud_file.dji_file_id, "workspaceId": route_cloud_file.workspace_id},
            )
        )

    drone_binding = _preflight_active_binding(ResourceType.DRONE, mission.drone_id)
    if drone_binding is None:
        checks.append(_preflight_fail("DRONE_BINDING_ACTIVE", "无人机绑定", "无人机没有 active 资源绑定"))
    else:
        dji_connection_id = dji_connection_id or drone_binding.dji_connection_id
        workspace_id = workspace_id or drone_binding.dji_connection.workspace_id
        checks.append(
            _preflight_pass(
                "DRONE_BINDING_ACTIVE",
                "无人机绑定",
                "无人机资源绑定有效",
                {"djiConnectionId": drone_binding.dji_connection_id},
            )
        )

    if mission.dock_id:
        dock_binding = _preflight_active_binding(ResourceType.DOCK, mission.dock_id)
        if dock_binding is None:
            checks.append(_preflight_fail("DOCK_BINDING_ACTIVE", "机场绑定", "机场没有 active 资源绑定"))
        else:
            execution_binding = dock_binding
            checks.append(
                _preflight_pass(
                    "DOCK_BINDING_ACTIVE",
                    "机场绑定",
                    "机场资源绑定有效",
                    {"djiConnectionId": dock_binding.dji_connection_id},
                )
            )

    if mission.executor_id:
        executor_binding = _preflight_active_binding(ResourceType.GATEWAY, mission.executor_id)
        if executor_binding is None:
            checks.append(_preflight_fail("EXECUTOR_BINDING_ACTIVE", "执行端绑定", "执行端/遥控器没有 active 资源绑定"))
        else:
            execution_binding = executor_binding
            checks.append(
                _preflight_pass(
                    "EXECUTOR_BINDING_ACTIVE",
                    "执行端绑定",
                    "执行端/遥控器资源绑定有效",
                    {"djiConnectionId": executor_binding.dji_connection_id},
                )
            )
    else:
        checks.append(_preflight_skipped("EXECUTOR_BINDING_ACTIVE", "执行端绑定", "非 Pilot2 手动模式，跳过执行端绑定检查"))

    if not mission.dock_id:
        checks.append(_preflight_skipped("DOCK_BINDING_ACTIVE", "机场绑定", "非机场自动模式，跳过机场绑定检查"))

    if route_cloud_file is not None and drone_binding is not None and execution_binding is not None:
        connection_ids = {route_cloud_file.dji_connection_id, drone_binding.dji_connection_id, execution_binding.dji_connection_id}
        if len(connection_ids) == 1:
            checks.append(_preflight_pass("SAME_DJI_CONNECTION", "DJI 连接一致性", f"航线、无人机和{execution_label}属于同一个 DJI 连接"))
        else:
            detail = {
                "routeDjiConnectionId": route_cloud_file.dji_connection_id,
                "droneDjiConnectionId": drone_binding.dji_connection_id,
            }
            if execution_mode == MissionExecutionMode.DOCK_AUTO:
                detail["dockDjiConnectionId"] = execution_binding.dji_connection_id
            else:
                detail["executorDjiConnectionId"] = execution_binding.dji_connection_id
            checks.append(
                _preflight_fail(
                    "SAME_DJI_CONNECTION",
                    "DJI 连接一致性",
                    f"航线、无人机和{execution_label}必须属于同一个 DJI 连接",
                    detail,
                )
            )
    else:
        checks.append(_preflight_skipped("SAME_DJI_CONNECTION", "DJI 连接一致性", "缺少航线、无人机或执行资源绑定，跳过连接一致性检查"))

    if mission.drone.online_status:
        checks.append(_preflight_pass("DRONE_ONLINE", "无人机在线状态", "无人机在线"))
    else:
        checks.append(_preflight_fail("DRONE_ONLINE", "无人机在线状态", "无人机不在线"))

    if mission.dock_id and mission.dock is not None:
        if mission.dock.online_status:
            checks.append(_preflight_pass("DOCK_ONLINE", "机场在线状态", "机场在线"))
        else:
            checks.append(_preflight_fail("DOCK_ONLINE", "机场在线状态", "机场不在线"))
    else:
        checks.append(_preflight_skipped("DOCK_ONLINE", "机场在线状态", "非机场自动模式，跳过机场在线检查"))

    if mission.executor_id and mission.executor is not None:
        if mission.executor.online_status:
            checks.append(_preflight_pass("EXECUTOR_ONLINE", "执行端在线状态", "执行端/遥控器在线"))
        else:
            checks.append(_preflight_fail("EXECUTOR_ONLINE", "执行端在线状态", "执行端/遥控器不在线"))
    else:
        checks.append(_preflight_skipped("EXECUTOR_ONLINE", "执行端在线状态", "非 Pilot2 手动模式，跳过执行端在线检查"))

    conflicts = _resource_occupancy_conflicts_for_mission(mission)
    if conflicts:
        checks.append(_preflight_fail("RESOURCES_AVAILABLE", "资源占用", "资源正在执行其他任务", {"conflicts": conflicts}))
    else:
        checks.append(_preflight_pass("RESOURCES_AVAILABLE", "资源占用", "任务资源当前未被其他运行中任务占用"))

    has_local_failures = any(check.get("status") == "FAIL" for check in checks)
    if has_local_failures or drone_binding is None:
        checks.append(_preflight_skipped("LIVE_CAPACITY_AVAILABLE", "DJI 直播能力", "本地前置条件未通过，跳过 DJI live capacity 检查"))
    else:
        try:
            capacity = DjiConnectionGateway(drone_binding.dji_connection).get_live_capacity(mission.drone.device_sn)
            selected_live_video_id = _select_live_video_id(capacity, device_sn=mission.drone.device_sn)
        except DjiGatewayError as exc:
            detail = {
                "upstreamStatus": getattr(exc, "status_code", 502),
                "upstream": getattr(exc, "data", None),
            }
            if execution_mode == MissionExecutionMode.PILOT2_MANUAL:
                checks.append(_preflight_warning("LIVE_CAPACITY_AVAILABLE", "DJI 直播能力", "DJI live capacity 查询失败，Pilot2 手动执行可继续", detail))
            else:
                checks.append(_preflight_fail("LIVE_CAPACITY_AVAILABLE", "DJI 直播能力", "DJI live capacity 查询失败", detail))
        else:
            if selected_live_video_id:
                checks.append(
                    _preflight_pass(
                        "LIVE_CAPACITY_AVAILABLE",
                        "DJI 直播能力",
                        "设备存在可用于任务启动的直播视频源",
                        {"selectedLiveVideoId": selected_live_video_id},
                    )
                )
            elif execution_mode == MissionExecutionMode.PILOT2_MANUAL:
                checks.append(_preflight_warning("LIVE_CAPACITY_AVAILABLE", "DJI 直播能力", "设备缺少可直播视频能力，Pilot2 手动执行可继续"))
            else:
                checks.append(_preflight_fail("LIVE_CAPACITY_AVAILABLE", "DJI 直播能力", "设备缺少可直播视频能力"))

    blocking_reasons = _preflight_blocking_reasons(checks)
    return {
        "missionId": mission.id,
        "canStart": not blocking_reasons,
        "status": "BLOCKED" if blocking_reasons else "READY",
        "blockingReasons": blocking_reasons,
        "warnings": _preflight_warnings(checks),
        "checks": checks,
        "execution": {
            "executionMode": execution_mode,
            "routeId": mission.route_id,
            "droneId": mission.drone_id,
            "droneSn": mission.drone.device_sn,
            "executorId": mission.executor_id,
            "executorSn": mission.executor.device_sn if mission.executor_id and mission.executor is not None else "",
            "dockId": mission.dock_id,
            "payloadId": mission.payload_id,
            "djiConnectionId": dji_connection_id,
            "workspaceId": workspace_id,
            "routeDjiFileId": route_dji_file_id,
            "selectedLiveVideoId": selected_live_video_id,
        },
    }


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
        owner_department__path__startswith=context.department.path,
    ).exists()


def can_transition_as_operator(context, mission: InspectionMission) -> bool:
    return is_dispatcher(context) or is_assigned_pilot(context, mission)


def require_transition_operator(context, mission: InspectionMission) -> None:
    if not can_transition_as_operator(context, mission):
        raise StandardForbidden()


def _live_request(video_id: str) -> dict:
    return {
        "video_id": video_id,
        "url_type": 1,
        "video_quality": 1,
    }


def _start_live_required(*, gateway: DjiConnectionGateway, mission: InspectionMission) -> tuple[str, dict, dict]:
    capacity = gateway.get_live_capacity(mission.drone.device_sn)
    live_video_id = _select_live_video_id(capacity, device_sn=mission.drone.device_sn)
    if not live_video_id:
        raise StandardConstraintConflict(msg="设备缺少可直播视频能力")
    live_request = _live_request(live_video_id)
    live_response_payload = gateway.start_live(mission.drone.device_sn, **live_request)
    return live_video_id, live_request, live_response_payload


def _start_live_best_effort(*, gateway: DjiConnectionGateway, mission: InspectionMission) -> dict:
    try:
        capacity = gateway.get_live_capacity(mission.drone.device_sn)
        live_video_id = _select_live_video_id(capacity, device_sn=mission.drone.device_sn)
        if not live_video_id:
            return {
                "status": LiveStreamStatus.FAILED,
                "video_id": "",
                "request": {},
                "response": {},
                "error": "设备缺少可直播视频能力",
            }
        live_request = _live_request(live_video_id)
        live_response_payload = gateway.start_live(mission.drone.device_sn, **live_request)
    except DjiGatewayError as exc:
        return {
            "status": LiveStreamStatus.FAILED,
            "video_id": "",
            "request": {},
            "response": {},
            "error": str(exc),
        }
    return {
        "status": LiveStreamStatus.RUNNING,
        "video_id": live_video_id,
        "request": live_request,
        "response": live_response_payload,
        "error": "",
    }


def start_mission(*, mission: InspectionMission, context, request) -> FlightSession:
    require_transition_operator(context, mission)
    if mission.status != MissionStatus.PENDING:
        raise StandardConstraintConflict(msg="只有待执行任务可以开始")
    execution_mode = mission_execution_mode(mission)
    route_cloud_file = _route_cloud_file_for_mission(mission)
    drone_binding = _active_binding(ResourceType.DRONE, mission.drone_id)
    execution_binding = _execution_binding_for_mission(mission, execution_mode)
    execution_label = "机场" if execution_mode == MissionExecutionMode.DOCK_AUTO else "执行端"
    _ensure_same_dji_connection_for_mission(
        route_cloud_file=route_cloud_file,
        drone_binding=drone_binding,
        execution_binding=execution_binding,
        execution_label=execution_label,
    )
    if not mission.drone.online_status:
        raise StandardConstraintConflict(msg="无人机不在线")
    if execution_mode == MissionExecutionMode.DOCK_AUTO:
        if mission.dock is None or not mission.dock.online_status:
            raise StandardConstraintConflict(msg="机场不在线")
    elif mission.executor is None or not mission.executor.online_status:
        raise StandardConstraintConflict(msg="执行端不在线")
    ensure_resources_available(
        drone_id=mission.drone_id,
        dock_id=mission.dock_id,
        executor_id=mission.executor_id,
        payload_id=mission.payload_id,
        exclude_mission_id=mission.id,
    )
    now = timezone.now()
    gateway = DjiConnectionGateway(drone_binding.dji_connection)

    dji_job_id = ""
    response_payload = {}
    if execution_mode == MissionExecutionMode.DOCK_AUTO:
        request_payload = {
            "mission_name": mission.name,
            "file_id": route_cloud_file.dji_file_id,
            "dock_sn": mission.dock.device_sn,
            "wayline_type": int(route_cloud_file.wayline_type),
            "task_type": 0,
            "rth_altitude": DjiConnectionGateway.DEFAULT_RTH_ALTITUDE,
            "out_of_control_action": DjiConnectionGateway.DEFAULT_OUT_OF_CONTROL_ACTION,
        }
        live_video_id, live_request, live_response_payload = _start_live_required(gateway=gateway, mission=mission)
        try:
            response_payload = gateway.create_dock_flight_task(
                mission_name=mission.name,
                file_id=route_cloud_file.dji_file_id,
                dock_sn=mission.dock.device_sn,
                wayline_type=route_cloud_file.wayline_type,
                task_type=request_payload["task_type"],
                rth_altitude=request_payload["rth_altitude"],
                out_of_control_action=request_payload["out_of_control_action"],
            )
        except Exception:
            try:
                gateway.stop_live(mission.drone.device_sn, video_id=live_video_id)
            except DjiGatewayError:
                pass
            raise
        dji_job_id = str(response_payload.get("dji_job_id") or response_payload.get("job_id") or "").strip()
        if not dji_job_id:
            try:
                gateway.stop_live(mission.drone.device_sn, video_id=live_video_id)
            except DjiGatewayError:
                pass
            raise StandardConstraintConflict(msg="DJI 创建任务后未返回 job_id")
        live_status = LiveStreamStatus.RUNNING
        live_error_message = ""
    else:
        live_result = _start_live_best_effort(gateway=gateway, mission=mission)
        live_video_id = live_result["video_id"]
        live_request = live_result["request"]
        live_response_payload = live_result["response"]
        live_status = live_result["status"]
        live_error_message = live_result["error"]
        request_payload = {
            "mission_name": mission.name,
            "file_id": route_cloud_file.dji_file_id,
            "executor_sn": mission.executor.device_sn,
            "execution_mode": MissionExecutionMode.PILOT2_MANUAL,
            "wayline_type": int(route_cloud_file.wayline_type),
        }

    mission.status = MissionStatus.RUNNING
    mission.started_at = now
    mission.save(update_fields=["status", "started_at", "updated_at"])
    session = FlightSession.objects.create(
        mission=mission,
        drone=mission.drone,
        dock=mission.dock,
        executor=mission.executor,
        payload=mission.payload,
        status=FlightSessionStatus.RUNNING,
        started_at=now,
        started_by_user=context.user,
    )
    executor_sn = mission.dock.device_sn if execution_mode == MissionExecutionMode.DOCK_AUTO else mission.executor.device_sn
    MissionCloudExecution.objects.update_or_create(
        mission=mission,
        defaults={
            "session": session,
            "dji_connection": drone_binding.dji_connection,
            "route_cloud_file": route_cloud_file,
            "workspace_id": route_cloud_file.workspace_id,
            "execution_mode": execution_mode,
            "dji_job_id": dji_job_id,
            "executor_sn": executor_sn,
            "drone_sn": mission.drone.device_sn,
            "status": CloudExecutionStatus.RUNNING,
            "started_at": now,
            "live_status": live_status,
            "live_video_id": live_video_id,
            "live_url_type": live_request.get("url_type", 1),
            "live_video_quality": live_request.get("video_quality", 1),
            "live_urls": _live_urls(live_response_payload),
            "live_started_at": now if live_status == LiveStreamStatus.RUNNING else None,
            "live_stopped_at": None,
            "live_error_message": live_error_message,
            "raw_request": {**request_payload, "live": live_request},
            "raw_response": response_payload,
            "error_code": "",
            "error_message": "",
        },
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
        after_data={"sessionId": session.id, "status": mission.status, "executionMode": execution_mode, "djiJobId": dji_job_id},
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
            "session": session,
            "flight_no": _flight_no(mission),
            "creator_department": mission.creator_department,
            "primary_resource_owner_department": mission.primary_resource_owner_department,
            "mission_name": mission.name,
            "route_name": mission.route.name,
            "drone_device_sn": mission.drone.device_sn,
            "drone_name": mission.drone.name,
            "pilot_name": mission.pilot_account_profile.name,
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
    execution = getattr(mission, "cloud_execution", None)
    if execution is not None:
        execution.status = CloudExecutionStatus.COMPLETED
        execution.ended_at = execution.ended_at or now
        execution.progress_percent = max(execution.progress_percent, 100)
        execution.save(update_fields=["status", "ended_at", "progress_percent", "updated_at"])
    _stop_live_for_execution(execution)
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
    execution = getattr(mission, "cloud_execution", None)
    if (
        status_value == MissionStatus.CANCELED
        and execution is not None
        and execution.execution_mode == MissionExecutionMode.DOCK_AUTO
        and execution.dji_job_id
    ):
        DjiConnectionGateway(execution.dji_connection).cancel_mission(execution.dji_job_id)
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
        if execution is not None:
            execution.status = CloudExecutionStatus.CANCELED
            execution.ended_at = now
            execution.error_message = reason
            execution.save(update_fields=["status", "ended_at", "error_message", "updated_at"])
    else:
        mission.finished_at = now
        mission.failure_reason = reason
        update_fields = ["status", "finished_at", "failure_reason", "updated_at"]
        if execution is not None:
            execution.status = CloudExecutionStatus.FAILED
            execution.ended_at = now
            execution.error_message = reason
            execution.save(update_fields=["status", "ended_at", "error_message", "updated_at"])
    mission.save(update_fields=update_fields)
    _stop_live_for_execution(execution)
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

    raw_payload = {
        key: (
            str(value)
            if isinstance(value, Decimal)
            else value.isoformat()
            if hasattr(value, "isoformat")
            else value
        )
        for key, value in payload.items()
    }
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


def _coerce_media_datetime(value):
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


def _parse_live_replay_started_at(payload: dict):
    sources = [
        payload.get("file_name"),
        payload.get("fileName"),
        payload.get("name"),
        payload.get("object_key"),
        payload.get("objectKey"),
        payload.get("file_path"),
        payload.get("filePath"),
    ]
    haystack = " ".join(str(value or "") for value in sources)
    if "live/replay" not in haystack.lower():
        return None
    match = LIVE_REPLAY_TIMESTAMP_PATTERN.search(haystack)
    if match is None:
        return None
    microsecond = (match.group("microsecond") or "0").ljust(6, "0")[:6]
    parsed = datetime(
        int(match.group("date")[0:4]),
        int(match.group("date")[5:7]),
        int(match.group("date")[8:10]),
        int(match.group("hour")),
        int(match.group("minute")),
        int(match.group("second")),
        int(microsecond),
        tzinfo=dt_timezone.utc,
    )
    return parsed


def _parse_captured_at(payload: dict):
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    explicit_value = payload.get("captured_at") or payload.get("capturedAt")
    explicit_parsed = _coerce_media_datetime(explicit_value)
    if explicit_parsed is not None:
        return explicit_parsed
    live_replay_started_at = _parse_live_replay_started_at(payload)
    if live_replay_started_at is not None:
        return live_replay_started_at
    value = (
        payload.get("create_time")
        or payload.get("createTime")
        or payload.get("created_time")
        or payload.get("createdTime")
        or metadata.get("created_time")
        or metadata.get("createdTime")
    )
    return _coerce_media_datetime(value)


def _media_identifier(payload: dict) -> str:
    ext = payload.get("ext") if isinstance(payload.get("ext"), dict) else {}
    return str(
        ext.get("cloud_file_id")
        or ext.get("dji_file_id")
        or ext.get("file_id")
        or ext.get("fileId")
        or payload.get("cloud_file_id")
        or payload.get("dji_file_id")
        or payload.get("file_id")
        or payload.get("fileId")
        or payload.get("id")
        or payload.get("object_key")
        or payload.get("objectKey")
        or payload.get("fingerprint")
        or ""
    ).strip()


def _media_string(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _media_dict(payload: dict, key: str) -> dict:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _media_job_id(payload: dict) -> str:
    ext = _media_dict(payload, "ext")
    metadata = _media_dict(payload, "metadata")
    for source in (ext, metadata, payload):
        value = (
            source.get("dji_job_id")
            or source.get("job_id")
            or source.get("jobId")
            or source.get("flight_id")
            or source.get("flightId")
        )
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _media_file_group_id(payload: dict) -> str:
    ext = _media_dict(payload, "ext")
    for source in (ext, payload):
        value = source.get("file_group_id") or source.get("fileGroupId") or source.get("group_id") or source.get("groupId")
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _media_object_key(payload: dict) -> str:
    return _media_string(payload, "object_key", "objectKey")


def _media_fingerprint(payload: dict) -> str:
    return _media_string(payload, "fingerprint", "tiny_fingerprint", "tinyFingerprint")


def _media_workspace_id(payload: dict) -> str:
    ext = _media_dict(payload, "ext")
    for source in (ext, payload):
        value = source.get("workspace_id") or source.get("workspaceId")
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _media_device_sn(payload: dict) -> str:
    ext = _media_dict(payload, "ext")
    metadata = _media_dict(payload, "metadata")
    for source in (ext, metadata, payload):
        value = (
            source.get("sn")
            or source.get("device_sn")
            or source.get("deviceSn")
            or source.get("drone_sn")
            or source.get("droneSn")
            or source.get("drone")
        )
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _cloud_media_defaults(
    *,
    payload: dict,
    workspace_id: str,
    dji_job_id: str,
    device_sn: str,
    mission: InspectionMission | None,
    flight_record: InspectionFlightRecord | None,
) -> dict:
    return {
        "workspace_id": workspace_id,
        "flight_record": flight_record,
        "mission": mission,
        "device_sn": device_sn,
        "dji_job_id": dji_job_id,
        "object_key": _media_object_key(payload),
        "fingerprint": _media_fingerprint(payload),
        "file_group_id": _media_file_group_id(payload),
        "media_type": _parse_media_type(payload),
        "file_name": _media_string(payload, "file_name", "fileName", "name"),
        "thumbnail_url": _media_string(payload, "thumbnail_url", "thumbnailUrl", "thumb_url", "thumbUrl"),
        "preview_url": _media_string(payload, "preview_url", "previewUrl"),
        "download_url": _media_string(payload, "download_url", "downloadUrl", "file_url", "fileUrl", "url"),
        "playback_url": _media_string(payload, "playback_url", "playbackUrl"),
        "file_size": payload.get("file_size") or payload.get("fileSize"),
        "captured_at": _parse_captured_at(payload),
        "metadata": payload,
    }


def _cloud_media_key(payload: dict) -> str:
    return _media_identifier(payload)


def _resource_binding_for_device_sn(device_sn: str) -> ResourceBinding | None:
    if not device_sn:
        return None
    drone = DroneResource.objects.filter(device_sn=device_sn).first()
    if drone is None:
        return None
    return (
        ResourceBinding.objects.select_related("owner_department", "dji_connection")
        .filter(resource_type=ResourceType.DRONE, resource_object_id=drone.id, status=BindingStatus.ACTIVE)
        .first()
    )


def _refresh_record_media_counts(record: InspectionFlightRecord) -> dict:
    counts = list(record.media_files.values_list("media_type", flat=True))
    photo_count = sum(1 for item in counts if item == CloudMediaType.PHOTO)
    video_count = sum(1 for item in counts if item == CloudMediaType.VIDEO)
    record.photo_count = photo_count
    record.video_count = video_count
    record.save(update_fields=["photo_count", "video_count", "updated_at"])
    return {"photoCount": photo_count, "videoCount": video_count}


def _bind_session_window_media_for_record(*, record: InspectionFlightRecord, workspace_id: str, device_sn: str) -> int:
    if not workspace_id or not device_sn:
        return 0
    return CloudMediaFile.objects.filter(
        workspace_id=workspace_id,
        device_sn=device_sn,
        dji_job_id="",
        captured_at__isnull=False,
        captured_at__gte=record.start_time,
        captured_at__lte=record.end_time,
        mission__isnull=True,
        flight_record__isnull=True,
    ).update(
        mission=record.mission,
        flight_record=record,
        updated_at=timezone.now(),
    )


def _sync_session_window_media_from_dji(
    *,
    gateway: DjiConnectionGateway,
    record: InspectionFlightRecord,
    workspace_id: str,
    device_sn: str,
) -> int:
    if not workspace_id or not device_sn:
        return 0
    synced = 0
    for payload in gateway.list_media_files():
        if not isinstance(payload, dict):
            continue
        if _media_job_id(payload):
            continue
        payload_workspace_id = _media_workspace_id(payload)
        if payload_workspace_id and payload_workspace_id != workspace_id:
            continue
        if _media_device_sn(payload) != device_sn:
            continue
        captured_at = _parse_captured_at(payload)
        if captured_at is None or captured_at < record.start_time or captured_at > record.end_time:
            continue
        cloud_file_id = _cloud_media_key(payload)
        if not cloud_file_id:
            continue
        CloudMediaFile.objects.update_or_create(
            workspace_id=workspace_id,
            cloud_file_id=cloud_file_id,
            defaults=_cloud_media_defaults(
                payload=payload,
                workspace_id=workspace_id,
                dji_job_id="",
                device_sn=device_sn,
                mission=record.mission,
                flight_record=record,
            ),
        )
        synced += 1
    return synced


def sync_media_for_record(*, record: InspectionFlightRecord) -> dict:
    execution = (
        MissionCloudExecution.objects.select_related("dji_connection")
        .filter(mission=record.mission)
        .first()
    )
    if execution is None:
        counts = _refresh_record_media_counts(record)
        return {"synced": 0, **counts}

    connection = execution.dji_connection
    dji_job_id = execution.dji_job_id
    workspace_id = execution.workspace_id or connection.workspace_id

    if not dji_job_id and execution.execution_mode != MissionExecutionMode.PILOT2_MANUAL:
        counts = _refresh_record_media_counts(record)
        return {"synced": 0, **counts}

    if not dji_job_id:
        device_sn = execution.drone_sn or record.drone_device_sn
        synced = _bind_session_window_media_for_record(
            record=record,
            workspace_id=workspace_id,
            device_sn=device_sn,
        )
        try:
            synced += _sync_session_window_media_from_dji(
                gateway=DjiConnectionGateway(connection),
                record=record,
                workspace_id=workspace_id,
                device_sn=device_sn,
            )
        except DjiGatewayError:
            _refresh_record_media_counts(record)
            raise
        counts = _refresh_record_media_counts(record)
        return {"synced": synced, **counts}

    gateway = DjiConnectionGateway(connection)

    CloudMediaFile.objects.filter(workspace_id=workspace_id, dji_job_id=dji_job_id).update(
        workspace_id=workspace_id,
        mission=record.mission,
        flight_record=record,
        updated_at=timezone.now(),
    )

    cloud_items = gateway.list_media_files()
    synced = 0
    for payload in cloud_items:
        if not isinstance(payload, dict) or _media_job_id(payload) != dji_job_id:
            continue
        cloud_file_id = _cloud_media_key(payload)
        if not cloud_file_id:
            continue
        CloudMediaFile.objects.update_or_create(
            workspace_id=workspace_id,
            cloud_file_id=cloud_file_id,
            defaults=_cloud_media_defaults(
                payload=payload,
                workspace_id=workspace_id,
                dji_job_id=dji_job_id,
                device_sn=_media_device_sn(payload) or execution.drone_sn or record.drone_device_sn,
                mission=record.mission,
                flight_record=record,
            ),
        )
        synced += 1
    counts = _refresh_record_media_counts(record)
    return {"synced": synced, **counts}


def _dji_connection_for_media_file(media: CloudMediaFile) -> DjiConnection:
    if media.mission_id:
        execution = (
            MissionCloudExecution.objects.select_related("dji_connection")
            .filter(mission_id=media.mission_id)
            .first()
        )
        if execution is not None:
            return execution.dji_connection

    binding = _resource_binding_for_device_sn(media.device_sn)
    if binding is not None:
        return binding.dji_connection

    if media.workspace_id:
        connections = DjiConnection.objects.filter(workspace_id=media.workspace_id)
        if connections.count() == 1:
            return connections.first()

    raise StandardConstraintConflict(msg="无法确定媒体文件所属 DJI 连接")


def _signed_url_expires_at(url: str) -> datetime | None:
    query = parse_qs(urlsplit(str(url or "")).query)
    amz_date = (query.get("X-Amz-Date") or [""])[0]
    amz_expires = (query.get("X-Amz-Expires") or [""])[0]
    if not amz_date or not amz_expires:
        return None
    try:
        issued_at = datetime.strptime(amz_date, _AMZ_DATE_FORMAT).replace(tzinfo=dt_timezone.utc)
        expires_seconds = int(amz_expires)
    except (TypeError, ValueError):
        return None
    return issued_at + timedelta(seconds=expires_seconds)


def _preview_url_needs_refresh(url: str, *, now: datetime | None = None) -> bool:
    if not str(url or "").strip():
        return True
    expires_at = _signed_url_expires_at(url)
    if expires_at is None:
        return False
    current_time = now or timezone.now()
    if timezone.is_naive(current_time):
        current_time = current_time.replace(tzinfo=dt_timezone.utc)
    return expires_at <= current_time.astimezone(dt_timezone.utc) + MEDIA_PREVIEW_URL_REFRESH_MARGIN


def refresh_cloud_media_file_url(*, media: CloudMediaFile, url_type: str) -> CloudMediaFile:
    connection = _dji_connection_for_media_file(media)
    gateway = DjiConnectionGateway(connection)
    if url_type == "preview":
        media.preview_url = str(gateway.get_media_preview_url(media.cloud_file_id) or "")
        update_fields = ["preview_url", "updated_at"]
    elif url_type == "playback":
        media.playback_url = str(gateway.get_media_playback_url(media.cloud_file_id) or "")
        update_fields = ["playback_url", "updated_at"]
    else:
        media.download_url = str(gateway.get_media_url(media.cloud_file_id) or "")
        update_fields = ["download_url", "updated_at"]
    media.save(update_fields=update_fields)
    return media


def ensure_cloud_media_preview_url(media: CloudMediaFile) -> CloudMediaFile:
    if not _preview_url_needs_refresh(media.preview_url):
        return media
    return refresh_cloud_media_file_url(media=media, url_type="preview")


def ensure_cloud_media_preview_urls(media_files: list[CloudMediaFile]) -> list[CloudMediaFile]:
    return [ensure_cloud_media_preview_url(media) for media in media_files]


def _flight_record_for_v2_mission(mission: InspectionMission | None) -> InspectionFlightRecord | None:
    if mission is None:
        return None
    try:
        return mission.flight_record
    except InspectionFlightRecord.DoesNotExist:
        return None


def handle_v2_media_upload_callback(payload: dict) -> dict[str, int]:
    payload = payload if isinstance(payload, dict) else {}
    cloud_file_id = _cloud_media_key(payload)
    if not cloud_file_id:
        return {"resolved_count": 0, "ignored_count": 1}

    dji_job_id = _media_job_id(payload)
    execution = None
    if dji_job_id:
        execution = (
            MissionCloudExecution.objects.select_related(
                "mission",
                "dji_connection",
            )
            .filter(dji_job_id=dji_job_id)
            .first()
        )

    device_sn = _media_device_sn(payload) or (execution.drone_sn if execution is not None else "")
    workspace_id = _media_workspace_id(payload)
    mission = execution.mission if execution is not None else None
    flight_record = _flight_record_for_v2_mission(mission)

    if execution is not None:
        workspace_id = workspace_id or execution.workspace_id or execution.dji_connection.workspace_id
        device_sn = device_sn or execution.drone_sn
    else:
        binding = _resource_binding_for_device_sn(device_sn)
        if binding is None:
            return {"resolved_count": 0, "ignored_count": 1}
        workspace_id = workspace_id or binding.dji_connection.workspace_id

    if not workspace_id:
        return {"resolved_count": 0, "ignored_count": 1}

    CloudMediaFile.objects.update_or_create(
        workspace_id=workspace_id,
        cloud_file_id=cloud_file_id,
        defaults=_cloud_media_defaults(
            payload=payload,
            workspace_id=workspace_id,
            dji_job_id=dji_job_id,
            device_sn=device_sn,
            mission=mission,
            flight_record=flight_record,
        ),
    )
    if flight_record is not None:
        _refresh_record_media_counts(flight_record)
    return {"resolved_count": 1, "ignored_count": 0}


def _event_progress(payload: dict) -> int:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    output = data.get("output") if isinstance(data.get("output"), dict) else {}
    value = None
    for source in (output, data, payload):
        value = source.get("progress") or source.get("progress_percent") or source.get("percent")
        if value not in (None, ""):
            break
    if isinstance(value, bool):
        return 0
    try:
        progress = int(value)
    except (TypeError, ValueError):
        return 0
    return min(max(progress, 0), 100)


def _cloud_status_from_event(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"ok", "success", "succeeded", "completed", "complete", "finish", "finished"}:
        return CloudExecutionStatus.COMPLETED
    if normalized in {"cancel", "canceled", "cancelled"}:
        return CloudExecutionStatus.CANCELED
    if normalized in {"failed", "failure", "error", "timeout"}:
        return CloudExecutionStatus.FAILED
    return CloudExecutionStatus.RUNNING


def _record_status_from_cloud(status_value: str) -> str:
    return {
        CloudExecutionStatus.COMPLETED: FlightRecordStatus.COMPLETED,
        CloudExecutionStatus.CANCELED: FlightRecordStatus.CANCELED,
        CloudExecutionStatus.FAILED: FlightRecordStatus.FAILED,
    }.get(status_value, FlightRecordStatus.COMPLETED)


def _mission_status_from_cloud(status_value: str) -> str:
    return {
        CloudExecutionStatus.COMPLETED: MissionStatus.COMPLETED,
        CloudExecutionStatus.CANCELED: MissionStatus.CANCELED,
        CloudExecutionStatus.FAILED: MissionStatus.FAILED,
    }.get(status_value, MissionStatus.RUNNING)


def _session_status_from_cloud(status_value: str) -> str:
    return {
        CloudExecutionStatus.COMPLETED: FlightSessionStatus.COMPLETED,
        CloudExecutionStatus.CANCELED: FlightSessionStatus.CANCELED,
        CloudExecutionStatus.FAILED: FlightSessionStatus.FAILED,
    }.get(status_value, FlightSessionStatus.RUNNING)


def _flight_record_from_terminal_execution(*, mission: InspectionMission, session: FlightSession, status_value: str):
    record, _created = InspectionFlightRecord.objects.get_or_create(
        mission=mission,
        defaults={
            "session": session,
            "flight_no": _flight_no(mission),
            "creator_department": mission.creator_department,
            "primary_resource_owner_department": mission.primary_resource_owner_department,
            "mission_name": mission.name,
            "route_name": mission.route.name,
            "drone_device_sn": mission.drone.device_sn,
            "drone_name": mission.drone.name,
            "pilot_name": mission.pilot_account_profile.name,
            "start_time": session.started_at,
            "end_time": session.ended_at or timezone.now(),
            "flight_duration": _duration_seconds(session.started_at, session.ended_at or timezone.now()),
            "status": _record_status_from_cloud(status_value),
        },
    )
    return record


def apply_cloud_execution_event(*, dji_job_id: str, status: str, payload: dict | None = None) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    execution = (
        MissionCloudExecution.objects.select_related(
            "mission",
            "mission__creator_department",
            "mission__primary_resource_owner_department",
            "mission__route",
            "mission__drone",
            "mission__pilot_account_profile",
            "session",
        )
        .filter(dji_job_id=dji_job_id)
        .first()
    )
    if execution is None:
        return {"updated": 0, "ignored": 1}

    now = timezone.now()
    cloud_status = _cloud_status_from_event(status)
    execution.status = cloud_status
    execution.progress_percent = _event_progress(payload) or execution.progress_percent
    execution.last_event_at = now
    execution.raw_last_event = payload
    if cloud_status == CloudExecutionStatus.FAILED:
        execution.error_code = str(payload.get("result_code") or payload.get("code") or "")
        execution.error_message = str(payload.get("message") or payload.get("msg") or "")

    mission = execution.mission
    session = execution.session or getattr(mission, "flight_session", None)
    media_result = {"synced": 0, "photoCount": 0, "videoCount": 0}
    if cloud_status in {CloudExecutionStatus.COMPLETED, CloudExecutionStatus.CANCELED, CloudExecutionStatus.FAILED}:
        mission.status = _mission_status_from_cloud(cloud_status)
        if cloud_status == CloudExecutionStatus.CANCELED:
            mission.canceled_at = mission.canceled_at or now
            mission.save(update_fields=["status", "canceled_at", "updated_at"])
        else:
            mission.finished_at = mission.finished_at or now
            mission.save(update_fields=["status", "finished_at", "updated_at"])
        if session is not None and session.status == FlightSessionStatus.RUNNING:
            session.status = _session_status_from_cloud(cloud_status)
            session.ended_at = session.ended_at or now
            session.save(update_fields=["status", "ended_at", "updated_at"])
        if session is not None:
            record = _flight_record_from_terminal_execution(mission=mission, session=session, status_value=cloud_status)
            try:
                media_result = sync_media_for_record(record=record)
            except DjiGatewayError:
                media_result = {"synced": 0, "photoCount": record.photo_count, "videoCount": record.video_count}
        execution.ended_at = execution.ended_at or now
        _stop_live_for_execution(execution)
    else:
        mission.status = MissionStatus.RUNNING
        mission.save(update_fields=["status", "updated_at"])

    execution.save(
        update_fields=[
            "status",
            "progress_percent",
            "last_event_at",
            "raw_last_event",
            "error_code",
            "error_message",
            "ended_at",
            "updated_at",
        ]
    )
    return {
        "updated": 1,
        "missionId": mission.id,
        "missionStatus": mission.status,
        "executionStatus": execution.status,
        "media": media_result,
    }


def _dji_job_id(payload: dict) -> str:
    return _media_string(payload, "dji_job_id", "djiJobId", "job_id", "jobId", "id")


def _dji_job_status(payload: dict):
    for key in ("status", "state", "job_status", "jobStatus"):
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return ""


def _cloud_status_from_dji_job_status(value) -> str:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        numeric = None
    if numeric == 1:
        return CloudExecutionStatus.STARTING
    if numeric in {2, 6}:
        return CloudExecutionStatus.RUNNING
    if numeric == 3:
        return CloudExecutionStatus.COMPLETED
    if numeric == 4:
        return CloudExecutionStatus.CANCELED
    if numeric == 5:
        return CloudExecutionStatus.FAILED

    normalized = str(value or "").strip().lower()
    if normalized in {"pending", "prepare", "preparing", "starting", "created"}:
        return CloudExecutionStatus.STARTING
    if normalized in {"in_progress", "running", "executing", "progress", "paused", "pause"}:
        return CloudExecutionStatus.RUNNING
    if normalized in {"success", "succeeded", "completed", "complete", "finished", "ok"}:
        return CloudExecutionStatus.COMPLETED
    if normalized in {"cancel", "canceled", "cancelled"}:
        return CloudExecutionStatus.CANCELED
    if normalized in {"failed", "failure", "error", "timeout"}:
        return CloudExecutionStatus.FAILED
    return CloudExecutionStatus.RUNNING


def _event_status_for_cloud_status(status_value: str) -> str:
    return {
        CloudExecutionStatus.COMPLETED: "ok",
        CloudExecutionStatus.CANCELED: "canceled",
        CloudExecutionStatus.FAILED: "failed",
    }.get(status_value, "running")


def refresh_mission_cloud_execution_from_dji(*, mission: InspectionMission, context) -> InspectionMission:
    require_transition_operator(context, mission)
    execution = (
        MissionCloudExecution.objects.select_related("dji_connection")
        .filter(mission=mission)
        .first()
    )
    if execution is not None and execution.execution_mode == MissionExecutionMode.PILOT2_MANUAL:
        raise StandardConstraintConflict(msg="Pilot2 手动执行任务没有 DJI job，不能通过 jobs 刷新")
    if execution is None and mission.execution_mode == MissionExecutionMode.PILOT2_MANUAL:
        raise StandardConstraintConflict(msg="Pilot2 手动执行任务没有 DJI job，不能通过 jobs 刷新")
    if execution is None or not execution.dji_job_id:
        raise StandardConstraintConflict(msg="任务缺少 DJI 云端执行记录")

    jobs = DjiConnectionGateway(execution.dji_connection).list_jobs()
    matched_job = next((job for job in jobs if isinstance(job, dict) and _dji_job_id(job) == execution.dji_job_id), None)
    if matched_job is None:
        raise StandardConstraintConflict(msg="DJI jobs 列表中未找到当前任务")

    cloud_status = _cloud_status_from_dji_job_status(_dji_job_status(matched_job))
    if cloud_status in {CloudExecutionStatus.COMPLETED, CloudExecutionStatus.CANCELED, CloudExecutionStatus.FAILED}:
        apply_cloud_execution_event(
            dji_job_id=execution.dji_job_id,
            status=_event_status_for_cloud_status(cloud_status),
            payload=matched_job,
        )
    else:
        now = timezone.now()
        progress = _event_progress(matched_job)
        execution.status = cloud_status
        if progress:
            execution.progress_percent = progress
        execution.last_event_at = now
        execution.raw_last_event = matched_job
        execution.save(update_fields=["status", "progress_percent", "last_event_at", "raw_last_event", "updated_at"])
        if mission.status != MissionStatus.RUNNING:
            mission.status = MissionStatus.RUNNING
            mission.save(update_fields=["status", "updated_at"])

    mission.refresh_from_db()
    return mission


def _osd_reported_at(payload: dict):
    timestamp = payload.get("timestamp")
    if isinstance(timestamp, (int, float)):
        seconds = timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
        return timezone.datetime.fromtimestamp(seconds, tz=dt_timezone.utc)
    return timezone.now()


def _osd_battery_percent(data: dict):
    battery = data.get("battery")
    if isinstance(battery, dict):
        value = battery.get("capacity_percent") or battery.get("percent") or battery.get("battery_percent")
    else:
        value = data.get("battery_percent") or data.get("capacity_percent")
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _status_online_value(payload: dict):
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for source in (data, payload):
        for key in ("online_status", "onlineStatus", "online", "status", "state"):
            value = source.get(key)
            if value in (None, ""):
                continue
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            normalized = str(value).strip().lower()
            if normalized in {"online", "connected", "success", "1", "true", "active"}:
                return True
            if normalized in {"offline", "disconnected", "0", "false", "inactive"}:
                return False
    return None


def apply_device_status_event(*, device_sn: str, payload: dict | None = None) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    online = _status_online_value(payload)
    if online is None:
        return {"updated": 0}

    now = timezone.now()
    updated = 0
    for model in (DroneResource, DockResource, GatewayResource):
        queryset = model.objects.filter(device_sn=device_sn)
        count = queryset.update(
            online_status=online,
            last_seen_at=now if online else None,
            last_payload=payload,
            updated_at=now,
        )
        updated += count
    return {"updated": updated, "onlineStatus": online}


def apply_osd_telemetry(*, device_sn: str, payload: dict | None = None, dji_connection: DjiConnection | None = None) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    upsert_drone_telemetry_from_osd(connection=dji_connection, device_sn=device_sn, payload=payload)
    apply_device_status_event(device_sn=device_sn, payload={"online": True, "data": data})
    sessions = FlightSession.objects.select_related("mission").filter(
        status=FlightSessionStatus.RUNNING,
        drone__device_sn=device_sn,
    )
    updated = 0
    for session in sessions:
        update_telemetry_snapshot(
            session=session,
            payload={
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "altitude": data.get("altitude", data.get("height")),
                "speed": data.get("speed", data.get("horizontal_speed")),
                "heading": data.get("heading", data.get("attitude_head")),
                "batteryPercent": _osd_battery_percent(data),
                "reportedAt": _osd_reported_at(payload),
                "raw": payload,
            },
        )
        updated += 1
    return {"updated": updated}


def visible_resource_for_live(context, drone_id: int) -> ResourceBinding:
    binding = monitorable_resource_binding(context, ResourceType.DRONE, drone_id)
    from apps.resource_v2.services import effective_permissions_for_binding

    effective = set(effective_permissions_for_binding(context, binding))
    if "monitor" not in effective and not is_platform_super_admin(context):
        raise StandardForbidden()
    return binding
