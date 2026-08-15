import re
import uuid
import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import parse_qs, urlsplit

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiRequest, OpenApiResponse, extend_schema
from rest_framework import parsers, serializers, status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.access.authentication import BearerAuthSessionAuthentication
from apps.access.api_base import EmptySerializer
from apps.access.exceptions import StandardConstraintConflict, StandardForbidden, StandardNotFound
from apps.access.models import DirectoryStatus
from apps.api_contracts.openapi import V2MediaRefreshSerializer, generic_object_response, list_data_serializer
from apps.audit_v2.services import log_v2_action
from apps.common.api_response import BusinessApiResponseMixin, StandardCode, standard_duplicate_response, standard_error_payload
from apps.iam_v2.account_profile_services import account_has_effective_qualification, account_has_role, require_active_account_role_profile
from apps.iam_v2.models import FixedRole, V2AccountProfile
from apps.iam_v2.services import department_in_context_scope, resolve_v2_context
from apps.inspection_v2.models import (
    CameraOperation,
    CameraOperationStatus,
    CloudMediaType,
    InspectionMission,
    MissionStatus,
    Waypoint,
    WaypointRouteCloudFile,
    WaypointRoute,
)
from apps.inspection_v2.drc_contract import (
    DrcCapabilityQuerySerializer,
    DrcCapabilityResponseSerializer,
    DrcConnectResponseSerializer,
    DrcConnectSerializer,
    DrcDockDebugActionResponseSerializer,
    DrcDockDebugActionSerializer,
    DrcExitResponseSerializer,
    DrcExitSerializer,
    DrcFlightActionResponseSerializer,
    DrcFlightActionSerializer,
)
from apps.inspection_v2.drc_services import (
    connect_drc,
    drc_capabilities,
    execute_dock_debug_action,
    execute_drc_flight_action,
    exit_drc,
)
from apps.inspection_v2.route_kmz import parse_route_kmz
from apps.inspection_v2.serializers import (
    ActiveFlightReadSerializer,
    CameraActionResponseSerializer,
    CameraActionSerializer,
    CloudMediaFileReadSerializer,
    CloudMediaFileUrlRefreshSerializer,
    FlightRecordReadSerializer,
    FlightRecordUpdateSerializer,
    LiveCapacityQuerySerializer,
    LiveCameraChangeSerializer,
    LiveStartSerializer,
    LiveStopSerializer,
    LiveSwitchSerializer,
    LiveUpdateSerializer,
    MissionCloseSerializer,
    MissionPreflightCheckResponseSerializer,
    MissionReadSerializer,
    MissionWriteSerializer,
    RouteCreateSerializer,
    RouteDeleteResponseSerializer,
    RouteKmzUpdateSerializer,
    RouteMetadataUpdateSerializer,
    RouteReadSerializer,
    TelemetrySnapshotReadSerializer,
    TelemetrySnapshotWriteSerializer,
)
from apps.inspection_v2.services import (
    complete_mission,
    assignable_routes_queryset,
    build_mission_preflight_check,
    create_assignments,
    editable_routes_queryset,
    ensure_cloud_media_access_url,
    ensure_cloud_media_access_urls,
    ensure_cloud_media_preview_url,
    ensure_resources_available,
    get_editable_route_or_404,
    get_visible_mission_or_404,
    get_visible_record_or_404,
    get_visible_route_or_404,
    is_assigned_pilot,
    is_dispatcher,
    require_dispatcher,
    maybe_sync_media_for_record_on_detail_read,
    route_snapshot,
    refresh_mission_cloud_execution_from_dji,
    refresh_cloud_media_file_url,
    safety_abort_mission,
    start_mission,
    sync_media_for_record_and_update_state,
    update_telemetry_snapshot,
    usable_resource_binding,
    visible_media_queryset,
    visible_missions_queryset,
    visible_records_queryset,
    visible_resource_for_live,
    visible_routes_queryset,
    visible_sessions_queryset,
)
from apps.resource_v2.gateway import DjiConnectionGateway, DjiGatewayError, dji_connection_gateway
from apps.resource_v2.models import ResourceType
from apps.resource_v2.models import DjiConnection
from apps.resource_v2.services import effective_permissions_for_binding, get_resource


logger = logging.getLogger(__name__)
ROUTE_DOWNLOAD_URL_REFRESH_MARGIN = timedelta(minutes=5)


class InspectionV2APIView(BusinessApiResponseMixin, GenericAPIView):
    authentication_classes = [BearerAuthSessionAuthentication]
    serializer_class = EmptySerializer


ROUTE_LIST_RESPONSE = list_data_serializer("V2InspectionRouteListData", RouteReadSerializer)
MISSION_LIST_RESPONSE = list_data_serializer("V2InspectionMissionListData", MissionReadSerializer)
ACTIVE_FLIGHT_LIST_RESPONSE = list_data_serializer("V2ActiveFlightListData", ActiveFlightReadSerializer)
FLIGHT_RECORD_LIST_RESPONSE = list_data_serializer("V2InspectionFlightRecordListData", FlightRecordReadSerializer)
MEDIA_FILE_LIST_RESPONSE = list_data_serializer("V2CloudMediaFileListData", CloudMediaFileReadSerializer)
ROUTE_MULTIPART_WRITE_REQUEST = OpenApiRequest(
    request={
        "type": "object",
        "properties": {
            "name": {"type": "string", "maxLength": 128},
            "status": {"type": "integer", "enum": [0, 1]},
            "coverImage": {"type": "string", "format": "binary"},
            "remark": {"type": "string"},
            "djiConnectionId": {"type": "integer"},
            "kmzFile": {"type": "string", "format": "binary"},
        },
        "required": ["name", "djiConnectionId", "kmzFile"],
    },
    encoding={
        "coverImage": {"contentType": "image/jpeg, image/png, image/webp"},
        "kmzFile": {"contentType": "application/vnd.google-earth.kmz, application/zip"},
    },
)
ROUTE_MULTIPART_UPDATE_REQUEST = OpenApiRequest(
    request={
        "type": "object",
        "properties": {
            "name": {"type": "string", "maxLength": 128},
            "status": {"type": "integer", "enum": [0, 1]},
            "coverImage": {"type": "string", "format": "binary"},
            "remark": {"type": "string"},
            "djiConnectionId": {"type": "integer"},
            "kmzFile": {"type": "string", "format": "binary"},
        },
        "required": ["djiConnectionId", "kmzFile"],
    },
    encoding={
        "coverImage": {"contentType": "image/jpeg, image/png, image/webp"},
        "kmzFile": {"contentType": "application/vnd.google-earth.kmz, application/zip"},
    },
)
EMPTY_OBJECT_REQUEST = OpenApiRequest(request={"type": "object", "properties": {}})


_duplicate_response = standard_duplicate_response


def _upstream_error_response(exc: DjiGatewayError):
    error_text = f"{exc} {getattr(exc, 'data', '')}"
    if "211001" in error_text:
        return Response(
            standard_error_payload(
                StandardCode.INTERNAL_ERROR,
                "机场未及时确认指令，动作可能已经执行，请先检查机场状态，勿重复下发",
                {
                    "reasonCode": "DJI_COMMAND_OUTCOME_UNKNOWN",
                    "detail": str(exc),
                    "upstreamStatus": getattr(exc, "status_code", 502),
                    "upstream": getattr(exc, "data", None),
                },
            ),
            status=status.HTTP_502_BAD_GATEWAY,
        )
    if "210003" in error_text:
        return Response(
            standard_error_payload(
                StandardCode.CONSTRAINT_CONFLICT,
                "当前设备不支持 DJI 航线任务能力",
                {
                    "detail": str(exc),
                    "upstreamStatus": getattr(exc, "status_code", 502),
                    "upstream": getattr(exc, "data", None),
                },
            ),
            status=status.HTTP_409_CONFLICT,
        )
    return Response(
        standard_error_payload(
            StandardCode.INTERNAL_ERROR,
            "DJI 上游服务异常",
            {
                "detail": str(exc),
                "upstreamStatus": getattr(exc, "status_code", 502),
                "upstream": getattr(exc, "data", None),
            },
        ),
        status=status.HTTP_502_BAD_GATEWAY,
    )


