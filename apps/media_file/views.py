from rest_framework import mixins, viewsets

from apps.access.drf_permissions import PermissionMapMixin, ScopedActionPermission
from apps.api_v1.business_response import BusinessApiResponseMixin
from apps.media_file.models import MediaFile
from apps.media_file.serializers import MediaFileReadSerializer


class MediaFileViewSet(
    BusinessApiResponseMixin,
    PermissionMapMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """媒体文件业务接口（V1）。"""

    queryset = MediaFile.objects.select_related("flight_record", "flight_record__mission", "flight_record__drone").all().order_by("-id")
    serializer_class = MediaFileReadSerializer
    permission_classes = [ScopedActionPermission]
    http_method_names = ["get", "head", "options"]

    permission_map = {
        "list": "media_file.view_media_file",
        "retrieve": "media_file.view_media_file",
    }

    def get_queryset(self):
        queryset = super().get_queryset().filter(is_deleted=False)
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
