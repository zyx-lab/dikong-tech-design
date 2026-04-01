import os
import uuid

from django.db import transaction
from django.http import FileResponse, Http404
from rest_framework import mixins, parsers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission, ScopedQuerysetMixin
from apps.access.services import log_action
from apps.api_v1.business_response import BusinessApiResponseMixin, StandardCode, standard_error_payload
from apps.api_v1.schema import (
    BusinessDeleteResultSerializer,
    BUSINESS_INVALID_PARAMS_RESPONSE,
    BUSINESS_INTERNAL_ERROR_RESPONSE,
    BUSINESS_NOT_FOUND_RESPONSE,
    BUSINESS_PERMISSION_DENIED_RESPONSE,
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
from apps.route.services import build_route_kmz_from_xml

ROUTE_LIST_RESPONSE = paginated_envelope_serializer("RouteListResponse", RouteReadSerializer)
ROUTE_DETAIL_RESPONSE = object_envelope_serializer("RouteDetailResponse", RouteReadSerializer)
ROUTE_DELETE_RESPONSE = object_envelope_serializer("RouteDeleteResponse", BusinessDeleteResultSerializer)

ROUTE_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(name="name", type=str, location=OpenApiParameter.QUERY, description="按航线名称模糊匹配。"),
]


def _route_success_response(view, route: Route, *, http_status: int, include_headers: bool = False):
    payload = view._payload(route)
    if include_headers:
        headers = view.get_success_headers(payload)
        return Response(payload, status=http_status, headers=headers)
    return Response(payload, status=http_status)


