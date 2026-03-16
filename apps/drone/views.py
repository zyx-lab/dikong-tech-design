from django.db import transaction
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ErrorDetail
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission, ScopedQuerysetMixin
from apps.api_v1.schema import (
    BUSINESS_INTERNAL_ERROR_RESPONSE,
    TENANT_CODE_HEADER_PARAMETER,
    BusinessDeleteResultSerializer,
    business_error_example,
    business_error_response,
    collection_envelope_serializer,
    nullable_result_envelope_serializer,
    object_envelope_serializer,
    paginated_envelope_serializer,
)
from apps.access.services import IdentityService, log_action, snapshot
from apps.api_v1.business_response import (
    BusinessApiResponseMixin,
    StandardCode,
    standard_error_payload,
    validation_error_payload,
)
from apps.api_v1.tenant_scope import TenantScopedBusinessMixin
from apps.drone.models import Drone, DroneStatus
from apps.drone.serializers import DroneReadSerializer, DroneWriteSerializer
from apps.drone_assignment.models import DroneAssignment, DroneAssignmentStatus
from apps.drone_assignment.serializers import DroneAssignmentReadSerializer


DRONE_LIST_RESPONSE = paginated_envelope_serializer("DroneListResponse", DroneReadSerializer)
DRONE_DETAIL_RESPONSE = object_envelope_serializer("DroneDetailResponse", DroneReadSerializer)
DRONE_ASSIGNMENT_HISTORY_RESPONSE = collection_envelope_serializer(
    "DroneAssignmentHistoryResponse",
    DroneAssignmentReadSerializer,
    extra_fields={"drone_id": serializers.IntegerField(help_text="当前查询的无人机 ID。")},
)
DRONE_ASSIGNMENT_LATEST_RESPONSE = nullable_result_envelope_serializer(
    "DroneAssignmentLatestResponse",
    DroneAssignmentReadSerializer,
    extra_fields={
        "drone_id": serializers.IntegerField(help_text="当前查询的无人机 ID。"),
        "has_record": serializers.BooleanField(help_text="是否存在至少一条分配记录。"),
    },
)

DRONE_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(
        name="code",
        type=str,
        location=OpenApiParameter.QUERY,
        description="按无人机业务编码做模糊匹配，例如 `DJ-01`。",
    ),
    OpenApiParameter(
        name="name",
        type=str,
        location=OpenApiParameter.QUERY,
        description="按无人机名称做模糊匹配，例如 `巡检机`。",
    ),
    OpenApiParameter(
        name="model",
        type=str,
        location=OpenApiParameter.QUERY,
        description="按无人机型号做模糊匹配，例如 `Matrice 30`。",
    ),
    OpenApiParameter(
        name="status",
        type=str,
        location=OpenApiParameter.QUERY,
        description="按状态精确过滤。",
        enum=[choice[0] for choice in DroneStatus.choices],
    ),
    OpenApiParameter(
        name="org_id",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按业务组织 ID 精确过滤。",
    ),
]

