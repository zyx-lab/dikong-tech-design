from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission, ScopedQuerysetMixin
from apps.access.services import log_action
from apps.api_v1.business_response import BusinessApiResponseMixin, BusinessCode
from apps.api_v1.schema import (
    BUSINESS_INTERNAL_ERROR_RESPONSE,
    TENANT_CODE_HEADER_PARAMETER,
    business_error_example,
    business_error_response,
    object_envelope_serializer,
    paginated_envelope_serializer,
)
from apps.api_v1.tenant_scope import TenantScopedBusinessMixin
from apps.mission.models import Mission, MissionStatus
from apps.mission.serializers import MissionReadSerializer, MissionWriteSerializer


MISSION_LIST_RESPONSE = paginated_envelope_serializer("MissionListResponse", MissionReadSerializer)
MISSION_DETAIL_RESPONSE = object_envelope_serializer("MissionDetailResponse", MissionReadSerializer)

MISSION_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(
        name="route_id",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按绑定航线 ID 精确过滤。",
    ),
    OpenApiParameter(
        name="drone_id",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按绑定无人机 ID 精确过滤。",
    ),
    OpenApiParameter(
        name="pilot_id",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按绑定飞手成员 ID 精确过滤。",
    ),
    OpenApiParameter(
        name="status",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按任务状态精确过滤。",
        enum=[choice[0] for choice in MissionStatus.choices],
    ),
]

MISSION_PERMISSION_DENIED_RESPONSE = business_error_response(
    description="未认证、无权限、缺少租户上下文，或 platform_admin 访问业务 API 被拒绝。",
    examples=[
        business_error_example(
            "未登录",
            business_code="PERMISSION_DENIED",
            business_detail_code="NOT_AUTHENTICATED",
            detail="Authentication credentials were not provided.",
            status_codes=["401"],
        ),
        business_error_example(
            "无权限",
            business_code="PERMISSION_DENIED",
            business_detail_code="FORBIDDEN",
            detail="PERMISSION_DENIED",
            status_codes=["403"],
        ),
        business_error_example(
            "缺少租户上下文",
            business_code="PERMISSION_DENIED",
            business_detail_code="TENANT_CONTEXT_REQUIRED",
            detail="tenant context required",
            status_codes=["403"],
        ),
        business_error_example(
            "平台管理员访问业务 API",
            business_code="PERMISSION_DENIED",
            business_detail_code="FORBIDDEN",
            detail="platform admin cannot access tenant business api",
            status_codes=["403"],
        ),
    ],
)

MISSION_INVALID_PARAMS_RESPONSE = business_error_response(
    description="请求体不合法或资源不满足绑定条件，business_code 固定为 INVALID_PARAMS。",
    examples=[
        business_error_example(
            "航线不属于当前租户",
            business_code="INVALID_PARAMS",
            business_detail_code="VALIDATION_ERROR",
            detail="参数校验失败",
            status_codes=["400"],
            extras={"errors": {"route": ["仅允许绑定当前租户下的航线"]}},
        ),
        business_error_example(
            "无人机未启用",
            business_code="INVALID_PARAMS",
            business_detail_code="VALIDATION_ERROR",
            detail="参数校验失败",
            status_codes=["400"],
            extras={"errors": {"drone": ["仅允许绑定启用状态无人机"]}},
        ),
        business_error_example(
            "飞手不具备角色",
            business_code="INVALID_PARAMS",
            business_detail_code="VALIDATION_ERROR",
            detail="参数校验失败",
            status_codes=["400"],
            extras={"errors": {"pilot": ["仅允许分配给飞手类型（pilot_operator）"]}},
        ),
        business_error_example(
            "提交不可写字段",
            business_code="INVALID_PARAMS",
            business_detail_code="VALIDATION_ERROR",
            detail="参数校验失败",
            status_codes=["400"],
            extras={"errors": {"status": ["该字段在此接口不可写"]}},
        ),
        business_error_example(
            "动作接口提交 body",
            business_code="INVALID_PARAMS",
            business_detail_code="VALIDATION_ERROR",
            detail="start 请求不支持提交 body 参数",
            status_codes=["400"],
            extras={"errors": {"body": "不支持请求体，请移除 body 后重试"}},
        ),
    ],
)

MISSION_NOT_FOUND_RESPONSE = business_error_response(
    description="目标任务不存在，或在当前租户/授权作用域下不可见。",
    examples=[
        business_error_example(
            "任务不存在",
            business_code="RESOURCE_NOT_FOUND",
            business_detail_code="NOT_FOUND",
            detail="No Mission matches the given query.",
            status_codes=["404"],
        )
    ],
)

