from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ErrorDetail
from rest_framework.response import Response
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission, ScopedQuerysetMixin
from apps.access.services import IdentityService, log_action, snapshot
from apps.api_v1.business_response import (
    BusinessApiResponseMixin,
    StandardCode,
    standard_error_payload,
    validation_error_payload,
)
from apps.api_v1.schema import (
    BUSINESS_DUPLICATE_RESPONSE,
    BUSINESS_INVALID_PARAMS_RESPONSE,
    BUSINESS_INTERNAL_ERROR_RESPONSE,
    BUSINESS_NOT_FOUND_RESPONSE,
    BUSINESS_PERMISSION_DENIED_RESPONSE,
    TENANT_CODE_HEADER_PARAMETER,
    object_envelope_serializer,
    paginated_envelope_serializer,
)
from apps.api_v1.tenant_scope import TenantScopedBusinessMixin
from apps.dji_bff.gateway import DjiGateway
from apps.dji_bff.models import DjiDeviceIndex
from apps.drone.models import Drone
from apps.drone_assignment.models import DroneAssignmentStatus
from apps.access.models import ScopeType
from apps.drone.serializers import (
    AvailableDroneReadSerializer,
    DroneClaimSerializer,
    DroneLiveStartSerializer,
    DroneLiveStopSerializer,
    DroneLiveVideoQualitySerializer,
    DroneLiveVideoSourceSerializer,
    DroneReadSerializer,
    DroneUpdateSerializer,
)

DRONE_LIST_RESPONSE = paginated_envelope_serializer("DroneListResponse", DroneReadSerializer)
DRONE_DETAIL_RESPONSE = object_envelope_serializer("DroneDetailResponse", DroneReadSerializer)
AVAILABLE_DRONE_LIST_RESPONSE = paginated_envelope_serializer("AvailableDroneListResponse", AvailableDroneReadSerializer)

DRONE_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(name="code", type=str, location=OpenApiParameter.QUERY, description="按本地编码模糊匹配。"),
    OpenApiParameter(name="name", type=str, location=OpenApiParameter.QUERY, description="按展示名称模糊匹配。"),
    OpenApiParameter(name="model", type=str, location=OpenApiParameter.QUERY, description="按型号模糊匹配。"),
    OpenApiParameter(name="device_sn", type=str, location=OpenApiParameter.QUERY, description="按设备 SN 精确过滤。"),
]

def _drone_duplicate_or_validation_error_response(view, errors):
    if view._contains_duplicate_error(errors):
        return Response(
            standard_error_payload(StandardCode.DUPLICATE, "资源已存在", errors),
            status=status.HTTP_409_CONFLICT,
        )
    return Response(validation_error_payload(errors), status=status.HTTP_400_BAD_REQUEST)


def _drone_validate_or_respond(view, serializer):
    serializer.is_valid(raise_exception=False)
    if serializer.errors:
        return _drone_duplicate_or_validation_error_response(view, serializer.errors)
    return None


def _drone_success_response(view, drone: Drone, *, http_status: int, include_headers: bool = False):
    payload = view._payload(drone)
    if include_headers:
        headers = view.get_success_headers(payload)
        return Response(payload, status=http_status, headers=headers)
    return Response(payload, status=http_status)


