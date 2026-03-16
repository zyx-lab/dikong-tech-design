from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission, ScopedQuerysetMixin
from apps.api_v1.schema import (
    BUSINESS_INTERNAL_ERROR_RESPONSE,
    TENANT_CODE_HEADER_PARAMETER,
    business_error_example,
    business_error_response,
    object_envelope_serializer,
    paginated_envelope_serializer,
)
from apps.access.services import IdentityService, log_action
from apps.api_v1.business_response import BusinessApiResponseMixin, StandardCode, standard_error_payload
from apps.api_v1.tenant_scope import TenantScopedBusinessMixin
from apps.drone_assignment.models import DroneAssignment, DroneAssignmentStatus
from apps.drone_assignment.serializers import DroneAssignmentCreateSerializer, DroneAssignmentReadSerializer


DRONE_ASSIGNMENT_LIST_RESPONSE = paginated_envelope_serializer("DroneAssignmentListResponse", DroneAssignmentReadSerializer)
DRONE_ASSIGNMENT_DETAIL_RESPONSE = object_envelope_serializer("DroneAssignmentDetailResponse", DroneAssignmentReadSerializer)

DRONE_ASSIGNMENT_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(
        name="drone_id",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按无人机 ID 精确过滤分配记录。",
    ),
    OpenApiParameter(
        name="tenant_member_id",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按成员 ID 精确过滤分配记录。",
    ),
    OpenApiParameter(
        name="status",
        type=str,
        location=OpenApiParameter.QUERY,
        description="按分配状态精确过滤。",
        enum=[choice[0] for choice in DroneAssignmentStatus.choices],
    ),
]

