import re
import uuid
import logging

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiRequest, OpenApiResponse, extend_schema
from rest_framework import parsers, serializers, status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.access.authentication import BearerAuthSessionAuthentication
from apps.access.api_base import EmptySerializer
from apps.access.exceptions import StandardConstraintConflict, StandardForbidden, StandardNotFound
from apps.api_v2.openapi import V2MediaRefreshSerializer, generic_object_response, list_data_serializer
from apps.common.api_response import BusinessApiResponseMixin, StandardCode, standard_error_payload
from apps.dji_cloud.gateway import DjiGatewayError
from apps.iam_v2.services import resolve_v2_context
from apps.inspection_v2.models import (
    InspectionMission,
    MissionStatus,
    Waypoint,
    WaypointRouteCloudFile,
    WaypointRoute,
)
from apps.inspection_v2.serializers import (
    ActiveFlightReadSerializer,
    CloudMediaFileReadSerializer,
    FlightRecordReadSerializer,
    FlightRecordUpdateSerializer,
    LiveActionSerializer,
    LiveCapacityQuerySerializer,
    MissionCloseSerializer,
    MissionReadSerializer,
    MissionWriteSerializer,
    RouteCloudFileReadSerializer,
    RouteKmzUploadSerializer,
    RouteReadSerializer,
    RouteWriteSerializer,
    TelemetrySnapshotReadSerializer,
    TelemetrySnapshotWriteSerializer,
)
from apps.inspection_v2.services import (
    complete_mission,
    assignable_routes_queryset,
    create_assignments,
    editable_routes_queryset,
    ensure_resources_available,
    get_editable_route_or_404,
    get_visible_mission_or_404,
    get_visible_record_or_404,
    get_visible_route_or_404,
    is_assigned_pilot,
    is_dispatcher,
    require_dispatcher,
    route_snapshot,
    safety_abort_mission,
    start_mission,
    sync_media_for_record,
    update_telemetry_snapshot,
    usable_resource_binding,
    visible_media_queryset,
    visible_missions_queryset,
    visible_records_queryset,
    visible_resource_for_live,
    visible_routes_queryset,
    visible_sessions_queryset,
)
from apps.resource_v2.audit import log_v2_action
from apps.resource_v2.gateway import DjiConnectionGateway
from apps.resource_v2.models import ResourceType
from apps.resource_v2.models import DjiConnection
from apps.resource_v2.services import get_resource
from apps.workforce_v2.services import pilot_has_effective_qualification, visible_pilots_queryset


logger = logging.getLogger(__name__)


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
            "defaultAltitude": {"type": "string", "format": "decimal", "nullable": True},
            "defaultSpeed": {"type": "string", "format": "decimal", "nullable": True},
            "coverImage": {"type": "string", "format": "binary"},
            "remark": {"type": "string"},
            "waypoints": {"type": "string", "description": "JSON 数组字符串。"},
        },
        "required": ["name", "waypoints"],
    },
    encoding={
        "coverImage": {"contentType": "image/jpeg, image/png, image/webp"},
        "waypoints": {"contentType": "application/json"},
    },
)


def _duplicate_response(errors=None):
    return Response(standard_error_payload(StandardCode.DUPLICATE, "资源已存在", errors), status=status.HTTP_409_CONFLICT)


def _upstream_error_response(exc: DjiGatewayError):
    error_text = f"{exc} {getattr(exc, 'data', '')}"
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
    route.waypoints.all().delete()
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
    if not old_cover_name or old_cover_name == route.cover_image.name:
        return
    try:
        route.cover_image.storage.delete(old_cover_name)
    except Exception:  # noqa: BLE001 - storage backends expose inconsistent deletion errors.
        logger.warning("failed to delete replaced route cover", extra={"route_id": route.id, "cover_name": old_cover_name}, exc_info=True)


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
        summary="创建巡检航线",
        request={
            "application/json": RouteWriteSerializer,
            "multipart/form-data": ROUTE_MULTIPART_WRITE_REQUEST,
        },
        responses={201: OpenApiResponse(response=RouteReadSerializer, description="创建成功。")},
    )
    @transaction.atomic
    def post(self, request):
        context = resolve_v2_context(request)
        require_dispatcher(context)
        serializer = RouteWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            route = WaypointRoute.objects.create(
                owner_department=context.department,
                name=serializer.validated_data["name"],
                status=serializer.validated_data.get("status", 1),
                default_altitude=serializer.validated_data.get("defaultAltitude"),
                default_speed=serializer.validated_data.get("defaultSpeed"),
                cover_image=serializer.validated_data.get("coverImage", ""),
                remark=serializer.validated_data.get("remark", ""),
                created_by_user=request.user,
            )
        except IntegrityError as exc:
            return _duplicate_response({"detail": str(exc)})
        _replace_waypoints(route, serializer.validated_data["waypoints"])
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
        return Response(RouteReadSerializer(route).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_inspection_routes_update",
        summary="更新巡检航线",
        request={
            "application/json": RouteWriteSerializer,
            "multipart/form-data": ROUTE_MULTIPART_WRITE_REQUEST,
        },
        responses={200: OpenApiResponse(response=RouteReadSerializer, description="更新成功。")},
    )
    @transaction.atomic
    def put(self, request, id: int):
        context = resolve_v2_context(request)
        require_dispatcher(context)
        route = get_editable_route_or_404(context, id)
        serializer = RouteWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before_data = RouteReadSerializer(route).data
        route.name = serializer.validated_data["name"]
        route.status = serializer.validated_data.get("status", route.status)
        route.default_altitude = serializer.validated_data.get("defaultAltitude")
        route.default_speed = serializer.validated_data.get("defaultSpeed")
        route.remark = serializer.validated_data.get("remark", "")
        old_cover_name = route.cover_image.name
        update_fields = ["name", "status", "default_altitude", "default_speed", "remark", "updated_at"]
        if "coverImage" in serializer.validated_data:
            route.cover_image = serializer.validated_data["coverImage"]
            update_fields.append("cover_image")
        try:
            route.save(update_fields=update_fields)
        except IntegrityError as exc:
            return _duplicate_response({"detail": str(exc)})
        _delete_replaced_route_cover(route, old_cover_name)
        _replace_waypoints(route, serializer.validated_data["waypoints"])
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


