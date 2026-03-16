from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission, ScopedQuerysetMixin
from apps.access.services import log_action
from apps.api_v1.business_response import BusinessApiResponseMixin, StandardCode, standard_error_payload
from apps.api_v1.schema import (
    BUSINESS_INTERNAL_ERROR_RESPONSE,
    TENANT_CODE_HEADER_PARAMETER,
    business_error_example,
    business_error_response,
    object_envelope_serializer,
    paginated_envelope_serializer,
)
from apps.api_v1.tenant_scope import TenantScopedBusinessMixin
from apps.flight_record.models import FlightRecord, FlightRecordStatus
from apps.flight_record.serializers import FlightRecordReadSerializer, FlightRecordWriteSerializer


FLIGHT_RECORD_LIST_RESPONSE = paginated_envelope_serializer("FlightRecordListResponse", FlightRecordReadSerializer)
FLIGHT_RECORD_DETAIL_RESPONSE = object_envelope_serializer("FlightRecordDetailResponse", FlightRecordReadSerializer)

FLIGHT_RECORD_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(
        name="mission_id",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按关联任务 ID 精确过滤。",
    ),
    OpenApiParameter(
        name="drone_id",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按执行无人机 ID 精确过滤。",
    ),
    OpenApiParameter(
        name="pilot_id",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按执行飞手成员 ID 精确过滤。",
    ),
    OpenApiParameter(
        name="status",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按飞行记录状态精确过滤。",
        enum=[choice[0] for choice in FlightRecordStatus.choices],
    ),
    OpenApiParameter(
        name="flight_no",
        type=str,
        location=OpenApiParameter.QUERY,
        description="按架次编号做模糊匹配。",
    ),
]

FLIGHT_RECORD_PERMISSION_DENIED_RESPONSE = business_error_response(
    description="未认证、无权限、缺少租户上下文，或 platform_admin 访问业务 API 被拒绝。",
    examples=[
        business_error_example(
            "未登录",
            code="A0401",
            msg="登录状态已失效",
            status_codes=["401"],
        ),
        business_error_example(
            "无权限",
            code="A0403",
            msg="无操作权限",
            status_codes=["403"],
        ),
        business_error_example(
            "缺少租户上下文",
            code="A0403",
            msg="缺少租户上下文",
            status_codes=["403"],
        ),
        business_error_example(
            "平台管理员访问业务 API",
            code="A0403",
            msg="平台管理员不可访问租户业务接口",
            status_codes=["403"],
        ),
    ],
)

FLIGHT_RECORD_INVALID_PARAMS_RESPONSE = business_error_response(
    description="请求体不合法或绑定关系不满足约束；架次编号重复时会返回 IDEMPOTENT_DUPLICATE。",
    examples=[
        business_error_example(
            "缺少架次编号",
            code="B0001",
            msg="参数校验失败",
            status_codes=["400"],
            data={"flight_no": ["该字段是必填项。"]},
        ),
        business_error_example(
            "架次编号重复",
            code="C0101",
            msg="资源已存在",
            status_codes=["400"],
            data={"flight_no": ["当前租户下已存在相同架次编号"]},
        ),
        business_error_example(
            "PATCH 直接改状态",
            code="B0001",
            msg="参数校验失败",
            status_codes=["400"],
            data={"status": ["status 不可通过 PATCH 直接修改，请使用状态动作接口"]},
        ),
        business_error_example(
            "动作接口提交 body",
            code="B0001",
            msg="complete 请求不支持提交 body 参数",
            status_codes=["400"],
            data={"body": "不支持请求体，请移除 body 后重试"},
        ),
    ],
)

FLIGHT_RECORD_NOT_FOUND_RESPONSE = business_error_response(
    description="目标飞行记录不存在，或在当前租户/授权作用域下不可见。",
    examples=[
        business_error_example(
            "飞行记录不存在",
            code="C0404",
            msg="资源不存在",
            status_codes=["404"],
        )
    ],
)

FLIGHT_RECORD_STATE_CONFLICT_RESPONSE = business_error_response(
    description="飞行记录当前状态不允许本次流转，code 固定为 C0201 / C0202。",
    examples=[
        business_error_example(
            "异常终止后不可完成",
            code="C0201",
            msg="当前飞行记录状态不允许完成",
            status_codes=["409"],
            data={"flight_record_id": 101, "status": FlightRecordStatus.ABORTED},
        ),
        business_error_example(
            "已完成后不可异常终止",
            code="C0201",
            msg="当前飞行记录状态不允许异常终止",
            status_codes=["409"],
            data={"flight_record_id": 102, "status": FlightRecordStatus.COMPLETED},
        ),
    ],
)


