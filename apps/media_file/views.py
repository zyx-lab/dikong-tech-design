from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.response import Response

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission, ScopedQuerysetMixin
from apps.access.services import log_action
from apps.api_v1.business_response import BusinessApiResponseMixin
from apps.api_v1.schema import (
    BUSINESS_INTERNAL_ERROR_RESPONSE,
    TENANT_CODE_HEADER_PARAMETER,
    business_error_example,
    business_error_response,
    object_envelope_serializer,
    paginated_envelope_serializer,
)
from apps.api_v1.tenant_scope import TenantScopedBusinessMixin
from apps.media_file.models import MediaFile, MediaType
from apps.media_file.serializers import MediaFileReadSerializer, MediaFileWriteSerializer


MEDIA_FILE_LIST_RESPONSE = paginated_envelope_serializer("MediaFileListResponse", MediaFileReadSerializer)
MEDIA_FILE_DETAIL_RESPONSE = object_envelope_serializer("MediaFileDetailResponse", MediaFileReadSerializer)
MEDIA_FILE_DELETE_RESPONSE = inline_serializer(
    name="MediaFileDeleteResponse",
    fields={
        "business_code": serializers.CharField(),
        "business_detail_code": serializers.CharField(),
        "id": serializers.IntegerField(),
        "is_deleted": serializers.BooleanField(help_text="固定为 true，表示已完成逻辑删除。"),
    },
)

MEDIA_FILE_FILTER_PARAMETERS = [
    TENANT_CODE_HEADER_PARAMETER,
    OpenApiParameter(
        name="flight_record_id",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按关联飞行记录 ID 精确过滤。",
    ),
    OpenApiParameter(
        name="media_type",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按媒体类型精确过滤。",
        enum=[choice[0] for choice in MediaType.choices],
    ),
    OpenApiParameter(
        name="mission_id",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按飞行记录关联任务 ID 精确过滤。",
    ),
    OpenApiParameter(
        name="drone_id",
        type=int,
        location=OpenApiParameter.QUERY,
        description="按飞行记录关联无人机 ID 精确过滤。",
    ),
    OpenApiParameter(
        name="file_name",
        type=str,
        location=OpenApiParameter.QUERY,
        description="按文件名做模糊匹配。",
    ),
]

MEDIA_FILE_PERMISSION_DENIED_RESPONSE = business_error_response(
    description="未认证、无权限、缺少租户上下文，或 platform_admin 访问业务 API 被拒绝。",
    examples=[
        business_error_example(
            "未登录",
            business_code="PERMISSION_DENIED",
            business_detail_code="NOT_AUTHENTICATED",
            detail="Authentication credentials were not provided.",
            status_codes=["401"],
        ),
        business_error_example(
            "无权限",
            business_code="PERMISSION_DENIED",
            business_detail_code="FORBIDDEN",
            detail="PERMISSION_DENIED",
            status_codes=["403"],
        ),
        business_error_example(
            "缺少租户上下文",
            business_code="PERMISSION_DENIED",
            business_detail_code="TENANT_CONTEXT_REQUIRED",
            detail="tenant context required",
            status_codes=["403"],
        ),
        business_error_example(
            "平台管理员访问业务 API",
            business_code="PERMISSION_DENIED",
            business_detail_code="FORBIDDEN",
            detail="platform admin cannot access tenant business api",
            status_codes=["403"],
        ),
    ],
)

MEDIA_FILE_INVALID_PARAMS_RESPONSE = business_error_response(
    description="请求体不合法或绑定关系不满足约束，business_code 固定为 INVALID_PARAMS。",
    examples=[
        business_error_example(
            "跨租户飞行记录",
            business_code="INVALID_PARAMS",
            business_detail_code="VALIDATION_ERROR",
            detail="参数校验失败",
            status_codes=["400"],
            extras={"errors": {"flight_record": ["仅允许绑定当前租户下的飞行记录"]}},
        ),
        business_error_example(
            "缺少文件名",
            business_code="INVALID_PARAMS",
            business_detail_code="VALIDATION_ERROR",
            detail="参数校验失败",
            status_codes=["400"],
            extras={"errors": {"file_name": ["该字段是必填项。"]}},
        ),
        business_error_example(
            "PATCH 空请求体",
            business_code="INVALID_PARAMS",
            business_detail_code="VALIDATION_ERROR",
            detail="PATCH 请求至少包含一个可写字段",
            status_codes=["400"],
            extras={"errors": {"body": "请至少提交一个可写字段"}},
        ),
    ],
)

MEDIA_FILE_NOT_FOUND_RESPONSE = business_error_response(
    description="目标媒体文件不存在，或在当前租户/授权作用域下不可见；已逻辑删除记录也按不存在处理。",
    examples=[
        business_error_example(
            "媒体文件不存在",
            business_code="RESOURCE_NOT_FOUND",
            business_detail_code="NOT_FOUND",
            detail="No MediaFile matches the given query.",
            status_codes=["404"],
        )
    ],
)


