from django.db import transaction
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission, ScopedQuerysetMixin
from apps.access.services import log_action, snapshot
from apps.api_v1.business_response import (
    BusinessApiResponseMixin,
    StandardCode,
    standard_error_payload,
    validation_error_payload,
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
from apps.mission.models import Mission
from apps.mission.serializers import MissionCreateSerializer, MissionReadSerializer, MissionUpdateSerializer

MISSION_LIST_RESPONSE = paginated_envelope_serializer("MissionListResponse", MissionReadSerializer)
MISSION_DETAIL_RESPONSE = object_envelope_serializer("MissionDetailResponse", MissionReadSerializer)
MISSION_DELETE_RESPONSE = object_envelope_serializer("MissionDeleteResponse", BusinessDeleteResultSerializer)

MISSION_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(name="route_id", type=int, location=OpenApiParameter.QUERY, description="按航线 ID 过滤。"),
    OpenApiParameter(name="drone_id", type=int, location=OpenApiParameter.QUERY, description="按无人机 ID 过滤。"),
    OpenApiParameter(name="pilot_id", type=int, location=OpenApiParameter.QUERY, description="按飞手成员 ID 过滤。"),
    OpenApiParameter(name="status", type=int, location=OpenApiParameter.QUERY, description="按任务状态过滤。"),
]

def _mission_success_response(view, mission: Mission, *, http_status: int, include_headers: bool = False):
    payload = view._payload(mission)
    if include_headers:
        headers = view.get_success_headers(payload)
        return Response(payload, status=http_status, headers=headers)
    return Response(payload, status=http_status)


def _reject_empty_update_request(request):
    if not request.data:
        return Response(
            standard_error_payload(
                StandardCode.INVALID_PARAMS,
                "更新请求至少包含一个可写字段",
                {"body": "请至少提交一个可写字段"},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


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
        summary="查询任务列表",
        parameters=MISSION_FILTER_PARAMETERS,
        responses={
            200: OpenApiResponse(response=MISSION_LIST_RESPONSE),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Mission"],
    ),
    retrieve=extend_schema(
        summary="读取任务详情",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=MISSION_DETAIL_RESPONSE),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Mission"],
    ),
    create=extend_schema(
        summary="创建任务",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=MissionCreateSerializer,
        responses={
            201: OpenApiResponse(response=MISSION_DETAIL_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Mission"],
    ),
    update=extend_schema(
        summary="全量更新本地任务字段",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=MissionUpdateSerializer,
        responses={
            200: OpenApiResponse(response=MISSION_DETAIL_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Mission"],
    ),
    destroy=extend_schema(
        summary="软删除任务",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=MISSION_DELETE_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Mission"],
    ),
)
class MissionViewSet(
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
    queryset = Mission.objects.select_related("route", "drone", "pilot__user__staff_profile").all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "put", "delete", "head", "options"]

    permission_map = {
        "list": "mission.view_mission",
        "retrieve": "mission.view_mission",
        "create": "mission.manage_mission",
        "update": "mission.manage_mission",
        "destroy": "mission.manage_mission",
    }

    @staticmethod
    def assigned_scope_filter_builder(tenant_member_id: int) -> dict:
        return {"pilot_id": tenant_member_id}

    def get_serializer_class(self):
        if self.action == "create":
            return MissionCreateSerializer
        if self.action == "update":
            return MissionUpdateSerializer
        return MissionReadSerializer

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset()).filter(is_deleted=False)
        params = self.request.query_params

        for query_key, model_field in (
            ("route_id", "route_id"),
            ("drone_id", "drone_id"),
            ("pilot_id", "pilot_id"),
            ("status", "status"),
        ):
            value = params.get(query_key)
            if value:
                queryset = queryset.filter(**{model_field: value})

        if self.action in {"list", "retrieve", "update", "destroy"}:
            return self.apply_scope(queryset)
        return queryset

    def _payload(self, mission: Mission) -> dict:
        return dict(MissionReadSerializer(mission, context={"request": self.request}).data)

    def _update_mission(self, request):
        error_response = _reject_empty_update_request(request)
        if error_response is not None:
            return error_response

        serializer = self.get_serializer(self.get_object(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        mission = self.perform_update(serializer)
        return _mission_success_response(self, mission, http_status=status.HTTP_200_OK)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        error_response = _reject_request_body_if_present(request, message="DELETE 请求不支持请求体")
        if error_response is not None:
            return error_response

        mission = self.get_object()
        before_data = snapshot(mission)
        deleted_at = timezone.now()
        update_fields = ["is_deleted", "deleted_at", "updated_at"]
        mission.is_deleted = True
        mission.deleted_at = deleted_at
        mission.save(update_fields=update_fields)

        deleted_payload = {"id": mission.id, "deleted": True}
        log_action(
            request=request,
            action="MISSION_DELETE",
            target_type="mission",
            target_id=mission.id,
            before_data=before_data,
            after_data=deleted_payload,
        )
        return Response(deleted_payload, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=False)
        if serializer.errors:
            return Response(validation_error_payload(serializer.errors), status=status.HTTP_400_BAD_REQUEST)

        mission = self.perform_create(serializer)
        return _mission_success_response(self, mission, http_status=status.HTTP_201_CREATED, include_headers=True)

    @transaction.atomic
    def perform_create(self, serializer):
        tenant = self.get_current_tenant()
        mission = serializer.save(tenant=tenant)
        log_action(
            request=self.request,
            action="MISSION_CREATE",
            target_type="mission",
            target_id=mission.id,
            after_data=self._payload(mission),
        )
        return mission

    def update(self, request, *args, **kwargs):
        return self._update_mission(request)

    @transaction.atomic
    def perform_update(self, serializer):
        mission = serializer.instance
        before_data = snapshot(mission)
        mission = serializer.save()
        log_action(
            request=self.request,
            action="MISSION_UPDATE",
            target_type="mission",
            target_id=mission.id,
            before_data=before_data,
            after_data=snapshot(mission),
        )
        return mission
