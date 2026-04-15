from rest_framework import serializers

from apps.api_v1.serializers import RejectUnknownFieldsMixin
from apps.flight_record.models import FlightRecord


class FlightRecordReadSerializer(serializers.ModelSerializer):
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
            "video_count",
        ]
        extra_kwargs = {
            "mission_name": {"required": False, "help_text": "任务名称展示文案，可人工修正。"},
            "route_name": {"required": False, "help_text": "航线名称展示文案，可人工修正。"},
            "airport_name": {"required": False, "help_text": "执行机场名称，当前阶段允许人工补录。"},
            "drone_name": {"required": False, "help_text": "无人机名称展示文案，可人工修正。"},
            "pilot_name": {"required": False, "help_text": "飞手名称展示文案，可人工修正。"},
            "flight_duration": {"required": False, "help_text": "飞行时长快照，单位秒。"},
            "photo_count": {"required": False, "help_text": "图片数量；当前阶段固定由人工修正，默认 0。"},
            "video_count": {"required": False, "help_text": "视频数量快照；创建时按 mission 已关联媒体数写入。"},
        }
