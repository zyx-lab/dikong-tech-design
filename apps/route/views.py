from django.db import transaction
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view, inline_serializer

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission
from apps.access.services import IdentityService, log_action
from apps.api_v1.business_response import BusinessApiResponseMixin, StandardCode, standard_error_payload
from apps.api_v1.schema import (
    BUSINESS_INTERNAL_ERROR_RESPONSE,
    TENANT_CODE_HEADER_PARAMETER,
    business_error_example,
    business_error_response,
    object_envelope_serializer,
    paginated_envelope_serializer,
)
from apps.api_v1.tenant_scope import TenantScopedBusinessMixin
from apps.mission.models import Mission
from apps.route.models import Route, RouteStatus, RouteType
from apps.route.serializers import RouteReadSerializer, RouteWriteSerializer
from apps.waypoint.models import Waypoint


ROUTE_LIST_RESPONSE = paginated_envelope_serializer("RouteListResponse", RouteReadSerializer)
ROUTE_DETAIL_RESPONSE = object_envelope_serializer("RouteDetailResponse", RouteReadSerializer)
ROUTE_DELETE_DATA = inline_serializer(
    name="RouteDeleteData",
    fields={
        "id": serializers.IntegerField(),
        "deleted": serializers.BooleanField(),
        "delete_mode": serializers.CharField(help_text="`disabled` 表示被任务引用时软删除为禁用；`hard` 表示直接物理删除。"),
        "deleted_waypoint_count": serializers.IntegerField(required=False, help_text="仅硬删除时返回，表示被连带删除的航点数量。"),
        "name": serializers.CharField(required=False),
        "route_type": serializers.IntegerField(required=False),
        "drone_type_id": serializers.IntegerField(required=False, allow_null=True),
        "total_distance": serializers.DecimalField(required=False, max_digits=12, decimal_places=2, allow_null=True),
        "estimated_duration": serializers.IntegerField(required=False, allow_null=True),
        "waypoint_count": serializers.IntegerField(required=False, allow_null=True),
        "creator_name": serializers.CharField(required=False),
        "status": serializers.IntegerField(required=False),
        "created_at": serializers.DateTimeField(required=False),
        "updated_at": serializers.DateTimeField(required=False),
    },
)
ROUTE_DELETE_RESPONSE = object_envelope_serializer("RouteDeleteResponse", ROUTE_DELETE_DATA)

ROUTE_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(
        name="status",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按航线状态精确过滤。",
        enum=[choice[0] for choice in RouteStatus.choices],
    ),
    OpenApiParameter(
        name="route_type",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按航线类型扩展位精确过滤。",
        enum=[choice[0] for choice in RouteType.choices],
    ),
    OpenApiParameter(
        name="name",
        type=str,
        location=OpenApiParameter.QUERY,
        description="按航线名称做模糊匹配。",
    ),
]

ROUTE_PERMISSION_DENIED_RESPONSE = business_error_response(
    description="未认证、无权限、缺少租户上下文，或 platform_admin 访问业务 API 被拒绝。",
    examples=[
        business_error_example(
            "未登录",
            code="A0401",
            msg="登录状态已失效",
            status_codes=["401"],
        ),
        business_error_example(
            "无权限",
            code="A0403",
            msg="无操作权限",
            status_codes=["403"],
        ),
    ],
)

