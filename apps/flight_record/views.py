from rest_framework import mixins, viewsets

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission
from apps.api_v1.business_response import BusinessApiResponseMixin
from apps.flight_record.models import FlightRecord
from apps.flight_record.serializers import FlightRecordReadSerializer


class FlightRecordViewSet(
    BusinessApiResponseMixin,
    PermissionMapMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """飞行记录业务接口（V1）。"""

    queryset = FlightRecord.objects.select_related("mission", "drone", "pilot").all().order_by("-id")
    serializer_class = FlightRecordReadSerializer
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "head", "options"]

    permission_map = {
        "list": "flight_record.view_flight_record",
        "retrieve": "flight_record.view_flight_record",
    }

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