def _int_query_param(params, name: str):
    value = params.get(name)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise serializers.ValidationError({name: ["必须是整数"]}) from exc


def _replace_waypoints(route: WaypointRoute, waypoints: list[dict]):
    route.clear_waypoints()
    Waypoint.objects.bulk_create(
        [
            Waypoint(
                route=route,
                sequence=item["sequence"],
                latitude=item["latitude"],
                longitude=item["longitude"],
                altitude=item["altitude"],
                speed=item.get("speed"),
                heading=item.get("heading"),
                hover_seconds=item.get("hoverSeconds", 0),
            )
            for item in waypoints
        ]
    )


def _delete_replaced_route_cover(route: WaypointRoute, old_cover_name: str) -> None:
    if not old_cover_name or old_cover_name == route.cover_image_name:
        return
    try:
        route.delete_cover_image_by_name(old_cover_name)
    except Exception:  # noqa: BLE001 - storage backends expose inconsistent deletion errors.
        logger.warning("failed to delete replaced route cover", extra={"route_id": route.id, "cover_name": old_cover_name}, exc_info=True)


def _delete_route_cover_best_effort(route: WaypointRoute, cover_name: str) -> None:
    if not cover_name:
        return
    try:
        route.delete_cover_image_by_name(cover_name)
    except Exception:  # noqa: BLE001 - storage cleanup must not make a committed delete fail.
        logger.warning("failed to delete route cover", extra={"route_id": route.id, "cover_name": cover_name}, exc_info=True)


def _safe_dji_upload_name(*, route_id: int) -> str:
    return f"v2-route-{route_id}-{uuid.uuid4().hex[:8]}"


def _clone_kmz_for_dji_upload(file_obj, *, upload_name: str) -> SimpleUploadedFile:
    safe_name = re.sub(r"[^A-Za-z0-9.-]+", "-", upload_name).strip(".-") or "route"
    safe_name = safe_name.replace("_", "-")
    file_obj.seek(0)
    content = file_obj.read()
    file_obj.seek(0)
    content_type = getattr(file_obj, "content_type", "application/vnd.google-earth.kmz")
    return SimpleUploadedFile(name=f"{safe_name}.kmz", content=content, content_type=content_type)


def _request_has_file(data, field_name: str) -> bool:
    if not isinstance(data, dict):
        return False
    value = data.get(field_name)
    return value not in (None, "", b"")


def _reject_execution_fields_without_kmz(data) -> None:
    if not isinstance(data, dict):
        return
    execution_fields = {"waypoints", "defaultAltitude", "defaultSpeed", "djiConnectionId", "waylineType"}
    if execution_fields.intersection(data.keys()):
        raise serializers.ValidationError({"kmzFile": ["修改航点或默认飞行参数必须同时上传 kmzFile"]})


def _ensure_route_without_open_missions(route: WaypointRoute) -> None:
    if InspectionMission.objects.filter(route=route, status__in=[MissionStatus.PENDING, MissionStatus.RUNNING]).exists():
        raise StandardConstraintConflict(msg="航线已被待执行或执行中的任务引用，不能更新")


def _ensure_route_without_missions(route: WaypointRoute) -> None:
    if InspectionMission.objects.filter(route=route).exists():
        raise StandardConstraintConflict(msg="航线已被任务引用，不能删除")


def _dji_connection_for_route(route: WaypointRoute, dji_connection_id: int) -> DjiConnection:
    connection = (
        DjiConnection.objects.select_related("owner_department")
        .filter(pk=dji_connection_id, owner_department=route.owner_department)
        .first()
    )
    if connection is None:
        raise StandardNotFound()
    return connection


def _is_absolute_http_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _absolute_dji_url(connection: DjiConnection, value: str) -> str:
    url = str(value or "").strip()
    if not url or _is_absolute_http_url(url):
        return url
    base_url = str(getattr(connection, "base_url", "") or "").rstrip("/")
    if not base_url:
        return url
    return f"{base_url}/{url.lstrip('/')}"


def _parse_download_url_expires_at(download_url: str):
    query = parse_qs(urlsplit(str(download_url or "")).query)
    params = {key.lower(): values[-1] for key, values in query.items() if values}

    amz_date = params.get("x-amz-date")
    amz_expires = params.get("x-amz-expires")
    if amz_date and amz_expires:
        try:
            issued_at = datetime.strptime(amz_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt_timezone.utc)
            return issued_at + timedelta(seconds=int(amz_expires))
        except (TypeError, ValueError):
            return None

    epoch_expires = params.get("expires")
    if epoch_expires:
        try:
            return datetime.fromtimestamp(int(epoch_expires), tz=dt_timezone.utc)
        except (TypeError, ValueError, OSError):
            return None
    return None


def _resolve_dji_download_url(*, gateway: DjiConnectionGateway, connection: DjiConnection, dji_file_id: str) -> tuple[str, object]:
    download_url = _absolute_dji_url(connection, str(gateway.get_route_download_url(dji_file_id) or ""))
    if not download_url or not _is_absolute_http_url(download_url):
        raise DjiGatewayError(
            "未获取到可直接访问的航线下载地址",
            status_code=status.HTTP_502_BAD_GATEWAY,
            data={"dji_file_id": dji_file_id, "download_url": download_url},
        )
    return download_url, _parse_download_url_expires_at(download_url)


def _download_url_needs_refresh(cloud_file: WaypointRouteCloudFile) -> bool:
    download_url = str(getattr(cloud_file, "download_url", "") or "").strip()
    if not download_url or not _is_absolute_http_url(download_url):
        return True
    expires_at = getattr(cloud_file, "download_url_expires_at", None)
    if expires_at is None:
        return True
    return expires_at <= timezone.now() + ROUTE_DOWNLOAD_URL_REFRESH_MARGIN


def _refresh_route_cloud_file_download_url(cloud_file: WaypointRouteCloudFile) -> WaypointRouteCloudFile:
    gateway = dji_connection_gateway(cloud_file.dji_connection)
    download_url, expires_at = _resolve_dji_download_url(
        gateway=gateway,
        connection=cloud_file.dji_connection,
        dji_file_id=cloud_file.dji_file_id,
    )
    cloud_file.download_url = download_url
    cloud_file.download_url_expires_at = expires_at
    cloud_file.save(update_fields=["download_url", "download_url_expires_at", "updated_at"])
    return cloud_file


def _refresh_route_cloud_file_download_url_if_needed(route: WaypointRoute) -> None:
    try:
        cloud_file = route.cloud_file
    except WaypointRouteCloudFile.DoesNotExist:
        return
    if _download_url_needs_refresh(cloud_file):
        _refresh_route_cloud_file_download_url(cloud_file)


def _upload_route_to_dji(*, route: WaypointRoute, connection: DjiConnection, kmz_file) -> dict:
    gateway = dji_connection_gateway(connection)
    upload_name = _safe_dji_upload_name(route_id=route.id)
    upload_file = _clone_kmz_for_dji_upload(kmz_file, upload_name=upload_name)
    upload_payload = gateway.upload_route(route_name=upload_name, file_obj=upload_file)
    dji_file_id = str(upload_payload["dji_wayline_id"])
    try:
        download_url, expires_at = _resolve_dji_download_url(
            gateway=gateway,
            connection=connection,
            dji_file_id=dji_file_id,
        )
    except DjiGatewayError:
        _delete_dji_route_best_effort(connection=connection, dji_file_id=dji_file_id)
        raise
    normalized_payload = dict(upload_payload)
    normalized_payload["download_url"] = download_url
    normalized_payload["download_url_expires_at"] = expires_at
    return normalized_payload