MISSION_STATE_CONFLICT_RESPONSE = business_error_response(
    description="任务当前状态不允许本次流转，business_code 固定为 STATE_CONFLICT。",
    examples=[
        business_error_example(
            "已完成任务不可启动",
            business_code="STATE_CONFLICT",
            business_detail_code="STATE_CONFLICT",
            detail="当前任务状态不允许启动",
            status_codes=["409"],
            extras={"mission_id": 101, "status": MissionStatus.COMPLETED},
        ),
        business_error_example(
            "待执行任务不可暂停",
            business_code="STATE_CONFLICT",
            business_detail_code="STATE_CONFLICT",
            detail="当前任务状态不允许暂停",
            status_codes=["409"],
            extras={"mission_id": 102, "status": MissionStatus.PENDING},
        ),
        business_error_example(
            "已取消任务不可恢复",
            business_code="STATE_CONFLICT",
            business_detail_code="STATE_CONFLICT",
            detail="当前任务状态不允许恢复",
            status_codes=["409"],
            extras={"mission_id": 103, "status": MissionStatus.CANCELED},
        ),
    ],
)


def _mission_transition_schema(*, summary, description):
    return extend_schema(
        summary=summary,
        description=description,
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=MISSION_DETAIL_RESPONSE, description="状态流转成功；若命中幂等条件，返回当前任务快照。"),
            400: MISSION_INVALID_PARAMS_RESPONSE,
            401: MISSION_PERMISSION_DENIED_RESPONSE,
            403: MISSION_PERMISSION_DENIED_RESPONSE,
            404: MISSION_NOT_FOUND_RESPONSE,
            409: MISSION_STATE_CONFLICT_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Mission"],
    )