ROUTE_INVALID_PARAMS_RESPONSE = business_error_response(
    description="请求体不合法，code 固定为 B0001。",
    examples=[
        business_error_example(
            "缺少必填字段",
            code="B0001",
            msg="参数校验失败",
            status_codes=["400"],
            data={"name": ["该字段是必填项。"]},
        ),
        business_error_example(
            "PATCH 空请求体",
            code="B0001",
            msg="PATCH 请求至少包含一个可写字段",
            status_codes=["400"],
            data={"body": "请至少提交一个可写字段"},
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

ROUTE_NOT_FOUND_RESPONSE = business_error_response(
    description="目标航线不存在，或当前租户上下文下不可见。",
    examples=[
        business_error_example(
            "航线不存在",
            code="C0404",
            msg="资源不存在",
            status_codes=["404"],
        )
    ],
)


@extend_schema_view(
    list=extend_schema(
        summary="查询航线列表",
        description="按当前租户查询航线台账，支持按状态、航线类型、名称过滤。",
        parameters=ROUTE_FILTER_PARAMETERS,
        responses={
            200: OpenApiResponse(response=ROUTE_LIST_RESPONSE, description="查询成功。"),
            401: ROUTE_PERMISSION_DENIED_RESPONSE,
            403: ROUTE_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    ),
    retrieve=extend_schema(
        summary="读取航线详情",
        description="按航线 ID 读取单条航线详情。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=ROUTE_DETAIL_RESPONSE, description="读取成功。"),
            401: ROUTE_PERMISSION_DENIED_RESPONSE,
            403: ROUTE_PERMISSION_DENIED_RESPONSE,
            404: ROUTE_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    ),
    create=extend_schema(
        summary="创建航线",
        description="在当前租户下新增航线主记录，作为任务编排前置资源。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=RouteWriteSerializer,
        examples=[
            OpenApiExample(
                "创建航线请求",
                request_only=True,
                value={
                    "name": "城市中心巡检航线",
                    "route_type": RouteType.PENDING_EXTENSION,
                    "drone_type_id": 1,
                    "total_distance": "2063.50",
                    "estimated_duration": 1200,
                },
            )
        ],
        responses={
            201: OpenApiResponse(response=ROUTE_DETAIL_RESPONSE, description="创建成功。"),
            400: ROUTE_INVALID_PARAMS_RESPONSE,
            401: ROUTE_PERMISSION_DENIED_RESPONSE,
            403: ROUTE_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    ),
    update=extend_schema(
        summary="全量更新航线",
        description="按航线 ID 全量更新主记录字段，未提交的可写字段将按 PUT 语义重置。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=RouteWriteSerializer,
        responses={
            200: OpenApiResponse(response=ROUTE_DETAIL_RESPONSE, description="更新成功。"),
            400: ROUTE_INVALID_PARAMS_RESPONSE,
            401: ROUTE_PERMISSION_DENIED_RESPONSE,
            403: ROUTE_PERMISSION_DENIED_RESPONSE,
            404: ROUTE_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    ),
    partial_update=extend_schema(
        summary="局部更新航线",
        description="按航线 ID 局部更新台账字段，不承担状态流转或删除恢复。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=RouteWriteSerializer,
        examples=[
            OpenApiExample(
                "PATCH 航线请求",
                request_only=True,
                value={"name": "城市中心巡检航线-修订版", "estimated_duration": 1500},
            )
        ],
        responses={
            200: OpenApiResponse(response=ROUTE_DETAIL_RESPONSE, description="更新成功。"),
            400: ROUTE_INVALID_PARAMS_RESPONSE,
            401: ROUTE_PERMISSION_DENIED_RESPONSE,
            403: ROUTE_PERMISSION_DENIED_RESPONSE,
            404: ROUTE_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    ),
    destroy=extend_schema(
        summary="删除航线",
        description="按航线 ID 删除。若该航线已被任务引用，则退化为置为 DISABLED 并返回 `delete_mode=disabled`。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(
                response=ROUTE_DELETE_RESPONSE,
                description="删除成功；可能是硬删除，也可能是被任务引用后的禁用删除。",
            ),
            400: ROUTE_INVALID_PARAMS_RESPONSE,
            401: ROUTE_PERMISSION_DENIED_RESPONSE,
            403: ROUTE_PERMISSION_DENIED_RESPONSE,
            404: ROUTE_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    ),
)
class RouteViewSet(
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
    """航线业务接口（V1）。"""

    queryset = Route.objects.all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    permission_map = {
        "list": "route.view_route",
        "retrieve": "route.view_route",
        "create": "route.manage_route",
        "update": "route.manage_route",
        "partial_update": "route.manage_route",
        "enable": "route.manage_route",
        "disable": "route.manage_route",
        "destroy": "route.manage_route",
    }

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return RouteWriteSerializer
        return RouteReadSerializer

    def _route_payload(self, route: Route) -> dict:
        return dict(RouteReadSerializer(route, context={"request": self.request}).data)

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset())
        params = self.request.query_params

        # 业务作用：
        # 提供航线列表读取能力，作为“航线台账创建后”的基础查询入口，供外部系统按需组合筛选。
        # 本接口只负责只读检索，不承担航线创建/编辑等写操作；响应统一采用 `code / msg / data`。
        # 常见业务码：
        # - SUCCESS/OK：查询成功，返回分页结果
        # - PERMISSION_DENIED/NOT_AUTHENTICATED 或 FORBIDDEN：未认证或无查看权限
        status_value = params.get("status")
        route_type = params.get("route_type")
        name = params.get("name")

        if status_value:
            queryset = queryset.filter(status=status_value)
        if route_type:
            queryset = queryset.filter(route_type=route_type)
        if name:
            queryset = queryset.filter(name__icontains=name)

        return queryset

    def retrieve(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 route_id 读取单条航线详情”的基础能力，供任务编排在已选航线后做精确取数。
        # 本接口是只读查询，不承担任何状态变更或业务编排；返回统一包含 code/msg/data。
        #
        # 关键语义：
        # 1) 有权限且路由存在 -> SUCCESS/OK（HTTP 200）；
        # 2) 路由不存在 -> RESOURCE_NOT_FOUND/NOT_FOUND（HTTP 404）；
        # 3) 未认证或无查看权限 -> PERMISSION_DENIED（HTTP 401/403）。
        return super().retrieve(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        # 业务作用：
        # 创建航线基础台账（name/route_type/drone_type_id 等），作为任务编排前置资源。
        # 该接口只负责“创建航线主记录”，不承载航点维护、调度编排、状态流转等扩展职责。
        #
        # 设计边界：
        # 1) 本轮开放 GET(只读列表/详情) + POST(创建)，仍不提前暴露更新/删除等写接口。
        # 2) route_type 当前仅保留“扩展位”数值字段，避免在业务未定时提前固化类型语义。
        # 3) 成功返回统一携带业务状态码，由 BusinessApiResponseMixin 自动补齐。
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        route = self.perform_create(serializer)
        read_serializer = RouteReadSerializer(route, context={"request": request})
        headers = self.get_success_headers(read_serializer.data)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def partial_update(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 route_id 局部更新航线主记录”的基础接口（PATCH /api/v1/routes/{id}），
        # 允许外部系统修正 route 名称、适配机型、里程与时长等台账元数据。
        #
        # 适用边界：
        # 1) 只更新 route 主记录，不承担航点重排、任务解绑、状态流转或删除恢复；
        # 2) PATCH 请求体必须至少包含一个可写字段；
        # 3) 成功返回最新 route 快照，业务码由统一响应层补齐。
        if not request.data:
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "PATCH 请求至少包含一个可写字段",
                    {"body": "请至少提交一个可写字段"},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        route = self.perform_update(serializer)

        if getattr(instance, "_prefetched_objects_cache", None):
            instance._prefetched_objects_cache = {}

        return Response(self._route_payload(route), status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=False)
        serializer.is_valid(raise_exception=True)
        route = self.perform_update(serializer)

        if getattr(instance, "_prefetched_objects_cache", None):
            instance._prefetched_objects_cache = {}

        return Response(self._route_payload(route), status=status.HTTP_200_OK)

    @extend_schema(
        summary="启用航线",
        description="将指定航线状态切换为 ACTIVE；若已是 ACTIVE，则按幂等成功返回。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=ROUTE_DETAIL_RESPONSE, description="启用成功或幂等命中。"),
            400: ROUTE_INVALID_PARAMS_RESPONSE,
            401: ROUTE_PERMISSION_DENIED_RESPONSE,
            403: ROUTE_PERMISSION_DENIED_RESPONSE,
            404: ROUTE_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    )
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def enable(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 route_id 启用航线”的基础状态流转入口（POST /api/v1/routes/{id}/enable），
        # 用于把被禁用的航线显式恢复为正常状态。
        #
        # 适用边界：
        # 1) 仅处理 route.status 自身流转，不承担航点重排、任务解绑或批量恢复编排；
        # 2) 请求体必须为空；
        # 3) 已处于 ACTIVE 的航线重复 enable 按幂等成功返回。
        if request.data:
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "enable 请求不支持提交 body 参数",
                    {"body": "不支持请求体，请移除 body 后重试"},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        route = self.get_object()
        before_payload = self._route_payload(route)

        if route.status == RouteStatus.ACTIVE:
            log_action(
                request=request,
                action="ROUTE_ENABLE",
                target_type="route",
                target_id=route.id,
                before_data=before_payload,
                after_data=before_payload,
            )
            return Response(before_payload, status=status.HTTP_200_OK)

        route.status = RouteStatus.ACTIVE
        route.save(update_fields=["status", "updated_at"])
        after_payload = self._route_payload(route)
        log_action(
            request=request,
            action="ROUTE_ENABLE",
            target_type="route",
            target_id=route.id,
            before_data=before_payload,
            after_data=after_payload,
        )
        return Response(after_payload, status=status.HTTP_200_OK)

    @extend_schema(
        summary="禁用航线",
        description="将指定航线状态切换为 DISABLED；若已是 DISABLED，则按幂等成功返回。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=ROUTE_DETAIL_RESPONSE, description="禁用成功或幂等命中。"),
            400: ROUTE_INVALID_PARAMS_RESPONSE,
            401: ROUTE_PERMISSION_DENIED_RESPONSE,
            403: ROUTE_PERMISSION_DENIED_RESPONSE,
            404: ROUTE_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    )
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def disable(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 route_id 禁用航线”的基础状态流转入口（POST /api/v1/routes/{id}/disable），
        # 用于把正常航线显式置为禁用状态。
        #
        # 适用边界：
        # 1) 仅处理 route.status 自身流转，不承担航点删除、任务解绑或批量停用编排；
        # 2) 请求体必须为空；
        # 3) 已处于 DISABLED 的航线重复 disable 按幂等成功返回。
        if request.data:
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "disable 请求不支持提交 body 参数",
                    {"body": "不支持请求体，请移除 body 后重试"},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        route = self.get_object()
        before_payload = self._route_payload(route)

        if route.status == RouteStatus.DISABLED:
            log_action(
                request=request,
                action="ROUTE_DISABLE",
                target_type="route",
                target_id=route.id,
                before_data=before_payload,
                after_data=before_payload,
            )
            return Response(before_payload, status=status.HTTP_200_OK)

        route.status = RouteStatus.DISABLED
        route.save(update_fields=["status", "updated_at"])
        after_payload = self._route_payload(route)
        log_action(
            request=request,
            action="ROUTE_DISABLE",
            target_type="route",
            target_id=route.id,
            before_data=before_payload,
            after_data=after_payload,
        )
        return Response(after_payload, status=status.HTTP_200_OK)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 route_id 删除航线”的最小基础写接口，补齐航线台账生命周期闭环。
        #
        # 适用边界：
        # 1) 若 route 已被任务引用，则不做物理删除，改为置为 DISABLED，保留任务引用完整性；
        # 2) 若 route 未被任务引用，则删除 route 及其下属 waypoints；
        # 3) DELETE 请求体必须为空，避免把删除动作扩展成编排型接口。
        if request.data:
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "DELETE 请求不支持提交 body 参数",
                    {"body": "不支持请求体，请移除 body 后重试"},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        route = self.get_object()
        route_id = route.id
        before_data = self._route_payload(route)

        if Mission.objects.filter(route_id=route_id, tenant=self.get_current_tenant()).exists():
            if route.status != RouteStatus.DISABLED:
                route.status = RouteStatus.DISABLED
                route.save(update_fields=["status", "updated_at"])
            response_data = self._route_payload(route)
            response_data.update({"deleted": True, "delete_mode": "disabled"})
            log_action(
                request=request,
                action="ROUTE_DELETE",
                target_type="route",
                target_id=route_id,
                before_data=before_data,
                after_data=response_data,
            )
            return Response(response_data, status=status.HTTP_200_OK)

        deleted_waypoint_count = Waypoint.objects.filter(route_id=route_id).count()
        if deleted_waypoint_count:
            Waypoint.objects.filter(route_id=route_id).delete()
        route.delete()

        response_data = {
            "id": route_id,
            "deleted": True,
            "delete_mode": "hard",
            "deleted_waypoint_count": deleted_waypoint_count,
        }
        log_action(
            request=request,
            action="ROUTE_DELETE",
            target_type="route",
            target_id=route_id,
            before_data=before_data,
            after_data=response_data,
        )
        return Response(response_data, status=status.HTTP_200_OK)

    @transaction.atomic
    def perform_create(self, serializer):
        staff = IdentityService.get_staff(self.request.user)
        route = serializer.save(
            tenant=self.get_current_tenant(),
            status=RouteStatus.ACTIVE,
            creator_name=staff.name if staff else "",
        )
        route_payload = dict(RouteReadSerializer(route, context={"request": self.request}).data)
        log_action(
            request=self.request,
            action="ROUTE_CREATE",
            target_type="route",
            target_id=route.id,
            after_data=route_payload,
        )
        return route

    @transaction.atomic
    def perform_update(self, serializer):
        route = self.get_object()
        before_payload = self._route_payload(route)
        route = serializer.save()
        after_payload = self._route_payload(route)
        log_action(
            request=self.request,
            action="ROUTE_UPDATE",
            target_type="route",
            target_id=route.id,
            before_data=before_payload,
            after_data=after_payload,
        )
        return route