def _sync_route_cloud_file(*, route: WaypointRoute, connection: DjiConnection, upload_payload: dict, wayline_type, user):
    raw_response = dict(upload_payload)
    raw_expires_at = raw_response.get("download_url_expires_at")
    if hasattr(raw_expires_at, "isoformat"):
        raw_response["download_url_expires_at"] = raw_expires_at.isoformat()
    return WaypointRouteCloudFile.objects.update_or_create(
        route=route,
        defaults={
            "dji_connection": connection,
            "workspace_id": connection.workspace_id,
            "dji_file_id": str(upload_payload["dji_wayline_id"]),
            "wayline_type": wayline_type,
            "download_url": str(upload_payload.get("download_url") or ""),
            "download_url_expires_at": upload_payload.get("download_url_expires_at"),
            "raw_response": raw_response,
            "uploaded_by_user": user,
            "uploaded_at": timezone.now(),
        },
    )[0]


def _delete_dji_route_best_effort(*, connection: DjiConnection | None, dji_file_id: str) -> None:
    if connection is None or not dji_file_id:
        return
    try:
        dji_connection_gateway(connection).delete_route(dji_file_id)
    except Exception:  # noqa: BLE001 - upstream cleanup must not make a committed write fail.
        logger.warning("failed to delete replaced DJI route file", extra={"dji_file_id": dji_file_id}, exc_info=True)


