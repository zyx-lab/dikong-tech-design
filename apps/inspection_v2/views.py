from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import parsers, serializers, status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.access.api_v1.authentication import BearerAuthSessionAuthentication
from apps.access.api_v1.base import EmptySerializer
from apps.access.exceptions import StandardConstraintConflict, StandardForbidden, StandardNotFound
from apps.api_v1.business_response import BusinessApiResponseMixin, StandardCode, standard_error_payload
from apps.dji_bff.gateway import DjiGatewayError
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


class InspectionV2APIView(BusinessApiResponseMixin, GenericAPIView):
    authentication_classes = [BearerAuthSessionAuthentication]
    serializer_class = EmptySerializer


def _duplicate_response(errors=None):
    return Response(standard_error_payload(StandardCode.DUPLICATE, "资源已存在", errors), status=status.HTTP_409_CONFLICT)


def _upstream_error_response(exc: DjiGatewayError):
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


class RouteListCreateView(InspectionV2APIView):
    @extend_schema(operation_id="v2_inspection_routes_list", responses=RouteReadSerializer)
    def get(self, request):
        context = resolve_v2_context(request)
        queryset = visible_routes_queryset(context)
        keywords = str(request.query_params.get("keywords") or "").strip()
        if keywords:
            queryset = queryset.filter(name__icontains=keywords)
        serializer = RouteReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)

    @extend_schema(operation_id="v2_inspection_routes_create", request=RouteWriteSerializer, responses=RouteReadSerializer)
    @transaction.atomic
    def post(self, request):
        context = resolve_v2_context(request)
        require_dispatcher(context)
        serializer = RouteWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            route = WaypointRoute.objects.create(
                tenant=context.department.tenant,
                owner_department=context.department,
                name=serializer.validated_data["name"],
                status=serializer.validated_data.get("status", 1),
                default_altitude=serializer.validated_data.get("defaultAltitude"),
                default_speed=serializer.validated_data.get("defaultSpeed"),
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
    @extend_schema(operation_id="v2_inspection_routes_retrieve", responses=RouteReadSerializer)
    def get(self, request, id: int):
        context = resolve_v2_context(request)
        route = get_visible_route_or_404(context, id)
        return Response(RouteReadSerializer(route).data, status=status.HTTP_200_OK)

    @extend_schema(operation_id="v2_inspection_routes_update", request=RouteWriteSerializer, responses=RouteReadSerializer)
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
        try:
            route.save(update_fields=["name", "status", "default_altitude", "default_speed", "remark", "updated_at"])
        except IntegrityError as exc:
            return _duplicate_response({"detail": str(exc)})
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

    @extend_schema(operation_id="v2_inspection_routes_upload_kmz", request=RouteKmzUploadSerializer, responses=RouteCloudFileReadSerializer)
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
        try:
            upload_payload = gateway.upload_route(route_name=f"v2-{route.id}-{route.name}", file_obj=serializer.validated_data["kmzFile"])
        except DjiGatewayError as exc:
            return _upstream_error_response(exc)
        cloud_file, _created = WaypointRouteCloudFile.objects.update_or_create(
            route=route,
            defaults={
                "dji_connection": connection,
                "workspace_id": connection.workspace_id,
                "dji_file_id": str(upload_payload["dji_wayline_id"]),
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

    @extend_schema(operation_id="v2_inspection_routes_replace_kmz", request=RouteKmzUploadSerializer, responses=RouteCloudFileReadSerializer)
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
    @extend_schema(operation_id="v2_inspection_missions_list", responses=MissionReadSerializer)
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

    @extend_schema(operation_id="v2_inspection_missions_create", request=MissionWriteSerializer, responses=MissionReadSerializer)
    @transaction.atomic
    def post(self, request):
        context = resolve_v2_context(request)
        require_dispatcher(context)
        serializer = MissionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        route, pilot, drone, dock, executor, payload, bindings = _validated_mission_inputs(context, serializer.validated_data)
        mission = InspectionMission.objects.create(
            tenant=context.department.tenant,
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
    @extend_schema(operation_id="v2_inspection_missions_retrieve", responses=MissionReadSerializer)
    def get(self, request, id: int):
        context = resolve_v2_context(request)
        mission = get_visible_mission_or_404(context, id)
        return Response(MissionReadSerializer(mission).data, status=status.HTTP_200_OK)

    @extend_schema(operation_id="v2_inspection_missions_update", request=MissionWriteSerializer, responses=MissionReadSerializer)
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
    @extend_schema(operation_id="v2_inspection_missions_start", responses=MissionReadSerializer)
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
    @extend_schema(operation_id="v2_inspection_missions_complete", responses=FlightRecordReadSerializer)
    @transaction.atomic
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        mission = get_visible_mission_or_404(context, id)
        record = complete_mission(mission=mission, context=context, request=request)
        return Response(FlightRecordReadSerializer(record).data, status=status.HTTP_200_OK)


class MissionCancelView(InspectionV2APIView):
    @extend_schema(operation_id="v2_inspection_missions_cancel", request=MissionCloseSerializer, responses=MissionReadSerializer)
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
    @extend_schema(operation_id="v2_inspection_missions_fail", request=MissionCloseSerializer, responses=MissionReadSerializer)
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
    @extend_schema(operation_id="v2_inspection_missions_abort", request=MissionCloseSerializer, responses=MissionReadSerializer)
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
    @extend_schema(operation_id="v2_inspection_active_flights_list", responses=ActiveFlightReadSerializer)
    def get(self, request):
        context = resolve_v2_context(request)
        queryset = visible_sessions_queryset(context)
        serializer = ActiveFlightReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)


class ActiveFlightDetailView(InspectionV2APIView):
    @extend_schema(operation_id="v2_inspection_active_flights_retrieve", responses=ActiveFlightReadSerializer)
    def get(self, request, id: int):
        context = resolve_v2_context(request)
        session = visible_sessions_queryset(context).filter(pk=id).first()
        if session is None:
            raise StandardNotFound()
        return Response(ActiveFlightReadSerializer(session).data, status=status.HTTP_200_OK)


class TelemetrySnapshotView(InspectionV2APIView):
    @extend_schema(operation_id="v2_inspection_telemetry_snapshot_upsert", request=TelemetrySnapshotWriteSerializer, responses=TelemetrySnapshotReadSerializer)
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
    @extend_schema(operation_id="v2_inspection_live_capacity")
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
    @extend_schema(operation_id="v2_inspection_flight_records_list", responses=FlightRecordReadSerializer)
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
    @extend_schema(operation_id="v2_inspection_flight_records_retrieve", responses=FlightRecordReadSerializer)
    def get(self, request, id: int):
        context = resolve_v2_context(request)
        record = get_visible_record_or_404(context, id)
        return Response(FlightRecordReadSerializer(record).data, status=status.HTTP_200_OK)

    @extend_schema(operation_id="v2_inspection_flight_records_update_note", request=FlightRecordUpdateSerializer, responses=FlightRecordReadSerializer)
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
    @extend_schema(operation_id="v2_inspection_flight_records_refresh_media")
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
    @extend_schema(operation_id="v2_inspection_media_files_list", responses=CloudMediaFileReadSerializer)
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
    @extend_schema(operation_id="v2_inspection_media_files_retrieve", responses=CloudMediaFileReadSerializer)
    def get(self, request, id: int):
        context = resolve_v2_context(request)
        media = visible_media_queryset(context).filter(pk=id).first()
        if media is None:
            raise StandardNotFound()
        return Response(CloudMediaFileReadSerializer(media).data, status=status.HTTP_200_OK)
