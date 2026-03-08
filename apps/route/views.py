from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission
from apps.access.services import IdentityService, log_action
from apps.api_v1.business_response import BusinessApiResponseMixin
from apps.route.models import Route, RouteStatus
from apps.route.serializers import RouteReadSerializer, RouteWriteSerializer


class RouteViewSet(
    BusinessApiResponseMixin,
    PermissionMapMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """航线业务接口（V1）。"""

    queryset = Route.objects.all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "head", "options"]

    permission_map = {
        "list": "route.view_route",
        "retrieve": "route.view_route",
        "create": "route.manage_route",
    }

    def get_serializer_class(self):
        if self.action == "create":
            return RouteWriteSerializer
        return RouteReadSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        # 业务作用：
        # 提供航线列表读取能力，作为“航线台账创建后”的基础查询入口，供外部系统按需组合筛选。
        # 本接口只负责只读检索，不承担航线创建/编辑等写操作；响应由统一 business_code 包装。
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
        # 本接口是只读查询，不承担任何状态变更或业务编排；返回统一包含 business_code/business_detail_code。
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

    @transaction.atomic
    def perform_create(self, serializer):
        staff = IdentityService.get_staff(self.request.user)
        route = serializer.save(
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
