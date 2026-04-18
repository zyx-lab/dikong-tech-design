from django.db import transaction
from django.utils import timezone
from django.shortcuts import get_object_or_404
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
    BusinessDeleteResultSerializer,
    BUSINESS_INVALID_PARAMS_RESPONSE,
    BUSINESS_INTERNAL_ERROR_RESPONSE,
    BUSINESS_NOT_FOUND_RESPONSE,
    BUSINESS_PERMISSION_DENIED_RESPONSE,
    TENANT_CODE_HEADER_PARAMETER,
    object_envelope_serializer,
    paginated_envelope_serializer,
)
from apps.api_v1.tenant_scope import TenantScopedBusinessMixin
from apps.flight_record.models import FlightRecord
from apps.mission.models import Mission, MissionStatus
from apps.mission.serializers import MissionCreateSerializer, MissionReadSerializer, MissionUpdateSerializer

MISSION_LIST_RESPONSE = paginated_envelope_serializer("MissionListResponse", MissionReadSerializer)
MISSION_DETAIL_RESPONSE = object_envelope_serializer("MissionDetailResponse", MissionReadSerializer)
MISSION_DELETE_RESPONSE = object_envelope_serializer("MissionDeleteResponse", BusinessDeleteResultSerializer)

MISSION_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(name="route_id", type=int, location=OpenApiParameter.QUERY, description="按航线 ID 过滤。"),
    OpenApiParameter(name="drone_id", type=int, location=OpenApiParameter.QUERY, description="按无人机 ID 过滤。"),
    OpenApiParameter(name="pilot_id", type=int, location=OpenApiParameter.QUERY, description="按飞手成员 ID 过滤。"),
    OpenApiParameter(
        name="status",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按任务状态过滤。状态值：0=待执行，1=执行中，2=执行完成。",
    ),
]


def _mission_success_response(view, mission: Mission, *, http_status: int, include_headers: bool = False):
    payload = view._payload(mission)
    if include_headers:
        headers = view.get_success_headers(payload)
        return Response(payload, status=http_status, headers=headers)
    return Response(payload, status=http_status)


