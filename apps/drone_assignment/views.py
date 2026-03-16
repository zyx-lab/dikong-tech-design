from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission, ScopedQuerysetMixin
from apps.access.services import IdentityService, log_action
from apps.api_v1.business_response import BusinessApiResponseMixin, BusinessCode
from apps.api_v1.tenant_scope import TenantScopedBusinessMixin
from apps.drone_assignment.models import DroneAssignment, DroneAssignmentStatus
from apps.drone_assignment.serializers import DroneAssignmentCreateSerializer, DroneAssignmentReadSerializer


class DroneAssignmentViewSet(
    BusinessApiResponseMixin,
    TenantScopedBusinessMixin,
    PermissionMapMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """无人机分配接口。"""

    queryset = DroneAssignment.objects.select_related("drone", "tenant_member__user__staff_profile").all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "head", "options"]

    permission_map = {
        "list": "drone_assignment.manage_drone_assignment",
        "retrieve": "drone_assignment.manage_drone_assignment",
        "create": "drone_assignment.manage_drone_assignment",
        "cancel": "drone_assignment.manage_drone_assignment",
        "reactivate": "drone_assignment.manage_drone_assignment",
    }

    def get_serializer_class(self):
        if self.action == "create":
            return DroneAssignmentCreateSerializer
        return DroneAssignmentReadSerializer

    def _assignment_payload(self, assignment: DroneAssignment) -> dict:
        # 避免 datetime 直接写入 JSONField，统一复用序列化结果（字符串时间）。
        return dict(DroneAssignmentReadSerializer(assignment, context={"request": self.request}).data)

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset())
        params = self.request.query_params

        drone_id = params.get("drone_id")
        tenant_member_id = params.get("tenant_member_id")
        status_value = params.get("status")

        if drone_id:
            queryset = queryset.filter(drone_id=drone_id)
        if tenant_member_id:
            queryset = queryset.filter(tenant_member_id=tenant_member_id)
        if status_value:
            queryset = queryset.filter(status=status_value)

        if self.action in {"list", "retrieve", "cancel"}:
            return self.apply_scope(queryset)
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        assignment = DroneAssignment.objects.select_related("drone", "tenant_member__user__staff_profile").get(id=serializer.instance.id)
        read_serializer = DroneAssignmentReadSerializer(assignment, context={"request": request})
        headers = self.get_success_headers(read_serializer.data)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @transaction.atomic
    def perform_create(self, serializer):
        tenant = self.get_current_tenant()
        operator_member = IdentityService.get_active_tenant_member(self.request.user, tenant)
        assignment = serializer.save(
            tenant=tenant,
            created_by_tenant_member_id=operator_member.id if operator_member else None,
        )
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

    @extend_schema(request=None, responses=DroneAssignmentReadSerializer)
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def reactivate(self, request, *args, **kwargs):
        if request.data:
            return Response(
                {
                    "business_code": BusinessCode.INVALID_PARAMS,
                    "detail": "reactivate 请求不支持提交 body 参数",
                    "errors": {"body": "不支持请求体，请移除 body 后重试"},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        assignment = self.get_object()
        before_data = self._assignment_payload(assignment)

        # 幂等：重复激活直接返回当前状态。
        if assignment.status == DroneAssignmentStatus.ACTIVE:
            log_action(
                request=request,
                action="DRONE_ASSIGNMENT_REACTIVATE",
                target_type="drone_assignment",
                target_id=assignment.id,
                before_data=before_data,
                after_data=before_data,
            )
            return Response(before_data, status=status.HTTP_200_OK)

        assignment.status = DroneAssignmentStatus.ACTIVE
        assignment.end_at = None
        try:
            assignment.save(update_fields=["status", "end_at", "updated_at"])
        except (IntegrityError, ValidationError):
            return Response(
                {
                    "business_code": BusinessCode.STATE_CONFLICT,
                    "detail": "存在同一无人机与飞手的 ACTIVE 分配，不能重复激活",
                    "assignment_id": assignment.id,
                },
                status=status.HTTP_409_CONFLICT,
            )

        after_data = self._assignment_payload(assignment)
        log_action(
            request=request,
            action="DRONE_ASSIGNMENT_REACTIVATE",
            target_type="drone_assignment",
            target_id=assignment.id,
            before_data=before_data,
            after_data=after_data,
        )
        return Response(after_data, status=status.HTTP_200_OK)
