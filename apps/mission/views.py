from django.db import transaction
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission, ScopedQuerysetMixin
from apps.access.services import log_action, snapshot
from apps.api_v1.business_response import (
    BusinessApiResponseMixin,
    StandardCode,
    standard_error_payload,
    validation_error_payload,
)
from apps.api_v1.schema import (
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
from apps.dji_bff.models import SyncStatus, TenantMissionIndex
from apps.mission.models import Mission, MissionStatus
from apps.mission.serializers import MissionCreateSerializer, MissionReadSerializer, MissionUpdateSerializer

MISSION_LIST_RESPONSE = paginated_envelope_serializer("MissionListResponse", MissionReadSerializer)
MISSION_DETAIL_RESPONSE = object_envelope_serializer("MissionDetailResponse", MissionReadSerializer)

MISSION_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(name="route_id", type=int, location=OpenApiParameter.QUERY, description="按航线 ID 过滤。"),
    OpenApiParameter(name="drone_id", type=int, location=OpenApiParameter.QUERY, description="按无人机 ID 过滤。"),
    OpenApiParameter(name="pilot_id", type=int, location=OpenApiParameter.QUERY, description="按飞手成员 ID 过滤。"),
    OpenApiParameter(name="status", type=int, location=OpenApiParameter.QUERY, description="按任务状态过滤。"),
]

def _mission_success_response(view, mission: Mission, *, http_status: int, include_headers: bool = False):
    payload = view._payload(mission)
    if include_headers:
        headers = view.get_success_headers(payload)
        return Response(payload, status=http_status, headers=headers)
    return Response(payload, status=http_status)