@extend_schema_view(
    list=extend_schema(
        summary="查询当前租户已认领设备",
        parameters=DRONE_FILTER_PARAMETERS,
        responses={
            200: OpenApiResponse(response=DRONE_LIST_RESPONSE),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    ),
    retrieve=extend_schema(
        summary="读取设备详情",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=DRONE_DETAIL_RESPONSE),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    ),
    create=extend_schema(
        summary="认领已绑定设备",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=DroneClaimSerializer,
        responses={
            201: OpenApiResponse(response=DRONE_DETAIL_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            409: BUSINESS_DUPLICATE_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    ),
    update=extend_schema(
        summary="全量更新本地管理字段",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=DroneUpdateSerializer,
        responses={
            200: OpenApiResponse(response=DRONE_DETAIL_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            409: BUSINESS_DUPLICATE_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    ),
    partial_update=extend_schema(
        summary="局部更新本地管理字段",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=DroneUpdateSerializer,
        responses={
            200: OpenApiResponse(response=DRONE_DETAIL_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            409: BUSINESS_DUPLICATE_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    ),
)
class DroneViewSet(
    BusinessApiResponseMixin,
    TenantScopedBusinessMixin,
    PermissionMapMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Drone.objects.all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    permission_map = {
        "list": "drone.view_drone",
        "retrieve": "drone.view_drone",
        "available": "drone.view_drone",
        "live_capacity": "drone.view_drone",
        "create": "drone.manage_drone",
        "update": "drone.manage_drone",
        "partial_update": "drone.manage_drone",
        "live_start": "drone.manage_drone",
        "live_stop": "drone.manage_drone",
        "live_video_quality": "drone.manage_drone",
        "live_video_source": "drone.manage_drone",
    }

    @staticmethod
    def assigned_scope_filter_builder(tenant_member_id: int) -> dict:
        return {
            "assignments__tenant_member_id": tenant_member_id,
            "assignments__status": DroneAssignmentStatus.ACTIVE,
        }

    def get_serializer_class(self):
        if self.action == "create":
            return DroneClaimSerializer
        if self.action in {"update", "partial_update"}:
            return DroneUpdateSerializer
        if self.action == "live_start":
            return DroneLiveStartSerializer
        if self.action == "live_stop":
            return DroneLiveStopSerializer
        if self.action == "live_video_quality":
            return DroneLiveVideoQualitySerializer
        if self.action == "live_video_source":
            return DroneLiveVideoSourceSerializer
        return DroneReadSerializer

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset())
        params = self.request.query_params

        if params.get("code"):
            queryset = queryset.filter(code__icontains=params["code"])
        if params.get("name"):
            queryset = queryset.filter(name__icontains=params["name"])
        if params.get("model"):
            queryset = queryset.filter(model__icontains=params["model"])
        if params.get("device_sn"):
            queryset = queryset.filter(device_sn=params["device_sn"])
        if self.action in {"list", "retrieve", "update", "partial_update", "live_capacity", "live_start", "live_stop", "live_video_quality", "live_video_source"}:
            return self.apply_scope(queryset)
        return queryset

    @staticmethod
    def _contains_duplicate_error(errors) -> bool:
        if isinstance(errors, dict):
            return any(DroneViewSet._contains_duplicate_error(value) for value in errors.values())
        if isinstance(errors, list):
            return any(DroneViewSet._contains_duplicate_error(item) for item in errors)
        if isinstance(errors, ErrorDetail):
            text = str(errors).lower()
            return getattr(errors, "code", "") == "unique" or any(
                marker in text for marker in ("已存在", "already exists", "认领", "claimed", "unique")
            )
        if isinstance(errors, str):
            text = errors.lower()
            return any(marker in text for marker in ("已存在", "already exists", "认领", "claimed", "unique"))
        return False

    def _payload(self, drone: Drone) -> dict:
        return dict(DroneReadSerializer(drone, context={"request": self.request}).data)

    @extend_schema(
        summary="查询可认领已绑定设备",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=AVAILABLE_DRONE_LIST_RESPONSE),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    )
    @action(detail=False, methods=["get"], url_path="available")
    def available(self, request, *args, **kwargs):
        decision = getattr(request, "_authz_decision", None)
        if decision is not None and decision.scope != ScopeType.ALL:
            return Response({"list": [], "total": 0}, status=status.HTTP_200_OK)

        claimed_device_sns = list(Drone.objects.values_list("device_sn", flat=True))
        queryset = DjiDeviceIndex.objects.exclude(device_sn__in=claimed_device_sns).order_by("device_sn")
        page = self.paginate_queryset(queryset)
        serializer = AvailableDroneReadSerializer(page, many=True, context={"request": request})
        return self.get_paginated_response(serializer.data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        error_response = _drone_validate_or_respond(self, serializer)
        if error_response is not None:
            return error_response

        drone = self.perform_create(serializer)
        return _drone_success_response(self, drone, http_status=status.HTTP_201_CREATED, include_headers=True)

    @transaction.atomic
    def perform_create(self, serializer):
        tenant = self.get_current_tenant()
        tenant_member = IdentityService.get_active_tenant_member(self.request.user, tenant)
        drone = serializer.save(
            tenant=tenant,
            created_by_tenant_member_id=tenant_member.id if tenant_member else None,
        )
        log_action(
            request=self.request,
            action="DRONE_CLAIM",
            target_type="drone",
            target_id=drone.id,
            after_data=snapshot(drone),
        )
        return drone

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data)
        error_response = _drone_validate_or_respond(self, serializer)
        if error_response is not None:
            return error_response

        drone = self.perform_update(serializer)
        return _drone_success_response(self, drone, http_status=status.HTTP_200_OK)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        error_response = _drone_validate_or_respond(self, serializer)
        if error_response is not None:
            return error_response

        drone = self.perform_update(serializer)
        return _drone_success_response(self, drone, http_status=status.HTTP_200_OK)

    @transaction.atomic
    def perform_update(self, serializer):
        drone = self.get_object()
        before_data = snapshot(drone)
        drone = serializer.save()
        log_action(
            request=self.request,
            action="DRONE_UPDATE",
            target_type="drone",
            target_id=drone.id,
            before_data=before_data,
            after_data=snapshot(drone),
        )
        return drone

    @extend_schema(
        summary="查询设备直播能力",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=OpenApiTypes.OBJECT),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    )
    @action(detail=True, methods=["get"], url_path="live/capacity")
    def live_capacity(self, request, *args, **kwargs):
        drone = self.get_object()
        payload = DjiGateway().get_live_capacity(drone.device_sn)
        log_action(
            request=request,
            action="DRONE_LIVE_CAPACITY",
            target_type="drone",
            target_id=drone.id,
            after_data=payload if isinstance(payload, dict) else {"result": payload},
        )
        return Response(payload, status=status.HTTP_200_OK)

    @extend_schema(
        summary="启动直播",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=DroneLiveStartSerializer,
        responses={
            200: OpenApiResponse(response=OpenApiTypes.OBJECT),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    )
    @action(detail=True, methods=["post"], url_path="live/start")
    def live_start(self, request, *args, **kwargs):
        drone = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = dict(serializer.validated_data)
        payload["video_id"] = f"{drone.device_sn}/{payload.pop('camera_index')}/{payload.pop('video_index')}"
        result = DjiGateway().start_live(drone.device_sn, **payload)
        log_action(
            request=request,
            action="DRONE_LIVE_START",
            target_type="drone",
            target_id=drone.id,
            after_data=result if isinstance(result, dict) else {"result": result},
        )
        return Response(result, status=status.HTTP_200_OK)

    @extend_schema(
        summary="停止直播",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=DroneLiveStopSerializer,
        responses={
            200: OpenApiResponse(response=OpenApiTypes.OBJECT),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    )
    @action(detail=True, methods=["post"], url_path="live/stop")
    def live_stop(self, request, *args, **kwargs):
        drone = self.get_object()
        serializer = self.get_serializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        result = DjiGateway().stop_live(drone.device_sn, **serializer.validated_data)
        log_action(
            request=request,
            action="DRONE_LIVE_STOP",
            target_type="drone",
            target_id=drone.id,
            after_data=result if isinstance(result, dict) else {"result": result},
        )
        return Response(result, status=status.HTTP_200_OK)

    @extend_schema(
        summary="调整直播画质",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=DroneLiveVideoQualitySerializer,
        responses={
            200: OpenApiResponse(response=OpenApiTypes.OBJECT),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    )
    @action(detail=True, methods=["post"], url_path="live/video-quality")
    def live_video_quality(self, request, *args, **kwargs):
        drone = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = DjiGateway().set_live_video_quality(drone.device_sn, **serializer.validated_data)
        log_action(
            request=request,
            action="DRONE_LIVE_VIDEO_QUALITY",
            target_type="drone",
            target_id=drone.id,
            after_data=result if isinstance(result, dict) else {"result": result},
        )
        return Response(result, status=status.HTTP_200_OK)

    @extend_schema(
        summary="切换直播视频源",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=DroneLiveVideoSourceSerializer,
        responses={
            200: OpenApiResponse(response=OpenApiTypes.OBJECT),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    )
    @action(detail=True, methods=["post"], url_path="live/video-source")
    def live_video_source(self, request, *args, **kwargs):
        drone = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = DjiGateway().set_live_video_source(drone.device_sn, **serializer.validated_data)
        log_action(
            request=request,
            action="DRONE_LIVE_VIDEO_SOURCE",
            target_type="drone",
            target_id=drone.id,
            after_data=result if isinstance(result, dict) else {"result": result},
        )
        return Response(result, status=status.HTTP_200_OK)
