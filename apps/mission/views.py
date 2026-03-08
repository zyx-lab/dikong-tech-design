from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission
from apps.access.services import log_action
from apps.api_v1.business_response import BusinessApiResponseMixin
from apps.mission.models import Mission
from apps.mission.serializers import MissionReadSerializer, MissionWriteSerializer


class MissionViewSet(
    BusinessApiResponseMixin,
    PermissionMapMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """任务业务接口（V1）。"""

    queryset = Mission.objects.select_related("route", "drone", "pilot").all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "head", "options"]

    permission_map = {
        "list": "mission.view_mission",
        "retrieve": "mission.view_mission",
        "create": "mission.manage_mission",
    }

    def get_serializer_class(self):
        if self.action == "create":
            return MissionWriteSerializer
        return MissionReadSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
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

    @transaction.atomic
    def perform_create(self, serializer):
        mission = serializer.save()
        mission_payload = dict(MissionReadSerializer(mission, context={"request": self.request}).data)
        log_action(
            request=self.request,
            action="MISSION_CREATE",
            target_type="mission",
            target_id=mission.id,
            after_data=mission_payload,
        )
        return mission
