from datetime import datetime

from rest_framework import serializers

from apps.route.models import Route


class RouteReadSerializer(serializers.ModelSerializer):
    sync_status = serializers.SerializerMethodField()
    last_sync_at = serializers.SerializerMethodField()
    error_msg = serializers.SerializerMethodField()

    class Meta:
        model = Route
        fields = [
            "id",
            "name",
            "route_type",
            "drone_type_id",
            "total_distance",
            "estimated_duration",
            "waypoint_count",
            "creator_name",
            "status",
            "sync_status",
            "last_sync_at",
            "error_msg",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_sync_status(self, obj) -> str:
        return getattr(getattr(obj, "dji_index", None), "sync_status", "")

    def get_last_sync_at(self, obj) -> datetime | None:
        return getattr(getattr(obj, "dji_index", None), "last_sync_at", None)

    def get_error_msg(self, obj) -> str:
        return getattr(getattr(obj, "dji_index", None), "error_msg", "")


class RouteCreateSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True, help_text="KMZ 航线文件。")

    class Meta:
        model = Route
        fields = [
            "name",
            "route_type",
            "drone_type_id",
            "total_distance",
            "estimated_duration",
            "file",
        ]
        extra_kwargs = {
            "name": {"help_text": "航线名称。"},
            "route_type": {"help_text": "航线类型扩展位。"},
            "drone_type_id": {"help_text": "适用无人机类型 ID，可为空。", "required": False},
            "total_distance": {"help_text": "航线总长度，单位米，可为空。", "required": False},
            "estimated_duration": {"help_text": "预计飞行时长，单位秒，可为空。", "required": False},
        }

    def validate(self, attrs):
        unknown_fields = sorted(set(self.initial_data.keys()) - set(self.fields.keys()))
        if unknown_fields:
            raise serializers.ValidationError({field: "该字段在此接口不可写" for field in unknown_fields})
        return attrs


class RouteUpdateSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True, required=False, help_text="可选。提交新 KMZ 时重新导入 DJI。")

    class Meta:
        model = Route
        fields = [
            "name",
            "route_type",
            "drone_type_id",
            "total_distance",
            "estimated_duration",
            "file",
        ]
        extra_kwargs = {
            "name": {"help_text": "航线名称。"},
            "route_type": {"help_text": "航线类型扩展位。"},
            "drone_type_id": {"help_text": "适用无人机类型 ID，可为空。", "required": False},
            "total_distance": {"help_text": "航线总长度，单位米，可为空。", "required": False},
            "estimated_duration": {"help_text": "预计飞行时长，单位秒，可为空。", "required": False},
        }

    def validate(self, attrs):
        unknown_fields = sorted(set(self.initial_data.keys()) - set(self.fields.keys()))
        if unknown_fields:
            raise serializers.ValidationError({field: "该字段在此接口不可写" for field in unknown_fields})
        return attrs