class RouteListCreateView(InspectionV2APIView):
    parser_classes = [parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser]

    @extend_schema(
        operation_id="v2_inspection_routes_list",
        summary="查询巡检航线列表",
        parameters=[OpenApiParameter("keywords", str, OpenApiParameter.QUERY, required=False, description="按航线名称模糊过滤。")],
        responses={200: OpenApiResponse(response=ROUTE_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        queryset = visible_routes_queryset(context)
        keywords = str(request.query_params.get("keywords") or "").strip()
        if keywords:
            queryset = queryset.filter(name__icontains=keywords)
        serializer = RouteReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_inspection_routes_create",
        summary="创建巡检航线并上传 KMZ",
        request={
            "multipart/form-data": ROUTE_MULTIPART_WRITE_REQUEST,
        },
        responses={201: OpenApiResponse(response=RouteReadSerializer, description="创建成功。")},
    )
    def post(self, request):
        context = resolve_v2_context(request)
        require_dispatcher(context)
        serializer = RouteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        parsed_kmz = parse_route_kmz(serializer.validated_data["kmzFile"])
        route = None
        connection = None
        upload_payload = None
        try:
            with transaction.atomic():
                route = WaypointRoute.objects.create(
                    owner_department=context.department,
                    name=serializer.validated_data["name"],
                    status=serializer.validated_data.get("status", 1),
                    default_altitude=parsed_kmz.default_altitude,
                    default_speed=parsed_kmz.default_speed,
                    cover_image=serializer.validated_data.get("coverImage", ""),
                    remark=serializer.validated_data.get("remark", ""),
                    created_by_user=request.user,
                )
                _replace_waypoints(route, parsed_kmz.waypoints)
                connection = _dji_connection_for_route(route, serializer.validated_data["djiConnectionId"])
                upload_payload = _upload_route_to_dji(
                    route=route,
                    connection=connection,
                    kmz_file=serializer.validated_data["kmzFile"],
                )
                _sync_route_cloud_file(
                    route=route,
                    connection=connection,
                    upload_payload=upload_payload,
                    wayline_type=parsed_kmz.wayline_type,
                    user=request.user,
                )
        except IntegrityError as exc:
            if upload_payload is not None:
                _delete_dji_route_best_effort(connection=connection, dji_file_id=str(upload_payload.get("dji_wayline_id") or ""))
            return _duplicate_response({"detail": str(exc)})
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        except Exception:
            if upload_payload is not None:
                _delete_dji_route_best_effort(connection=connection, dji_file_id=str(upload_payload.get("dji_wayline_id") or ""))
            raise
        data = RouteReadSerializer(route).data
        log_v2_action(
            request=request,
            context=context,
            action="create_waypoint_route",
            target_type="waypoint_route",
            target_id=route.id,
            resource_owner_department=route.owner_department,
            after_data=data,
        )
        return Response(data, status=status.HTTP_201_CREATED)


class RouteDetailView(InspectionV2APIView):
    parser_classes = [parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser]

    @extend_schema(
        operation_id="v2_inspection_routes_retrieve",
        summary="读取巡检航线详情",
        responses={200: OpenApiResponse(response=RouteReadSerializer, description="读取成功。")},
    )
    def get(self, request, id: int):
        context = resolve_v2_context(request)
        route = get_visible_route_or_404(context, id)
        try:
            _refresh_route_cloud_file_download_url_if_needed(route)
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        return Response(RouteReadSerializer(route).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_inspection_routes_update",
        summary="更新巡检航线",
        request={
            "application/json": RouteMetadataUpdateSerializer,
            "multipart/form-data": ROUTE_MULTIPART_UPDATE_REQUEST,
        },
        responses={200: OpenApiResponse(response=RouteReadSerializer, description="更新成功。")},
    )
    def put(self, request, id: int):
        context = resolve_v2_context(request)
        require_dispatcher(context)
        route = get_editable_route_or_404(context, id)
        _ensure_route_without_open_missions(route)
        before_data = RouteReadSerializer(route).data

        if not _request_has_file(request.data, "kmzFile"):
            _reject_execution_fields_without_kmz(request.data)
            serializer = RouteMetadataUpdateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            old_cover_name = route.cover_image_name
            update_fields = ["updated_at"]
            if "name" in serializer.validated_data:
                route.name = serializer.validated_data["name"]
                update_fields.append("name")
            if "status" in serializer.validated_data:
                route.status = serializer.validated_data["status"]
                update_fields.append("status")
            if "remark" in serializer.validated_data:
                route.remark = serializer.validated_data["remark"]
                update_fields.append("remark")
            if "coverImage" in serializer.validated_data:
                route.cover_image = serializer.validated_data["coverImage"]
                update_fields.append("cover_image")
            try:
                with transaction.atomic():
                    route.save(update_fields=update_fields)
            except IntegrityError as exc:
                return _duplicate_response({"detail": str(exc)})
            _delete_replaced_route_cover(route, old_cover_name)
            data = RouteReadSerializer(route).data
            log_v2_action(
                request=request,
                context=context,
                action="update_waypoint_route",
                target_type="waypoint_route",
                target_id=route.id,
                resource_owner_department=route.owner_department,
                before_data=before_data,
                after_data=data,
            )
            return Response(data, status=status.HTTP_200_OK)

        serializer = RouteKmzUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        parsed_kmz = parse_route_kmz(serializer.validated_data["kmzFile"])
        connection = _dji_connection_for_route(route, serializer.validated_data["djiConnectionId"])
        old_cover_name = route.cover_image_name
        try:
            old_cloud_file = route.cloud_file
            old_connection = old_cloud_file.dji_connection
            old_dji_file_id = old_cloud_file.dji_file_id
        except WaypointRouteCloudFile.DoesNotExist:
            old_connection = None
            old_dji_file_id = ""
        try:
            upload_payload = _upload_route_to_dji(
                route=route,
                connection=connection,
                kmz_file=serializer.validated_data["kmzFile"],
            )
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        update_fields = ["updated_at"]
        if "name" in serializer.validated_data:
            route.name = serializer.validated_data["name"]
            update_fields.append("name")
        if "status" in serializer.validated_data:
            route.status = serializer.validated_data["status"]
            update_fields.append("status")
        route.default_altitude = parsed_kmz.default_altitude
        update_fields.append("default_altitude")
        route.default_speed = parsed_kmz.default_speed
        update_fields.append("default_speed")
        if "remark" in serializer.validated_data:
            route.remark = serializer.validated_data["remark"]
            update_fields.append("remark")
        if "coverImage" in serializer.validated_data:
            route.cover_image = serializer.validated_data["coverImage"]
            update_fields.append("cover_image")
        try:
            with transaction.atomic():
                route.save(update_fields=update_fields)
                _replace_waypoints(route, parsed_kmz.waypoints)
                _sync_route_cloud_file(
                    route=route,
                    connection=connection,
                    upload_payload=upload_payload,
                    wayline_type=parsed_kmz.wayline_type,
                    user=request.user,
                )
        except IntegrityError as exc:
            _delete_dji_route_best_effort(connection=connection, dji_file_id=str(upload_payload.get("dji_wayline_id") or ""))
            return _duplicate_response({"detail": str(exc)})
        except Exception:
            _delete_dji_route_best_effort(connection=connection, dji_file_id=str(upload_payload.get("dji_wayline_id") or ""))
            raise
        _delete_replaced_route_cover(route, old_cover_name)
        if old_dji_file_id and old_dji_file_id != str(upload_payload.get("dji_wayline_id") or ""):
            _delete_dji_route_best_effort(connection=old_connection, dji_file_id=old_dji_file_id)
        route.refresh_from_db()
        data = RouteReadSerializer(route).data
        log_v2_action(
            request=request,
            context=context,
            action="update_waypoint_route",
            target_type="waypoint_route",
            target_id=route.id,
            resource_owner_department=route.owner_department,
            before_data=before_data,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_inspection_routes_delete",
        summary="删除巡检航线",
        description="删除未被任务引用的巡检航线；会同步删除本地航点、DJI 云端 KMZ 记录并 best-effort 清理 DJI wayline 文件和封面文件。",
        request=None,
        responses={200: OpenApiResponse(response=RouteDeleteResponseSerializer, description="删除成功。")},
    )
    def delete(self, request, id: int):
        context = resolve_v2_context(request)
        require_dispatcher(context)
        route = get_editable_route_or_404(context, id)
        _ensure_route_without_missions(route)
        before_data = RouteReadSerializer(route).data
        cover_name = route.cover_image_name
        try:
            cloud_file = route.cloud_file
            dji_connection = cloud_file.dji_connection
            dji_file_id = cloud_file.dji_file_id
        except WaypointRouteCloudFile.DoesNotExist:
            dji_connection = None
            dji_file_id = ""
        route_id = route.id
        owner_department = route.owner_department
        try:
            with transaction.atomic():
                route.delete()
                log_v2_action(
                    request=request,
                    context=context,
                    action="delete_waypoint_route",
                    target_type="waypoint_route",
                    target_id=route_id,
                    resource_owner_department=owner_department,
                    before_data=before_data,
                )
        except ProtectedError as exc:
            raise StandardConstraintConflict(msg="航线已被任务引用，不能删除") from exc
        _delete_route_cover_best_effort(route, cover_name)
        _delete_dji_route_best_effort(connection=dji_connection, dji_file_id=dji_file_id)
        return Response({"id": route_id, "deleted": True}, status=status.HTTP_200_OK)


def _validated_mission_inputs(context, data):
    route = assignable_routes_queryset(context).filter(pk=data["routeId"]).first()
    if route is None:
        raise StandardNotFound()
    pilot_account = (
        V2AccountProfile.objects.select_related("user", "department")
        .filter(
            pk=data["pilotAccountProfileId"],
            status=DirectoryStatus.ACTIVE,
            department__status=DirectoryStatus.ACTIVE,
        )
        .first()
    )
    if pilot_account is None:
        raise StandardNotFound()
    if not department_in_context_scope(context, pilot_account.department):
        raise StandardForbidden()
    if not account_has_role(pilot_account, FixedRole.PILOT):
        raise StandardConstraintConflict(msg="飞手账号未分配 pilot 角色")
    require_active_account_role_profile(pilot_account, FixedRole.PILOT)
    if not account_has_effective_qualification(pilot_account, FixedRole.PILOT):
        raise StandardConstraintConflict(msg="飞手缺少有效资质")
    drone_binding = usable_resource_binding(context, ResourceType.DRONE, data["droneId"])
    try:
        route_cloud_file = route.cloud_file
    except WaypointRouteCloudFile.DoesNotExist as exc:
        raise StandardConstraintConflict(msg="任务航线尚未同步到当前 DJI 连接，请重新上传/更新航线") from exc
    if route_cloud_file.dji_connection_id != drone_binding.dji_connection_id:
        raise StandardConstraintConflict(msg="任务航线尚未同步到当前 DJI 连接，请重新上传/更新航线")
    bindings = [drone_binding]
    drone = get_resource(ResourceType.DRONE, data["droneId"])
    dock = None
    executor = None
    payload = None
    dock_id = data.get("dockId")
    executor_id = data.get("executorId")
    payload_id = data.get("payloadId")
    if dock_id:
        dock_binding = usable_resource_binding(context, ResourceType.DOCK, dock_id)
        if dock_binding.dji_connection_id != drone_binding.dji_connection_id:
            raise StandardConstraintConflict(msg="机场必须与无人机属于同一个 DJI 连接")
        bindings.append(dock_binding)
        dock = get_resource(ResourceType.DOCK, dock_id)
    if executor_id:
        executor_binding = usable_resource_binding(context, ResourceType.GATEWAY, executor_id)
        if executor_binding.dji_connection_id != drone_binding.dji_connection_id:
            raise StandardConstraintConflict(msg="执行端必须与无人机属于同一个 DJI 连接")
        bindings.append(executor_binding)
        executor = get_resource(ResourceType.GATEWAY, executor_id)
    if payload_id:
        payload_binding = usable_resource_binding(context, ResourceType.PAYLOAD, payload_id)
        if payload_binding.dji_connection_id != drone_binding.dji_connection_id:
            raise StandardConstraintConflict(msg="负载必须与无人机属于同一个 DJI 连接")
        bindings.append(payload_binding)
        payload = get_resource(ResourceType.PAYLOAD, payload_id)
    ensure_resources_available(
        drone_id=drone.id,
        dock_id=dock.id if dock else None,
        executor_id=executor.id if executor else None,
        payload_id=payload.id if payload else None,
    )
    return route, pilot_account, drone, dock, executor, payload, bindings


def _mission_option_values(data):
    fields = {
        "waylinePrecisionType": "wayline_precision_type",
        "rthMode": "rth_mode",
        "rthAltitude": "rth_altitude",
        "exitWaylineWhenRcLost": "exit_wayline_when_rc_lost",
        "outOfControlAction": "out_of_control_action",
    }
    return {model_field: data[api_field] for api_field, model_field in fields.items() if api_field in data}


class MissionListCreateView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_missions_list",
        summary="查询巡检任务列表",
        parameters=[
            OpenApiParameter("routeId", int, OpenApiParameter.QUERY, required=False, description="按航线 ID 精确过滤。"),
            OpenApiParameter("status", str, OpenApiParameter.QUERY, required=False, description="按任务状态过滤。"),
            OpenApiParameter("keywords", str, OpenApiParameter.QUERY, required=False, description="按任务、航线、无人机模糊过滤。"),
        ],
        responses={200: OpenApiResponse(response=MISSION_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        queryset = visible_missions_queryset(context)
        route_id = _int_query_param(request.query_params, "routeId")
        if route_id:
            queryset = queryset.filter(route_id=route_id)
        status_value = str(request.query_params.get("status") or "").strip()
        if status_value:
            queryset = queryset.filter(status=status_value)
        keywords = str(request.query_params.get("keywords") or "").strip()
        if keywords:
            queryset = queryset.filter(
                Q(name__icontains=keywords)
                | Q(route__name__icontains=keywords)
                | Q(drone__device_sn__icontains=keywords)
                | Q(drone__name__icontains=keywords)
            )
        serializer = MissionReadSerializer(queryset.distinct(), many=True)
        return Response({"list": serializer.data, "total": queryset.distinct().count()}, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_inspection_missions_create",
        summary="创建巡检任务",
        request=MissionWriteSerializer,
        responses={201: OpenApiResponse(response=MissionReadSerializer, description="创建成功。")},
    )
    @transaction.atomic
    def post(self, request):
        context = resolve_v2_context(request)
        require_dispatcher(context)
        serializer = MissionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        route, pilot_account, drone, dock, executor, payload, bindings = _validated_mission_inputs(context, serializer.validated_data)
        mission = InspectionMission.objects.create(
            creator_department=context.department,
            primary_resource_owner_department=bindings[0].owner_department,
            route=route,
            route_snapshot=route_snapshot(route),
            name=serializer.validated_data["name"],
            drone=drone,
            dock=dock,
            executor=executor,
            payload=payload,
            pilot_account_profile=pilot_account,
            scheduled_at=serializer.validated_data.get("scheduledAt"),
            remark=serializer.validated_data.get("remark", ""),
            created_by_user=request.user,
            **_mission_option_values(serializer.validated_data),
        )
        create_assignments(mission, bindings)
        data = MissionReadSerializer(mission).data
        log_v2_action(
            request=request,
            context=context,
            action="create_inspection_mission",
            target_type="inspection_mission",
            target_id=mission.id,
            resource_owner_department=mission.primary_resource_owner_department,
            resource_type=ResourceType.DRONE,
            resource_object_id=mission.drone_id,
            after_data=data,
        )
        return Response(data, status=status.HTTP_201_CREATED)


class MissionDetailView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_missions_retrieve",
        summary="读取巡检任务详情",
        responses={200: OpenApiResponse(response=MissionReadSerializer, description="读取成功。")},
    )
    def get(self, request, id: int):
        context = resolve_v2_context(request)
        mission = get_visible_mission_or_404(context, id)
        return Response(MissionReadSerializer(mission).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_inspection_missions_update",
        summary="更新巡检任务",
        request=MissionWriteSerializer,
        responses={200: OpenApiResponse(response=MissionReadSerializer, description="更新成功。")},
    )
    @transaction.atomic
    def put(self, request, id: int):
        context = resolve_v2_context(request)
        require_dispatcher(context)
        mission = get_visible_mission_or_404(context, id)
        if mission.creator_department_id != context.department_id:
            raise StandardForbidden()
        if mission.status != MissionStatus.PENDING:
            raise StandardConstraintConflict(msg="只有待执行任务可以编辑")
        serializer = MissionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        route, pilot_account, drone, dock, executor, payload, bindings = _validated_mission_inputs(context, serializer.validated_data)
        before_data = MissionReadSerializer(mission).data
        mission.route = route
        mission.route_snapshot = route_snapshot(route)
        mission.name = serializer.validated_data["name"]
        mission.primary_resource_owner_department = bindings[0].owner_department
        mission.drone = drone
        mission.dock = dock
        mission.executor = executor
        mission.payload = payload
        mission.pilot_account_profile = pilot_account
        mission.scheduled_at = serializer.validated_data.get("scheduledAt")
        mission.remark = serializer.validated_data.get("remark", "")
        option_values = _mission_option_values(serializer.validated_data)
        for field, value in option_values.items():
            setattr(mission, field, value)
        mission.save(
            update_fields=[
                "route",
                "route_snapshot",
                "name",
                "primary_resource_owner_department",
                "drone",
                "dock",
                "executor",
                "payload",
                "pilot_account_profile",
                "scheduled_at",
                "remark",
                *option_values,
                "updated_at",
            ]
        )
        mission.clear_resource_assignments()
        create_assignments(mission, bindings)
        data = MissionReadSerializer(mission).data
        log_v2_action(
            request=request,
            context=context,
            action="update_inspection_mission",
            target_type="inspection_mission",
            target_id=mission.id,
            resource_owner_department=mission.primary_resource_owner_department,
            before_data=before_data,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_inspection_missions_delete",
        summary="删除巡检任务",
        description="删除待执行巡检任务；该接口只删除本地 PENDING 任务，不取消 DJI job，也不停止直播。",
        request=None,
        responses={200: OpenApiResponse(response=RouteDeleteResponseSerializer, description="删除成功。")},
    )
    @transaction.atomic
    def delete(self, request, id: int):
        context = resolve_v2_context(request)
        require_dispatcher(context)
        mission = get_visible_mission_or_404(context, id)
        if mission.creator_department_id != context.department_id:
            raise StandardForbidden()
        if mission.status != MissionStatus.PENDING:
            raise StandardConstraintConflict(msg="只有待执行任务可以删除")
        before_data = MissionReadSerializer(mission).data
        mission_id = mission.id
        owner_department = mission.primary_resource_owner_department
        mission.delete()
        log_v2_action(
            request=request,
            context=context,
            action="delete_inspection_mission",
            target_type="inspection_mission",
            target_id=mission_id,
            resource_owner_department=owner_department,
            before_data=before_data,
        )
        return Response({"id": mission_id, "deleted": True}, status=status.HTTP_200_OK)


class MissionStartView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_missions_start",
        summary="开始巡检任务",
        description="请求体固定为空对象或无请求体。",
        request=None,
        responses={200: OpenApiResponse(response=MissionReadSerializer, description="开始成功。")},
    )
    @transaction.atomic
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        mission = get_visible_mission_or_404(context, id)
        try:
            start_mission(mission=mission, context=context, request=request)
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        return Response(MissionReadSerializer(mission).data, status=status.HTTP_200_OK)


class MissionPreflightCheckView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_missions_preflight_check",
        summary="检查巡检任务是否可以启动",
        description=(
            "DJI 上游调用：该接口不会启动直播，也不会创建 DJI wayline flight task。"
            "它会基于本地 mission、航线 DJI 文件、资源绑定、在线状态和资源占用做启动前检查；"
            "当本地前置条件通过时，会额外调用 DJI live capacity 这个只读能力。"
            "`dockId` 表示机场自动执行，`executorId` 表示 Pilot2/遥控器手动执行，二者必须且只能一个。"
            "Dock 模式直播能力失败会阻断启动；Pilot2 模式直播失败只进入 warnings，不影响飞手在遥控器执行。"
        ),
        request=None,
        responses={200: OpenApiResponse(response=MissionPreflightCheckResponseSerializer, description="检查完成。")},
    )
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        mission = get_visible_mission_or_404(context, id)
        data = build_mission_preflight_check(mission=mission, context=context)
        return Response(data, status=status.HTTP_200_OK)


class MissionCloudExecutionRefreshView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_missions_cloud_execution_refresh",
        summary="刷新任务 DJI 云端执行状态",
        description="请求体固定为空对象或无请求体。",
        request=None,
        responses={200: OpenApiResponse(response=MissionReadSerializer, description="刷新成功。")},
    )
    @transaction.atomic
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        mission = get_visible_mission_or_404(context, id)
        try:
            mission = refresh_mission_cloud_execution_from_dji(mission=mission, context=context)
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        return Response(MissionReadSerializer(mission).data, status=status.HTTP_200_OK)


