import uuid

from django.db import transaction
from django.http import HttpResponseRedirect
from rest_framework import mixins, parsers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission
from apps.access.services import IdentityService, log_action, snapshot
from apps.api_v1.business_response import BusinessApiResponseMixin, StandardCode, standard_error_payload
from apps.api_v1.schema import (
    BUSINESS_INTERNAL_ERROR_RESPONSE,
    TENANT_CODE_HEADER_PARAMETER,
    object_envelope_serializer,
    paginated_envelope_serializer,
)
from apps.api_v1.tenant_scope import TenantScopedBusinessMixin
from apps.dji_bff.gateway import DjiGateway, DjiGatewayUpstreamError
from apps.dji_bff.models import TenantRouteIndex
from apps.mission.models import Mission, MissionStatus
from apps.route.models import Route
from apps.route.serializers import RouteCreateSerializer, RouteReadSerializer, RouteUpdateSerializer
from apps.route.services import build_route_kmz, replace_route_waypoints

ROUTE_LIST_RESPONSE = paginated_envelope_serializer("RouteListResponse", RouteReadSerializer)
ROUTE_DETAIL_RESPONSE = object_envelope_serializer("RouteDetailResponse", RouteReadSerializer)

ROUTE_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(name="name", type=str, location=OpenApiParameter.QUERY, description="按航线名称模糊匹配。"),
    OpenApiParameter(name="route_type", type=int, location=OpenApiParameter.QUERY, description="按航线类型过滤。"),
]

_WAYPOINTS_MISSING = object()

def _route_success_response(view, route: Route, *, http_status: int, include_headers: bool = False):
    payload = view._payload(route)
    if include_headers:
        headers = view.get_success_headers(payload)
        return Response(payload, status=http_status, headers=headers)
    return Response(payload, status=http_status)