@extend_schema_view(
    list=extend_schema(
        summary="查询任务列表",
        description=(
            "按当前租户查询任务，支持按航线、无人机、飞手、状态过滤。"
            " 若当前角色是 `ASSIGNED` 作用域，则仅返回 `pilot_id=当前成员` 的任务。"
        ),
        parameters=MISSION_FILTER_PARAMETERS,
        responses={
            200: OpenApiResponse(response=MISSION_LIST_RESPONSE, description="查询成功。"),
            401: MISSION_PERMISSION_DENIED_RESPONSE,
            403: MISSION_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Mission"],
    ),
    retrieve=extend_schema(
        summary="读取任务详情",
        description="按任务 ID 读取单条任务详情；若不在当前租户或作用域内，会按不可见处理。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=MISSION_DETAIL_RESPONSE, description="读取成功。"),
            401: MISSION_PERMISSION_DENIED_RESPONSE,
            403: MISSION_PERMISSION_DENIED_RESPONSE,
            404: MISSION_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Mission"],
    ),
    create=extend_schema(
        summary="创建任务",
        description="创建一条任务执行计划，绑定航线、无人机和飞手成员；任务初始状态固定为 PENDING。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=MissionWriteSerializer,
        examples=[
            OpenApiExample(
                "创建任务请求",
                request_only=True,
                value={
                    "name": "园区巡检-上午批次",
                    "route": 1,
                    "drone": 2,
                    "pilot": 8,
                    "scheduled_at": "2026-03-16T10:30:00+08:00",
                    "remark": "起飞前检查电池",
                },
            )
        ],
        responses={
            201: OpenApiResponse(response=MISSION_DETAIL_RESPONSE, description="创建成功。"),
            400: MISSION_INVALID_PARAMS_RESPONSE,
            401: MISSION_PERMISSION_DENIED_RESPONSE,
            403: MISSION_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Mission"],
    ),
    partial_update=extend_schema(
        summary="局部更新任务",
        description="按任务 ID 局部更新任务基础字段或重新绑定资源；不允许在该接口直接修改状态。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=MissionWriteSerializer,
        examples=[
            OpenApiExample(
                "PATCH 任务请求",
                request_only=True,
                value={"scheduled_at": "2026-03-16T11:00:00+08:00", "remark": "改为 11 点起飞"},
            )
        ],
        responses={
            200: OpenApiResponse(response=MISSION_DETAIL_RESPONSE, description="更新成功。"),
            400: MISSION_INVALID_PARAMS_RESPONSE,
            401: MISSION_PERMISSION_DENIED_RESPONSE,
            403: MISSION_PERMISSION_DENIED_RESPONSE,
            404: MISSION_NOT_FOUND_RESPONSE,
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
    """任务业务接口（V1）。"""

    queryset = Mission.objects.select_related("route", "drone", "pilot__user__staff_profile").all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "patch", "head", "options"]

    permission_map = {
        "list": "mission.view_mission",
        "retrieve": "mission.view_mission",
        "create": "mission.manage_mission",
        "partial_update": "mission.manage_mission",
        "start": "mission.manage_mission",
        "pause": "mission.manage_mission",
        "resume": "mission.manage_mission",
        "complete": "mission.manage_mission",
        "fail": "mission.manage_mission",
        "cancel": "mission.manage_mission",
    }

    @staticmethod
    def assigned_scope_filter_builder(tenant_member_id: int) -> dict:
        return {"pilot_id": tenant_member_id}

    def get_serializer_class(self):
        if self.action in {"create", "partial_update"}:
            return MissionWriteSerializer
        return MissionReadSerializer

    def _mission_payload(self, mission: Mission) -> dict:
        return dict(MissionReadSerializer(mission, context={"request": self.request}).data)

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset())
        params = self.request.query_params

        # 业务作用：
        # 提供任务列表查询能力，作为“任务创建后”的基础读取接口，供外部系统组合筛选和调度。
        # 该接口只做只读检索，不承载任务状态流转编排；返回数据由统一 business_code 包装。
        route_id = params.get("route_id")
        drone_id = params.get("drone_id")
        pilot_id = params.get("pilot_id")
        status_value = params.get("status")

        if route_id:
            queryset = queryset.filter(route_id=route_id)
        if drone_id:
            queryset = queryset.filter(drone_id=drone_id)
        if pilot_id:
            queryset = queryset.filter(pilot_id=pilot_id)
        if status_value:
            queryset = queryset.filter(status=status_value)

        if self.action in {
            "list",
            "retrieve",
            "partial_update",
            "start",
            "pause",
            "resume",
            "complete",
            "fail",
            "cancel",
        }:
            return self.apply_scope(queryset)

        return queryset

    def create(self, request, *args, **kwargs):
        # 业务作用：
        # 新建任务主记录（mission），建立“航线 + 无人机 + 飞手”的一次执行计划绑定。
        # 该接口只负责任务创建，不负责任务执行启动、暂停、完成、取消等状态流转编排。
        #
        # 边界与输入输出：
        # 1) 输入要求 route/drone/pilot 为已存在且满足可分配状态的资源；
        # 2) 成功返回任务快照（含冗余名称字段）；
        # 3) 响应统一携带 business_code/business_detail_code。
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        mission = self.perform_create(serializer)
        read_serializer = MissionReadSerializer(mission, context={"request": request})
        headers = self.get_success_headers(read_serializer.data)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def retrieve(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 mission_id 精确读取任务详情”的基础只读接口，供外部系统在任务编排动作前先确认任务当前快照。
        # 本接口保持单一职责：只负责详情读取，不承担任务状态流转（开始/暂停/取消/完成）等写操作。
        #
        # 边界与返回语义：
        # 1) 有查看权限且任务存在：返回任务详情快照，business_code=SUCCESS（HTTP 200）；
        # 2) 任务不存在：business_code=RESOURCE_NOT_FOUND（HTTP 404）；
        # 3) 未认证或无查看权限：business_code=PERMISSION_DENIED（HTTP 401/403）。
        # 业务码字段 business_code/business_detail_code 由 BusinessApiResponseMixin 统一补齐。
        return super().retrieve(request, *args, **kwargs)

    @_mission_transition_schema(
        summary="取消任务",
        description="将任务状态切换为 CANCELED。仅处理任务自身状态流转，请求体必须为空。",
    )
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def cancel(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 mission_id 取消任务”的基础状态流转入口（POST /api/v1/missions/{id}/cancel），
        # 用于将待执行、执行中或已暂停任务显式置为已取消。
        #
        # 适用边界：
        # 1) 仅处理 mission.status 自身流转，不承担无人机回收、飞行记录补录或其他跨实体编排；
        # 2) 请求体必须为空，避免把取消动作扩展成编排型接口；
        # 3) 已完成、已取消或已失败任务不允许再次取消，返回状态冲突。
        if request.data:
            return Response(
                {
                    "business_code": BusinessCode.INVALID_PARAMS,
                    "detail": "cancel 请求不支持提交 body 参数",
                    "errors": {"body": "不支持请求体，请移除 body 后重试"},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        mission = self.get_object()
        before_payload = self._mission_payload(mission)

        if mission.status in {MissionStatus.COMPLETED, MissionStatus.CANCELED, MissionStatus.FAILED}:
            return Response(
                {
                    "business_code": BusinessCode.STATE_CONFLICT,
                    "business_detail_code": "STATE_CONFLICT",
                    "detail": "当前任务状态不允许取消",
                    "mission_id": mission.id,
                    "status": mission.status,
                },
                status=status.HTTP_409_CONFLICT,
            )

        mission.status = MissionStatus.CANCELED
        mission.save(update_fields=["status", "updated_at"])
        after_payload = self._mission_payload(mission)
        log_action(
            request=request,
            action="MISSION_CANCEL",
            target_type="mission",
            target_id=mission.id,
            before_data=before_payload,
            after_data=after_payload,
        )
        return Response(after_payload, status=status.HTTP_200_OK)

    @_mission_transition_schema(
        summary="启动任务",
        description="将 PENDING 任务切换为 RUNNING；若已是 RUNNING，则按幂等成功返回当前任务快照。",
    )
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def start(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 mission_id 启动任务”的基础状态流转入口（POST /api/v1/missions/{id}/start），
        # 用于将待执行任务显式置为执行中。
        #
        # 适用边界：
        # 1) 仅处理 mission.status 自身流转，不承担飞行记录创建、无人机回收或其他跨实体编排；
        # 2) 请求体必须为空；
        # 3) 已执行中的任务重复 start 按幂等成功返回；
        # 4) 已暂停、已完成、已取消、已失败任务不允许 start，返回状态冲突。
        if request.data:
            return Response(
                {
                    "business_code": BusinessCode.INVALID_PARAMS,
                    "detail": "start 请求不支持提交 body 参数",
                    "errors": {"body": "不支持请求体，请移除 body 后重试"},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        mission = self.get_object()
        before_payload = self._mission_payload(mission)

        if mission.status == MissionStatus.RUNNING:
            log_action(
                request=request,
                action="MISSION_START",
                target_type="mission",
                target_id=mission.id,
                before_data=before_payload,
                after_data=before_payload,
            )
            return Response(before_payload, status=status.HTTP_200_OK)

        if mission.status in {MissionStatus.PAUSED, MissionStatus.COMPLETED, MissionStatus.CANCELED, MissionStatus.FAILED}:
            return Response(
                {
                    "business_code": BusinessCode.STATE_CONFLICT,
                    "business_detail_code": "STATE_CONFLICT",
                    "detail": "当前任务状态不允许启动",
                    "mission_id": mission.id,
                    "status": mission.status,
                },
                status=status.HTTP_409_CONFLICT,
            )

        mission.status = MissionStatus.RUNNING
        mission.save(update_fields=["status", "updated_at"])
        after_payload = self._mission_payload(mission)
        log_action(
            request=request,
            action="MISSION_START",
            target_type="mission",
            target_id=mission.id,
            before_data=before_payload,
            after_data=after_payload,
        )
        return Response(after_payload, status=status.HTTP_200_OK)

    @_mission_transition_schema(
        summary="暂停任务",
        description="将 RUNNING 任务切换为 PAUSED；若已是 PAUSED，则按幂等成功返回当前任务快照。",
    )
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def pause(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 mission_id 暂停任务”的基础状态流转入口（POST /api/v1/missions/{id}/pause），
        # 用于将执行中的任务显式置为已暂停。
        #
        # 适用边界：
        # 1) 仅处理 mission.status 自身流转，不承担飞行记录创建、无人机回收或其他跨实体编排；
        # 2) 请求体必须为空；
        # 3) 已暂停任务重复 pause 按幂等成功返回；
        # 4) 待执行、已完成、已取消、已失败任务不允许 pause，返回状态冲突。
        if request.data:
            return Response(
                {
                    "business_code": BusinessCode.INVALID_PARAMS,
                    "detail": "pause 请求不支持提交 body 参数",
                    "errors": {"body": "不支持请求体，请移除 body 后重试"},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        mission = self.get_object()
        before_payload = self._mission_payload(mission)

        if mission.status == MissionStatus.PAUSED:
            log_action(
                request=request,
                action="MISSION_PAUSE",
                target_type="mission",
                target_id=mission.id,
                before_data=before_payload,
                after_data=before_payload,
            )
            return Response(before_payload, status=status.HTTP_200_OK)

        if mission.status in {MissionStatus.PENDING, MissionStatus.COMPLETED, MissionStatus.CANCELED, MissionStatus.FAILED}:
            return Response(
                {
                    "business_code": BusinessCode.STATE_CONFLICT,
                    "business_detail_code": "STATE_CONFLICT",
                    "detail": "当前任务状态不允许暂停",
                    "mission_id": mission.id,
                    "status": mission.status,
                },
                status=status.HTTP_409_CONFLICT,
            )

        mission.status = MissionStatus.PAUSED
        mission.save(update_fields=["status", "updated_at"])
        after_payload = self._mission_payload(mission)
        log_action(
            request=request,
            action="MISSION_PAUSE",
            target_type="mission",
            target_id=mission.id,
            before_data=before_payload,
            after_data=after_payload,
        )
        return Response(after_payload, status=status.HTTP_200_OK)

    @_mission_transition_schema(
        summary="恢复任务",
        description="将 PAUSED 任务切换回 RUNNING；若已是 RUNNING，则按幂等成功返回当前任务快照。",
    )
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def resume(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 mission_id 恢复任务”的基础状态流转入口（POST /api/v1/missions/{id}/resume），
        # 用于将已暂停任务显式恢复为执行中。
        #
        # 适用边界：
        # 1) 仅处理 mission.status 自身流转，不承担飞行记录创建、无人机回收或其他跨实体编排；
        # 2) 请求体必须为空；
        # 3) 已执行中的任务重复 resume 按幂等成功返回；
        # 4) 待执行、已完成、已取消、已失败任务不允许 resume，返回状态冲突。
        if request.data:
            return Response(
                {
                    "business_code": BusinessCode.INVALID_PARAMS,
                    "detail": "resume 请求不支持提交 body 参数",
                    "errors": {"body": "不支持请求体，请移除 body 后重试"},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        mission = self.get_object()
        before_payload = self._mission_payload(mission)

        if mission.status == MissionStatus.RUNNING:
            log_action(
                request=request,
                action="MISSION_RESUME",
                target_type="mission",
                target_id=mission.id,
                before_data=before_payload,
                after_data=before_payload,
            )
            return Response(before_payload, status=status.HTTP_200_OK)

        if mission.status in {MissionStatus.PENDING, MissionStatus.COMPLETED, MissionStatus.CANCELED, MissionStatus.FAILED}:
            return Response(
                {
                    "business_code": BusinessCode.STATE_CONFLICT,
                    "business_detail_code": "STATE_CONFLICT",
                    "detail": "当前任务状态不允许恢复",
                    "mission_id": mission.id,
                    "status": mission.status,
                },
                status=status.HTTP_409_CONFLICT,
            )

        mission.status = MissionStatus.RUNNING
        mission.save(update_fields=["status", "updated_at"])
        after_payload = self._mission_payload(mission)
        log_action(
            request=request,
            action="MISSION_RESUME",
            target_type="mission",
            target_id=mission.id,
            before_data=before_payload,
            after_data=after_payload,
        )
        return Response(after_payload, status=status.HTTP_200_OK)

    @_mission_transition_schema(
        summary="完成任务",
        description="将 RUNNING 任务切换为 COMPLETED；若已是 COMPLETED，则按幂等成功返回当前任务快照。",
    )
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def complete(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 mission_id 完成任务”的基础状态流转入口（POST /api/v1/missions/{id}/complete），
        # 用于将执行中的任务显式置为已完成。
        #
        # 适用边界：
        # 1) 仅处理 mission.status 自身流转，不承担飞行记录补录、无人机回收或其他跨实体编排；
        # 2) 请求体必须为空；
        # 3) 已完成任务重复 complete 按幂等成功返回；
        # 4) 待执行、已暂停、已取消、已失败任务不允许 complete，返回状态冲突。
        if request.data:
            return Response(
                {
                    "business_code": BusinessCode.INVALID_PARAMS,
                    "detail": "complete 请求不支持提交 body 参数",
                    "errors": {"body": "不支持请求体，请移除 body 后重试"},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        mission = self.get_object()
        before_payload = self._mission_payload(mission)

        if mission.status == MissionStatus.COMPLETED:
            log_action(
                request=request,
                action="MISSION_COMPLETE",
                target_type="mission",
                target_id=mission.id,
                before_data=before_payload,
                after_data=before_payload,
            )
            return Response(before_payload, status=status.HTTP_200_OK)

        if mission.status in {MissionStatus.PENDING, MissionStatus.PAUSED, MissionStatus.CANCELED, MissionStatus.FAILED}:
            return Response(
                {
                    "business_code": BusinessCode.STATE_CONFLICT,
                    "business_detail_code": "STATE_CONFLICT",
                    "detail": "当前任务状态不允许完成",
                    "mission_id": mission.id,
                    "status": mission.status,
                },
                status=status.HTTP_409_CONFLICT,
            )

        mission.status = MissionStatus.COMPLETED
        mission.save(update_fields=["status", "updated_at"])
        after_payload = self._mission_payload(mission)
        log_action(
            request=request,
            action="MISSION_COMPLETE",
            target_type="mission",
            target_id=mission.id,
            before_data=before_payload,
            after_data=after_payload,
        )
        return Response(after_payload, status=status.HTTP_200_OK)

    @_mission_transition_schema(
        summary="标记任务失败",
        description="将 RUNNING 任务切换为 FAILED；若已是 FAILED，则按幂等成功返回当前任务快照。",
    )
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def fail(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 mission_id 标记任务失败”的基础状态流转入口（POST /api/v1/missions/{id}/fail），
        # 用于将执行中的任务显式置为执行失败。
        #
        # 适用边界：
        # 1) 仅处理 mission.status 自身流转，不承担飞行记录补录、无人机回收或其他跨实体编排；
        # 2) 请求体必须为空；
        # 3) 已失败任务重复 fail 按幂等成功返回；
        # 4) 待执行、已暂停、已完成、已取消任务不允许 fail，返回状态冲突。
        if request.data:
            return Response(
                {
                    "business_code": BusinessCode.INVALID_PARAMS,
                    "detail": "fail 请求不支持提交 body 参数",
                    "errors": {"body": "不支持请求体，请移除 body 后重试"},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        mission = self.get_object()
        before_payload = self._mission_payload(mission)

        if mission.status == MissionStatus.FAILED:
            log_action(
                request=request,
                action="MISSION_FAIL",
                target_type="mission",
                target_id=mission.id,
                before_data=before_payload,
                after_data=before_payload,
            )
            return Response(before_payload, status=status.HTTP_200_OK)

        if mission.status in {MissionStatus.PENDING, MissionStatus.PAUSED, MissionStatus.COMPLETED, MissionStatus.CANCELED}:
            return Response(
                {
                    "business_code": BusinessCode.STATE_CONFLICT,
                    "business_detail_code": "STATE_CONFLICT",
                    "detail": "当前任务状态不允许标记失败",
                    "mission_id": mission.id,
                    "status": mission.status,
                },
                status=status.HTTP_409_CONFLICT,
            )

        mission.status = MissionStatus.FAILED
        mission.save(update_fields=["status", "updated_at"])
        after_payload = self._mission_payload(mission)
        log_action(
            request=request,
            action="MISSION_FAIL",
            target_type="mission",
            target_id=mission.id,
            before_data=before_payload,
            after_data=after_payload,
        )
        return Response(after_payload, status=status.HTTP_200_OK)

    def partial_update(self, request, *args, **kwargs):
        # 业务作用：
        # 提供任务的最小可组合更新入口（PATCH /api/v1/missions/{id}），
        # 仅更新请求体中明确给出的字段，避免一次性整体替换任务快照。
        #
        # 适用边界：
        # 1) 允许更新任务基础属性（如 name/scheduled_at/remark）及可重绑定资源（route/drone/pilot）；
        # 2) 不承担任务状态流转编排（开始/暂停/完成/取消），状态字段在此接口不可写；
        # 3) 成功返回最新任务快照，业务码由统一响应层补齐。
        return super().partial_update(request, *args, **kwargs)

    @transaction.atomic
    def perform_create(self, serializer):
        mission = serializer.save(tenant=self.get_current_tenant())
        mission_payload = self._mission_payload(mission)
        log_action(
            request=self.request,
            action="MISSION_CREATE",
            target_type="mission",
            target_id=mission.id,
            after_data=mission_payload,
        )
        return mission

    @transaction.atomic
    def perform_update(self, serializer):
        mission = self.get_object()
        before_payload = self._mission_payload(mission)
        mission = serializer.save()
        after_payload = self._mission_payload(mission)
        log_action(
            request=self.request,
            action="MISSION_UPDATE",
            target_type="mission",
            target_id=mission.id,
            before_data=before_payload,
            after_data=after_payload,
        )
        return mission
