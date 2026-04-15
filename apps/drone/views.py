from django.db import IntegrityError, transaction
from django.utils import timezone
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
    BusinessDeleteResultSerializer,
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
from apps.dji_bff.gateway import DjiGateway, DjiGatewayUpstreamError
from apps.dji_bff.models import DjiDeviceIndex
from apps.drone.models import Drone, DroneStatus
from apps.drone_assignment.models import DroneAssignmentStatus
from apps.access.models import ScopeType
from apps.drone.serializers import (
    AvailableDroneReadSerializer,
    DroneClaimSerializer,
    DroneLiveStreamSerializer,
    DroneReadSerializer,
    DroneUpdateSerializer,
)

DRONE_LIST_RESPONSE = paginated_envelope_serializer("DroneListResponse", DroneReadSerializer)
DRONE_DETAIL_RESPONSE = object_envelope_serializer("DroneDetailResponse", DroneReadSerializer)
DRONE_DELETE_RESPONSE = object_envelope_serializer("DroneDeleteResponse", BusinessDeleteResultSerializer)
AVAILABLE_DRONE_LIST_RESPONSE = paginated_envelope_serializer("AvailableDroneListResponse", AvailableDroneReadSerializer)

DRONE_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(name="code", type=str, location=OpenApiParameter.QUERY, description="按本地编码模糊匹配。"),
    OpenApiParameter(name="name", type=str, location=OpenApiParameter.QUERY, description="按展示名称模糊匹配。"),
    OpenApiParameter(name="model", type=str, location=OpenApiParameter.QUERY, description="按型号模糊匹配。"),
    OpenApiParameter(name="device_sn", type=str, location=OpenApiParameter.QUERY, description="按设备 SN 精确过滤。"),
]

LIVE_PASSTHROUGH_DESCRIPTION = (
    "该组接口为 DJI 直播能力的薄代理，不再做 `camera_index` / `video_index` -> `video_id` 的本地映射。"
    "请求体字段名与 DJI `/api/v1/manage/live/streams/*` 保持一致，当前支持传入 "
    "`video_id`、`url_type`、`video_quality`、`videoType`。"
    "不属于当前接口的字段会在 Django 层直接返回 `400 + B0001`。"
)

LIVE_ERROR_DESCRIPTION = (
    "错误语义：`400` 表示请求字段缺失、字段名错误，或上游明确返回参数错误；"
    "`404` 表示当前设备在 Django 租户侧不存在，或 `live/capacity` 未找到该设备能力；"
    "`502` 表示 DJI 上游超时、不可达，或上游直播服务异常。"
)

LIVE_START_DESCRIPTION = (
    LIVE_PASSTHROUGH_DESCRIPTION
    + "推荐先调用 `/live/capacity` 观察上游是否返回直播能力，再调用 `/live/start`。"
    "常见调用体至少包含 `video_id`，通常同时传入 `url_type` 与 `video_quality`。"
    "示例："
    "`{\"video_id\":\"1581F7FVC252A00CJ5TT/88-0-0/normal-0\",\"url_type\":1,\"video_quality\":0}`。"
    + LIVE_ERROR_DESCRIPTION
)

LIVE_STOP_DESCRIPTION = (
    LIVE_PASSTHROUGH_DESCRIPTION
    + "`/live/stop` 通常只需要 `video_id`。"
    "示例：`{\"video_id\":\"1581F7FVC252A00CJ5TT/88-0-0/normal-0\"}`。"
    + LIVE_ERROR_DESCRIPTION
)

LIVE_UPDATE_DESCRIPTION = (
    LIVE_PASSTHROUGH_DESCRIPTION
    + "`/live/update` 用于把请求体原样透传到 DJI `/manage/live/streams/update`。"
    "当前常见用法是传 `video_id` 与 `video_quality`。"
    "示例：`{\"video_id\":\"1581F7FVC252A00CJ5TT/88-0-0/normal-0\",\"video_quality\":3}`。"
    + LIVE_ERROR_DESCRIPTION
)

