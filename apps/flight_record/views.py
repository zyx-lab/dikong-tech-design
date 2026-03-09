from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission
from apps.access.services import log_action
from apps.api_v1.business_response import BusinessApiResponseMixin
from apps.flight_record.models import FlightRecord
from apps.flight_record.serializers import FlightRecordReadSerializer, FlightRecordWriteSerializer


class FlightRecordViewSet(
    BusinessApiResponseMixin,
    PermissionMapMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """飞行记录业务接口（V1）。"""

    queryset = FlightRecord.objects.select_related("mission", "drone", "pilot").all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "patch", "head", "options"]

    permission_map = {
        "list": "flight_record.view_flight_record",
        "retrieve": "flight_record.view_flight_record",
        "create": "flight_record.manage_flight_record",
        "partial_update": "flight_record.manage_flight_record",
    }

    def get_serializer_class(self):
        if self.action in {"create", "partial_update"}:
            return FlightRecordWriteSerializer
        return FlightRecordReadSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
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

        return queryset

    def list(self, request, *args, **kwargs):
        # 边界与返回语义：
        # 1) 有查看权限：返回分页列表，business_code=SUCCESS（HTTP 200）；
        # 2) 未认证或无查看权限：business_code=PERMISSION_DENIED（HTTP 401/403）。
        # 业务码字段 business_code/business_detail_code 由 BusinessApiResponseMixin 统一补齐。
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        # 业务作用：
        # 创建一条飞行记录主数据，作为“任务执行结果沉淀 -> 媒体归档 -> 复盘查询”的基础起点能力。
        # 外部系统可在创建后继续组合媒体写入、统计分析、详情查询等后续流程。
        #
        # 适用边界：
        # 1) 本接口只创建单条 flight_record，不承担任务状态编排或批量导入；
        # 2) 可选绑定 mission/drone/pilot，若绑定则会自动回填冗余名称字段；
        # 3) 响应统一携带 business_code/business_detail_code。
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = serializer.save()

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

    def retrieve(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 flight_record_id 精确读取飞行记录详情”的基础只读能力，
        # 供外部系统在任务复盘、媒体检索、异常追踪前获取单次飞行执行快照。
        # 本接口只负责详情读取，不承担飞行记录创建、编辑、状态流转等写操作。
        #
        # 边界与返回语义：
        # 1) 有查看权限且记录存在：返回飞行记录详情，business_code=SUCCESS（HTTP 200）；
        # 2) 记录不存在：business_code=RESOURCE_NOT_FOUND（HTTP 404）；
        # 3) 未认证或无查看权限：business_code=PERMISSION_DENIED（HTTP 401/403）。
        # 业务码字段 business_code/business_detail_code 由 BusinessApiResponseMixin 统一补齐。
        return super().retrieve(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 flight_record_id 局部更新飞行记录主数据”的基础接口（PATCH /api/v1/flight-records/{id}），
        # 允许外部系统修正机场、飞行时间、图片视频数量、绑定关系与结果状态等执行结果元数据。
        #
        # 适用边界：
        # 1) 仅更新单条 flight_record 自身字段，不承担媒体文件编排、删除或跨实体状态流转；
        # 2) PATCH 请求体必须至少包含一个可写字段；
        # 3) 成功返回最新 flight_record 快照，业务码由统一响应层补齐。
        if not request.data:
            return Response(
                {
                    "business_code": "INVALID_PARAMS",
                    "detail": "PATCH 请求至少包含一个可写字段",
                    "errors": {"body": "请至少提交一个可写字段"},
                },
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