def _flight_record_transition_schema(*, summary, description):
    return extend_schema(
        summary=summary,
        description=description,
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=FLIGHT_RECORD_DETAIL_RESPONSE, description="状态流转成功；若命中幂等条件，返回当前飞行记录快照。"),
            400: FLIGHT_RECORD_INVALID_PARAMS_RESPONSE,
            401: FLIGHT_RECORD_PERMISSION_DENIED_RESPONSE,
            403: FLIGHT_RECORD_PERMISSION_DENIED_RESPONSE,
            404: FLIGHT_RECORD_NOT_FOUND_RESPONSE,
            409: FLIGHT_RECORD_STATE_CONFLICT_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Flight Record"],
    )


@extend_schema_view(
    list=extend_schema(
        summary="查询飞行记录列表",
        description="按当前租户查询飞行记录，支持按任务、无人机、飞手、状态、架次编号过滤。",
        parameters=FLIGHT_RECORD_FILTER_PARAMETERS,
        responses={
            200: OpenApiResponse(response=FLIGHT_RECORD_LIST_RESPONSE, description="查询成功。"),
            401: FLIGHT_RECORD_PERMISSION_DENIED_RESPONSE,
            403: FLIGHT_RECORD_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Flight Record"],
    ),
    retrieve=extend_schema(
        summary="读取飞行记录详情",
        description="按飞行记录 ID 读取单条执行详情。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=FLIGHT_RECORD_DETAIL_RESPONSE, description="读取成功。"),
            401: FLIGHT_RECORD_PERMISSION_DENIED_RESPONSE,
            403: FLIGHT_RECORD_PERMISSION_DENIED_RESPONSE,
            404: FLIGHT_RECORD_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Flight Record"],
    ),
    create=extend_schema(
        summary="创建飞行记录",
        description="创建一条飞行记录主数据，可绑定任务、无人机和飞手，并沉淀执行结果元数据。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=FlightRecordWriteSerializer,
        examples=[
            OpenApiExample(
                "创建飞行记录请求",
                request_only=True,
                value={
                    "flight_no": "YJ202603180001",
                    "mission": 1,
                    "drone": 2,
                    "pilot": 8,
                    "airport_name": "珠海金湾机场",
                    "start_time": "2026-03-18T08:00:00+08:00",
                    "end_time": "2026-03-18T08:18:20+08:00",
                    "photo_count": 20,
                    "video_count": 4,
                    "status": FlightRecordStatus.COMPLETED,
                },
            )
        ],
        responses={
            201: OpenApiResponse(response=FLIGHT_RECORD_DETAIL_RESPONSE, description="创建成功。"),
            400: FLIGHT_RECORD_INVALID_PARAMS_RESPONSE,
            401: FLIGHT_RECORD_PERMISSION_DENIED_RESPONSE,
            403: FLIGHT_RECORD_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Flight Record"],
    ),
    update=extend_schema(
        summary="全量更新飞行记录",
        description="按飞行记录 ID 全量更新执行结果元数据；不允许在该接口直接修改状态。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=FlightRecordWriteSerializer,
        responses={
            200: OpenApiResponse(response=FLIGHT_RECORD_DETAIL_RESPONSE, description="更新成功。"),
            400: FLIGHT_RECORD_INVALID_PARAMS_RESPONSE,
            401: FLIGHT_RECORD_PERMISSION_DENIED_RESPONSE,
            403: FLIGHT_RECORD_PERMISSION_DENIED_RESPONSE,
            404: FLIGHT_RECORD_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Flight Record"],
    ),
    partial_update=extend_schema(
        summary="局部更新飞行记录",
        description="按飞行记录 ID 局部更新执行结果元数据；不允许在该接口直接修改状态。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=FlightRecordWriteSerializer,
        examples=[
            OpenApiExample(
                "PATCH 飞行记录请求",
                request_only=True,
                value={"airport_name": "珠海金湾机场 T2", "photo_count": 22, "video_count": 5},
            )
        ],
        responses={
            200: OpenApiResponse(response=FLIGHT_RECORD_DETAIL_RESPONSE, description="更新成功。"),
            400: FLIGHT_RECORD_INVALID_PARAMS_RESPONSE,
            401: FLIGHT_RECORD_PERMISSION_DENIED_RESPONSE,
            403: FLIGHT_RECORD_PERMISSION_DENIED_RESPONSE,
            404: FLIGHT_RECORD_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Flight Record"],
    ),
)
class FlightRecordViewSet(
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
    """飞行记录业务接口（V1）。"""

    queryset = FlightRecord.objects.select_related("mission", "drone", "pilot__user__staff_profile").all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    permission_map = {
        "list": "flight_record.view_flight_record",
        "retrieve": "flight_record.view_flight_record",
        "create": "flight_record.manage_flight_record",
        "update": "flight_record.manage_flight_record",
        "partial_update": "flight_record.manage_flight_record",
        "complete": "flight_record.manage_flight_record",
        "abort": "flight_record.manage_flight_record",
    }

    @staticmethod
    def assigned_scope_filter_builder(tenant_member_id: int) -> dict:
        return {"pilot_id": tenant_member_id}

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return FlightRecordWriteSerializer
        return FlightRecordReadSerializer

    def _record_payload(self, record: FlightRecord) -> dict:
        return dict(FlightRecordReadSerializer(record, context={"request": self.request}).data)

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset())
        params = self.request.query_params

        # 业务作用：
        # 提供“飞行记录列表检索”能力，作为飞行复盘场景的基础入口，
        # 供外部系统先按 mission/drone/pilot/status/flight_no 条件筛选，再按 id 拉取详情。
        # 本接口只负责只读查询，不承担飞行记录创建、编辑、状态流转等写操作。
        mission_id = params.get("mission_id")
        drone_id = params.get("drone_id")
        pilot_id = params.get("pilot_id")
        status_value = params.get("status")
        flight_no = params.get("flight_no")

        if mission_id:
            queryset = queryset.filter(mission_id=mission_id)
        if drone_id:
            queryset = queryset.filter(drone_id=drone_id)
        if pilot_id:
            queryset = queryset.filter(pilot_id=pilot_id)
        if status_value:
            queryset = queryset.filter(status=status_value)
        if flight_no:
            queryset = queryset.filter(flight_no__icontains=flight_no)

        if self.action in {"list", "retrieve", "update", "partial_update", "complete", "abort"}:
            return self.apply_scope(queryset)

        return queryset

    def list(self, request, *args, **kwargs):
        # 边界与返回语义：
        # 1) 有查看权限：返回分页列表，code=00000（HTTP 200）；
        # 2) 未认证或无查看权限：code=A0401/A0403（HTTP 401/403）。
        # 响应由 BusinessApiResponseMixin 统一包装为 `code / msg / data`。
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        # 业务作用：
        # 创建一条飞行记录主数据，作为“任务执行结果沉淀 -> 媒体归档 -> 复盘查询”的基础起点能力。
        # 外部系统可在创建后继续组合媒体写入、统计分析、详情查询等后续流程。
        #
        # 适用边界：
        # 1) 本接口只创建单条 flight_record，不承担任务状态编排或批量导入；
        # 2) 可选绑定 mission/drone/pilot，若绑定则会自动回填冗余名称字段；
        # 3) 响应统一携带 code/msg/data。
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = self.perform_create(serializer)

        read_serializer = FlightRecordReadSerializer(record, context={"request": request})
        log_action(
            request=request,
            action="FLIGHT_RECORD_CREATE",
            target_type="flight_record",
            target_id=record.id,
            after_data=dict(read_serializer.data),
        )
        headers = self.get_success_headers(read_serializer.data)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @transaction.atomic
    def perform_create(self, serializer):
        return serializer.save(tenant=self.get_current_tenant())

    def retrieve(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 flight_record_id 精确读取飞行记录详情”的基础只读能力，
        # 供外部系统在任务复盘、媒体检索、异常追踪前获取单次飞行执行快照。
        # 本接口只负责详情读取，不承担飞行记录创建、编辑、状态流转等写操作。
        #
        # 边界与返回语义：
        # 1) 有查看权限且记录存在：返回飞行记录详情，code=00000（HTTP 200）；
        # 2) 记录不存在：code=C0404（HTTP 404）；
        # 3) 未认证或无查看权限：code=A0401/A0403（HTTP 401/403）。
        # 响应由 BusinessApiResponseMixin 统一包装为 `code / msg / data`。
        return super().retrieve(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 flight_record_id 局部更新飞行记录主数据”的基础接口（PATCH /api/v1/flight-records/{id}），
        # 允许外部系统修正机场、飞行时间、图片视频数量与绑定关系等执行结果元数据。
        #
        # 适用边界：
        # 1) 仅更新单条 flight_record 自身字段，不承担媒体文件编排、删除或状态流转；
        # 2) PATCH 请求体必须至少包含一个可写字段；
        # 3) 成功返回最新 flight_record 快照，业务码由统一响应层补齐。
        if not request.data:
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "PATCH 请求至少包含一个可写字段",
                    {"body": "请至少提交一个可写字段"},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        record = self.perform_update(serializer)

        if getattr(instance, "_prefetched_objects_cache", None):
            instance._prefetched_objects_cache = {}

        read_serializer = FlightRecordReadSerializer(record, context={"request": request})
        return Response(read_serializer.data, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=False)
        serializer.is_valid(raise_exception=True)
        record = self.perform_update(serializer)

        if getattr(instance, "_prefetched_objects_cache", None):
            instance._prefetched_objects_cache = {}

        read_serializer = FlightRecordReadSerializer(record, context={"request": request})
        return Response(read_serializer.data, status=status.HTTP_200_OK)

    @_flight_record_transition_schema(
        summary="完成飞行记录",
        description="将 IN_PROGRESS 记录切换为 COMPLETED；若已是 COMPLETED，则按幂等成功返回。",
    )
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def complete(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 flight_record_id 完成飞行记录”的基础状态流转入口（POST /api/v1/flight-records/{id}/complete），
        # 用于将飞行中的记录显式置为已完成。
        #
        # 适用边界：
        # 1) 仅处理 flight_record.status 自身流转，不承担任务状态联动、媒体归档或统计编排；
        # 2) 请求体必须为空；
        # 3) 已完成记录重复 complete 按幂等成功返回；
        # 4) 已异常终止记录不允许 complete，返回状态冲突。
        if request.data:
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "complete 请求不支持提交 body 参数",
                    {"body": "不支持请求体，请移除 body 后重试"},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        record = self.get_object()
        before_payload = self._record_payload(record)

        if record.status == FlightRecordStatus.COMPLETED:
            log_action(
                request=request,
                action="FLIGHT_RECORD_COMPLETE",
                target_type="flight_record",
                target_id=record.id,
                before_data=before_payload,
                after_data=before_payload,
            )
            return Response(before_payload, status=status.HTTP_200_OK)

        if record.status == FlightRecordStatus.ABORTED:
            return Response(
                standard_error_payload(
                    StandardCode.STATE_CONFLICT,
                    "当前飞行记录状态不允许完成",
                    {"flight_record_id": record.id, "status": record.status},
                ),
                status=status.HTTP_409_CONFLICT,
            )

        record.status = FlightRecordStatus.COMPLETED
        record.save(update_fields=["status", "updated_at"])
        after_payload = self._record_payload(record)
        log_action(
            request=request,
            action="FLIGHT_RECORD_COMPLETE",
            target_type="flight_record",
            target_id=record.id,
            before_data=before_payload,
            after_data=after_payload,
        )
        return Response(after_payload, status=status.HTTP_200_OK)

    @_flight_record_transition_schema(
        summary="异常终止飞行记录",
        description="将 IN_PROGRESS 记录切换为 ABORTED；若已是 ABORTED，则按幂等成功返回。",
    )
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def abort(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 flight_record_id 异常终止飞行记录”的基础状态流转入口（POST /api/v1/flight-records/{id}/abort），
        # 用于将飞行中的记录显式置为异常终止。
        #
        # 适用边界：
        # 1) 仅处理 flight_record.status 自身流转，不承担任务状态联动、媒体归档或统计编排；
        # 2) 请求体必须为空；
        # 3) 已异常终止记录重复 abort 按幂等成功返回；
        # 4) 已完成记录不允许 abort，返回状态冲突。
        if request.data:
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "abort 请求不支持提交 body 参数",
                    {"body": "不支持请求体，请移除 body 后重试"},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        record = self.get_object()
        before_payload = self._record_payload(record)

        if record.status == FlightRecordStatus.ABORTED:
            log_action(
                request=request,
                action="FLIGHT_RECORD_ABORT",
                target_type="flight_record",
                target_id=record.id,
                before_data=before_payload,
                after_data=before_payload,
            )
            return Response(before_payload, status=status.HTTP_200_OK)

        if record.status == FlightRecordStatus.COMPLETED:
            return Response(
                standard_error_payload(
                    StandardCode.STATE_CONFLICT,
                    "当前飞行记录状态不允许异常终止",
                    {"flight_record_id": record.id, "status": record.status},
                ),
                status=status.HTTP_409_CONFLICT,
            )

        record.status = FlightRecordStatus.ABORTED
        record.save(update_fields=["status", "updated_at"])
        after_payload = self._record_payload(record)
        log_action(
            request=request,
            action="FLIGHT_RECORD_ABORT",
            target_type="flight_record",
            target_id=record.id,
            before_data=before_payload,
            after_data=after_payload,
        )
        return Response(after_payload, status=status.HTTP_200_OK)

    @transaction.atomic
    def perform_update(self, serializer):
        record = self.get_object()
        before_payload = dict(FlightRecordReadSerializer(record, context={"request": self.request}).data)
        record = serializer.save()
        after_payload = dict(FlightRecordReadSerializer(record, context={"request": self.request}).data)
        log_action(
            request=self.request,
            action="FLIGHT_RECORD_UPDATE",
            target_type="flight_record",
            target_id=record.id,
            before_data=before_payload,
            after_data=after_payload,
        )
        return record