class RouteKmzView(InspectionV2APIView):
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    @extend_schema(
        operation_id="v2_inspection_routes_upload_kmz",
        summary="上传巡检航线 KMZ",
        request=RouteKmzUploadSerializer,
        responses={200: OpenApiResponse(response=RouteCloudFileReadSerializer, description="上传成功。")},
    )
    @transaction.atomic
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        require_dispatcher(context)
        route = get_editable_route_or_404(context, id)
        serializer = RouteKmzUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        connection = (
            DjiConnection.objects.select_related("owner_department")
            .filter(pk=serializer.validated_data["djiConnectionId"], owner_department=route.owner_department)
            .first()
        )
        if connection is None:
            raise StandardNotFound()
        gateway = DjiConnectionGateway(connection)
        upload_name = _safe_dji_upload_name(route_id=route.id)
        upload_file = _clone_kmz_for_dji_upload(serializer.validated_data["kmzFile"], upload_name=upload_name)
        try:
            upload_payload = gateway.upload_route(route_name=upload_name, file_obj=upload_file)
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        cloud_file, _created = WaypointRouteCloudFile.objects.update_or_create(
            route=route,
            defaults={
                "dji_connection": connection,
                "workspace_id": connection.workspace_id,
                "dji_file_id": str(upload_payload["dji_wayline_id"]),
                "wayline_type": serializer.validated_data["waylineType"],
                "download_url": str(upload_payload.get("download_url") or ""),
                "raw_response": upload_payload,
                "uploaded_by_user": request.user,
                "uploaded_at": timezone.now(),
            },
        )
        data = RouteCloudFileReadSerializer(cloud_file).data
        log_v2_action(
            request=request,
            context=context,
            action="upload_route_kmz",
            target_type="waypoint_route",
            target_id=route.id,
            resource_owner_department=route.owner_department,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_inspection_routes_replace_kmz",
        summary="替换巡检航线 KMZ",
        request=RouteKmzUploadSerializer,
        responses={200: OpenApiResponse(response=RouteCloudFileReadSerializer, description="替换成功。")},
    )
    def put(self, request, id: int):
        return self.post(request, id=id)


def _validated_mission_inputs(context, data):
    route = assignable_routes_queryset(context).filter(pk=data["routeId"]).first()
    if route is None:
        raise StandardNotFound()
    pilot = visible_pilots_queryset(context).filter(pk=data["pilotId"]).first()
    if pilot is None:
        raise StandardNotFound()
    if not pilot_has_effective_qualification(pilot):
        raise StandardConstraintConflict(msg="飞手缺少有效资质")
    drone_binding = usable_resource_binding(context, ResourceType.DRONE, data["droneId"])
    bindings = [drone_binding]
    drone = get_resource(ResourceType.DRONE, data["droneId"])
    dock = None
    executor = None
    payload = None
    dock_id = data.get("dockId")
    executor_id = data.get("executorId")
    payload_id = data.get("payloadId")
    if dock_id:
        bindings.append(usable_resource_binding(context, ResourceType.DOCK, dock_id))
        dock = get_resource(ResourceType.DOCK, dock_id)
    if executor_id:
        executor_binding = usable_resource_binding(context, ResourceType.GATEWAY, executor_id)
        if executor_binding.dji_connection_id != drone_binding.dji_connection_id:
            raise StandardConstraintConflict(msg="执行端必须与无人机属于同一个 DJI 连接")
        bindings.append(executor_binding)
        executor = get_resource(ResourceType.GATEWAY, executor_id)
    if payload_id:
        bindings.append(usable_resource_binding(context, ResourceType.PAYLOAD, payload_id))
        payload = get_resource(ResourceType.PAYLOAD, payload_id)
    ensure_resources_available(
        drone_id=drone.id,
        dock_id=dock.id if dock else None,
        executor_id=executor.id if executor else None,
        payload_id=payload.id if payload else None,
    )
    return route, pilot, drone, dock, executor, payload, bindings


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
        route, pilot, drone, dock, executor, payload, bindings = _validated_mission_inputs(context, serializer.validated_data)
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
            pilot=pilot,
            scheduled_at=serializer.validated_data.get("scheduledAt"),
            remark=serializer.validated_data.get("remark", ""),
            created_by_user=request.user,
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
        if mission.creator_department_id != context.department.id:
            raise StandardForbidden()
        if mission.status != MissionStatus.PENDING:
            raise StandardConstraintConflict(msg="只有待执行任务可以编辑")
        serializer = MissionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        route, pilot, drone, dock, executor, payload, bindings = _validated_mission_inputs(context, serializer.validated_data)
        before_data = MissionReadSerializer(mission).data
        mission.route = route
        mission.route_snapshot = route_snapshot(route)
        mission.name = serializer.validated_data["name"]
        mission.primary_resource_owner_department = bindings[0].owner_department
        mission.drone = drone
        mission.dock = dock
        mission.executor = executor
        mission.payload = payload
        mission.pilot = pilot
        mission.scheduled_at = serializer.validated_data.get("scheduledAt")
        mission.remark = serializer.validated_data.get("remark", "")
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
                "pilot",
                "scheduled_at",
                "remark",
                "updated_at",
            ]
        )
        mission.resource_assignments.all().delete()
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
    if "videoId" in data and "video_id" not in data:
        data["video_id"] = data.pop("videoId")
    if "urlType" in data and "url_type" not in data:
        data["url_type"] = data.pop("urlType")
    if "videoQuality" in data and "video_quality" not in data:
        data["video_quality"] = data.pop("videoQuality")
    return data


