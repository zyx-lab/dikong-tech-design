from django.db import transaction
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ErrorDetail
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission, ScopedQuerysetMixin
from apps.access.services import IdentityService, log_action, snapshot
from apps.api_v1.business_response import BusinessApiResponseMixin, StandardCode, standard_error_payload, validation_error_payload
from apps.api_v1.schema import (
    BUSINESS_DUPLICATE_RESPONSE,
    BUSINESS_INVALID_PARAMS_RESPONSE,
    BUSINESS_INTERNAL_ERROR_RESPONSE,
    BUSINESS_NOT_FOUND_RESPONSE,
    BUSINESS_PERMISSION_DENIED_RESPONSE,
    TENANT_CODE_HEADER_PARAMETER,
    object_envelope_serializer,
    paginated_envelope_serializer,
)
from apps.api_v1.tenant_scope import TenantScopedBusinessMixin
from apps.drone_assignment.models import DroneAssignment, DroneAssignmentStatus
from apps.drone_assignment.serializers import DroneAssignmentCreateSerializer, DroneAssignmentReadSerializer

DRONE_ASSIGNMENT_LIST_RESPONSE = paginated_envelope_serializer("DroneAssignmentListResponse", DroneAssignmentReadSerializer)
DRONE_ASSIGNMENT_DETAIL_RESPONSE = object_envelope_serializer("DroneAssignmentDetailResponse", DroneAssignmentReadSerializer)

DRONE_ASSIGNMENT_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(name="drone_id", type=int, location=OpenApiParameter.QUERY, description="按无人机 ID 过滤。"),
    OpenApiParameter(name="tenant_member_id", type=int, location=OpenApiParameter.QUERY, description="按成员 ID 过滤。"),
    OpenApiParameter(name="status", type=str, location=OpenApiParameter.QUERY, description="按分配状态过滤。"),
]


@extend_schema_view(
    list=extend_schema(
        summary="查询无人机分配列表",
        parameters=DRONE_ASSIGNMENT_FILTER_PARAMETERS,
        responses={
            200: OpenApiResponse(response=DRONE_ASSIGNMENT_LIST_RESPONSE),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone Assignment"],
    ),
    retrieve=extend_schema(
        summary="读取无人机分配详情",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=DRONE_ASSIGNMENT_DETAIL_RESPONSE),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone Assignment"],
    ),
    create=extend_schema(
        summary="创建无人机分配",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=DroneAssignmentCreateSerializer,
        responses={
            201: OpenApiResponse(response=DRONE_ASSIGNMENT_DETAIL_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            409: BUSINESS_DUPLICATE_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone Assignment"],
    ),
)
class DroneAssignmentViewSet(
    BusinessApiResponseMixin,
    TenantScopedBusinessMixin,
    PermissionMapMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = DroneAssignment.objects.select_related("drone", "tenant_member__user__staff_profile").all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "head", "options"]

    permission_map = {
        "list": "drone_assignment.manage_drone_assignment",
        "retrieve": "drone_assignment.manage_drone_assignment",
        "create": "drone_assignment.manage_drone_assignment",
        "cancel": "drone_assignment.manage_drone_assignment",
    }

    def get_serializer_class(self):
        if self.action == "create":
            return DroneAssignmentCreateSerializer
        return DroneAssignmentReadSerializer

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset())
        params = self.request.query_params

        if params.get("drone_id"):
            queryset = queryset.filter(drone_id=params["drone_id"])
        if params.get("tenant_member_id"):
            queryset = queryset.filter(tenant_member_id=params["tenant_member_id"])
        if params.get("status"):
            queryset = queryset.filter(status=params["status"])
        return self.apply_scope(queryset)

    @staticmethod
    def _contains_duplicate_error(errors) -> bool:
        if isinstance(errors, dict):
            return any(DroneAssignmentViewSet._contains_duplicate_error(value) for value in errors.values())
        if isinstance(errors, list):
            return any(DroneAssignmentViewSet._contains_duplicate_error(item) for item in errors)
        if isinstance(errors, ErrorDetail):
            return "已存在" in str(errors) or "unique" in str(errors).lower()
        if isinstance(errors, str):
            return "已存在" in errors or "unique" in errors.lower()
        return False

    def _payload(self, assignment: DroneAssignment) -> dict:
        return dict(DroneAssignmentReadSerializer(assignment, context={"request": self.request}).data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=False)
        if serializer.errors:
            if "non_field_errors" in serializer.errors or self._contains_duplicate_error(serializer.errors):
                return Response(
                    standard_error_payload(StandardCode.DUPLICATE, "资源已存在", serializer.errors),
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(validation_error_payload(serializer.errors), status=status.HTTP_400_BAD_REQUEST)

        assignment = self.perform_create(serializer)
        headers = self.get_success_headers(self._payload(assignment))
        return Response(self._payload(assignment), status=status.HTTP_201_CREATED, headers=headers)

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
            after_data=self._payload(assignment),
        )
        return assignment

    @extend_schema(
        summary="取消无人机分配",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=DRONE_ASSIGNMENT_DETAIL_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone Assignment"],
    )
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def cancel(self, request, *args, **kwargs):
        if request.data:
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "cancel 请求不支持提交 body 参数",
                    {"body": "不支持请求体，请移除 body 后重试"},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        assignment = self.get_object()
        before_data = snapshot(assignment)

        if assignment.status == DroneAssignmentStatus.INACTIVE:
            return Response(self._payload(assignment), status=status.HTTP_200_OK)

        assignment.status = DroneAssignmentStatus.INACTIVE
        assignment.end_at = timezone.now()
        assignment.save(update_fields=["status", "end_at", "updated_at"])
        log_action(
            request=request,
            action="DRONE_ASSIGNMENT_CANCEL",
            target_type="drone_assignment",
            target_id=assignment.id,
            before_data=before_data,
            after_data=snapshot(assignment),
        )
        return Response(self._payload(assignment), status=status.HTTP_200_OK)
