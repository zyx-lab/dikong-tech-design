import re
import uuid
import logging
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.http import FileResponse
from rest_framework.decorators import action
from rest_framework import mixins, parsers, status, viewsets
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission, ScopedQuerysetMixin
from apps.access.exceptions import StandardizedApiException
from apps.access.services import log_action
from apps.api_v1.business_response import (
    BusinessApiResponseMixin,
    StandardCode,
    build_instance_payload,
    build_instance_response,
    reject_request_body_if_present,
    standard_error_payload,
)
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
from apps.route.serializers import RouteCreateSerializer, RouteReadSerializer, RouteUpdateJsonSerializer, RouteUpdateSerializer

logger = logging.getLogger(__name__)

ROUTE_LIST_RESPONSE = paginated_envelope_serializer("RouteListResponse", RouteReadSerializer)
ROUTE_DETAIL_RESPONSE = object_envelope_serializer("RouteDetailResponse", RouteReadSerializer)
ROUTE_DELETE_RESPONSE = object_envelope_serializer("RouteDeleteResponse", BusinessDeleteResultSerializer)

ROUTE_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(name="name", type=str, location=OpenApiParameter.QUERY, description="按航线名称模糊匹配。"),
]
ROUTE_CREATE_REQUEST = {
    "multipart/form-data": RouteCreateSerializer,
    "application/x-www-form-urlencoded": RouteCreateSerializer,
}
ROUTE_UPDATE_REQUEST = {
    "application/json": RouteUpdateJsonSerializer,
    "multipart/form-data": RouteUpdateSerializer,
    "application/x-www-form-urlencoded": RouteUpdateSerializer,
}

def _route_not_found_response():
    return Response(
        standard_error_payload(StandardCode.NOT_FOUND, "资源不存在", None),
        status=status.HTTP_404_NOT_FOUND,
    )


def _route_upload_verification_exception(*, detail: str, dji_wayline_id: str, download_url: str, upstream_status: int | None = None):
    data = {
        "detail": detail,
        "dji_wayline_id": dji_wayline_id,
        "download_url": download_url,
    }
    if upstream_status is not None:
        data["upstream_status"] = upstream_status
    return StandardizedApiException(
        msg="上传后航线文件校验失败",
        data=data,
        standard_code=StandardCode.INTERNAL_ERROR,
        status_code=status.HTTP_502_BAD_GATEWAY,
    )


def _sanitize_route_name(name: str) -> str:
    normalized = re.sub(r"[^\w]+", "-", (name or "").strip())
    normalized = normalized.strip("-_")
    return normalized if normalized else "route"


def _format_upstream_route_name(route_id: int, route_name: str) -> str:
    sanitized = _sanitize_route_name(route_name)
    return f"{route_id}-{sanitized}-{uuid.uuid4().hex[:8]}"


def _clone_kmz_for_upload(file_obj, *, upload_name: str) -> SimpleUploadedFile:
    file_obj.seek(0)
    content = file_obj.read()
    file_obj.seek(0)
    content_type = getattr(file_obj, "content_type", "application/vnd.google-earth.kmz")
    return SimpleUploadedFile(name=f"{upload_name}.kmz", content=content, content_type=content_type)


def _require_kmz_file(serializer) -> SimpleUploadedFile:
    kmz_file = serializer.validated_data.get("kmz_file")
    if kmz_file is None:
        raise RuntimeError("kmz_file missing")
    return kmz_file


def _upload_route_to_upstream(*, gateway: DjiGateway, route_id: int, route_name: str, kmz_file) -> tuple[str, str]:
    upstream_name = _format_upstream_route_name(route_id, route_name)
    upload_file = _clone_kmz_for_upload(kmz_file, upload_name=upstream_name)
    payload = gateway.upload_route(route_name=upstream_name, file_obj=upload_file)
    return payload["dji_wayline_id"], str(payload["download_url"])


