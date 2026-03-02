from django.db import transaction
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission, ScopedQuerysetMixin
from apps.access.services import IdentityService, log_action, snapshot
from apps.drone.models import Drone, DroneAssignment, DroneAssignmentStatus, DroneStatus
from apps.drone.serializers import (
    DroneAssignmentCreateSerializer,
    DroneAssignmentReadSerializer,
    DroneReadSerializer,
    DroneWriteSerializer,
)


class DroneViewSet(
    PermissionMapMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """无人机业务接口（V1）。"""

    queryset = Drone.objects.all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    permission_map = {
        "list": "drone.view_drone",
        "retrieve": "drone.view_drone",
        "create": "drone.manage_drone",
        "update": "drone.manage_drone",
        "partial_update": "drone.manage_drone",
        "enable": "drone.change_drone_status",
        "disable": "drone.change_drone_status",
        "maintenance": "drone.change_drone_status",
        "retire": "drone.change_drone_status",
    }

    @staticmethod
    def assigned_scope_filter_builder(staff_id: int) -> dict:
        return {
            "assignments__staff_id": staff_id,
            "assignments__status": DroneAssignmentStatus.ACTIVE,
        }

    def get_serializer_class(self):
        if self.action in {"list", "retrieve", "enable", "disable", "maintenance", "retire"}:
            return DroneReadSerializer
        return DroneWriteSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        code = params.get("code")
        name = params.get("name")
        model = params.get("model")
        status_value = params.get("status")
        org_id = params.get("org_id")

        if code:
            queryset = queryset.filter(code__icontains=code)
        if name:
            queryset = queryset.filter(name__icontains=name)
        if model:
            queryset = queryset.filter(model__icontains=model)
        if status_value:
            queryset = queryset.filter(status=status_value)
        if org_id:
            queryset = queryset.filter(org_id=org_id)

        # V1 虽然仅配置 ALL，但仍统一走 scope 收敛流程，避免后续扩展时遗漏。
        if self.action in {"list", "retrieve", "update", "partial_update", "enable", "disable", "maintenance", "retire"}:
            return self.apply_scope(queryset)
        return queryset

    @transaction.atomic
    def perform_create(self, serializer):
        staff = IdentityService.get_staff(self.request.user)
        drone = serializer.save(created_by_staff_id=staff.id if staff else None)
        log_action(
            request=self.request,
            action="DRONE_CREATE",
            target_type="drone",
            target_id=drone.id,
            after_data=snapshot(drone),
        )

    @transaction.atomic
    def perform_update(self, serializer):
        before_data = snapshot(self.get_object())
        drone = serializer.save()
        log_action(
            request=self.request,
            action="DRONE_UPDATE",
            target_type="drone",
            target_id=drone.id,
            before_data=before_data,
            after_data=snapshot(drone),
        )

    def _status_response(self, drone: Drone) -> Response:
        serializer = DroneReadSerializer(drone, context={"request": self.request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @transaction.atomic
    def _change_status(self, drone: Drone, target_status: str) -> Response:
        before_data = snapshot(drone)

        # 幂等：重复提交同一状态，直接返回 200。
        if drone.status == target_status:
            log_action(
                request=self.request,
                action="DRONE_STATUS_CHANGE",
                target_type="drone",
                target_id=drone.id,
                before_data=before_data,
                after_data=before_data,
            )
            return self._status_response(drone)

        # V1 最小状态机：RETIRED 不可逆。
        if drone.status == DroneStatus.RETIRED and target_status != DroneStatus.RETIRED:
            return Response(
                {
                    "detail": "RETIRED 状态不可逆，不能变更为其他状态",
                    "current_status": drone.status,
                    "target_status": target_status,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        drone.status = target_status
        drone.save(update_fields=["status", "updated_at"])

        log_action(
            request=self.request,
            action="DRONE_STATUS_CHANGE",
            target_type="drone",
            target_id=drone.id,
            before_data=before_data,
            after_data=snapshot(drone),
        )
        return self._status_response(drone)

    @extend_schema(request=None, responses=DroneReadSerializer)
    @action(detail=True, methods=["post"])
    def enable(self, request, *args, **kwargs):
        return self._change_status(self.get_object(), DroneStatus.ENABLED)

    @extend_schema(request=None, responses=DroneReadSerializer)
    @action(detail=True, methods=["post"])
    def disable(self, request, *args, **kwargs):
        return self._change_status(self.get_object(), DroneStatus.DISABLED)

    @extend_schema(request=None, responses=DroneReadSerializer)
    @action(detail=True, methods=["post"])
    def maintenance(self, request, *args, **kwargs):
        return self._change_status(self.get_object(), DroneStatus.MAINTENANCE)

    @extend_schema(request=None, responses=DroneReadSerializer)
    @action(detail=True, methods=["post"])
    def retire(self, request, *args, **kwargs):
        return self._change_status(self.get_object(), DroneStatus.RETIRED)


class DroneAssignmentViewSet(
    PermissionMapMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """无人机分配接口。

    用途：
    - 由运营/调度维护无人机与飞手关系。
    - 作为飞手 ASSIGNED 范围的授权事实来源。
    """

    queryset = DroneAssignment.objects.select_related("drone", "staff", "staff__staff_type").all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "head", "options"]

    permission_map = {
        "list": "drone.manage_drone_assignment",
        "retrieve": "drone.manage_drone_assignment",
        "create": "drone.manage_drone_assignment",
        "cancel": "drone.manage_drone_assignment",
    }

    def get_serializer_class(self):
        if self.action == "create":
            return DroneAssignmentCreateSerializer
        return DroneAssignmentReadSerializer

    def _assignment_payload(self, assignment: DroneAssignment) -> dict:
        # 避免 datetime 直接写入 JSONField，统一复用序列化结果（字符串时间）。
        return dict(DroneAssignmentReadSerializer(assignment, context={"request": self.request}).data)

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        drone_id = params.get("drone_id")
        staff_id = params.get("staff_id")
        status_value = params.get("status")

        if drone_id:
            queryset = queryset.filter(drone_id=drone_id)
        if staff_id:
            queryset = queryset.filter(staff_id=staff_id)
        if status_value:
            queryset = queryset.filter(status=status_value)

        if self.action in {"list", "retrieve", "cancel"}:
            return self.apply_scope(queryset)
        return queryset

    @transaction.atomic
    def perform_create(self, serializer):
        operator_staff = IdentityService.get_staff(self.request.user)
        assignment = serializer.save(created_by_staff_id=operator_staff.id if operator_staff else None)
        log_action(
            request=self.request,
            action="DRONE_ASSIGNMENT_CREATE",
            target_type="drone_assignment",
            target_id=assignment.id,
            after_data=self._assignment_payload(assignment),
        )

    @extend_schema(request=None, responses=DroneAssignmentReadSerializer)
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def cancel(self, request, *args, **kwargs):
        assignment = self.get_object()
        before_data = self._assignment_payload(assignment)

        # 幂等：重复取消直接返回当前状态。
        if assignment.status == DroneAssignmentStatus.INACTIVE:
            log_action(
                request=request,
                action="DRONE_ASSIGNMENT_CANCEL",
                target_type="drone_assignment",
                target_id=assignment.id,
                before_data=before_data,
                after_data=before_data,
            )
            return Response(before_data, status=status.HTTP_200_OK)

        assignment.status = DroneAssignmentStatus.INACTIVE
        assignment.end_at = timezone.now()
        assignment.save(update_fields=["status", "end_at", "updated_at"])

        log_action(
            request=request,
            action="DRONE_ASSIGNMENT_CANCEL",
            target_type="drone_assignment",
            target_id=assignment.id,
            before_data=before_data,
            after_data=self._assignment_payload(assignment),
        )
        return Response(self._assignment_payload(assignment), status=status.HTTP_200_OK)
