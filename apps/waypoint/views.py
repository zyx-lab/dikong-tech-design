from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission
from apps.access.services import log_action
from apps.api_v1.business_response import BusinessApiResponseMixin
from apps.waypoint.models import Waypoint
from apps.waypoint.serializers import WaypointReadSerializer, WaypointWriteSerializer


class WaypointViewSet(
    BusinessApiResponseMixin,
    PermissionMapMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """航点业务接口（V1）。"""

    queryset = Waypoint.objects.select_related("route").all().order_by("route_id", "sequence", "id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "head", "options"]

    permission_map = {
        "list": "waypoint.view_waypoint",
        "retrieve": "waypoint.view_waypoint",
        "create": "waypoint.manage_waypoint",
    }

    def get_serializer_class(self):
        if self.action == "create":
            return WaypointWriteSerializer
        return WaypointReadSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        # 业务作用：
        # 提供航点基础读取接口（GET /api/v1/waypoints），供外部系统按 route_id/sequence 查询航点，
        # 与 POST 能力组合形成“写入后查询校验”的最小闭环。
        #
        # 设计边界：
        # 1) 仅做列表读取，不承担航点编辑、删除、重排等编排行为；
        # 2) 过滤参数仅提供基础维度，复杂业务编排由外部系统组合实现；
        # 3) 返回统一携带 business_code/business_detail_code。
        route_id = params.get("route_id")
        sequence = params.get("sequence")
        if route_id:
            queryset = queryset.filter(route_id=route_id)
        if sequence:
            queryset = queryset.filter(sequence=sequence)
        return queryset

    def list(self, request, *args, **kwargs):
        # 返回语义：
        # 1) 有查看权限：HTTP 200 + business_code=SUCCESS；
        # 2) 未认证或无权限：HTTP 401/403 + business_code=PERMISSION_DENIED。
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按航点 ID 精确读取”的基础接口（GET /api/v1/waypoints/{id}），
        # 让外部系统在写入后可做单条核验或按 ID 进行后续业务编排。
        #
        # 设计边界：
        # 1) 只做单条读取，不承担状态流转、编辑、删除等动作；
        # 2) 不新增业务编排语义，仅暴露最小读能力；
        # 3) 成功/失败均由统一响应层补齐 business_code/business_detail_code。
        return super().retrieve(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“向指定航线新增航点”的最小基础写接口（POST /api/v1/waypoints），
        # 让外部系统可以组合“创建航线 -> 批量写入航点 -> 创建任务”的业务流。
        #
        # 设计边界：
        # 1) 本接口只负责新增单条航点，不承担航点批量导入、航线重排、任务编排等职责；
        # 2) 仅允许写入 ACTIVE 航线，并保证同一航线下 sequence 不重复；
        # 3) 成功返回航点快照，business_code/business_detail_code 由统一响应层补齐。
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        waypoint = self.perform_create(serializer)
        read_serializer = WaypointReadSerializer(waypoint, context={"request": request})
        headers = self.get_success_headers(read_serializer.data)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @transaction.atomic
    def perform_create(self, serializer):
        waypoint = serializer.save()
        route = waypoint.route

        # 航点写入后同步更新航线的 waypoint_count，保证 routes 台账中的冗余计数可直接查询。
        route.waypoint_count = Waypoint.objects.filter(route_id=route.id).count()
        route.save(update_fields=["waypoint_count", "updated_at"])

        waypoint_payload = dict(WaypointReadSerializer(waypoint, context={"request": self.request}).data)
        log_action(
            request=self.request,
            action="WAYPOINT_CREATE",
            target_type="waypoint",
            target_id=waypoint.id,
            after_data=waypoint_payload,
        )
        return waypoint