def _sync_route_index(
    *,
    tenant,
    route: Route,
    dji_wayline_id: str,
    download_url: str,
    workspace_id: str,
    route_index: TenantRouteIndex | None = None,
    dji_platform=None,
):
    if route_index is None:
        route_index = TenantRouteIndex.objects.create(
            tenant=tenant,
            route=route,
            dji_platform=dji_platform or route.dji_platform,
            workspace_id=workspace_id,
            dji_wayline_id=dji_wayline_id,
            download_url=download_url,
            is_published=True,
        )
    else:
        route_index.dji_platform = dji_platform or route.dji_platform
        route_index.workspace_id = workspace_id
        route_index.dji_wayline_id = dji_wayline_id
        route_index.download_url = download_url
        route_index.is_published = True
        route_index.save(update_fields=["dji_platform", "workspace_id", "dji_wayline_id", "download_url", "is_published", "updated_at"])
    route.dji_index = route_index
    return route_index


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
        summary="上传航线 KMZ 并创建航线",
        description="仅接受表单提交；必须同时提供 `name` 与 `kmz_file`，创建成功后会立即上传 DJI 并返回当前航线快照。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=ROUTE_CREATE_REQUEST,
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
        summary="局部更新航线名称或替换 KMZ",
        description=(
            "支持三种更新路径："
            "1) `application/json` 仅提交 `name` 时，只更新本地航线名称；"
            "2) `multipart/form-data`/`application/x-www-form-urlencoded` 提交 `kmz_file` 时，"
            "替换当前 DJI 航线文件；"
            "3) 同时提交 `name` 与 `kmz_file` 时，两者一起更新。"
            "空请求体会返回当前航线快照并视为 no-op。`PATCH` 仍然不支持。"
        ),
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=ROUTE_UPDATE_REQUEST,
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
        "kmz": "route.view_route",
        "create": "route.manage_route",
        "update": "route.manage_route",
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
        if self.action in {"list", "retrieve", "kmz", "update", "destroy"}:
            return self.apply_scope(queryset)
        return queryset

    def get_parsers(self):
        parser_classes = list(self.parser_classes)
        if "put" in getattr(self, "action_map", {}) and "post" not in getattr(self, "action_map", {}):
            parser_classes = [parsers.JSONParser, *parser_classes]
        return [parser() for parser in parser_classes]

    def _cleanup_created_route_after_failure(self, *, route_id: int | None):
        if not route_id:
            return
        try:
            Route.objects.filter(id=route_id).delete()
        except Exception:
            logger.exception("route cleanup failed after rollback", extra={"route_id": route_id})

    def _cleanup_uploaded_wayline_after_failure(self, *, gateway: DjiGateway, wayline_id: str):
        if not wayline_id:
            return
        try:
            self._delete_upstream_wayline_if_exists(gateway=gateway, wayline_id=wayline_id, best_effort=True)
        except Exception:
            logger.exception("route upload cleanup failed after rollback", extra={"wayline_id": wayline_id})

    def _gateway_for_route(self, route: Route | None = None) -> DjiGateway:
        return DjiGateway()

    def _verify_uploaded_route_file(self, *, gateway: DjiGateway, dji_wayline_id: str, download_url: str) -> str:
        current_download_url = str(download_url or "").strip()
        if current_download_url:
            try:
                gateway.download_route_file(current_download_url)
                return current_download_url
            except DjiGatewayUpstreamError as exc:
                if exc.status_code != status.HTTP_404_NOT_FOUND:
                    raise _route_upload_verification_exception(
                        detail="上传成功，但航线文件下载校验失败",
                        dji_wayline_id=dji_wayline_id,
                        download_url=current_download_url,
                        upstream_status=exc.status_code,
                    ) from exc

        try:
            refreshed_download_url = str(gateway.get_route_download_url(dji_wayline_id) or "").strip()
        except DjiGatewayUpstreamError as exc:
            raise _route_upload_verification_exception(
                detail="上传成功，但无法获取最新航线下载地址",
                dji_wayline_id=dji_wayline_id,
                download_url=current_download_url,
                upstream_status=exc.status_code,
            ) from exc

        if not refreshed_download_url:
            raise _route_upload_verification_exception(
                detail="上传成功，但未获取到可用航线下载地址",
                dji_wayline_id=dji_wayline_id,
                download_url=current_download_url,
            )

        try:
            gateway.download_route_file(refreshed_download_url)
        except DjiGatewayUpstreamError as exc:
            raise _route_upload_verification_exception(
                detail="上传成功，但航线文件对象不存在或不可下载",
                dji_wayline_id=dji_wayline_id,
                download_url=refreshed_download_url,
                upstream_status=exc.status_code,
            ) from exc
        return refreshed_download_url

    @transaction.atomic
    def _finalize_create(self, *, route: Route, tenant, dji_wayline_id: str, download_url: str, workspace_id: str):
        _sync_route_index(
            tenant=tenant,
            route=route,
            dji_wayline_id=dji_wayline_id,
            download_url=download_url,
            workspace_id=workspace_id,
        )
        log_action(
            request=self.request,
            action="ROUTE_CREATE",
            target_type="route",
            target_id=route.id,
            after_data=build_instance_payload(RouteReadSerializer, route, self.request),
        )
        return route

    def perform_create(self, serializer, *, gateway: DjiGateway, cleanup_state: dict[str, object]):
        tenant = self.get_current_tenant()
        kmz_file = _require_kmz_file(serializer)

        route = serializer.save(tenant=tenant)
        cleanup_state["route_id"] = route.id
        dji_wayline_id, download_url = _upload_route_to_upstream(
            gateway=gateway,
            route_id=route.id,
            route_name=route.name,
            kmz_file=kmz_file,
        )
        cleanup_state["wayline_id"] = dji_wayline_id
        download_url = self._verify_uploaded_route_file(
            gateway=gateway,
            dji_wayline_id=dji_wayline_id,
            download_url=download_url,
        )

        route = self._finalize_create(
            route=route,
            tenant=tenant,
            dji_wayline_id=dji_wayline_id,
            download_url=download_url,
            workspace_id=gateway._workspace_id(),
        )
        cleanup_state["wayline_id"] = ""
        cleanup_state["route_id"] = None

        return route

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        gateway = DjiGateway()
        cleanup_state: dict[str, object] = {"wayline_id": "", "route_id": None}
        try:
            route = self.perform_create(serializer, gateway=gateway, cleanup_state=cleanup_state)
        except Exception:
            self._cleanup_uploaded_wayline_after_failure(gateway=gateway, wayline_id=cleanup_state["wayline_id"])
            self._cleanup_created_route_after_failure(route_id=cleanup_state["route_id"])
            raise
        return build_instance_response(
            RouteReadSerializer,
            route,
            self.request,
            http_status=status.HTTP_201_CREATED,
            include_headers=True,
            headers_builder=self.get_success_headers,
        )

    @transaction.atomic
    def _finalize_update(
        self,
        *,
        serializer,
        tenant,
        route: Route,
        route_index: TenantRouteIndex | None,
        before_data: dict,
        new_wayline_id: str,
        new_download_url: str,
        old_wayline_id: str,
        gateway: DjiGateway,
    ):
        route = serializer.save()
        _sync_route_index(
            tenant=tenant,
            route=route,
            dji_wayline_id=new_wayline_id,
            download_url=new_download_url,
            workspace_id=gateway._workspace_id(),
            route_index=route_index,
        )
        log_action(
            request=self.request,
            action="ROUTE_UPDATE",
            target_type="route",
            target_id=route.id,
            before_data=before_data,
            after_data=build_instance_payload(RouteReadSerializer, route, self.request),
        )
        if old_wayline_id and old_wayline_id != new_wayline_id:
            transaction.on_commit(
                lambda wayline_id=old_wayline_id: self._delete_upstream_wayline_if_exists(
                    gateway=gateway,
                    wayline_id=wayline_id,
                    best_effort=True,
                )
        )
        return route

    @transaction.atomic
    def _finalize_local_only_update(self, *, serializer, route: Route):
        before_data = build_instance_payload(RouteReadSerializer, route, self.request)
        route = serializer.save()
        log_action(
            request=self.request,
            action="ROUTE_UPDATE",
            target_type="route",
            target_id=route.id,
            before_data=before_data,
            after_data=build_instance_payload(RouteReadSerializer, route, self.request),
        )
        return route

    def perform_update(self, serializer, *, gateway: DjiGateway, cleanup_state: dict[str, str]):
        route = serializer.instance
        before_data = build_instance_payload(RouteReadSerializer, route, self.request)
        tenant = self.get_current_tenant()
        kmz_file = _require_kmz_file(serializer)

        route_index = getattr(route, "dji_index", None)
        old_wayline_id = getattr(route_index, "dji_wayline_id", "")
        new_name = serializer.validated_data.get("name", route.name)
        new_wayline_id, new_download_url = _upload_route_to_upstream(
            gateway=gateway,
            route_id=route.id,
            route_name=new_name,
            kmz_file=kmz_file,
        )
        cleanup_state["wayline_id"] = new_wayline_id
        new_download_url = self._verify_uploaded_route_file(
            gateway=gateway,
            dji_wayline_id=new_wayline_id,
            download_url=new_download_url,
        )

        route = self._finalize_update(
            serializer=serializer,
            tenant=tenant,
            route=route,
            route_index=route_index,
            before_data=before_data,
            new_wayline_id=new_wayline_id,
            new_download_url=new_download_url,
            old_wayline_id=old_wayline_id,
            gateway=gateway,
        )
        cleanup_state["wayline_id"] = ""
        return route

    def _has_bound_mission_blocker(self, route: Route) -> bool:
        return Mission.objects.filter(
            tenant=self.get_current_tenant(),
            route=route,
            is_deleted=False,
            drone_id__isnull=False,
            status__in=(MissionStatus.PENDING, MissionStatus.RUNNING),
        ).exists()

    def update(self, request, *args, **kwargs):
        route = self.get_object()
        if self._has_bound_mission_blocker(route):
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "航线已被已绑定无人机的任务占用，无法更新",
                    {"route_id": route.id},
                ),
                status=status.HTTP_400_BAD_REQUEST,
        )
        serializer = self.get_serializer(route, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        if not serializer.validated_data:
            return build_instance_response(
                RouteReadSerializer,
                route,
                self.request,
                http_status=status.HTTP_200_OK,
            )
        if "kmz_file" not in serializer.validated_data:
            route = self._finalize_local_only_update(serializer=serializer, route=route)
            return build_instance_response(
                RouteReadSerializer,
                route,
                self.request,
                http_status=status.HTTP_200_OK,
            )
        gateway = self._gateway_for_route(route)
        cleanup_state = {"wayline_id": ""}
        try:
            route = self.perform_update(serializer, gateway=gateway, cleanup_state=cleanup_state)
        except Exception:
            self._cleanup_uploaded_wayline_after_failure(gateway=gateway, wayline_id=cleanup_state["wayline_id"])
            raise
        return build_instance_response(
            RouteReadSerializer,
            route,
            self.request,
            http_status=status.HTTP_200_OK,
        )

    @extend_schema(
        summary="下载航线 KMZ",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(description="KMZ 二进制文件。"),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Route"],
    )
    @action(detail=True, methods=["get"], url_path="kmz")
    def kmz(self, request, *args, **kwargs):
        route = self.get_object()
        route_index = getattr(route, "dji_index", None)
        gateway = self._gateway_for_route(route)
        download_url = str(getattr(route_index, "download_url", "") or "").strip()
        if not download_url:
            try:
                download_url = self._refresh_route_download_url(gateway=gateway, route_index=route_index)
            except DjiGatewayUpstreamError as exc:
                if exc.status_code == status.HTTP_404_NOT_FOUND:
                    return _route_not_found_response()
                raise
        if not download_url:
            return _route_not_found_response()

        try:
            upstream_response = gateway.download_route_file(download_url)
        except DjiGatewayUpstreamError as exc:
            if exc.status_code != status.HTTP_404_NOT_FOUND:
                raise
            try:
                refreshed_download_url = self._refresh_route_download_url(gateway=gateway, route_index=route_index)
            except DjiGatewayUpstreamError as refresh_exc:
                if refresh_exc.status_code == status.HTTP_404_NOT_FOUND:
                    return _route_not_found_response()
                raise
            if not refreshed_download_url:
                return _route_not_found_response()
            try:
                upstream_response = gateway.download_route_file(refreshed_download_url)
            except DjiGatewayUpstreamError as retry_exc:
                if retry_exc.status_code == status.HTTP_404_NOT_FOUND:
                    return _route_not_found_response()
                raise
        else:
            refreshed_download_url = download_url

        content_type = upstream_response.headers.get("Content-Type") or "application/vnd.google-earth.kmz"
        return FileResponse(
            BytesIO(upstream_response.data),
            as_attachment=True,
            filename=f"route-{route.id}.kmz",
            content_type=content_type,
        )

    def _delete_upstream_wayline_if_exists(self, *, gateway: DjiGateway, wayline_id: str, best_effort: bool = False):
        if not wayline_id:
            return
        try:
            gateway.delete_route(wayline_id)
        except DjiGatewayUpstreamError as exc:
            if not best_effort and exc.status_code != 404:
                raise
            logger.debug(
                "upstream delete best effort",
                extra={"wayline_id": wayline_id, "status_code": exc.status_code, "best_effort": best_effort},
            )

    def _refresh_route_download_url(self, *, gateway: DjiGateway, route_index: TenantRouteIndex | None) -> str:
        wayline_id = str(getattr(route_index, "dji_wayline_id", "") or "").strip()
        if not wayline_id:
            return ""
        download_url = str(gateway.get_route_download_url(wayline_id) or "").strip()
        if not download_url:
            return ""
        if route_index is not None and route_index.download_url != download_url:
            route_index.download_url = download_url
            route_index.save(update_fields=["download_url", "updated_at"])
        return download_url

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        error_response = reject_request_body_if_present(
            request,
            message="DELETE 请求不支持提交 body 参数",
            use_content_length=True,
        )
        if error_response is not None:
            return error_response

        route = self.get_object()
        if self._has_bound_mission_blocker(route):
            return Response(
                standard_error_payload(
                    StandardCode.INVALID_PARAMS,
                    "航线已被已绑定无人机的任务占用，无法删除",
                    {"route_id": route.id},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        before_data = build_instance_payload(RouteReadSerializer, route, self.request)
        route_index = getattr(route, "dji_index", None)
        gateway = self._gateway_for_route(route)
        route_id = route.id
        wayline_id = route_index.dji_wayline_id if route_index is not None else ""
        # `waypoints` 仅保留为历史内部表；删除 route 时一并清理残留行。
        route.waypoint_rows.all().delete()
        route.delete()

        if wayline_id:
            transaction.on_commit(
                lambda current_wayline_id=wayline_id: self._delete_upstream_wayline_if_exists(
                    gateway=gateway,
                    wayline_id=current_wayline_id,
                    best_effort=True,
                ),
                robust=True,
            )
        transaction.on_commit(
            lambda: log_action(
                request=request,
                action="ROUTE_DELETE",
                target_type="route",
                target_id=route_id,
                before_data=before_data,
                after_data={"id": route_id, "deleted": True},
            ),
            robust=True,
        )
        return Response({"id": route_id, "deleted": True}, status=status.HTTP_200_OK)