def _require_active_session_for_drone(context, drone_id: int):
    if not visible_sessions_queryset(context).filter(drone_id=drone_id).exists():
        raise StandardConstraintConflict(msg="当前无人机没有执行中的飞行会话")


class LiveCapacityView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_live_capacity",
        summary="查询直播能力",
        parameters=[OpenApiParameter("droneId", int, OpenApiParameter.QUERY, required=True, description="无人机资源 ID。")],
        responses={200: generic_object_response("查询成功。返回 DJI 直播能力数据。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        serializer = LiveCapacityQuerySerializer(data=request.query_params.dict())
        serializer.is_valid(raise_exception=True)
        drone_id = serializer.validated_data["droneId"]
        _require_active_session_for_drone(context, drone_id)
        binding = visible_resource_for_live(context, drone_id)
        try:
            data = DjiConnectionGateway(binding.dji_connection).get_live_capacity(get_resource(ResourceType.DRONE, drone_id).device_sn)
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        return Response(data, status=status.HTTP_200_OK)


class LiveActionView(InspectionV2APIView):
    gateway_method = ""
    audit_action = ""

    @extend_schema(
        request=LiveActionSerializer,
        responses={200: generic_object_response("操作成功。返回 DJI 直播操作结果。")},
    )
    def post(self, request):
        context = resolve_v2_context(request)
        serializer = LiveActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        drone_id = serializer.validated_data["droneId"]
        _require_active_session_for_drone(context, drone_id)
        binding = visible_resource_for_live(context, drone_id)
        drone = get_resource(ResourceType.DRONE, drone_id)
        try:
            gateway = DjiConnectionGateway(binding.dji_connection)
            data = getattr(gateway, self.gateway_method)(drone.device_sn, **_live_payload(serializer.validated_data))
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        log_v2_action(
            request=request,
            context=context,
            action=self.audit_action,
            target_type="live_stream",
            target_id=drone_id,
            resource_owner_department=binding.owner_department,
            resource_type=ResourceType.DRONE,
            resource_object_id=drone_id,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)


class LiveStartView(LiveActionView):
    gateway_method = "start_live"
    audit_action = "start_live_stream"


class LiveStopView(LiveActionView):
    gateway_method = "stop_live"
    audit_action = "stop_live_stream"


class LiveUpdateView(LiveActionView):
    gateway_method = "update_live"
    audit_action = "update_live_stream"


class LiveSwitchView(LiveActionView):
    gateway_method = "switch_live"
    audit_action = "switch_live_stream"


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
        serializer = FlightRecordReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)


class FlightRecordDetailView(InspectionV2APIView):
    @extend_schema(
        operation_id="v2_inspection_flight_records_retrieve",
        summary="读取飞行记录详情",
        responses={200: OpenApiResponse(response=FlightRecordReadSerializer, description="读取成功。")},
    )
    def get(self, request, id: int):
        context = resolve_v2_context(request)
        record = get_visible_record_or_404(context, id)
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
            data = sync_media_for_record(record=record)
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
        queryset = visible_media_queryset(context)
        flight_record_id = _int_query_param(request.query_params, "flightRecordId")
        if flight_record_id:
            queryset = queryset.filter(flight_record_id=flight_record_id)
        mission_id = _int_query_param(request.query_params, "missionId")
        if mission_id:
            queryset = queryset.filter(mission_id=mission_id)
        serializer = CloudMediaFileReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)


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
        return Response(CloudMediaFileReadSerializer(media).data, status=status.HTTP_200_OK)