class MissionCompleteView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_missions_complete",
        summary="完成巡检任务",
        description="请求体固定为空对象或无请求体。",
        request=None,
        responses={200: OpenApiResponse(response=FlightRecordReadSerializer, description="完成成功。")},
    )
    @transaction.atomic
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        mission = get_visible_mission_or_404(context, id)
        record = complete_mission(mission=mission, context=context, request=request)
        return Response(FlightRecordReadSerializer(record).data, status=status.HTTP_200_OK)


class MissionCancelView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_missions_cancel",
        summary="取消巡检任务",
        request=MissionCloseSerializer,
        responses={200: OpenApiResponse(response=MissionReadSerializer, description="取消成功。")},
    )
    @transaction.atomic
    def post(self, request, id: int):
        from apps.inspection_v2.services import close_mission

        context = resolve_v2_context(request)
        mission = get_visible_mission_or_404(context, id)
        serializer = MissionCloseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            mission = close_mission(
                mission=mission,
                context=context,
                request=request,
                status_value=MissionStatus.CANCELED,
                reason=serializer.validated_data.get("reason", ""),
            )
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        return Response(MissionReadSerializer(mission).data, status=status.HTTP_200_OK)


class MissionFailView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_missions_fail",
        summary="标记巡检任务失败",
        request=MissionCloseSerializer,
        responses={200: OpenApiResponse(response=MissionReadSerializer, description="标记成功。")},
    )
    @transaction.atomic
    def post(self, request, id: int):
        from apps.inspection_v2.services import close_mission

        context = resolve_v2_context(request)
        mission = get_visible_mission_or_404(context, id)
        serializer = MissionCloseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mission = close_mission(
            mission=mission,
            context=context,
            request=request,
            status_value=MissionStatus.FAILED,
            reason=serializer.validated_data.get("reason", ""),
        )
        return Response(MissionReadSerializer(mission).data, status=status.HTTP_200_OK)


