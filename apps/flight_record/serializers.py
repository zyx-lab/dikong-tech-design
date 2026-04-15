from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.reverse import reverse

from apps.api_v1.serializers import RejectUnknownFieldsMixin
from apps.flight_record.models import FlightRecord
from apps.media_file.models import MediaFile


class FlightRecordSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = FlightRecord
        fields = [
            "id",
            "flight_no",
            "mission",
            "mission_name",
            "route_name",
            "airport_name",
            "drone",
            "device_sn",
            "drone_name",
            "pilot",
            "pilot_name",
            "start_time",
            "end_time",
            "flight_duration",
            "photo_count",
            "video_count",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class FlightRecordMediaFileSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField(help_text="平台媒体下载接口。")

    class Meta:
        model = MediaFile
        fields = [
            "id",
            "media_type",
            "file_name",
            "thumbnail_url",
            "captured_at",
            "download_url",
        ]
        read_only_fields = fields

    def get_download_url(self, obj: MediaFile) -> str:
        return reverse("media-file-download", kwargs={"pk": obj.id})


class FlightRecordDetailSerializer(FlightRecordSummarySerializer):
    media_files = serializers.SerializerMethodField()

    class Meta(FlightRecordSummarySerializer.Meta):
        fields = [*FlightRecordSummarySerializer.Meta.fields, "media_files"]
        read_only_fields = fields

    @extend_schema_field(FlightRecordMediaFileSerializer(many=True))
    def get_media_files(self, obj: FlightRecord) -> list[dict]:
        media_queryset = obj.media_files.filter(is_deleted=False, dji_index__isnull=False).order_by("-captured_at", "-id")
        return FlightRecordMediaFileSerializer(media_queryset, many=True, context=self.context).data


class FlightRecordWriteSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = FlightRecord
        fields = [
            "mission_name",
            "route_name",
            "airport_name",
            "drone_name",
            "pilot_name",
            "flight_duration",
            "photo_count",
        ]
        extra_kwargs = {
            "mission_name": {"required": False, "help_text": "任务名称展示文案，可人工修正。"},
            "route_name": {"required": False, "help_text": "航线名称展示文案，可人工修正。"},
            "airport_name": {"required": False, "help_text": "执行机场名称，当前阶段允许人工补录。"},
            "drone_name": {"required": False, "help_text": "无人机名称展示文案，可人工修正。"},
            "pilot_name": {"required": False, "help_text": "飞手名称展示文案，可人工修正。"},
            "flight_duration": {"required": False, "help_text": "飞行时长快照，单位秒。"},
            "photo_count": {"required": False, "help_text": "图片数量；当前阶段固定由人工修正，默认 0。"},
        }
