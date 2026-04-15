from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission, ScopedQuerysetMixin
from apps.access.services import log_action, snapshot
from apps.api_v1.business_response import (
    BusinessApiResponseMixin,
    StandardCode,
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
from apps.flight_record.models import FlightRecord, FlightRecordStatus
from apps.flight_record.serializers import (
    FlightRecordDetailSerializer,
    FlightRecordSummarySerializer,
    FlightRecordWriteSerializer,
)

FLIGHT_RECORD_LIST_RESPONSE = paginated_envelope_serializer("FlightRecordListResponse", FlightRecordSummarySerializer)
FLIGHT_RECORD_DETAIL_RESPONSE = object_envelope_serializer("FlightRecordDetailResponse", FlightRecordDetailSerializer)
FLIGHT_RECORD_DELETE_RESPONSE = object_envelope_serializer("FlightRecordDeleteResponse", BusinessDeleteResultSerializer)

FLIGHT_RECORD_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(name="mission_id", type=int, location=OpenApiParameter.QUERY, description="按关联任务 ID 精确过滤。"),
    OpenApiParameter(name="drone_id", type=int, location=OpenApiParameter.QUERY, description="按执行无人机 ID 精确过滤。"),
    OpenApiParameter(name="pilot_id", type=int, location=OpenApiParameter.QUERY, description="按执行飞手成员 ID 精确过滤。"),
    OpenApiParameter(
        name="status",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按飞行记录状态精确过滤。",
        enum=[choice[0] for choice in FlightRecordStatus.choices],
    ),
    OpenApiParameter(name="flight_no", type=str, location=OpenApiParameter.QUERY, description="按架次编号做模糊匹配。"),
]


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


@extend_schema_view(
    list=extend_schema(
        summary="查询飞行记录列表",
        description="查询任务执行完成后自动生成的飞行记录历史快照，支持按任务、无人机、飞手、状态、架次编号过滤。",
        parameters=FLIGHT_RECORD_FILTER_PARAMETERS,
        responses={
            200: OpenApiResponse(response=FLIGHT_RECORD_LIST_RESPONSE, description="查询成功。"),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Flight Record"],
    ),
    retrieve=extend_schema(
        summary="读取飞行记录详情",
        description="按飞行记录 ID 读取单条历史快照。飞行记录由 mission 在执行完成时自动生成，并返回当前可下载媒体列表。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=FLIGHT_RECORD_DETAIL_RESPONSE, description="读取成功。"),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Flight Record"],
    ),
    update=extend_schema(
        summary="更新飞行记录展示摘要",
        description="仅允许修正历史快照中的摘要字段，不允许修改 mission、device_sn、drone、pilot、开始结束时间或状态等锚点字段。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=FlightRecordWriteSerializer,
        responses={
            200: OpenApiResponse(response=FLIGHT_RECORD_DETAIL_RESPONSE, description="更新成功。"),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Flight Record"],
    ),
    destroy=extend_schema(
        summary="软删除飞行记录",
        description="软删除历史快照。删除后记录不再出现在列表中，详情读取按不存在处理。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=FLIGHT_RECORD_DELETE_RESPONSE, description="删除成功。"),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Flight Record"],
    ),
)
class FlightRecordViewSet(
    BusinessApiResponseMixin,
    TenantScopedBusinessMixin,
    PermissionMapMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = FlightRecord.objects.select_related("mission", "drone", "pilot__user__staff_profile").all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "put", "delete", "head", "options"]

    permission_map = {
        "list": "flight_record.view_flight_record",
        "retrieve": "flight_record.view_flight_record",
        "update": "flight_record.manage_flight_record",
        "destroy": "flight_record.manage_flight_record",
        "complete": "flight_record.manage_flight_record",
        "abort": "flight_record.manage_flight_record",
    }

    @staticmethod
    def assigned_scope_filter_builder(tenant_member_id: int) -> dict:
        return {"pilot_id": tenant_member_id}

    def get_serializer_class(self):
        if self.action == "update":
            return FlightRecordWriteSerializer
        if self.action == "retrieve":
            return FlightRecordDetailSerializer
        return FlightRecordSummarySerializer

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset()).filter(is_deleted=False)
        params = self.request.query_params

        for query_key, lookup in (
            ("mission_id", "mission_id"),
            ("drone_id", "drone_id"),
            ("pilot_id", "pilot_id"),
            ("status", "status"),
            ("flight_no", "flight_no__icontains"),
        ):
            value = params.get(query_key)
            if value:
                queryset = queryset.filter(**{lookup: value})

        if self.action in {"list", "retrieve", "update", "destroy", "complete", "abort"}:
            return self.apply_scope(queryset)
        return queryset

    def _payload(self, record: FlightRecord) -> dict:
        return dict(FlightRecordSummarySerializer(record, context={"request": self.request}).data)

    def update(self, request, *args, **kwargs):
        error_response = _reject_empty_update_request(request)
        if error_response is not None:
            return error_response

        record = self.get_object()
        serializer = self.get_serializer(record, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        before_data = snapshot(record)
        record = serializer.save()
        after_data = snapshot(record)
        log_action(
            request=request,
            action="FLIGHT_RECORD_UPDATE",
            target_type="flight_record",
            target_id=record.id,
            before_data=before_data,
            after_data=after_data,
        )
        return Response(self._payload(record), status=status.HTTP_200_OK)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        error_response = _reject_request_body_if_present(request, message="DELETE 请求不支持请求体")
        if error_response is not None:
            return error_response

        record = self.get_object()
        before_data = snapshot(record)
        deleted_at = timezone.now()
        record.is_deleted = True
        record.deleted_at = deleted_at
        record.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

        deleted_payload = {"id": record.id, "deleted": True}
        log_action(
            request=request,
            action="FLIGHT_RECORD_DELETE",
            target_type="flight_record",
            target_id=record.id,
            before_data=before_data,
            after_data=deleted_payload,
        )
        return Response(deleted_payload, status=status.HTTP_200_OK)

    @extend_schema(exclude=True)
    @action(detail=True, methods=["get"], url_path="complete")
    def complete(self, request, *args, **kwargs):
        return self.http_method_not_allowed(request, *args, **kwargs)

    @extend_schema(exclude=True)
    @action(detail=True, methods=["get"], url_path="abort")
    def abort(self, request, *args, **kwargs):
        return self.http_method_not_allowed(request, *args, **kwargs)