class MissionAbortView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_missions_abort",
        summary="安全中止巡检任务",
        request=MissionCloseSerializer,
        responses={200: OpenApiResponse(response=MissionReadSerializer, description="中止成功。")},
    )
    @transaction.atomic
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        mission = get_visible_mission_or_404(context, id)
        serializer = MissionCloseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mission = safety_abort_mission(
            mission=mission,
            context=context,
            request=request,
            reason=serializer.validated_data.get("reason", ""),
        )
        return Response(MissionReadSerializer(mission).data, status=status.HTTP_200_OK)


class ActiveFlightListView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_active_flights_list",
        summary="查询活动飞行列表",
        responses={200: OpenApiResponse(response=ACTIVE_FLIGHT_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        queryset = visible_sessions_queryset(context)
        serializer = ActiveFlightReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)


class ActiveFlightDetailView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_active_flights_retrieve",
        summary="读取活动飞行详情",
        responses={200: OpenApiResponse(response=ActiveFlightReadSerializer, description="读取成功。")},
    )
    def get(self, request, id: int):
        context = resolve_v2_context(request)
        session = visible_sessions_queryset(context).filter(pk=id).first()
        if session is None:
            raise StandardNotFound()
        return Response(ActiveFlightReadSerializer(session).data, status=status.HTTP_200_OK)


class TelemetrySnapshotView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_telemetry_snapshot_upsert",
        summary="上报飞行遥测快照",
        request=TelemetrySnapshotWriteSerializer,
        responses={200: OpenApiResponse(response=TelemetrySnapshotReadSerializer, description="上报成功。")},
    )
    @transaction.atomic
    def post(self, request):
        context = resolve_v2_context(request)
        serializer = TelemetrySnapshotWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = visible_sessions_queryset(context).filter(pk=serializer.validated_data["sessionId"]).first()
        if session is None:
            raise StandardNotFound()
        if not (is_dispatcher(context) or is_assigned_pilot(context, session.mission)):
            raise StandardForbidden()
        snapshot = update_telemetry_snapshot(session=session, payload=serializer.validated_data)
        return Response(TelemetrySnapshotReadSerializer(snapshot).data, status=status.HTTP_200_OK)


def _live_payload(validated_data: dict) -> dict:
    data = dict(validated_data)
    data.pop("droneId", None)
    data.pop("dockId", None)
    if "videoId" in data:
        data.setdefault("video_id", data.pop("videoId"))
    if "videoType" in data:
        data.setdefault("video_type", data.pop("videoType"))
    if "urlType" in data:
        data.setdefault("url_type", data.pop("urlType"))
    if "videoQuality" in data:
        data.setdefault("video_quality", data.pop("videoQuality"))
    return data


def _live_resource(validated_data: dict):
    if "droneId" in validated_data:
        return ResourceType.DRONE, validated_data["droneId"]
    return ResourceType.DOCK, validated_data["dockId"]


def _ensure_live_video_id_matches_resource(video_id: str, resource) -> None:
    if video_id.split("/", 1)[0] != resource.device_sn:
        raise StandardConstraintConflict(msg="videoId 与所选资源不匹配")


def _require_control_operator(context) -> None:
    if context.is_super_admin or is_dispatcher(context) or FixedRole.PILOT in context.role_codes:
        return
    raise StandardForbidden()


def _resource_binding_for_control(context, resource_type: str, resource_id: int):
    binding = usable_resource_binding(context, resource_type, resource_id)
    effective_permissions = set(effective_permissions_for_binding(context, binding))
    if not context.is_super_admin and not effective_permissions.intersection({"use", "dispatch_task"}):
        raise StandardForbidden()
    return binding


def _ensure_online(resource, message: str) -> None:
    if not getattr(resource, "online_status", False):
        raise StandardConstraintConflict(msg=message)


def _camera_action_response(*, operation: CameraOperation, upstream: dict) -> dict:
    return {
        "operationId": operation.id,
        "status": operation.status,
        "action": operation.action,
        "droneId": operation.drone_id,
        "droneSn": operation.drone.device_sn,
        "executorId": operation.executor_id,
        "gatewaySn": operation.executor.device_sn,
        "payloadIndex": operation.payload_index,
        "upstream": upstream,
    }


