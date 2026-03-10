from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission
from apps.access.services import IdentityService, log_action
from apps.api_v1.business_response import BusinessApiResponseMixin, BusinessCode
from apps.mission.models import Mission
from apps.route.models import Route, RouteStatus
from apps.route.serializers import RouteReadSerializer, RouteWriteSerializer
from apps.waypoint.models import Waypoint


class RouteViewSet(
    BusinessApiResponseMixin,
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
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    permission_map = {
        "list": "route.view_route",
        "retrieve": "route.view_route",
        "create": "route.manage_route",
        "partial_update": "route.manage_route",
        "enable": "route.manage_route",
        "disable": "route.manage_route",
        "destroy": "route.manage_route",
    }

    def get_serializer_class(self):
        if self.action in {"create", "partial_update"}:
            return RouteWriteSerializer
        return RouteReadSerializer

    def _route_payload(self, route: Route) -> dict:
        return dict(RouteReadSerializer(route, context={"request": self.request}).data)

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
                {
                    "business_code": "INVALID_PARAMS",
                    "detail": "PATCH 请求至少包含一个可写字段",
                    "errors": {"body": "请至少提交一个可写字段"},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        route = self.perform_update(serializer)

        if getattr(instance, "_prefetched_objects_cache", None):
            instance._prefetched_objects_cache = {}

        return Response(self._route_payload(route), status=status.HTTP_200_OK)

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
                {
                    "business_code": BusinessCode.INVALID_PARAMS,
                    "detail": "enable 请求不支持提交 body 参数",
                    "errors": {"body": "不支持请求体，请移除 body 后重试"},
                },
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
                {
                    "business_code": BusinessCode.INVALID_PARAMS,
                    "detail": "disable 请求不支持提交 body 参数",
                    "errors": {"body": "不支持请求体，请移除 body 后重试"},
                },
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
                {
                    "business_code": "INVALID_PARAMS",
                    "detail": "DELETE 请求不支持提交 body 参数",
                    "errors": {"body": "不支持请求体，请移除 body 后重试"},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        route = self.get_object()
        route_id = route.id
        before_data = self._route_payload(route)

        if Mission.objects.filter(route_id=route_id).exists():
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
