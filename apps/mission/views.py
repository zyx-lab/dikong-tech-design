from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission
from apps.access.services import log_action
from apps.api_v1.business_response import BusinessApiResponseMixin, BusinessCode
from apps.mission.models import Mission, MissionStatus
from apps.mission.serializers import MissionReadSerializer, MissionWriteSerializer


class MissionViewSet(
    BusinessApiResponseMixin,
    PermissionMapMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """任务业务接口（V1）。"""

    queryset = Mission.objects.select_related("route", "drone", "pilot").all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "patch", "head", "options"]

    permission_map = {
        "list": "mission.view_mission",
        "retrieve": "mission.view_mission",
        "create": "mission.manage_mission",
        "partial_update": "mission.manage_mission",
        "start": "mission.manage_mission",
        "pause": "mission.manage_mission",
        "cancel": "mission.manage_mission",
    }

    def get_serializer_class(self):
        if self.action in {"create", "partial_update"}:
            return MissionWriteSerializer
        return MissionReadSerializer

    def _mission_payload(self, mission: Mission) -> dict:
        return dict(MissionReadSerializer(mission, context={"request": self.request}).data)

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
        mission = serializer.save()
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
