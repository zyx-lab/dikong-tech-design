from django.http import HttpResponseRedirect
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission, ScopedQuerysetMixin
from apps.api_v1.business_response import BusinessApiResponseMixin
from apps.api_v1.schema import (
    BUSINESS_INTERNAL_ERROR_RESPONSE,
    BUSINESS_NOT_FOUND_RESPONSE,
    BUSINESS_PERMISSION_DENIED_RESPONSE,
    TENANT_CODE_HEADER_PARAMETER,
    object_envelope_serializer,
    paginated_envelope_serializer,
)
from apps.api_v1.tenant_scope import TenantScopedBusinessMixin
from apps.dji_bff.gateway import DjiGateway
from apps.media_file.models import MediaFile, MediaType
from apps.media_file.serializers import MediaFileReadSerializer

MEDIA_FILE_LIST_RESPONSE = paginated_envelope_serializer("MediaFileListResponse", MediaFileReadSerializer)
MEDIA_FILE_DETAIL_RESPONSE = object_envelope_serializer("MediaFileDetailResponse", MediaFileReadSerializer)

MEDIA_FILE_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(name="flight_record_id", type=int, location=OpenApiParameter.QUERY, description="按飞行记录过滤。"),
    OpenApiParameter(name="mission_id", type=int, location=OpenApiParameter.QUERY, description="按任务过滤。"),
    OpenApiParameter(name="device_sn", type=str, location=OpenApiParameter.QUERY, description="按设备 SN 过滤。"),
    OpenApiParameter(
        name="media_type",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按媒体类型过滤。",
        enum=[choice[0] for choice in MediaType.choices],
    ),
    OpenApiParameter(name="file_name", type=str, location=OpenApiParameter.QUERY, description="按文件名模糊匹配。"),
]


@extend_schema_view(
    list=extend_schema(
        summary="查询媒体列表",
        parameters=MEDIA_FILE_FILTER_PARAMETERS,
        responses={
            200: OpenApiResponse(response=MEDIA_FILE_LIST_RESPONSE),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Media File"],
    ),
    retrieve=extend_schema(
        summary="读取媒体详情",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=MEDIA_FILE_DETAIL_RESPONSE),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Media File"],
    ),
)
class MediaFileViewSet(
    BusinessApiResponseMixin,
    TenantScopedBusinessMixin,
    PermissionMapMixin,
    ScopedQuerysetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = MediaFile.objects.select_related("flight_record", "dji_index").all().order_by("-id")
    serializer_class = MediaFileReadSerializer
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "head", "options"]

    permission_map = {
        "list": "media_file.view_media_file",
        "retrieve": "media_file.view_media_file",
        "download": "media_file.view_media_file",
    }

    @staticmethod
    def assigned_scope_filter_builder(tenant_member_id: int) -> dict:
        return {"flight_record__pilot_id": tenant_member_id}

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset()).filter(is_deleted=False, dji_index__isnull=False)
        params = self.request.query_params

        for query_key, model_field in (
            ("flight_record_id", "flight_record_id"),
            ("mission_id", "dji_index__mission_id"),
            ("device_sn", "dji_index__device_sn"),
            ("media_type", "media_type"),
            ("file_name", "file_name__icontains"),
        ):
            value = params.get(query_key)
            if value:
                queryset = queryset.filter(**{model_field: value})

        return self.apply_scope(queryset)

    @extend_schema(
        summary="下载媒体文件",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            302: OpenApiResponse(description="302 重定向到 DJI 下载地址。"),
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Media File"],
    )
    @action(detail=True, methods=["get"])
    def download(self, request, *args, **kwargs):
        media_file = self.get_object()
        download_url = DjiGateway().get_media_url(media_file.dji_index.dji_file_id)
        return HttpResponseRedirect(download_url)