DRONE_ASSIGNMENT_PERMISSION_DENIED_RESPONSE = business_error_response(
    description="未认证、无权限、缺少租户上下文，或 platform_admin 访问业务 API 被拒绝。",
    examples=[
        business_error_example(
            "未登录",
            code="A0401",
            msg="登录状态已失效",
            status_codes=["401"],
        ),
        business_error_example(
            "无管理权限",
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
    ],
)

DRONE_ASSIGNMENT_INVALID_PARAMS_RESPONSE = business_error_response(
    description="请求体校验失败，code 固定为 B0001。",
    examples=[
        business_error_example(
            "跨租户无人机",
            code="B0001",
            msg="参数校验失败",
            status_codes=["400"],
            data={"drone": ["仅允许绑定当前租户下的无人机"]},
        ),
        business_error_example(
            "非飞手成员",
            code="B0001",
            msg="参数校验失败",
            status_codes=["400"],
            data={"tenant_member": ["仅允许分配给飞手类型（pilot_operator）"]},
        ),
        business_error_example(
            "请求体不允许",
            code="B0001",
            msg="reactivate 请求不支持提交 body 参数",
            status_codes=["400"],
            data={"body": "不支持请求体，请移除 body 后重试"},
        ),
    ],
)

DRONE_ASSIGNMENT_NOT_FOUND_RESPONSE = business_error_response(
    description="目标分配记录不存在，或在当前租户上下文下不可见。",
    examples=[
        business_error_example(
            "分配记录不存在",
            code="C0404",
            msg="资源不存在",
            status_codes=["404"],
        )
    ],
)

DRONE_ASSIGNMENT_STATE_CONFLICT_RESPONSE = business_error_response(
    description="当前状态不允许本次操作，code 固定为 C0201 / C0202。",
    examples=[
        business_error_example(
            "重复激活冲突",
            code="C0201",
            msg="存在同一无人机与飞手的 ACTIVE 分配，不能重复激活",
            status_codes=["409"],
            data={"assignment_id": 8},
        )
    ],
)


@extend_schema_view(
    list=extend_schema(
        summary="查询分配列表",
        description="按当前租户查询无人机分配记录，支持按无人机、成员、状态过滤。",
        parameters=DRONE_ASSIGNMENT_FILTER_PARAMETERS,
        responses={
            200: OpenApiResponse(
                response=DRONE_ASSIGNMENT_LIST_RESPONSE,
                description="查询成功。`code=00000`，结果为分页列表。",
            ),
            401: DRONE_ASSIGNMENT_PERMISSION_DENIED_RESPONSE,
            403: DRONE_ASSIGNMENT_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone Assignment"],
    ),
    retrieve=extend_schema(
        summary="读取分配详情",
        description="按分配记录 ID 读取单条无人机分配详情。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=DRONE_ASSIGNMENT_DETAIL_RESPONSE, description="读取成功。"),
            401: DRONE_ASSIGNMENT_PERMISSION_DENIED_RESPONSE,
            403: DRONE_ASSIGNMENT_PERMISSION_DENIED_RESPONSE,
            404: DRONE_ASSIGNMENT_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone Assignment"],
    ),
    create=extend_schema(
        summary="创建分配关系",
        description=(
            "在当前租户下建立一条无人机与成员的分配关系。"
            " 成员必须是 ACTIVE 且具备 `pilot_operator` 角色，无人机不可为 RETIRED。"
        ),
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=DroneAssignmentCreateSerializer,
        examples=[
            OpenApiExample(
                "创建分配请求",
                request_only=True,
                value={"drone": 1, "tenant_member": 9},
            )
        ],
        responses={
            201: OpenApiResponse(
                response=DRONE_ASSIGNMENT_DETAIL_RESPONSE,
                description="创建成功。`code=00000`，返回创建后的分配快照。",
            ),
            400: DRONE_ASSIGNMENT_INVALID_PARAMS_RESPONSE,
            401: DRONE_ASSIGNMENT_PERMISSION_DENIED_RESPONSE,
            403: DRONE_ASSIGNMENT_PERMISSION_DENIED_RESPONSE,
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

    @extend_schema(
        summary="取消分配",
        description="将指定分配记录置为 INACTIVE，并回写结束时间。若本来已是 INACTIVE，则按幂等成功返回。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=DRONE_ASSIGNMENT_DETAIL_RESPONSE, description="取消成功或幂等命中。"),
            401: DRONE_ASSIGNMENT_PERMISSION_DENIED_RESPONSE,
            403: DRONE_ASSIGNMENT_PERMISSION_DENIED_RESPONSE,
            404: DRONE_ASSIGNMENT_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone Assignment"],
    )
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

    @extend_schema(
        summary="恢复分配",
        description=(
            "将指定分配记录重新激活为 ACTIVE。若恢复后会与现有 ACTIVE 分配冲突，则返回 STATE_CONFLICT。"
        ),
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(
                response=DRONE_ASSIGNMENT_DETAIL_RESPONSE,
                description="恢复成功或幂等命中。",
                examples=[
                    OpenApiExample(
                        "恢复成功响应",
                        response_only=True,
                        status_codes=["200"],
                        value={
                            "code": "00000",
                            "msg": "success",
                            "data": {
                                "id": 3,
                                "drone": 1,
                                "drone_code": "DJ-A-01",
                                "drone_name": "调度分配测试机",
                                "tenant_member": 9,
                                "member_no": "P-200",
                                "staff_name": "飞手B",
                                "status": DroneAssignmentStatus.ACTIVE,
                                "start_at": "2026-03-16T10:00:00+08:00",
                                "end_at": None,
                                "created_by_tenant_member_id": 2,
                                "created_at": "2026-03-16T10:00:00+08:00",
                                "updated_at": "2026-03-16T11:00:00+08:00",
                            },
                        },
                    )
                ],
            ),
            400: DRONE_ASSIGNMENT_INVALID_PARAMS_RESPONSE,
            401: DRONE_ASSIGNMENT_PERMISSION_DENIED_RESPONSE,
            403: DRONE_ASSIGNMENT_PERMISSION_DENIED_RESPONSE,
            404: DRONE_ASSIGNMENT_NOT_FOUND_RESPONSE,
            409: DRONE_ASSIGNMENT_STATE_CONFLICT_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone Assignment"],
    )
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def reactivate(self, request, *args, **kwargs):
        if request.data:
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "reactivate 请求不支持提交 body 参数",
                    {"body": "不支持请求体，请移除 body 后重试"},
                ),
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
                standard_error_payload(
                    StandardCode.STATE_CONFLICT,
                    "存在同一无人机与飞手的 ACTIVE 分配，不能重复激活",
                    {"assignment_id": assignment.id},
                ),
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