@extend_schema_view(
    list=extend_schema(
        summary="查询媒体文件列表",
        description="按当前租户查询媒体文件，支持按飞行记录、任务、无人机、媒体类型、文件名过滤。",
        parameters=MEDIA_FILE_FILTER_PARAMETERS,
        responses={
            200: OpenApiResponse(response=MEDIA_FILE_LIST_RESPONSE, description="查询成功。"),
            401: MEDIA_FILE_PERMISSION_DENIED_RESPONSE,
            403: MEDIA_FILE_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Media File"],
    ),
    retrieve=extend_schema(
        summary="读取媒体文件详情",
        description="按媒体文件 ID 读取单条详情；已逻辑删除记录按不可见处理。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=MEDIA_FILE_DETAIL_RESPONSE, description="读取成功。"),
            401: MEDIA_FILE_PERMISSION_DENIED_RESPONSE,
            403: MEDIA_FILE_PERMISSION_DENIED_RESPONSE,
            404: MEDIA_FILE_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Media File"],
    ),
    create=extend_schema(
        summary="创建媒体文件",
        description="创建一条媒体文件元数据记录，用于沉淀照片或视频文件与飞行记录的关联信息。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=MediaFileWriteSerializer,
        examples=[
            OpenApiExample(
                "创建媒体文件请求",
                request_only=True,
                value={
                    "flight_record": 1,
                    "media_type": MediaType.PHOTO,
                    "file_name": "IMG_CREATE_OK.JPG",
                    "file_url": "https://example.com/IMG_CREATE_OK.JPG",
                    "thumbnail_url": "https://example.com/thumb/IMG_CREATE_OK.JPG",
                    "file_size": 4096,
                },
            )
        ],
        responses={
            201: OpenApiResponse(response=MEDIA_FILE_DETAIL_RESPONSE, description="创建成功。"),
            400: MEDIA_FILE_INVALID_PARAMS_RESPONSE,
            401: MEDIA_FILE_PERMISSION_DENIED_RESPONSE,
            403: MEDIA_FILE_PERMISSION_DENIED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Media File"],
    ),
    partial_update=extend_schema(
        summary="局部更新媒体文件",
        description="按媒体文件 ID 局部更新元数据字段，不承担逻辑删除恢复或批量操作。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=MediaFileWriteSerializer,
        examples=[
            OpenApiExample(
                "PATCH 媒体文件请求",
                request_only=True,
                value={
                    "media_type": MediaType.VIDEO,
                    "file_name": "IMG_PATCH_NEW.MP4",
                    "file_url": "https://example.com/IMG_PATCH_NEW.MP4",
                },
            )
        ],
        responses={
            200: OpenApiResponse(response=MEDIA_FILE_DETAIL_RESPONSE, description="更新成功。"),
            400: MEDIA_FILE_INVALID_PARAMS_RESPONSE,
            401: MEDIA_FILE_PERMISSION_DENIED_RESPONSE,
            403: MEDIA_FILE_PERMISSION_DENIED_RESPONSE,
            404: MEDIA_FILE_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        tags=["Business API - Media File"],
    ),
    destroy=extend_schema(
        summary="逻辑删除媒体文件",
        description="按媒体文件 ID 执行逻辑删除，写入 `is_deleted=true` 和 `deleted_at`。",
        parameters=[TENANT_CODE_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=MEDIA_FILE_DELETE_RESPONSE, description="删除成功。"),
            401: MEDIA_FILE_PERMISSION_DENIED_RESPONSE,
            403: MEDIA_FILE_PERMISSION_DENIED_RESPONSE,
            404: MEDIA_FILE_NOT_FOUND_RESPONSE,
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
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """媒体文件业务接口（V1）。"""

    queryset = MediaFile.objects.select_related("flight_record", "flight_record__mission", "flight_record__drone").all().order_by("-id")
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    permission_map = {
        "list": "media_file.view_media_file",
        "retrieve": "media_file.view_media_file",
        "create": "media_file.manage_media_file",
        "partial_update": "media_file.manage_media_file",
        "destroy": "media_file.manage_media_file",
    }

    @staticmethod
    def assigned_scope_filter_builder(tenant_member_id: int) -> dict:
        return {"flight_record__pilot_id": tenant_member_id}

    def get_serializer_class(self):
        if self.action in {"create", "partial_update"}:
            return MediaFileWriteSerializer
        return MediaFileReadSerializer

    def _media_file_payload(self, media_file: MediaFile) -> dict:
        return dict(MediaFileReadSerializer(media_file, context={"request": self.request}).data)

    def get_queryset(self):
        queryset = self.scope_queryset_to_tenant(super().get_queryset()).filter(is_deleted=False)
        params = self.request.query_params

        # 业务作用：
        # 提供媒体文件列表检索能力，作为“飞行记录复盘 -> 媒体浏览/下载”链路中的基础只读入口。
        # 外部系统可组合 flight_record_id/media_type/file_name 过滤条件实现不同业务流，不需要编排型接口。
        #
        # 边界语义：
        # 1) 仅返回未逻辑删除（is_deleted=false）的媒体文件；
        # 2) 只做读取，不承担上传、删除、转码、归档等写操作；
        # 3) business_code/business_detail_code 由 BusinessApiResponseMixin 统一补齐。
        flight_record_id = params.get("flight_record_id")
        media_type = params.get("media_type")
        mission_id = params.get("mission_id")
        drone_id = params.get("drone_id")
        file_name = params.get("file_name")

        if flight_record_id:
            queryset = queryset.filter(flight_record_id=flight_record_id)
        if media_type:
            queryset = queryset.filter(media_type=media_type)
        if mission_id:
            queryset = queryset.filter(flight_record__mission_id=mission_id)
        if drone_id:
            queryset = queryset.filter(flight_record__drone_id=drone_id)
        if file_name:
            queryset = queryset.filter(file_name__icontains=file_name)

        if self.action in {"list", "retrieve", "partial_update", "destroy"}:
            return self.apply_scope(queryset)

        return queryset

    def list(self, request, *args, **kwargs):
        # 返回语义：
        # 1) 有查看权限：HTTP 200 + business_code=SUCCESS；
        # 2) 未认证或无权限：HTTP 401/403 + business_code=PERMISSION_DENIED。
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 media_file_id 精确读取媒体文件详情”的基础只读能力，
        # 供外部系统在列表筛选后拉取单条媒体元数据（文件地址、类型、拍摄时间、关联飞行记录）。
        # 本接口仅做详情读取，不承担上传、删除、转码、归档等写操作。
        #
        # 边界与返回语义：
        # 1) 有查看权限且记录存在：business_code=SUCCESS（HTTP 200）；
        # 2) 记录不存在或已逻辑删除：business_code=RESOURCE_NOT_FOUND（HTTP 404）；
        # 3) 未认证或无查看权限：business_code=PERMISSION_DENIED（HTTP 401/403）。
        return super().retrieve(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        # 业务作用：
        # 创建媒体文件主记录（media_files），用于沉淀飞行记录下的照片/视频元数据，
        # 作为后续“媒体列表检索 -> 详情读取 -> 逻辑删除”链路的起点能力。
        #
        # 适用边界：
        # 1) 本接口只写入媒体元数据，不负责文件上传、转码、分发、归档；
        # 2) 默认写入未删除状态（is_deleted=false, deleted_at=null）；
        # 3) 返回统一携带 business_code/business_detail_code。
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        media_file = self.perform_create(serializer)

        read_serializer = MediaFileReadSerializer(media_file, context={"request": request})
        log_action(
            request=request,
            action="MEDIA_FILE_CREATE",
            target_type="media_file",
            target_id=media_file.id,
            after_data=dict(read_serializer.data),
        )
        headers = self.get_success_headers(read_serializer.data)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @transaction.atomic
    def perform_create(self, serializer):
        return serializer.save(
            tenant=self.get_current_tenant(),
            is_deleted=False,
            deleted_at=None,
        )

    def partial_update(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 media_file_id 局部更新媒体元数据”的基础接口（PATCH /api/v1/media-files/{id}），
        # 允许外部系统修正文件名、URL、拍摄位置、媒体类型以及归属飞行记录等字段。
        #
        # 适用边界：
        # 1) 仅更新单条 media_file 自身元数据，不承担文件上传、转码、逻辑删除恢复或批量编排；
        # 2) PATCH 请求体必须至少包含一个可写字段；
        # 3) 已逻辑删除记录不参与更新，统一按资源不存在处理。
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
        media_file = self.perform_update(serializer)

        if getattr(instance, "_prefetched_objects_cache", None):
            instance._prefetched_objects_cache = {}

        return Response(self._media_file_payload(media_file), status=status.HTTP_200_OK)

    def destroy(self, request, *args, **kwargs):
        # 业务作用：
        # 提供“按 media_file_id 逻辑删除媒体文件”的基础写操作能力。
        # 该接口只做单条删除，不做批量删除或回收站恢复；外部系统可自行组合业务流程。
        #
        # 边界与返回语义：
        # 1) 有删除权限且记录存在（且未删除）：标记 is_deleted=true 并写入 deleted_at，
        #    返回 business_code=SUCCESS（HTTP 200）；
        # 2) 记录不存在或已逻辑删除：business_code=RESOURCE_NOT_FOUND（HTTP 404）；
        # 3) 未认证或无删除权限：business_code=PERMISSION_DENIED（HTTP 401/403）。
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(
            {
                "id": instance.id,
                "is_deleted": True,
            },
            status=status.HTTP_200_OK,
        )

    def perform_destroy(self, instance):
        # 设计约束：
        # media_files 采用逻辑删除，不做物理删除，避免破坏飞行记录与媒体审计追溯链路。
        instance.is_deleted = True
        instance.deleted_at = timezone.now()
        instance.save(update_fields=["is_deleted", "deleted_at"])

    @transaction.atomic
    def perform_update(self, serializer):
        media_file = self.get_object()
        before_payload = self._media_file_payload(media_file)
        media_file = serializer.save()
        after_payload = self._media_file_payload(media_file)
        log_action(
            request=self.request,
            action="MEDIA_FILE_UPDATE",
            target_type="media_file",
            target_id=media_file.id,
            before_data=before_payload,
            after_data=after_payload,
        )
        return media_file