def _reject_request_body_if_present(request, *, message: str):
    if request.META.get("CONTENT_LENGTH") not in (None, "", "0"):
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
        responses={
            200: OpenApiResponse(response=ROUTE_LIST_RESPONSE),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    ),
    retrieve=extend_schema(
        summary="读取航线详情",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=ROUTE_DETAIL_RESPONSE),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    ),
    create=extend_schema(
        summary="创建本地航线",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=RouteCreateSerializer,
        responses={
            201: OpenApiResponse(response=ROUTE_DETAIL_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    ),
    update=extend_schema(
        summary="全量更新本地航线 XML 草稿",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=RouteUpdateSerializer,
        responses={
            200: OpenApiResponse(response=ROUTE_DETAIL_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    ),
    destroy=extend_schema(
        summary="删除航线",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=ROUTE_DELETE_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    ),
)
class RouteViewSet(
    BusinessApiResponseMixin,
    TenantScopedBusinessMixin,
    PermissionMapMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Route.objects.select_related("dji_index").all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    http_method_names = ["get", "post", "put", "delete", "head", "options"]

    permission_map = {
        "list": "route.view_route",
        "retrieve": "route.view_route",
        "xml": "route.view_route",
        "create": "route.manage_route",
        "update": "route.manage_route",
        "publish": "route.manage_route",
        "destroy": "route.manage_route",
    }

    def get_serializer_class(self):
        if self.action == "create":
            return RouteCreateSerializer
        if self.action == "update":
            return RouteUpdateSerializer
        return RouteReadSerializer

    def check_permissions(self, request):
        if request.method.lower() not in getattr(self, "action_map", {}):
            return
        return super().check_permissions(request)

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset())
        if self.request.query_params.get("name"):
            queryset = queryset.filter(name__icontains=self.request.query_params["name"])
        if self.action in {"list", "retrieve", "update", "destroy", "publish", "xml"}:
            return self.apply_scope(queryset)
        return queryset

    def _payload(self, route: Route) -> dict:
        return dict(RouteReadSerializer(route, context={"request": self.request}).data)

    def _mark_route_unpublished(self, route: Route):
        route_index, _ = TenantRouteIndex.objects.get_or_create(
            tenant=self.get_current_tenant(),
            route=route,
            defaults={"is_published": False, "dji_wayline_id": ""},
        )
        route_index.is_published = False
        route_index.save(update_fields=["is_published", "updated_at"])
        route.dji_index = route_index
        return route_index

    @transaction.atomic
    def perform_create(self, serializer):
        route = serializer.save(tenant=self.get_current_tenant())
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
        old_xml_name = route.xml_file.name
        xml_storage = route.xml_file.storage
        route = serializer.save()
        self._mark_route_unpublished(route)

        if old_xml_name and old_xml_name != route.xml_file.name:
            xml_storage.delete(old_xml_name)

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

    @extend_schema(
        summary="发布航线",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=ROUTE_DETAIL_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    )
    @transaction.atomic
    @action(detail=True, methods=["post"])
    def publish(self, request, *args, **kwargs):
        error_response = _reject_request_body_if_present(request, message="publish 请求不支持提交 body 参数")
        if error_response is not None:
            return error_response

        route = self.get_object()
        before_data = self._payload(route)
        route_index, _ = TenantRouteIndex.objects.get_or_create(
            tenant=self.get_current_tenant(),
            route=route,
            defaults={"dji_wayline_id": "", "is_published": False},
        )
        old_wayline_id = route_index.dji_wayline_id

        try:
            kmz_file = build_route_kmz_from_xml(route)
        except ValueError:
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "当前 XML 草稿无法转换为可发布 KMZ",
                    {"route_id": route.id},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        gateway = DjiGateway()

        def _delete_upstream_wayline_if_exists(wayline_id: str, *, best_effort: bool = False):
            if not wayline_id:
                return
            try:
                gateway.delete_route(wayline_id)
            except DjiGatewayUpstreamError as exc:
                if exc.status_code != 404 and not best_effort:
                    raise

        upstream_payload = gateway.upload_route(
            route_name=f"route-{route.id}-{uuid.uuid4().hex}",
            file_obj=kmz_file,
        )
        new_wayline_id = upstream_payload["dji_wayline_id"]

        try:
            route_index.dji_wayline_id = new_wayline_id
            route_index.is_published = True
            route_index.save(update_fields=["dji_wayline_id", "is_published", "updated_at"])
            route.dji_index = route_index

            if old_wayline_id and old_wayline_id != new_wayline_id:
                transaction.on_commit(
                    lambda wayline_id=old_wayline_id: _delete_upstream_wayline_if_exists(
                        wayline_id,
                        best_effort=True,
                    )
                )

            log_action(
                request=request,
                action="ROUTE_PUBLISH",
                target_type="route",
                target_id=route.id,
                before_data=before_data,
                after_data=self._payload(route),
            )
        except Exception:
            _delete_upstream_wayline_if_exists(new_wayline_id)
            raise

        return _route_success_response(self, route, http_status=status.HTTP_200_OK)

    @extend_schema(
        summary="读取航线 XML 草稿",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=OpenApiTypes.BINARY),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    )
    @action(detail=True, methods=["get"])
    def xml(self, request, *args, **kwargs):
        route = self.get_object()
        if not route.xml_file or not route.xml_file.name or not route.xml_file.storage.exists(route.xml_file.name):
            raise Http404("航线 XML 不存在")
        filename = os.path.basename(route.xml_file.name) or "route.xml"
        return FileResponse(route.xml_file.open("rb"), content_type="application/xml", filename=filename)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        error_response = _reject_request_body_if_present(request, message="DELETE 请求不支持提交 body 参数")
        if error_response is not None:
            return error_response

        route = self.get_object()
        if Mission.objects.filter(
            tenant=self.get_current_tenant(),
            route=route,
            status__in=[MissionStatus.PENDING, MissionStatus.RUNNING, MissionStatus.PAUSED],
        ).exists():
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "航线正在被任务使用，无法删除",
                    {"route_id": route.id},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        before_data = self._payload(route)
        route_index = getattr(route, "dji_index", None)
        if route_index is not None and route_index.dji_wayline_id:
            try:
                DjiGateway().delete_route(route_index.dji_wayline_id)
            except DjiGatewayUpstreamError as exc:
                if exc.status_code != 404:
                    raise

        route_id = route.id
        xml_name = route.xml_file.name
        xml_storage = route.xml_file.storage
        # `waypoints` 仅保留为历史内部表；删除 route 时一并清理残留行。
        route.waypoint_rows.all().delete()
        route.delete()
        if xml_name:
            xml_storage.delete(xml_name)

        log_action(
            request=request,
            action="ROUTE_DELETE",
            target_type="route",
            target_id=route_id,
            before_data=before_data,
            after_data={"id": route_id, "deleted": True},
        )
        return Response({"id": route_id, "deleted": True}, status=status.HTTP_200_OK)