LIVE_SWITCH_DESCRIPTION = (
    LIVE_PASSTHROUGH_DESCRIPTION
    + "`/live/switch` 用于把请求体原样透传到 DJI `/manage/live/streams/switch`。"
    "当前常见用法是传 `video_id` 与 `videoType`。"
    "示例：`{\"video_id\":\"1581F7FVC252A00CJ5TT/88-0-0/normal-0\",\"videoType\":\"wide\"}`。"
    + LIVE_ERROR_DESCRIPTION
)

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


def _dji_live_action_error_response(exc: DjiGatewayUpstreamError):
    payload = dict(exc.data) if isinstance(exc.data, dict) else {}
    detail = str(payload.get("msg") or exc).strip() or "DJI upstream error"
    message = detail.lower()
    is_invalid_params = exc.status_code == status.HTTP_400_BAD_REQUEST or any(
        marker in message
        for marker in (
            "invalid parameter",
            "parameters are abnormal or incomplete",
            "parameter..",
            "must not be null",
            "incomplete",
            "210002",
        )
    )
    resolved_status = exc.status_code if exc.status_code >= 400 else (
        status.HTTP_400_BAD_REQUEST if is_invalid_params else status.HTTP_502_BAD_GATEWAY
    )
    detail_data = {
        "detail": detail,
        "upstream_status": exc.status_code,
    }
    if payload:
        raw_data = payload.get("data")
        if isinstance(raw_data, dict):
            enriched_data = dict(raw_data)
            enriched_data.setdefault("detail", detail)
            enriched_data.setdefault("upstream_status", exc.status_code)
        else:
            enriched_data = dict(detail_data)
            if raw_data not in (None, ""):
                enriched_data["upstream_data"] = raw_data
        payload["data"] = enriched_data
        if not str(payload.get("msg") or "").strip():
            payload["msg"] = detail
        return Response(payload, status=resolved_status)
    return Response(
        standard_error_payload(StandardCode.INTERNAL_ERROR, detail, detail_data),
        status=resolved_status,
    )


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
        summary="更新本地管理字段",
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
    destroy=extend_schema(
        summary="解除认领设备",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=DRONE_DELETE_RESPONSE),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
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
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Drone.objects.all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "put", "delete", "head", "options"]

    permission_map = {
        "list": "drone.view_drone",
        "retrieve": "drone.view_drone",
        "available": "drone.view_drone",
        "live_capacity": "drone.view_drone",
        "create": "drone.manage_drone",
        "update": "drone.manage_drone",
        "destroy": "drone.manage_drone",
        "live_start": "drone.manage_drone",
        "live_stop": "drone.manage_drone",
        "live_update": "drone.manage_drone",
        "live_switch": "drone.manage_drone",
    }

    @staticmethod
    def assigned_scope_filter_builder(tenant_member_id: int) -> dict:
        return {
            "assignments__tenant_member_id": tenant_member_id,
            "assignments__status": DroneAssignmentStatus.ACTIVE,
        }

    def get_serializer_class(self):
        serializer_map = {
            "create": DroneClaimSerializer,
            "update": DroneUpdateSerializer,
            "live_start": DroneLiveStreamSerializer,
            "live_stop": DroneLiveStreamSerializer,
            "live_update": DroneLiveStreamSerializer,
            "live_switch": DroneLiveStreamSerializer,
        }
        return serializer_map.get(self.action, DroneReadSerializer)

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset())
        params = self.request.query_params

        for param, lookup in (
            ("code", "code__icontains"),
            ("name", "name__icontains"),
            ("model", "model__icontains"),
            ("device_sn", "device_sn"),
        ):
            value = params.get(param)
            if value:
                queryset = queryset.filter(**{lookup: value})
        if self.action in {"list", "retrieve", "update", "destroy", "live_capacity", "live_start", "live_stop", "live_update", "live_switch"}:
            queryset = queryset.exclude(status=DroneStatus.RELEASED)
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

    def _duplicate_integrity_response(self, serializer, exc: IntegrityError):
        error_text = str(exc).lower()
        if any(marker in error_text for marker in ("uniq_drone_device_sn_global", "drones.device_sn")):
            device_sn = serializer.validated_data.get("device_sn")
            existing_drone = Drone.objects.exclude(status=DroneStatus.RELEASED).filter(device_sn=device_sn).first()
            current_tenant = self.get_current_tenant()
            if existing_drone is not None and existing_drone.tenant_id == current_tenant.id:
                errors = {"device_sn": ["当前租户下已认领该设备"]}
            else:
                errors = {"device_sn": ["该设备已被其他租户认领"]}
            return Response(
                standard_error_payload(StandardCode.DUPLICATE, "资源已存在", errors),
                status=status.HTTP_409_CONFLICT,
            )

        if any(marker in error_text for marker in ("uniq_drone_tenant_code", "drones.tenant_id, drones.code")):
            return Response(
                standard_error_payload(
                    StandardCode.DUPLICATE,
                    "资源已存在",
                    {"code": ["当前租户下已存在相同业务编码"]},
                ),
                status=status.HTTP_409_CONFLICT,
            )

        raise exc

    def _payload(self, drone: Drone) -> dict:
        return dict(DroneReadSerializer(drone, context={"request": self.request}).data)

    @staticmethod
    def _audit_after_data(payload):
        return payload if isinstance(payload, dict) else {"result": payload}

    def _execute_live_action(
        self,
        request,
        *,
        drone: Drone,
        action_name: str,
        gateway_method_name: str,
        allow_empty_body: bool = False,
        payload_transform=None,
    ):
        serializer_input = (request.data or {}) if allow_empty_body else request.data
        serializer = self.get_serializer(data=serializer_input)
        serializer.is_valid(raise_exception=True)
        payload = dict(serializer.validated_data)
        if payload_transform is not None:
            payload = payload_transform(drone, payload)
        gateway_method = getattr(DjiGateway(), gateway_method_name)
        try:
            result = gateway_method(drone.device_sn, **payload)
        except DjiGatewayUpstreamError as exc:
            return _dji_live_action_error_response(exc)
        if isinstance(result, dict) and "video_id" not in result and payload.get("video_id"):
            result = dict(result)
            result["video_id"] = payload["video_id"]
        log_action(
            request=request,
            action=action_name,
            target_type="drone",
            target_id=drone.id,
            after_data=self._audit_after_data(result),
        )
        return Response(result, status=status.HTTP_200_OK)

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

        claimed_device_sns = list(
            Drone.objects.exclude(status=DroneStatus.RELEASED).values_list("device_sn", flat=True)
        )
        queryset = DjiDeviceIndex.objects.exclude(device_sn__in=claimed_device_sns).order_by("device_sn")
        page = self.paginate_queryset(queryset)
        serializer = AvailableDroneReadSerializer(page, many=True, context={"request": request})
        return self.get_paginated_response(serializer.data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        error_response = _drone_validate_or_respond(self, serializer)
        if error_response is not None:
            return error_response

        try:
            drone = self.perform_create(serializer)
        except IntegrityError as exc:
            return self._duplicate_integrity_response(serializer, exc)
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
        return self._update_with_serializer(request)

    def _update_with_serializer(self, request):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        error_response = _drone_validate_or_respond(self, serializer)
        if error_response is not None:
            return error_response
        drone = self.perform_update(serializer)
        return _drone_success_response(self, drone, http_status=status.HTTP_200_OK)

    @transaction.atomic
    def perform_update(self, serializer):
        drone = serializer.instance
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

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        drone = self.get_object()
        before_data = snapshot(drone)
        drone.status = DroneStatus.RELEASED
        drone.save(update_fields=["status", "updated_at"])
        now = timezone.now()
        drone.assignments.filter(status=DroneAssignmentStatus.ACTIVE).update(
            status=DroneAssignmentStatus.INACTIVE,
            end_at=now,
            updated_at=now,
        )

        deleted_payload = {"id": drone.id, "deleted": True}
        log_action(
            request=request,
            action="DRONE_DELETE",
            target_type="drone",
            target_id=drone.id,
            before_data=before_data,
            after_data=deleted_payload,
        )
        return Response(deleted_payload, status=status.HTTP_200_OK)

    @extend_schema(
        summary="查询设备直播能力",
        description=(
            "读取 DJI `/api/v1/manage/live/capacity` 并按当前无人机 `device_sn` 过滤。"
            "如果 DJI 上游没有返回该设备的直播能力，Django 侧会返回 `404 + C0404`，"
            "这通常意味着上游当前没有为该设备暴露 live capacity。"
        ),
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
        if not payload:
            return Response(
                standard_error_payload(
                    StandardCode.NOT_FOUND,
                    "未找到设备直播能力",
                    {"device_sn": drone.device_sn},
                ),
                status=status.HTTP_404_NOT_FOUND,
            )
        log_action(
            request=request,
            action="DRONE_LIVE_CAPACITY",
            target_type="drone",
            target_id=drone.id,
            after_data=self._audit_after_data(payload),
        )
        return Response(payload, status=status.HTTP_200_OK)

    @extend_schema(
        summary="启动直播（DJI 参数透传）",
        description=LIVE_START_DESCRIPTION,
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=DroneLiveStreamSerializer,
        responses={
            200: OpenApiResponse(response=OpenApiTypes.OBJECT),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            502: OpenApiResponse(description="DJI 上游服务失败或直播服务不可用。"),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    )
    @action(detail=True, methods=["post"], url_path="live/start")
    def live_start(self, request, *args, **kwargs):
        drone = self.get_object()
        return self._execute_live_action(
            request,
            drone=drone,
            action_name="DRONE_LIVE_START",
            gateway_method_name="start_live",
        )

    @extend_schema(
        summary="停止直播（DJI 参数透传）",
        description=LIVE_STOP_DESCRIPTION,
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=DroneLiveStreamSerializer,
        responses={
            200: OpenApiResponse(response=OpenApiTypes.OBJECT),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            502: OpenApiResponse(description="DJI 上游服务失败或直播服务不可用。"),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    )
    @action(detail=True, methods=["post"], url_path="live/stop")
    def live_stop(self, request, *args, **kwargs):
        drone = self.get_object()
        return self._execute_live_action(
            request,
            drone=drone,
            action_name="DRONE_LIVE_STOP",
            gateway_method_name="stop_live",
        )

    @extend_schema(
        summary="更新直播参数（DJI 参数透传）",
        description=LIVE_UPDATE_DESCRIPTION,
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=DroneLiveStreamSerializer,
        responses={
            200: OpenApiResponse(response=OpenApiTypes.OBJECT),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            502: OpenApiResponse(description="DJI 上游服务失败或直播服务不可用。"),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    )
    @action(detail=True, methods=["post"], url_path="live/update")
    def live_update(self, request, *args, **kwargs):
        drone = self.get_object()
        return self._execute_live_action(
            request,
            drone=drone,
            action_name="DRONE_LIVE_UPDATE",
            gateway_method_name="update_live",
        )

    @extend_schema(
        summary="切换直播视频源（DJI 参数透传）",
        description=LIVE_SWITCH_DESCRIPTION,
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=DroneLiveStreamSerializer,
        responses={
            200: OpenApiResponse(response=OpenApiTypes.OBJECT),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            502: OpenApiResponse(description="DJI 上游服务失败或直播服务不可用。"),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    )
    @action(detail=True, methods=["post"], url_path="live/switch")
    def live_switch(self, request, *args, **kwargs):
        drone = self.get_object()
        return self._execute_live_action(
            request,
            drone=drone,
            action_name="DRONE_LIVE_SWITCH",
            gateway_method_name="switch_live",
        )