DRONE_PERMISSION_DENIED_RESPONSE = business_error_response(
    description="未认证、无权限、缺少租户上下文，或 platform_admin 访问业务 API 被拒绝。",
    examples=[
        business_error_example(
            "未登录",
            code="A0401",
            msg="登录状态已失效",
            status_codes=["401"],
        ),
        business_error_example(
            "无查看权限",
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
        business_error_example(
            "平台管理员访问业务 API",
            code="A0403",
            msg="平台管理员不可访问租户业务接口",
            status_codes=["403"],
        ),
    ],
)

DRONE_INVALID_PARAMS_RESPONSE = business_error_response(
    description="请求体或查询参数不合法，code 固定为 B0001。",
    examples=[
        business_error_example(
            "字段校验失败",
            code="B0001",
            msg="参数校验失败",
            status_codes=["400"],
            data={"serial_no": ["当前租户下已存在相同出厂序列号"]},
        ),
        business_error_example(
            "DELETE 带 body",
            code="B0001",
            msg="DELETE 请求不支持提交 body 参数",
            status_codes=["400"],
            data={"body": "不支持请求体，请移除 body 后重试"},
        ),
    ],
)

DRONE_NOT_FOUND_RESPONSE = business_error_response(
    description="目标无人机不存在，或当前租户上下文下不可见。",
    examples=[
        business_error_example(
            "无人机不存在",
            code="C0404",
            msg="资源不存在",
            status_codes=["404"],
        )
    ],
)

DRONE_STATE_CONFLICT_RESPONSE = business_error_response(
    description="资源当前状态不允许本次操作，code 固定为 C0201 / C0202。",
    examples=[
        business_error_example(
            "状态不可逆",
            code="C0201",
            msg="RETIRED 状态不可逆，不能变更为其他状态",
            status_codes=["409"],
            data={"current_status": DroneStatus.RETIRED, "target_status": DroneStatus.ENABLED},
        ),
        business_error_example(
            "存在生效分配",
            code="C0201",
            msg="无人机存在 ACTIVE 分配关系，不能删除",
            status_codes=["409"],
            data={"drone_id": 101},
        ),
    ],
)

DRONE_DUPLICATE_RESPONSE = business_error_response(
    description="重复提交或租户内唯一键冲突，code 固定为 C0101。",
    examples=[
        business_error_example(
            "租户内编码重复",
            code="C0101",
            msg="资源已存在",
            status_codes=["409"],
            data={"code": ["drone with this tenant and 业务编码 already exists."]},
        )
    ],
)


@extend_schema_view(
    list=extend_schema(
        summary="查询无人机列表",
        description=(
            "按当前租户查询无人机台账，支持按业务编码、名称、型号、状态、组织 ID 过滤。"
            " 返回统一采用 `code / msg / data` 包裹。"
        ),
        parameters=DRONE_FILTER_PARAMETERS,
        responses={
            200: OpenApiResponse(
                response=DRONE_LIST_RESPONSE,
                description="查询成功。`code=00000`，结果为分页列表。",
                examples=[
                    OpenApiExample(
                        "列表成功示例",
                        response_only=True,
                        status_codes=["200"],
                        value={
                            "code": "00000",
                            "msg": "success",
                            "data": {
                                "list": [
                                    {
                                        "id": 1,
                                        "code": "DJ-0001",
                                        "name": "巡检一号机",
                                        "model": "Matrice 30",
                                        "serial_no": "SN-0001",
                                        "status": DroneStatus.ENABLED,
                                        "org_id": 1001,
                                        "created_by_tenant_member_id": 12,
                                        "created_at": "2026-03-16T09:00:00+08:00",
                                        "updated_at": "2026-03-16T09:00:00+08:00",
                                    }
                                ],
                                "total": 1,
                            },
                        },
                    )
                ],
            ),
            401: DRONE_PERMISSION_DENIED_RESPONSE,
            403: DRONE_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    ),
    retrieve=extend_schema(
        summary="读取无人机详情",
        description="按无人机 ID 读取单条台账详情。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=DRONE_DETAIL_RESPONSE, description="读取成功。"),
            401: DRONE_PERMISSION_DENIED_RESPONSE,
            403: DRONE_PERMISSION_DENIED_RESPONSE,
            404: DRONE_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    ),
    create=extend_schema(
        summary="创建无人机",
        description=(
            "在当前租户下新增无人机台账。创建只允许写基础台账字段，"
            " 状态字段由系统按默认值初始化，不允许在该接口直接写入。"
        ),
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=DroneWriteSerializer,
        examples=[
            OpenApiExample(
                "创建请求示例",
                request_only=True,
                value={
                    "code": "DJ-0008",
                    "name": "巡检备份机",
                    "model": "Mavic 3E",
                    "serial_no": "SN-0008",
                    "org_id": 2001,
                },
            )
        ],
        responses={
            201: OpenApiResponse(
                response=DRONE_DETAIL_RESPONSE,
                description="创建成功。`code=00000`，返回新建后的无人机快照。",
            ),
            400: DRONE_INVALID_PARAMS_RESPONSE,
            401: DRONE_PERMISSION_DENIED_RESPONSE,
            403: DRONE_PERMISSION_DENIED_RESPONSE,
            409: DRONE_DUPLICATE_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    ),
    update=extend_schema(
        summary="全量更新无人机",
        description="按无人机 ID 全量更新基础台账字段，不允许在该接口直接修改状态。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=DroneWriteSerializer,
        responses={
            200: OpenApiResponse(response=DRONE_DETAIL_RESPONSE, description="更新成功。"),
            400: DRONE_INVALID_PARAMS_RESPONSE,
            401: DRONE_PERMISSION_DENIED_RESPONSE,
            403: DRONE_PERMISSION_DENIED_RESPONSE,
            404: DRONE_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    ),
    partial_update=extend_schema(
        summary="局部更新无人机",
        description=(
            "按无人机 ID 局部更新基础台账字段。"
            " 该接口不允许直接修改 `status`，状态流转必须使用专用动作接口。"
        ),
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=DroneWriteSerializer,
        examples=[
            OpenApiExample(
                "PATCH 请求示例",
                request_only=True,
                value={"name": "巡检一号机-已校正", "org_id": 2002},
            )
        ],
        responses={
            200: OpenApiResponse(response=DRONE_DETAIL_RESPONSE, description="更新成功。"),
            400: DRONE_INVALID_PARAMS_RESPONSE,
            401: DRONE_PERMISSION_DENIED_RESPONSE,
            403: DRONE_PERMISSION_DENIED_RESPONSE,
            404: DRONE_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    ),
    destroy=extend_schema(
        summary="删除无人机",
        description=(
            "按无人机 ID 删除台账。若该无人机仍存在 ACTIVE 分配关系，则返回状态冲突，不执行删除。"
        ),
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(
                response=BusinessDeleteResultSerializer,
                description="删除成功。`code=00000`，返回删除结果。",
            ),
            400: DRONE_INVALID_PARAMS_RESPONSE,
            401: DRONE_PERMISSION_DENIED_RESPONSE,
            403: DRONE_PERMISSION_DENIED_RESPONSE,
            404: DRONE_NOT_FOUND_RESPONSE,
            409: DRONE_STATE_CONFLICT_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    ),
)
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
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

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
                    standard_error_payload(StandardCode.DUPLICATE, "资源已存在", serializer.errors),
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(validation_error_payload(serializer.errors), status=status.HTTP_400_BAD_REQUEST)

        drone = self.perform_create(serializer)
        payload = self._drone_payload(drone)
        headers = self.get_success_headers(payload)
        return Response(payload, status=status.HTTP_201_CREATED, headers=headers)

    def _drone_payload(self, drone: Drone) -> dict:
        return dict(DroneReadSerializer(drone, context={"request": self.request}).data)

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
        return drone

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
        return drone

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=False)
        serializer.is_valid(raise_exception=False)
        if serializer.errors:
            if self._contains_unique_error(serializer.errors):
                return Response(
                    standard_error_payload(StandardCode.DUPLICATE, "资源已存在", serializer.errors),
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(validation_error_payload(serializer.errors), status=status.HTTP_400_BAD_REQUEST)

        drone = self.perform_update(serializer)
        if getattr(instance, "_prefetched_objects_cache", None):
            instance._prefetched_objects_cache = {}
        return Response(self._drone_payload(drone), status=status.HTTP_200_OK)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=False)
        if serializer.errors:
            if self._contains_unique_error(serializer.errors):
                return Response(
                    standard_error_payload(StandardCode.DUPLICATE, "资源已存在", serializer.errors),
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(validation_error_payload(serializer.errors), status=status.HTTP_400_BAD_REQUEST)

        drone = self.perform_update(serializer)
        if getattr(instance, "_prefetched_objects_cache", None):
            instance._prefetched_objects_cache = {}
        return Response(self._drone_payload(drone), status=status.HTTP_200_OK)

    def _status_response(self, drone: Drone) -> Response:
        serializer = DroneReadSerializer(drone, context={"request": self.request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        if request.data:
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "DELETE 请求不支持提交 body 参数",
                    {"body": "不支持请求体，请移除 body 后重试"},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        drone = self.get_object()

        if DroneAssignment.objects.filter(drone=drone, status=DroneAssignmentStatus.ACTIVE).exists():
            return Response(
                standard_error_payload(
                    StandardCode.STATE_CONFLICT,
                    "无人机存在 ACTIVE 分配关系，不能删除",
                    {"drone_id": drone.id},
                ),
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
                standard_error_payload(
                    StandardCode.STATE_CONFLICT,
                    "RETIRED 状态不可逆，不能变更为其他状态",
                    {
                        "current_status": drone.status,
                        "target_status": target_status,
                    },
                ),
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

    @extend_schema(
        summary="启用无人机",
        description="将指定无人机状态切换为 ENABLED。若当前已是 ENABLED，则按幂等成功返回。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=DRONE_DETAIL_RESPONSE, description="状态切换成功或幂等命中。"),
            401: DRONE_PERMISSION_DENIED_RESPONSE,
            403: DRONE_PERMISSION_DENIED_RESPONSE,
            404: DRONE_NOT_FOUND_RESPONSE,
            409: DRONE_STATE_CONFLICT_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    )
    @action(detail=True, methods=["post"])
    def enable(self, request, *args, **kwargs):
        return self._change_status(self.get_object(), DroneStatus.ENABLED)

    @extend_schema(
        summary="停用无人机",
        description="将指定无人机状态切换为 DISABLED。若当前已是 DISABLED，则按幂等成功返回。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=DRONE_DETAIL_RESPONSE, description="状态切换成功或幂等命中。"),
            401: DRONE_PERMISSION_DENIED_RESPONSE,
            403: DRONE_PERMISSION_DENIED_RESPONSE,
            404: DRONE_NOT_FOUND_RESPONSE,
            409: DRONE_STATE_CONFLICT_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    )
    @action(detail=True, methods=["post"])
    def disable(self, request, *args, **kwargs):
        return self._change_status(self.get_object(), DroneStatus.DISABLED)

    @extend_schema(
        summary="置为维护中",
        description="将指定无人机状态切换为 MAINTENANCE。若当前已是 MAINTENANCE，则按幂等成功返回。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=DRONE_DETAIL_RESPONSE, description="状态切换成功或幂等命中。"),
            401: DRONE_PERMISSION_DENIED_RESPONSE,
            403: DRONE_PERMISSION_DENIED_RESPONSE,
            404: DRONE_NOT_FOUND_RESPONSE,
            409: DRONE_STATE_CONFLICT_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    )
    @action(detail=True, methods=["post"])
    def maintenance(self, request, *args, **kwargs):
        return self._change_status(self.get_object(), DroneStatus.MAINTENANCE)

    @extend_schema(
        summary="退役无人机",
        description="将指定无人机状态切换为 RETIRED。该状态不可逆，后续不能再切回其他状态。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=DRONE_DETAIL_RESPONSE, description="状态切换成功或幂等命中。"),
            401: DRONE_PERMISSION_DENIED_RESPONSE,
            403: DRONE_PERMISSION_DENIED_RESPONSE,
            404: DRONE_NOT_FOUND_RESPONSE,
            409: DRONE_STATE_CONFLICT_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    )
    @action(detail=True, methods=["post"])
    def retire(self, request, *args, **kwargs):
        return self._change_status(self.get_object(), DroneStatus.RETIRED)

    @extend_schema(
        summary="查询分配历史",
        description="返回指定无人机的全部历史分配记录，包含 ACTIVE 与 INACTIVE。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=DRONE_ASSIGNMENT_HISTORY_RESPONSE, description="查询成功。"),
            401: DRONE_PERMISSION_DENIED_RESPONSE,
            403: DRONE_PERMISSION_DENIED_RESPONSE,
            404: DRONE_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    )
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
                "list": serializer.data,
                "total": len(serializer.data),
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        summary="查询当前生效分配",
        description="仅返回当前仍生效的分配记录（status=ACTIVE），常用于调度前核验当前占用情况。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=DRONE_ASSIGNMENT_HISTORY_RESPONSE, description="查询成功，空列表也是成功结果。"),
            401: DRONE_PERMISSION_DENIED_RESPONSE,
            403: DRONE_PERMISSION_DENIED_RESPONSE,
            404: DRONE_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    )
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
        # - HTTP 200: 查询成功（含 total=0 的空结果）
        # - HTTP 404: 无人机不存在（RESOURCE_NOT_FOUND）
        # - HTTP 401/403: 未认证或无权限（PERMISSION_DENIED）
        # 响应由 BusinessApiResponseMixin 统一包装为 `code / msg / data`。
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
                "list": serializer.data,
                "total": len(serializer.data),
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        summary="查询最近一条分配记录",
        description="按分配记录 ID 倒序返回最近一条记录；若从未分配过，则 `has_record=false` 且 `result=null`。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(
                response=DRONE_ASSIGNMENT_LATEST_RESPONSE,
                description="查询成功，支持“无记录但成功返回”的场景。",
                examples=[
                    OpenApiExample(
                        "存在最近记录",
                        response_only=True,
                        status_codes=["200"],
                        value={
                            "code": "00000",
                            "msg": "success",
                            "data": {
                                "drone_id": 1,
                                "has_record": True,
                                "result": {
                                    "id": 3,
                                    "drone": 1,
                                    "drone_code": "DJ-0001",
                                    "drone_name": "巡检一号机",
                                    "tenant_member": 9,
                                    "member_no": "P-200",
                                    "staff_name": "飞手A",
                                    "status": "ACTIVE",
                                    "start_at": "2026-03-16T10:00:00+08:00",
                                    "end_at": None,
                                    "created_by_tenant_member_id": 12,
                                    "created_at": "2026-03-16T10:00:00+08:00",
                                    "updated_at": "2026-03-16T10:00:00+08:00",
                                },
                            },
                        },
                    ),
                    OpenApiExample(
                        "无分配记录",
                        response_only=True,
                        status_codes=["200"],
                        value={
                            "code": "00000",
                            "msg": "success",
                            "data": {
                                "drone_id": 1,
                                "has_record": False,
                                "result": None,
                            },
                        },
                    ),
                ],
            ),
            401: DRONE_PERMISSION_DENIED_RESPONSE,
            403: DRONE_PERMISSION_DENIED_RESPONSE,
            404: DRONE_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Drone"],
    )
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
        # 响应由 BusinessApiResponseMixin 统一包装为 `code / msg / data`。
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