def _reject_empty_update_request(request):
    if not request.data:
        return Response(
            standard_error_payload(
                StandardCode.INVALID_PARAMS,
                "更新请求至少包含一个可写字段",
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


def _mission_state_conflict_response(*, mission: Mission, message: str):
    return Response(
        standard_error_payload(
            StandardCode.STATE_CONFLICT,
            message,
            {"mission_id": mission.id, "status": mission.status},
        ),
        status=status.HTTP_409_CONFLICT,
    )


@extend_schema_view(
    list=extend_schema(
        summary="查询任务列表",
        description=(
            "任务当前状态通过返回列表项中的 `status` 字段读取；状态值为 "
            "0=待执行、1=执行中、2=执行完成、3=飞行中。"
            "`3=飞行中` 为实时派生状态，仅在任务已处于执行中且对应无人机被实时判定为在飞时返回。"
        ),
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
        description=(
            "任务当前状态通过响应 `data.status` 字段读取；状态值为 "
            "0=待执行、1=执行中、2=执行完成、3=飞行中。"
            "`3=飞行中` 为实时派生状态，仅在任务已处于执行中且对应无人机被实时判定为在飞时返回。"
            "`started_at` 表示开始执行时间，`finished_at` 仅在任务执行完成后非空。"
        ),
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
        summary="创建任务",
        description="创建后返回完整 mission 快照；初始状态固定为 `status=0（待执行）`，且 `started_at`、`finished_at` 为空。",
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
        summary="局部更新待执行任务字段",
        description=(
            "仅待执行任务允许更新，且必须至少提交一个可写字段。更新成功后返回完整 mission 快照，可直接读取 "
            "`data.status`、`data.started_at`、`data.finished_at`。"
        ),
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
    destroy=extend_schema(
        summary="软删除任务",
        description="软删除 mission。删除动作不返回任务状态快照，仅返回 `{id, deleted}`。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=MISSION_DELETE_RESPONSE),
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
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Mission.objects.select_related("route", "drone", "pilot__user__staff_profile").all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "put", "delete", "head", "options"]

    permission_map = {
        "list": "mission.view_mission",
        "retrieve": "mission.view_mission",
        "create": "mission.manage_mission",
        "update": "mission.manage_mission",
        "advance": "mission.manage_mission",
        "destroy": "mission.manage_mission",
    }

    @staticmethod
    def assigned_scope_filter_builder(tenant_member_id: int) -> dict:
        return {"pilot_id": tenant_member_id}

    def get_serializer_class(self):
        if self.action == "create":
            return MissionCreateSerializer
        if self.action == "update":
            return MissionUpdateSerializer
        return MissionReadSerializer

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset()).filter(is_deleted=False)
        params = self.request.query_params

        for query_key, model_field in (
            ("route_id", "route_id"),
            ("drone_id", "drone_id"),
            ("pilot_id", "pilot_id"),
            ("status", "status"),
        ):
            value = params.get(query_key)
            if value:
                queryset = queryset.filter(**{model_field: value})

        if self.action in {"list", "retrieve", "update", "advance", "destroy"}:
            return self.apply_scope(queryset)
        return queryset

    def _payload(self, mission: Mission) -> dict:
        return dict(MissionReadSerializer(mission, context={"request": self.request}).data)

    def _update_mission(self, request):
        error_response = _reject_empty_update_request(request)
        if error_response is not None:
            return error_response

        mission = self.get_object()
        if mission.status != MissionStatus.PENDING:
            return _mission_state_conflict_response(mission=mission, message="仅允许修改待执行任务")

        serializer = self.get_serializer(mission, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        mission = self.perform_update(serializer)
        return _mission_success_response(self, mission, http_status=status.HTTP_200_OK)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        error_response = _reject_request_body_if_present(request, message="DELETE 请求不支持请求体")
        if error_response is not None:
            return error_response

        mission = self.get_object()
        before_data = snapshot(mission)
        deleted_at = timezone.now()
        update_fields = ["is_deleted", "deleted_at", "updated_at"]
        mission.is_deleted = True
        mission.deleted_at = deleted_at
        mission.save(update_fields=update_fields)

        deleted_payload = {"id": mission.id, "deleted": True}
        log_action(
            request=request,
            action="MISSION_DELETE",
            target_type="mission",
            target_id=mission.id,
            before_data=before_data,
            after_data=deleted_payload,
        )
        return Response(deleted_payload, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=False)
        if serializer.errors:
            return Response(validation_error_payload(serializer.errors), status=status.HTTP_400_BAD_REQUEST)

        mission = self.perform_create(serializer)
        return _mission_success_response(self, mission, http_status=status.HTTP_201_CREATED, include_headers=True)

    @transaction.atomic
    def perform_create(self, serializer):
        tenant = self.get_current_tenant()
        mission = serializer.save(tenant=tenant)
        log_action(
            request=self.request,
            action="MISSION_CREATE",
            target_type="mission",
            target_id=mission.id,
            after_data=self._payload(mission),
        )
        return mission

    def update(self, request, *args, **kwargs):
        return self._update_mission(request)

    @extend_schema(
        summary="推进任务状态",
        description=(
            "推进任务状态：`待执行 -> 执行中 -> 执行完成`。调用成功后返回完整 mission 快照，"
            "可直接读取 `data.status`、`data.started_at`、`data.finished_at` 获取最新任务状态。"
            "`待执行 -> 执行中` 仅更新 mission；`执行中 -> 执行完成` 时会自动生成一条 flight record 历史快照。"
            "`status=3（飞行中）` 为实时派生状态，不写入数据库；仅当任务已处于执行中且对应无人机被实时判定为在飞时返回。"
        ),
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=MISSION_DETAIL_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            409: OpenApiResponse(description="任务当前状态不允许推进，或同一无人机已有执行中的任务。"),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Mission"],
    )
    @action(detail=True, methods=["post"], url_path="advance")
    @transaction.atomic
    def advance(self, request, *args, **kwargs):
        error_response = _reject_request_body_if_present(request, message="advance 请求不支持请求体")
        if error_response is not None:
            return error_response

        mission = get_object_or_404(self.get_queryset().select_for_update(), pk=kwargs["pk"])
        before_data = snapshot(mission)

        if mission.status == MissionStatus.PENDING:
            occupied = (
                self.get_queryset()
                .select_for_update()
                .filter(drone_id=mission.drone_id, status=MissionStatus.RUNNING)
                .exclude(pk=mission.pk)
                .exists()
            )
            if occupied:
                return _mission_state_conflict_response(mission=mission, message="当前无人机已有执行中的任务")

            mission.status = MissionStatus.RUNNING
            mission.started_at = timezone.now()
            mission.finished_at = None
            mission.save(update_fields=["status", "started_at", "finished_at", "updated_at"])
        elif mission.status == MissionStatus.RUNNING:
            mission.status = MissionStatus.COMPLETED
            mission.finished_at = timezone.now()
            mission.save(update_fields=["status", "finished_at", "updated_at"])
            FlightRecord.create_from_completed_mission(mission=mission)
        else:
            return _mission_state_conflict_response(mission=mission, message="当前任务状态不允许继续推进")

        after_data = snapshot(mission)
        log_action(
            request=request,
            action="MISSION_ADVANCE",
            target_type="mission",
            target_id=mission.id,
            before_data=before_data,
            after_data=after_data,
        )
        return _mission_success_response(self, mission, http_status=status.HTTP_200_OK)

    @transaction.atomic
    def perform_update(self, serializer):
        mission = serializer.instance
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
