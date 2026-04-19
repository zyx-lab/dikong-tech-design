from django.db import transaction
from django.http import HttpResponseRedirect
from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view, inline_serializer

from apps.access.models import ScopeType
from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission, ScopedQuerysetMixin
from apps.access.services import AuthzService, log_action, snapshot
from apps.api_v1.business_response import (
    BusinessApiResponseMixin,
    StandardCode,
    reject_request_body_if_present,
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
from apps.dji_bff.gateway import DjiGateway
from apps.flight_record.models import FlightRecord
from apps.media_file.models import MediaFile, MediaType
from apps.media_file.serializers import (
    MediaFileBindMissionResultSerializer,
    MediaFileBindMissionSerializer,
    MediaFileReadSerializer,
)

MEDIA_FILE_LIST_RESPONSE = paginated_envelope_serializer("MediaFileListResponse", MediaFileReadSerializer)
MEDIA_FILE_DETAIL_RESPONSE = object_envelope_serializer("MediaFileDetailResponse", MediaFileReadSerializer)
MEDIA_FILE_DELETE_RESPONSE = object_envelope_serializer("MediaFileDeleteResponse", BusinessDeleteResultSerializer)
MEDIA_FILE_BIND_MISSION_RESPONSE = object_envelope_serializer(
    "MediaFileBindMissionResponse",
    MediaFileBindMissionResultSerializer,
)
MEDIA_FILE_PLAYBACK_URL_RESULT_SERIALIZER = inline_serializer(
    name="MediaFilePlaybackUrlResult",
    fields={"playback_url": serializers.CharField(help_text="DJI 侧视频播放地址。")},
)
MEDIA_FILE_PLAYBACK_URL_RESPONSE = object_envelope_serializer(
    "MediaFilePlaybackUrlResponse",
    MEDIA_FILE_PLAYBACK_URL_RESULT_SERIALIZER,
)

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
    destroy=extend_schema(
        summary="软删除媒体记录",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=MEDIA_FILE_DELETE_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
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
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = MediaFile.objects.select_related("flight_record", "dji_index").all().order_by("-id")
    serializer_class = MediaFileReadSerializer
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "delete", "head", "options"]

    permission_map = {
        "list": "media_file.view_media_file",
        "retrieve": "media_file.view_media_file",
        "download": "media_file.view_media_file",
        "playback": "media_file.view_media_file",
        "playback_url": "media_file.view_media_file",
        "destroy": "media_file.manage_media_file",
        "bind_mission": "media_file.manage_media_file",
    }

    def get_serializer_class(self):
        if self.action == "bind_mission":
            return MediaFileBindMissionSerializer
        return MediaFileReadSerializer

    @staticmethod
    def assigned_scope_filter_builder(tenant_member_id: int) -> dict:
        return {"flight_record__pilot_id": tenant_member_id}

    def apply_scope(self, queryset):
        perm_code = self.get_required_permission()
        if not perm_code:
            return queryset.none()

        decision = getattr(self.request, "_authz_decision", None)
        if decision is None:
            decision = AuthzService.authorize(self.request, perm_code)

        if not decision.allowed:
            return queryset.none()

        if decision.scope == ScopeType.ASSIGNED:
            tenant_member_id = decision.tenant_member_id
            return queryset.filter(
                Q(flight_record__pilot_id=tenant_member_id)
                | Q(flight_record__isnull=True, mission__pilot_id=tenant_member_id)
            ).distinct()

        return super().apply_scope(queryset)

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset()).filter(is_deleted=False, dji_index__isnull=False)
        params = self.request.query_params

        for query_key, model_field in (
            ("flight_record_id", "flight_record_id"),
            ("mission_id", "mission_id"),
            ("device_sn", "device_sn"),
            ("media_type", "media_type"),
            ("file_name", "file_name__icontains"),
        ):
            value = params.get(query_key)
            if value:
                queryset = queryset.filter(**{model_field: value})

        return self.apply_scope(queryset)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        error_response = reject_request_body_if_present(request, message="DELETE 请求不支持请求体")
        if error_response is not None:
            return error_response

        media_file = self.get_object()
        before_data = snapshot(media_file)
        media_file.is_deleted = True
        media_file.deleted_at = timezone.now()
        media_file.save(update_fields=["is_deleted", "deleted_at"])
        FlightRecord.sync_video_count_from_media(flight_record=media_file.flight_record)

        deleted_payload = {"id": media_file.id, "deleted": True}
        log_action(
            request=request,
            action="MEDIA_FILE_DELETE",
            target_type="media_file",
            target_id=media_file.id,
            before_data=before_data,
            after_data=deleted_payload,
        )
        return Response(deleted_payload, status=status.HTTP_200_OK)

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

    @extend_schema(
        summary="获取媒体播放地址",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            302: OpenApiResponse(description="302 重定向到 DJI 播放地址。"),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Media File"],
    )
    @action(detail=True, methods=["get"])
    def playback(self, request, *args, **kwargs):
        media_file = self.get_object()
        if media_file.media_type != MediaType.VIDEO:
            return Response(
                validation_error_payload({"media_type": ["该媒体不支持 playback"]}),
                status=status.HTTP_400_BAD_REQUEST,
            )
        playback_url = DjiGateway().get_media_playback_url(media_file.dji_index.dji_file_id)
        return HttpResponseRedirect(playback_url)

    @extend_schema(
        summary="获取媒体播放地址（JSON）",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=MEDIA_FILE_PLAYBACK_URL_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            404: BUSINESS_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Media File"],
    )
    @action(detail=True, methods=["get"], url_path="playback-url")
    def playback_url(self, request, *args, **kwargs):
        media_file = self.get_object()
        if media_file.media_type != MediaType.VIDEO:
            return Response(
                validation_error_payload({"media_type": ["该媒体不支持 playback"]}),
                status=status.HTTP_400_BAD_REQUEST,
            )
        playback_url = DjiGateway().get_media_playback_url(media_file.dji_index.dji_file_id)
        return Response({"playback_url": playback_url}, status=status.HTTP_200_OK)

    @extend_schema(
        summary="批量绑定媒体到任务",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=MediaFileBindMissionSerializer,
        responses={
            200: OpenApiResponse(response=MEDIA_FILE_BIND_MISSION_RESPONSE),
            400: BUSINESS_INVALID_PARAMS_RESPONSE,
            401: BUSINESS_PERMISSION_DENIED_RESPONSE,
            403: BUSINESS_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Media File"],
    )
    @action(detail=False, methods=["post"], url_path="bind-mission")
    @transaction.atomic
    def bind_mission(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mission = serializer.validated_data["mission"]
        media_ids = serializer.validated_data["media_file_ids"]

        queryset = self.apply_scope(
            self.scope_queryset_to_tenant(
                MediaFile.objects.select_related("mission", "flight_record", "dji_index")
            ).filter(is_deleted=False, dji_index__isnull=False, id__in=media_ids)
        )
        media_files = list(queryset.select_for_update().order_by("id"))
        if len(media_files) != len(media_ids):
            return Response(
                validation_error_payload({"media_file_ids": ["存在不存在、已删除或无权限的媒体记录"]}),
                status=status.HTTP_400_BAD_REQUEST,
            )

        mismatched_ids = [item.id for item in media_files if item.device_sn != mission.device_sn]
        if mismatched_ids:
            return Response(
                validation_error_payload({"media_file_ids": [f"以下媒体 device_sn 不匹配: {mismatched_ids}"]}),
                status=status.HTTP_400_BAD_REQUEST,
            )

        conflicting_ids = [
            item.id
            for item in media_files
            if item.flight_record_id
            and item.flight_record
            and item.flight_record.mission_id
            and item.flight_record.mission_id != mission.id
        ]
        if conflicting_ids:
            return Response(
                validation_error_payload(
                    {"media_file_ids": [f"以下媒体已被 flight_record 锁定到其他任务: {conflicting_ids}"]}
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated_ids = []
        for media_file in media_files:
            media_file.mission = mission
            media_file.save(update_fields=["mission"])
            if getattr(media_file, "dji_index", None) is not None:
                media_file.dji_index.mission = mission
                media_file.dji_index.save(update_fields=["mission", "updated_at"])
            updated_ids.append(media_file.id)

        payload = {
            "mission_id": mission.id,
            "media_file_ids": updated_ids,
            "updated_count": len(updated_ids),
        }
        log_action(
            request=request,
            action="MEDIA_FILE_BIND_MISSION",
            target_type="mission",
            target_id=mission.id,
            after_data=payload,
        )
        return Response(payload, status=status.HTTP_200_OK)