def _reject_empty_patch_request(request):
    if not request.data:
        return Response(
            standard_error_payload(
                StandardCode.INVALID_PARAMS,
                "PATCH 请求至少包含一个可写字段",
                {"body": "请至少提交一个可写字段"},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


def _reject_request_body_if_present(request, *, message: str):
    if request.data:
        return Response(
            standard_error_payload(
                StandardCode.INVALID_PARAMS,
                message,
                {"body": "不支持请求体，请移除 body 后重试"},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


@extend_schema_view(
    list=extend_schema(
        summary="查询任务列表",
        parameters=MISSION_FILTER_PARAMETERS,
        responses={
            200: OpenApiResponse(response=MISSION_LIST_RESPONSE),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Mission"],
    ),
    retrieve=extend_schema(
        summary="读取任务详情",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=MISSION_DETAIL_RESPONSE),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Mission"],
    ),
    create=extend_schema(
        summary="创建任务并同步 DJI job",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=MissionCreateSerializer,
        responses={
            201: OpenApiResponse(response=MISSION_DETAIL_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Mission"],
    ),
    update=extend_schema(
        summary="全量更新本地任务字段",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=MissionUpdateSerializer,
        responses={
            200: OpenApiResponse(response=MISSION_DETAIL_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Mission"],
    ),
    partial_update=extend_schema(
        summary="局部更新本地任务字段",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=MissionUpdateSerializer,
        responses={
            200: OpenApiResponse(response=MISSION_DETAIL_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Mission"],
    ),
)
class MissionViewSet(
    BusinessApiResponseMixin,
    TenantScopedBusinessMixin,
    PermissionMapMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Mission.objects.select_related("route", "drone", "pilot__user__staff_profile", "dji_index").all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    permission_map = {
        "list": "mission.view_mission",
        "retrieve": "mission.view_mission",
        "create": "mission.manage_mission",
        "update": "mission.manage_mission",
        "partial_update": "mission.manage_mission",
        "cancel": "mission.manage_mission",
    }

    @staticmethod
    def assigned_scope_filter_builder(tenant_member_id: int) -> dict:
        return {"pilot_id": tenant_member_id}

    def get_serializer_class(self):
        if self.action == "create":
            return MissionCreateSerializer
        if self.action in {"update", "partial_update"}:
            return MissionUpdateSerializer
        return MissionReadSerializer

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset())
        params = self.request.query_params

        if params.get("route_id"):
            queryset = queryset.filter(route_id=params["route_id"])
        if params.get("drone_id"):
            queryset = queryset.filter(drone_id=params["drone_id"])
        if params.get("pilot_id"):
            queryset = queryset.filter(pilot_id=params["pilot_id"])
        if params.get("status"):
            queryset = queryset.filter(status=params["status"])

        if self.action in {"list", "retrieve", "update", "partial_update", "cancel"}:
            return self.apply_scope(queryset)
        return queryset

    def _payload(self, mission: Mission) -> dict:
        return dict(MissionReadSerializer(mission, context={"request": self.request}).data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=False)
        if serializer.errors:
            return Response(validation_error_payload(serializer.errors), status=status.HTTP_400_BAD_REQUEST)

        route = serializer.validated_data["route"]
        route_index = getattr(route, "dji_index", None)
        if route_index is None or not route_index.is_published or not route_index.dji_wayline_id:
            return Response(
                validation_error_payload({"route": ["航线尚未发布到 DJI"]}),
                status=status.HTTP_400_BAD_REQUEST,
            )

        mission = self.perform_create(serializer)
        return _mission_success_response(self, mission, http_status=status.HTTP_201_CREATED, include_headers=True)

    @transaction.atomic
    def perform_create(self, serializer):
        tenant = self.get_current_tenant()
        dock_sn = serializer.validated_data.get("dock_sn", "")
        mission = serializer.save(tenant=tenant)
        upstream_payload = DjiGateway().create_mission(mission_name=mission.name, dock_sn=dock_sn)
        mission.dji_job_id = upstream_payload["dji_job_id"]
        mission.save(update_fields=["dji_job_id", "updated_at"])
        TenantMissionIndex.objects.create(
            tenant=tenant,
            mission=mission,
            dji_job_id=mission.dji_job_id,
            execution_status=str(mission.status),
            sync_status=SyncStatus.SYNCED,
            last_sync_at=timezone.now(),
        )
        log_action(
            request=self.request,
            action="MISSION_CREATE",
            target_type="mission",
            target_id=mission.id,
            after_data=self._payload(mission),
        )
        return mission

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        mission = self.perform_update(serializer)
        return _mission_success_response(self, mission, http_status=status.HTTP_200_OK)

    def partial_update(self, request, *args, **kwargs):
        error_response = _reject_empty_patch_request(request)
        if error_response is not None:
            return error_response
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        mission = self.perform_update(serializer)
        return _mission_success_response(self, mission, http_status=status.HTTP_200_OK)

    @transaction.atomic
    def perform_update(self, serializer):
        mission = self.get_object()
        before_data = snapshot(mission)
        mission = serializer.save()
        log_action(
            request=self.request,
            action="MISSION_UPDATE",
            target_type="mission",
            target_id=mission.id,
            before_data=before_data,
            after_data=snapshot(mission),
        )
        return mission

    @extend_schema(
        summary="取消任务",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=MISSION_DETAIL_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Mission"],
    )
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def cancel(self, request, *args, **kwargs):
        error_response = _reject_request_body_if_present(request, message="cancel 请求不支持提交 body 参数")
        if error_response is not None:
            return error_response

        mission = self.get_object()
        if not mission.dji_job_id:
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "任务尚未同步",
                    {"mission_id": mission.id},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        before_data = snapshot(mission)
        try:
            DjiGateway().cancel_mission(mission.dji_job_id)
        except DjiGatewayUpstreamError as exc:
            if exc.status_code != 404:
                raise

        mission.status = MissionStatus.CANCELED
        mission.save(update_fields=["status", "updated_at"])
        if getattr(mission, "dji_index", None) is not None:
            mission.dji_index.execution_status = str(MissionStatus.CANCELED)
            mission.dji_index.sync_status = SyncStatus.SYNCED
            mission.dji_index.last_sync_at = timezone.now()
            mission.dji_index.error_msg = ""
            mission.dji_index.save(update_fields=["execution_status", "sync_status", "last_sync_at", "error_msg", "updated_at"])

        log_action(
            request=request,
            action="MISSION_CANCEL",
            target_type="mission",
            target_id=mission.id,
            before_data=before_data,
            after_data=snapshot(mission),
        )
        return _mission_success_response(self, mission, http_status=status.HTTP_200_OK)
