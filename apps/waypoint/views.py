from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission
from apps.access.services import log_action
from apps.api_v1.business_response import BusinessApiResponseMixin
from apps.api_v1.schema import (
    TENANT_CODE_HEADER_PARAMETER,
    BusinessDeleteResultSerializer,
    business_error_example,
    business_error_response,
    object_envelope_serializer,
    paginated_envelope_serializer,
)
from apps.api_v1.tenant_scope import TenantScopedBusinessMixin
from apps.waypoint.models import Waypoint
from apps.waypoint.serializers import WaypointCreateSerializer, WaypointPatchSerializer, WaypointReadSerializer


WAYPOINT_LIST_RESPONSE = paginated_envelope_serializer("WaypointListResponse", WaypointReadSerializer)
WAYPOINT_DETAIL_RESPONSE = object_envelope_serializer("WaypointDetailResponse", WaypointReadSerializer)

WAYPOINT_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(
        name="route_id",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按所属航线 ID 精确过滤。",
    ),
    OpenApiParameter(
        name="sequence",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按航点序号精确过滤。",
    ),
]

WAYPOINT_PERMISSION_DENIED_RESPONSE = business_error_response(
    description="未认证、无权限、缺少租户上下文，或 platform_admin 访问业务 API 被拒绝。",
    examples=[
        business_error_example(
            "未登录",
            business_code="PERMISSION_DENIED",
            business_detail_code="NOT_AUTHENTICATED",
            detail="Authentication credentials were not provided.",
            status_codes=["401"],
        ),
        business_error_example(
            "无权限",
            business_code="PERMISSION_DENIED",
            business_detail_code="FORBIDDEN",
            detail="PERMISSION_DENIED",
            status_codes=["403"],
        ),
    ],
)

WAYPOINT_INVALID_PARAMS_RESPONSE = business_error_response(
    description="请求体不合法。业务码可能是 INVALID_PARAMS，也可能在重复提交时为 IDEMPOTENT_DUPLICATE。",
    examples=[
        business_error_example(
            "航线不属于当前租户",
            business_code="INVALID_PARAMS",
            business_detail_code="VALIDATION_ERROR",
            detail="参数校验失败",
            status_codes=["400"],
            extras={"errors": {"route": ["仅允许绑定当前租户下的航线"]}},
        ),
        business_error_example(
            "航线已禁用",
            business_code="INVALID_PARAMS",
            business_detail_code="VALIDATION_ERROR",
            detail="参数校验失败",
            status_codes=["400"],
            extras={"errors": {"route": ["仅允许向状态为正常的航线新增航点"]}},
        ),
        business_error_example(
            "航点序号重复",
            business_code="IDEMPOTENT_DUPLICATE",
            business_detail_code="DUPLICATE_REQUEST",
            detail="重复提交，资源已存在",
            status_codes=["400"],
            extras={"non_field_errors": ["The fields route, sequence must make a unique set."]},
        ),
    ],
)

WAYPOINT_NOT_FOUND_RESPONSE = business_error_response(
    description="目标航点不存在，或在当前租户上下文下不可见。",
    examples=[
        business_error_example(
            "航点不存在",
            business_code="RESOURCE_NOT_FOUND",
            business_detail_code="NOT_FOUND",
            detail="No Waypoint matches the given query.",
            status_codes=["404"],
        )
    ],
)


