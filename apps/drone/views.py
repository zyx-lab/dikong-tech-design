from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ErrorDetail, MethodNotAllowed
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission, ScopedQuerysetMixin
from apps.access.services import IdentityService, log_action, snapshot
from apps.api_v1.business_response import BusinessApiResponseMixin, BusinessCode
from apps.api_v1.tenant_scope import TenantScopedBusinessMixin
from apps.drone.models import Drone, DroneStatus
from apps.drone.serializers import DroneReadSerializer, DroneWriteSerializer
from apps.drone_assignment.models import DroneAssignment, DroneAssignmentStatus
from apps.drone_assignment.serializers import DroneAssignmentReadSerializer


class DroneViewSet(
    BusinessApiResponseMixin,
    TenantScopedBusinessMixin,
    PermissionMapMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """无人机业务接口（V1）。"""

    queryset = Drone.objects.all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    permission_map = {
        "list": "drone.view_drone",
        "retrieve": "drone.view_drone",
        "history": "drone.view_drone",
        "active_assignments": "drone.view_drone",
        "latest_assignment": "drone.view_drone",
        "create": "drone.manage_drone",
        "update": "drone.manage_drone",
        "partial_update": "drone.manage_drone",
        "destroy": "drone.manage_drone",
        "enable": "drone.change_drone_status",
        "disable": "drone.change_drone_status",
        "maintenance": "drone.change_drone_status",
        "retire": "drone.change_drone_status",
    }

    @staticmethod
    def assigned_scope_filter_builder(tenant_member_id: int) -> dict:
        return {
            "assignments__tenant_member_id": tenant_member_id,
            "assignments__status": DroneAssignmentStatus.ACTIVE,
        }

    def get_serializer_class(self):
        if self.action in {"list", "retrieve", "destroy", "enable", "disable", "maintenance", "retire"}:
            return DroneReadSerializer
        return DroneWriteSerializer

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset())
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
        if self.action in {
            "list",
            "retrieve",
            "history",
            "active_assignments",
            "latest_assignment",
            "update",
            "partial_update",
            "destroy",
            "enable",
            "disable",
            "maintenance",
            "retire",
        }:
            return self.apply_scope(queryset)
        return queryset

    @staticmethod
    def _contains_unique_error(errors) -> bool:
        if isinstance(errors, dict):
            return any(DroneViewSet._contains_unique_error(value) for value in errors.values())
        if isinstance(errors, list):
            return any(DroneViewSet._contains_unique_error(item) for item in errors)
        if isinstance(errors, ErrorDetail):
            if getattr(errors, "code", "") == "unique":
                return True
            text = str(errors).lower()
            return "unique" in text or "已存在" in text or "already exists" in text
        if isinstance(errors, str):
            text = errors.lower()
            return "unique" in text or "已存在" in text or "already exists" in text
        return False

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=False)
        if serializer.errors:
            if self._contains_unique_error(serializer.errors):
                return Response(
                    {
                        "business_code": BusinessCode.IDEMPOTENT_DUPLICATE,
                        "detail": "重复提交，资源已存在",
                        "errors": serializer.errors,
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @extend_schema(exclude=True)
    def update(self, request, *args, **kwargs):
        if request.method.upper() == "PUT":
            raise MethodNotAllowed("PUT")
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def perform_create(self, serializer):
        tenant = self.get_current_tenant()
        tenant_member = IdentityService.get_active_tenant_member(self.request.user, tenant)
        drone = serializer.save(
            tenant=tenant,
            created_by_tenant_member_id=tenant_member.id if tenant_member else None,
        )
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
    def destroy(self, request, *args, **kwargs):
        if request.data:
            return Response(
                {
                    "business_code": BusinessCode.INVALID_PARAMS,
                    "detail": "DELETE 请求不支持提交 body 参数",
                    "errors": {"body": "不支持请求体，请移除 body 后重试"},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        drone = self.get_object()

        if DroneAssignment.objects.filter(drone=drone, status=DroneAssignmentStatus.ACTIVE).exists():
            return Response(
                {
                    "business_code": BusinessCode.STATE_CONFLICT,
                    "detail": "无人机存在 ACTIVE 分配关系，不能删除",
                    "drone_id": drone.id,
                },
                status=status.HTTP_409_CONFLICT,
            )

        before_data = snapshot(drone)
        drone_id = drone.id
        drone.delete()
        log_action(
            request=request,
            action="DRONE_DELETE",
            target_type="drone",
            target_id=drone_id,
            before_data=before_data,
            after_data=None,
        )
        return Response({"id": drone_id, "deleted": True}, status=status.HTTP_200_OK)

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
                    "business_code": BusinessCode.STATE_CONFLICT,
                    "detail": "RETIRED 状态不可逆，不能变更为其他状态",
                    "current_status": drone.status,
                    "target_status": target_status,
                },
                status=status.HTTP_409_CONFLICT,
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

    @extend_schema(request=None, responses=DroneAssignmentReadSerializer(many=True))
    @action(detail=True, methods=["get"], url_path="assignments/history")
    def history(self, request, *args, **kwargs):
        drone = self.get_object()
        assignments = (
            DroneAssignment.objects.select_related("drone", "tenant_member__user__staff_profile")
            .filter(drone=drone, tenant=self.get_current_tenant())
            .order_by("-id")
        )
        serializer = DroneAssignmentReadSerializer(assignments, many=True, context={"request": request})
        return Response(
            {
                "drone_id": drone.id,
                "count": len(serializer.data),
                "results": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(request=None, responses=DroneAssignmentReadSerializer(many=True))
    @action(detail=True, methods=["get"], url_path="assignments/active")
    def active_assignments(self, request, *args, **kwargs):
        # 业务作用：
        # 返回“当前时刻仍生效”的无人机分配关系（status=ACTIVE），用于调用方拼装
        # “查询当前分配 -> 取消旧分配 -> 新建分配”等流程，接口自身不承担编排职责。
        #
        # 适用边界：
        # 1) 仅按无人机维度查询，不跨无人机聚合。
        # 2) 不返回 INACTIVE 历史记录；历史全量查询应使用 assignments/history。
        # 3) 通过 get_object() 复用对象级权限与 scope 控制，越权场景返回 PERMISSION_DENIED。
        #
        # 响应语义：
        # - HTTP 200: 查询成功（含 count=0 的空结果）
        # - HTTP 404: 无人机不存在（RESOURCE_NOT_FOUND）
        # - HTTP 401/403: 未认证或无权限（PERMISSION_DENIED）
        # 业务状态码字段 business_code/business_detail_code 由 BusinessApiResponseMixin 统一补齐。
        drone = self.get_object()
        assignments = (
            DroneAssignment.objects.select_related("drone", "tenant_member__user__staff_profile")
            .filter(drone=drone, tenant=self.get_current_tenant(), status=DroneAssignmentStatus.ACTIVE)
            .order_by("-id")
        )
        serializer = DroneAssignmentReadSerializer(assignments, many=True, context={"request": request})
        return Response(
            {
                "drone_id": drone.id,
                "count": len(serializer.data),
                "results": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(request=None, responses=DroneAssignmentReadSerializer)
    @action(detail=True, methods=["get"], url_path="assignments/latest")
    def latest_assignment(self, request, *args, **kwargs):
        # 业务作用：
        # 返回指定无人机“最近一条”分配记录（按 id 倒序取第一条），用于外部快速判断
        # 最新关系状态，再自行组合“取消旧分配/新建分配”等后续动作；接口本身不做编排。
        #
        # 适用边界：
        # 1) 仅按单个无人机查询，不跨无人机聚合。
        # 2) 最近记录可能是 ACTIVE 或 INACTIVE，均按真实最新记录返回。
        # 3) 无分配记录不是错误场景，返回 HTTP 200 且 result 为 null。
        #
        # 响应语义：
        # - HTTP 200: 查询成功（含无记录场景）
        # - HTTP 404: 无人机不存在（RESOURCE_NOT_FOUND）
        # - HTTP 401/403: 未认证或无权限（PERMISSION_DENIED）
        # business_code/business_detail_code 由 BusinessApiResponseMixin 统一补齐。
        drone = self.get_object()
        latest = (
            DroneAssignment.objects.select_related("drone", "tenant_member__user__staff_profile")
            .filter(drone=drone, tenant=self.get_current_tenant())
            .order_by("-id")
            .first()
        )
        serializer = (
            DroneAssignmentReadSerializer(latest, context={"request": request})
            if latest is not None
            else None
        )
        return Response(
            {
                "drone_id": drone.id,
                "has_record": latest is not None,
                "result": serializer.data if serializer is not None else None,
            },
            status=status.HTTP_200_OK,
        )