def _reject_request_body_if_present(request, *, message: str):
    if request.data:
        return Response(
            standard_error_payload(
                StandardCode.INVALID_PARAMS,
                message,
                {"body": "不支持请求体，请移除 body 后重试"},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


@extend_schema_view(
    list=extend_schema(
        summary="查询当前租户航线",
        parameters=ROUTE_FILTER_PARAMETERS,
        responses={200: OpenApiResponse(response=ROUTE_LIST_RESPONSE), 500: BUSINESS_INTERNAL_ERROR_RESPONSE},
        tags=["Business API - Route"],
    ),
    retrieve=extend_schema(
        summary="读取航线详情",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={200: OpenApiResponse(response=ROUTE_DETAIL_RESPONSE), 500: BUSINESS_INTERNAL_ERROR_RESPONSE},
        tags=["Business API - Route"],
    ),
    create=extend_schema(
        summary="创建本地航线",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=RouteCreateSerializer,
        responses={201: OpenApiResponse(response=ROUTE_DETAIL_RESPONSE), 500: BUSINESS_INTERNAL_ERROR_RESPONSE},
        tags=["Business API - Route"],
    ),
    update=extend_schema(
        summary="全量更新本地航线字段",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=RouteUpdateSerializer,
        responses={200: OpenApiResponse(response=ROUTE_DETAIL_RESPONSE), 500: BUSINESS_INTERNAL_ERROR_RESPONSE},
        tags=["Business API - Route"],
    ),
    partial_update=extend_schema(
        summary="局部更新本地航线字段",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=RouteUpdateSerializer,
        responses={200: OpenApiResponse(response=ROUTE_DETAIL_RESPONSE), 500: BUSINESS_INTERNAL_ERROR_RESPONSE},
        tags=["Business API - Route"],
    ),
    destroy=extend_schema(
        summary="删除航线",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT), 500: BUSINESS_INTERNAL_ERROR_RESPONSE},
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
    queryset = Route.objects.select_related("dji_index").prefetch_related("waypoint_rows").all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    permission_map = {
        "list": "route.view_route",
        "retrieve": "route.view_route",
        "download": "route.view_route",
        "create": "route.manage_route",
        "update": "route.manage_route",
        "partial_update": "route.manage_route",
        "publish": "route.manage_route",
        "destroy": "route.manage_route",
    }

    def get_serializer_class(self):
        if self.action == "create":
            return RouteCreateSerializer
        if self.action in {"update", "partial_update"}:
            return RouteUpdateSerializer
        return RouteReadSerializer

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset())
        params = self.request.query_params

        if params.get("name"):
            queryset = queryset.filter(name__icontains=params["name"])
        if params.get("route_type"):
            queryset = queryset.filter(route_type=params["route_type"])
        return queryset

    def _payload(self, route: Route) -> dict:
        return dict(RouteReadSerializer(route, context={"request": self.request}).data)

    def _mark_route_unpublished(self, route: Route):
        route_index, _ = TenantRouteIndex.objects.get_or_create(
            tenant=self.get_current_tenant(),
            route=route,
            defaults={"is_published": False},
        )
        route_index.is_published = False
        route_index.save(update_fields=["is_published", "updated_at"])
        route.dji_index = route_index
        return route_index

    @transaction.atomic
    def perform_create(self, serializer):
        waypoints = serializer.validated_data.pop("waypoints", [])
        staff = IdentityService.get_staff(self.request.user)
        route = serializer.save(
            tenant=self.get_current_tenant(),
            creator_name=staff.name if staff else "",
        )
        if waypoints:
            replace_route_waypoints(route, waypoints)
        route_index = TenantRouteIndex.objects.create(
            tenant=self.get_current_tenant(),
            route=route,
            dji_wayline_id="",
            is_published=False,
        )
        route.dji_index = route_index
        log_action(
            request=self.request,
            action="ROUTE_CREATE",
            target_type="route",
            target_id=route.id,
            after_data=self._payload(route),
        )
        return route

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        route = self.perform_create(serializer)
        return _route_success_response(self, route, http_status=status.HTTP_201_CREATED, include_headers=True)

    @transaction.atomic
    def perform_update(self, serializer):
        route = self.get_object()
        before_data = self._payload(route)
        waypoints = serializer.validated_data.pop("waypoints", _WAYPOINTS_MISSING)
        route = serializer.save()

        if waypoints is not _WAYPOINTS_MISSING:
            replace_route_waypoints(route, waypoints)
            if getattr(route, "_prefetched_objects_cache", None):
                route._prefetched_objects_cache = {}

        self._mark_route_unpublished(route)

        log_action(
            request=self.request,
            action="ROUTE_UPDATE",
            target_type="route",
            target_id=route.id,
            before_data=before_data,
            after_data=self._payload(route),
        )
        return route

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        route = self.perform_update(serializer)
        return _route_success_response(self, route, http_status=status.HTTP_200_OK)

    def partial_update(self, request, *args, **kwargs):
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
        return _route_success_response(self, route, http_status=status.HTTP_200_OK)

    @extend_schema(
        summary="发布航线",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={200: OpenApiResponse(response=ROUTE_DETAIL_RESPONSE), 500: BUSINESS_INTERNAL_ERROR_RESPONSE},
        tags=["Business API - Route"],
    )
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def publish(self, request, *args, **kwargs):
        error_response = _reject_request_body_if_present(request, message="publish 请求不支持提交 body 参数")
        if error_response is not None:
            return error_response

        route = self.get_object()
        before_data = self._payload(route)
        waypoints = list(route.waypoint_rows.all())
        if not waypoints:
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "航线至少需要一个航点后才能发布",
                    {"route_id": route.id},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        route_index, _ = TenantRouteIndex.objects.get_or_create(
            tenant=self.get_current_tenant(),
            route=route,
            defaults={"is_published": False},
        )
        old_wayline_id = route_index.dji_wayline_id
        kmz_file = build_route_kmz(route, waypoints)
        upstream_payload = DjiGateway().upload_route(
            route_name=f"route-{route.id}-{uuid.uuid4().hex}",
            file_obj=kmz_file,
        )
        route_index.dji_wayline_id = upstream_payload["dji_wayline_id"]
        route_index.is_published = True
        route_index.save(update_fields=["dji_wayline_id", "is_published", "updated_at"])
        route.dji_index = route_index

        if old_wayline_id and old_wayline_id != route_index.dji_wayline_id:
            try:
                DjiGateway().delete_route(old_wayline_id)
            except DjiGatewayUpstreamError as exc:
                if exc.status_code != 404:
                    raise

        log_action(
            request=request,
            action="ROUTE_PUBLISH",
            target_type="route",
            target_id=route.id,
            before_data=before_data,
            after_data=self._payload(route),
        )
        return _route_success_response(self, route, http_status=status.HTTP_200_OK)

    @extend_schema(
        summary="下载航线文件",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            302: OpenApiResponse(description="302 重定向到 DJI 下载地址。"),
            409: OpenApiResponse(description="航线尚未发布。"),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    )
    @action(detail=True, methods=["get"])
    def download(self, request, *args, **kwargs):
        route = self.get_object()
        route_index = getattr(route, "dji_index", None)
        if route_index is None or not route_index.is_published or not route_index.dji_wayline_id:
            return Response(
                standard_error_payload(
                    StandardCode.STATE_CONFLICT,
                    "航线尚未发布",
                    {"route_id": route.id},
                ),
                status=status.HTTP_409_CONFLICT,
            )
        download_url = DjiGateway().get_route_download_url(route_index.dji_wayline_id)
        return HttpResponseRedirect(download_url)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        error_response = _reject_request_body_if_present(request, message="DELETE 请求不支持提交 body 参数")
        if error_response is not None:
            return error_response

        route = self.get_object()
        if Mission.objects.filter(
            tenant=self.get_current_tenant(),
            route=route,
            status__in=[MissionStatus.PENDING, MissionStatus.RUNNING],
        ).exists():
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "航线正在被任务使用，无法删除",
                    {"route_id": route.id},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        before_data = snapshot(route)
        route_index = getattr(route, "dji_index", None)
        if route_index is not None and route_index.dji_wayline_id:
            try:
                DjiGateway().delete_route(route_index.dji_wayline_id)
            except DjiGatewayUpstreamError as exc:
                if exc.status_code != 404:
                    raise
        route_id = route.id
        route.delete()
        log_action(
            request=request,
            action="ROUTE_DELETE",
            target_type="route",
            target_id=route_id,
            before_data=before_data,
            after_data={"id": route_id, "deleted": True},
        )
        return Response({"id": route_id, "deleted": True}, status=status.HTTP_200_OK)