class LiveCapacityView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_live_capacity",
        summary="查询直播能力",
        parameters=[
            OpenApiParameter("droneId", int, OpenApiParameter.QUERY, required=False, description="无人机资源 ID；与 dockId 二选一。"),
            OpenApiParameter("dockId", int, OpenApiParameter.QUERY, required=False, description="机场资源 ID；与 droneId 二选一。"),
        ],
        responses={200: generic_object_response("查询成功。返回 DJI 直播能力数据。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        serializer = LiveCapacityQuerySerializer(data=request.query_params.dict())
        serializer.is_valid(raise_exception=True)
        resource_type, resource_id = (
            (ResourceType.DRONE, serializer.validated_data["droneId"])
            if "droneId" in serializer.validated_data
            else (ResourceType.DOCK, serializer.validated_data["dockId"])
        )
        binding = visible_resource_for_live(context, resource_id, resource_type=resource_type)
        resource = get_resource(resource_type, resource_id)
        _ensure_online(resource, "无人机不在线" if resource_type == ResourceType.DRONE else "机场不在线")
        try:
            data = dji_connection_gateway(binding.dji_connection).get_live_capacity(resource.device_sn)
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        return Response(data, status=status.HTTP_200_OK)


class LiveActionView(InspectionV2APIView):
    gateway_method = ""
    audit_action = ""
    serializer_class = LiveStartSerializer

    @extend_schema(
        request=LiveStartSerializer,
        responses={200: generic_object_response("操作成功。返回 DJI 直播操作结果。")},
    )
    def post(self, request):
        context = resolve_v2_context(request)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        resource_type, resource_id = _live_resource(serializer.validated_data)
        _require_control_operator(context)
        binding = _resource_binding_for_control(context, resource_type, resource_id)
        resource = get_resource(resource_type, resource_id)
        _ensure_online(resource, "无人机不在线" if resource_type == ResourceType.DRONE else "机场不在线")
        _ensure_live_video_id_matches_resource(serializer.validated_data["videoId"], resource)
        try:
            gateway = dji_connection_gateway(binding.dji_connection)
            data = getattr(gateway, self.gateway_method)(resource.device_sn, **_live_payload(serializer.validated_data))
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        log_v2_action(
            request=request,
            context=context,
            action=self.audit_action,
            target_type="live_stream",
            target_id=resource_id,
            resource_owner_department=binding.owner_department,
            resource_type=resource_type,
            resource_object_id=resource_id,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)


class LiveStartView(LiveActionView):
    gateway_method = "start_live"
    audit_action = "start_live_stream"
    serializer_class = LiveStartSerializer


class LiveStopView(LiveActionView):
    gateway_method = "stop_live"
    audit_action = "stop_live_stream"
    serializer_class = LiveStopSerializer

    @extend_schema(request=LiveStopSerializer)
    def post(self, request):
        return super().post(request)


class LiveUpdateView(LiveActionView):
    gateway_method = "update_live"
    audit_action = "update_live_stream"
    serializer_class = LiveUpdateSerializer

    @extend_schema(request=LiveUpdateSerializer)
    def post(self, request):
        return super().post(request)


class LiveSwitchView(LiveActionView):
    gateway_method = "switch_live"
    audit_action = "switch_live_stream"
    serializer_class = LiveSwitchSerializer

    @extend_schema(request=LiveSwitchSerializer)
    def post(self, request):
        return super().post(request)


class LiveCameraChangeView(InspectionV2APIView):
    @extend_schema(
        request=LiveCameraChangeSerializer,
        responses={200: generic_object_response("操作成功。返回 DJI 直播相机切换结果。")},
    )
    def post(self, request):
        context = resolve_v2_context(request)
        serializer = LiveCameraChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dock_id = serializer.validated_data["dockId"]
        _require_control_operator(context)
        binding = _resource_binding_for_control(context, ResourceType.DOCK, dock_id)
        dock = get_resource(ResourceType.DOCK, dock_id)
        _ensure_online(dock, "机场不在线")
        _ensure_live_video_id_matches_resource(serializer.validated_data["videoId"], dock)
        try:
            data = dji_connection_gateway(binding.dji_connection).change_live_camera(
                dock.device_sn,
                video_id=serializer.validated_data["videoId"],
                camera_position=serializer.validated_data["cameraPosition"],
            )
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        log_v2_action(
            request=request,
            context=context,
            action="camera_change_live_stream",
            target_type="live_stream",
            target_id=dock_id,
            resource_owner_department=binding.owner_department,
            resource_type=ResourceType.DOCK,
            resource_object_id=dock_id,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)


class CameraActionView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_camera_action",
        summary="控制 DJI 相机与云台",
        request=CameraActionSerializer,
        responses={200: OpenApiResponse(response=CameraActionResponseSerializer, description="操作成功。")},
    )
    def post(self, request):
        context = resolve_v2_context(request)
        _require_control_operator(context)
        serializer = CameraActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        drone_id = serializer.validated_data["droneId"]
        executor_id = serializer.validated_data["executorId"]
        drone_binding = _resource_binding_for_control(context, ResourceType.DRONE, drone_id)
        executor_binding = _resource_binding_for_control(context, ResourceType.GATEWAY, executor_id)
        if drone_binding.dji_connection_id != executor_binding.dji_connection_id:
            raise StandardConstraintConflict(msg="执行端必须与无人机属于同一个 DJI 连接")

        drone = get_resource(ResourceType.DRONE, drone_id)
        executor = get_resource(ResourceType.GATEWAY, executor_id)
        _ensure_online(drone, "无人机不在线")
        _ensure_online(executor, "执行端不在线")

        action = serializer.validated_data["action"]
        payload_index = serializer.validated_data["_payload_index"]
        command_data = serializer.validated_data["_dji_data"]
        operation = CameraOperation.objects.create(
            action=action,
            status=CameraOperationStatus.FAILED,
            drone=drone,
            executor=executor,
            payload_index=payload_index,
            dji_connection=drone_binding.dji_connection,
            actor=request.user,
            started_at=timezone.now(),
            upstream_request={
                "authority": {"gateway_sn": executor.device_sn, "payload_index": payload_index},
                "command": {"gateway_sn": executor.device_sn, "cmd": action, "data": command_data},
            },
        )
        gateway = dji_connection_gateway(drone_binding.dji_connection)
        authority_payload = None
        command_payload = None
        try:
            authority_payload = gateway.grab_payload_authority(executor.device_sn, payload_index)
            command_payload = gateway.send_payload_command(executor.device_sn, action, command_data)
        except DjiGatewayError as exc:
            operation.status = CameraOperationStatus.FAILED
            operation.completed_at = timezone.now()
            operation.upstream_response = {
                "authority": authority_payload if authority_payload is not None else {},
                "command": command_payload if command_payload is not None else {},
                "error": {
                    "detail": str(exc),
                    "upstreamStatus": getattr(exc, "status_code", 502),
                    "upstream": getattr(exc, "data", None),
                },
            }
            operation.error_message = str(exc)
            operation.save(update_fields=["status", "completed_at", "upstream_response", "error_message", "updated_at"])
            log_v2_action(
                request=request,
                context=context,
                action="camera_control_action",
                target_type="camera_operation",
                target_id=operation.id,
                resource_owner_department=drone_binding.owner_department,
                resource_type=ResourceType.DRONE,
                resource_object_id=drone.id,
                after_data={"operationId": operation.id, "action": action, "status": operation.status},
            )
            return _upstream_error_response(exc)

        upstream = {"authority": authority_payload, "command": command_payload}
        operation.status = CameraOperationStatus.SUCCEEDED
        operation.completed_at = timezone.now()
        operation.upstream_response = upstream
        operation.error_message = ""
        operation.save(update_fields=["status", "completed_at", "upstream_response", "error_message", "updated_at"])
        data = _camera_action_response(operation=operation, upstream=upstream)
        log_v2_action(
            request=request,
            context=context,
            action="camera_control_action",
            target_type="camera_operation",
            target_id=operation.id,
            resource_owner_department=drone_binding.owner_department,
            resource_type=ResourceType.DRONE,
            resource_object_id=drone.id,
            after_data={"operationId": operation.id, "action": action, "status": operation.status},
        )
        return Response(data, status=status.HTTP_200_OK)


class FlightRecordListView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_flight_records_list",
        summary="查询飞行记录列表",
        parameters=[
            OpenApiParameter("missionId", int, OpenApiParameter.QUERY, required=False, description="按任务 ID 精确过滤。"),
            OpenApiParameter("status", str, OpenApiParameter.QUERY, required=False, description="按飞行记录状态过滤。"),
        ],
        responses={200: OpenApiResponse(response=FLIGHT_RECORD_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        queryset = visible_records_queryset(context)
        mission_id = _int_query_param(request.query_params, "missionId")
        if mission_id:
            queryset = queryset.filter(mission_id=mission_id)
        status_value = str(request.query_params.get("status") or "").strip()
        if status_value:
            queryset = queryset.filter(status=status_value)
        records = list(queryset)
        serializer = FlightRecordReadSerializer(records, many=True)
        return Response({"list": serializer.data, "total": len(records)}, status=status.HTTP_200_OK)


class FlightRecordDetailView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_flight_records_retrieve",
        summary="读取飞行记录详情",
        responses={200: OpenApiResponse(response=FlightRecordReadSerializer, description="读取成功。")},
    )
    def get(self, request, id: int):
        context = resolve_v2_context(request)
        record = get_visible_record_or_404(context, id)
        maybe_sync_media_for_record_on_detail_read(record)
        record.refresh_from_db()
        return Response(FlightRecordReadSerializer(record).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_inspection_flight_records_update_note",
        summary="更新飞行记录备注",
        request=FlightRecordUpdateSerializer,
        responses={200: OpenApiResponse(response=FlightRecordReadSerializer, description="更新成功。")},
    )
    @transaction.atomic
    def put(self, request, id: int):
        context = resolve_v2_context(request)
        if not is_dispatcher(context):
            raise StandardForbidden()
        record = get_visible_record_or_404(context, id)
        serializer = FlightRecordUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before_data = FlightRecordReadSerializer(record).data
        record.remark = serializer.validated_data.get("remark", record.remark)
        record.abnormal_reason = serializer.validated_data.get("abnormalReason", record.abnormal_reason)
        record.save(update_fields=["remark", "abnormal_reason", "updated_at"])
        data = FlightRecordReadSerializer(record).data
        log_v2_action(
            request=request,
            context=context,
            action="update_flight_record_note",
            target_type="inspection_flight_record",
            target_id=record.id,
            resource_owner_department=record.primary_resource_owner_department,
            before_data=before_data,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)


class FlightRecordMediaRefreshView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_flight_records_refresh_media",
        summary="刷新飞行记录媒体",
        description="请求体固定为空对象或无请求体。",
        request=None,
        responses={200: OpenApiResponse(response=V2MediaRefreshSerializer, description="刷新成功。")},
    )
    @transaction.atomic
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        if not is_dispatcher(context):
            raise StandardForbidden()
        record = get_visible_record_or_404(context, id)
        try:
            data = sync_media_for_record_and_update_state(record=record)
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        log_v2_action(
            request=request,
            context=context,
            action="refresh_flight_record_media",
            target_type="inspection_flight_record",
            target_id=record.id,
            resource_owner_department=record.primary_resource_owner_department,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)


class MediaFileListView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_media_files_list",
        summary="查询云媒体文件列表",
        parameters=[
            OpenApiParameter("flightRecordId", int, OpenApiParameter.QUERY, required=False, description="按飞行记录 ID 精确过滤。"),
            OpenApiParameter("missionId", int, OpenApiParameter.QUERY, required=False, description="按任务 ID 精确过滤。"),
        ],
        responses={200: OpenApiResponse(response=MEDIA_FILE_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        flight_record_id = _int_query_param(request.query_params, "flightRecordId")
        mission_id = _int_query_param(request.query_params, "missionId")

        queryset = visible_media_queryset(context)
        if flight_record_id:
            queryset = queryset.filter(flight_record_id=flight_record_id)
        if mission_id:
            queryset = queryset.filter(mission_id=mission_id)
        media_files = list(queryset)
        try:
            media_files = ensure_cloud_media_access_urls(media_files)
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        serializer = CloudMediaFileReadSerializer(media_files, many=True)
        return Response({"list": serializer.data, "total": len(media_files)}, status=status.HTTP_200_OK)

class MediaFileDetailView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_media_files_retrieve",
        summary="读取云媒体文件详情",
        responses={200: OpenApiResponse(response=CloudMediaFileReadSerializer, description="读取成功。")},
    )
    def get(self, request, id: int):
        context = resolve_v2_context(request)
        media = visible_media_queryset(context).filter(pk=id).first()
        if media is None:
            raise StandardNotFound()
        if media.media_type == CloudMediaType.VIDEO:
            try:
                media = ensure_cloud_media_access_url(media)
            except DjiGatewayError as exc:
                return _upstream_error_response(exc)
            return Response(CloudMediaFileReadSerializer(media).data, status=status.HTTP_200_OK)
        try:
            media = ensure_cloud_media_preview_url(media)
        except (DjiGatewayError, StandardConstraintConflict):
            logger.warning(
                "failed to auto refresh media preview url",
                extra={
                    "media_id": media.id,
                    "cloud_file_id": media.cloud_file_id,
                    "workspace_id": media.workspace_id,
                },
                exc_info=True,
            )
        return Response(CloudMediaFileReadSerializer(media).data, status=status.HTTP_200_OK)


class MediaFileUrlRefreshView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_media_files_refresh_url",
        summary="刷新云媒体文件访问地址",
        request=CloudMediaFileUrlRefreshSerializer,
        responses={200: OpenApiResponse(response=CloudMediaFileReadSerializer, description="刷新成功。")},
    )
    @transaction.atomic
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        media = visible_media_queryset(context).filter(pk=id).first()
        if media is None:
            raise StandardNotFound()
        serializer = CloudMediaFileUrlRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            media = refresh_cloud_media_file_url(media=media, url_type=serializer.validated_data["urlType"])
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        return Response(CloudMediaFileReadSerializer(media).data, status=status.HTTP_200_OK)


class DrcCapabilityView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_drc_capabilities",
        summary="查询 Dock 3 DRC 能力",
        parameters=[OpenApiParameter("dockId", int, OpenApiParameter.QUERY, required=True)],
        responses={200: DrcCapabilityResponseSerializer},
    )
    def get(self, request):
        serializer = DrcCapabilityQuerySerializer(data=request.query_params.dict())
        serializer.is_valid(raise_exception=True)
        return Response(
            drc_capabilities(
                context=resolve_v2_context(request),
                dock_id=serializer.validated_data["dockId"],
            )
        )