@extend_schema_view(
    list=extend_schema(
        summary="查询航点列表",
        description="按当前租户查询航点，支持按航线 ID 和航点序号过滤。",
        parameters=WAYPOINT_FILTER_PARAMETERS,
        responses={
            200: OpenApiResponse(response=WAYPOINT_LIST_RESPONSE, description="查询成功。"),
            401: WAYPOINT_PERMISSION_DENIED_RESPONSE,
            403: WAYPOINT_PERMISSION_DENIED_RESPONSE,
        },
        tags=["Business API - Waypoint"],
    ),
    retrieve=extend_schema(
        summary="读取航点详情",
        description="按航点 ID 读取单条航点详情。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=WAYPOINT_DETAIL_RESPONSE, description="读取成功。"),
            401: WAYPOINT_PERMISSION_DENIED_RESPONSE,
            403: WAYPOINT_PERMISSION_DENIED_RESPONSE,
            404: WAYPOINT_NOT_FOUND_RESPONSE,
        },
        tags=["Business API - Waypoint"],
    ),
    create=extend_schema(
        summary="创建航点",
        description="向指定 ACTIVE 航线新增单条航点；同一航线下 `sequence` 必须唯一。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=WaypointCreateSerializer,
        examples=[
            OpenApiExample(
                "创建航点请求",
                request_only=True,
                value={
                    "route": 1,
                    "sequence": 3,
                    "latitude": "22.28612345",
                    "longitude": "113.56781234",
                    "altitude": "120.50",
                },
            )
        ],
        responses={
            201: OpenApiResponse(response=WAYPOINT_DETAIL_RESPONSE, description="创建成功。"),
            400: WAYPOINT_INVALID_PARAMS_RESPONSE,
            401: WAYPOINT_PERMISSION_DENIED_RESPONSE,
            403: WAYPOINT_PERMISSION_DENIED_RESPONSE,
        },
        tags=["Business API - Waypoint"],
    ),
    partial_update=extend_schema(
        summary="局部更新航点",
        description="按航点 ID 局部更新 sequence/latitude/longitude/altitude，不支持切换所属航线。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=WaypointPatchSerializer,
        examples=[
            OpenApiExample(
                "PATCH 航点请求",
                request_only=True,
                value={"sequence": 4, "altitude": "125.00"},
            )
        ],
        responses={
            200: OpenApiResponse(response=WAYPOINT_DETAIL_RESPONSE, description="更新成功。"),
            400: WAYPOINT_INVALID_PARAMS_RESPONSE,
            401: WAYPOINT_PERMISSION_DENIED_RESPONSE,
            403: WAYPOINT_PERMISSION_DENIED_RESPONSE,
            404: WAYPOINT_NOT_FOUND_RESPONSE,
        },
        tags=["Business API - Waypoint"],
    ),
    destroy=extend_schema(
        summary="删除航点",
        description="按航点 ID 删除单条航点，并同步回写所属航线的 waypoint_count。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=BusinessDeleteResultSerializer, description="删除成功。"),
            401: WAYPOINT_PERMISSION_DENIED_RESPONSE,
            403: WAYPOINT_PERMISSION_DENIED_RESPONSE,
            404: WAYPOINT_NOT_FOUND_RESPONSE,
        },
        tags=["Business API - Waypoint"],
    ),
)
class WaypointViewSet(
    BusinessApiResponseMixin,
    TenantScopedBusinessMixin,
    PermissionMapMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """航点业务接口（V1）。"""

    queryset = Waypoint.objects.select_related("route").all().order_by("route_id", "sequence", "id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    tenant_lookup = "route__tenant"

    permission_map = {
        "list": "waypoint.view_waypoint",
        "retrieve": "waypoint.view_waypoint",
        "create": "waypoint.manage_waypoint",
        "partial_update": "waypoint.manage_waypoint",
        "destroy": "waypoint.manage_waypoint",
    }

    def get_serializer_class(self):
        if self.action == "create":
            return WaypointCreateSerializer
        if self.action == "partial_update":
            return WaypointPatchSerializer
        return WaypointReadSerializer

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset())
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

    def partial_update(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按航点 ID 局部更新”的基础接口（PATCH /api/v1/waypoints/{id}），
        # 允许外部系统仅修改定位相关字段（sequence/latitude/longitude/altitude），
        # 以最小代价修正航点坐标或顺序。
        #
        # 适用边界：
        # 1) 不支持通过该接口切换所属航线，跨航线调整应由外部组合“新建+停用旧数据”等流程实现；
        # 2) 仅允许更新 ACTIVE 航线下的航点；
        # 3) 成功/失败响应统一包含 business_code/business_detail_code（SUCCESS、INVALID_PARAMS、RESOURCE_NOT_FOUND、PERMISSION_DENIED）。
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        waypoint = self.perform_update(serializer)

        if getattr(instance, "_prefetched_objects_cache", None):
            instance._prefetched_objects_cache = {}

        read_serializer = WaypointReadSerializer(waypoint, context={"request": request})
        return Response(read_serializer.data, status=status.HTTP_200_OK)

    def destroy(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按航点 ID 删除航点”的最小基础写接口（DELETE /api/v1/waypoints/{id}），
        # 让外部系统可组合出“创建 -> 校验 -> 修正 -> 删除”的完整维护闭环。
        #
        # 适用边界：
        # 1) 本接口只处理单条航点删除，不承担批量删除、历史归档、跨航线重排等编排能力；
        # 2) 删除后会同步回写 route.waypoint_count，保证航线冗余计数字段与实际数据一致；
        # 3) 成功/失败响应统一包含 business_code/business_detail_code（SUCCESS、RESOURCE_NOT_FOUND、PERMISSION_DENIED）。
        waypoint = self.get_object()
        waypoint_id = waypoint.id
        self.perform_destroy(waypoint)
        return Response({"id": waypoint_id, "deleted": True}, status=status.HTTP_200_OK)

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

    @transaction.atomic
    def perform_update(self, serializer):
        waypoint = self.get_object()
        before_payload = dict(WaypointReadSerializer(waypoint, context={"request": self.request}).data)
        waypoint = serializer.save()
        after_payload = dict(WaypointReadSerializer(waypoint, context={"request": self.request}).data)
        log_action(
            request=self.request,
            action="WAYPOINT_UPDATE",
            target_type="waypoint",
            target_id=waypoint.id,
            before_data=before_payload,
            after_data=after_payload,
        )
        return waypoint

    @transaction.atomic
    def perform_destroy(self, instance):
        route = instance.route
        before_payload = dict(WaypointReadSerializer(instance, context={"request": self.request}).data)
        waypoint_id = instance.id
        instance.delete()

        # 航点删除后同步更新航线的 waypoint_count，避免路由台账中的冗余计数漂移。
        route.waypoint_count = Waypoint.objects.filter(route_id=route.id).count()
        route.save(update_fields=["waypoint_count", "updated_at"])

        log_action(
            request=self.request,
            action="WAYPOINT_DELETE",
            target_type="waypoint",
            target_id=waypoint_id,
            before_data=before_payload,
            after_data={"id": waypoint_id, "deleted": True},
        )