class DrcConnectView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_drc_connect",
        summary="创建 Dock 3 DRC 代理会话并进入 DRC",
        request=DrcConnectSerializer,
        responses={200: DrcConnectResponseSerializer},
    )
    def post(self, request):
        serializer = DrcConnectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        context = resolve_v2_context(request)
        payload = connect_drc(context=context, data=serializer.validated_data)
        log_v2_action(
            request=request,
            context=context,
            action="drc_connect",
            target_type="dock",
            target_id=payload["dockId"],
            resource_type=ResourceType.DOCK,
            resource_object_id=payload["dockId"],
            after_data={"connected": True},
        )
        return Response(payload)


class DrcFlightActionView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_drc_flight_action",
        summary="执行 Dock 3 起飞或 FlyTo 动作",
        request=DrcFlightActionSerializer,
        responses={200: DrcFlightActionResponseSerializer},
    )
    def post(self, request):
        serializer = DrcFlightActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        context = resolve_v2_context(request)
        try:
            payload = execute_drc_flight_action(context=context, data=serializer.validated_data)
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        log_v2_action(
            request=request,
            context=context,
            action="drc_flight_action",
            target_type="dock",
            target_id=payload["dockId"],
            resource_type=ResourceType.DOCK,
            resource_object_id=payload["dockId"],
            after_data={"action": payload["action"], "status": payload["status"]},
        )
        return Response(payload)


class DrcDockDebugActionView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_drc_dock_debug_action",
        summary="执行 Dock 3 远程调试动作",
        request=DrcDockDebugActionSerializer,
        responses={200: DrcDockDebugActionResponseSerializer},
    )
    def post(self, request):
        serializer = DrcDockDebugActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        context = resolve_v2_context(request)
        try:
            payload = execute_dock_debug_action(context=context, data=serializer.validated_data)
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        log_v2_action(
            request=request,
            context=context,
            action="drc_dock_debug_action",
            target_type="dock",
            target_id=payload["dockId"],
            resource_type=ResourceType.DOCK,
            resource_object_id=payload["dockId"],
            after_data={"action": payload["action"], "status": payload["status"]},
        )
        return Response(payload)


class DrcExitView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_drc_exit",
        summary="退出 Dock 3 DRC",
        request=DrcExitSerializer,
        responses={200: DrcExitResponseSerializer},
    )
    def post(self, request):
        serializer = DrcExitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        context = resolve_v2_context(request)
        session_id = serializer.validated_data["sessionId"]
        dock_id = exit_drc(context=context, session_id=session_id)
        payload = {"status": "CLOSED"}
        log_v2_action(
            request=request,
            context=context,
            action="drc_exit",
            target_type="dock",
            target_id=dock_id,
            resource_type=ResourceType.DOCK,
            resource_object_id=dock_id,
            after_data=payload,
        )
        return Response(payload)
