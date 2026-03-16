from rest_framework import serializers

from apps.route.models import Route


class RouteReadSerializer(serializers.ModelSerializer):
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
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class RouteWriteSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        unknown_fields = sorted(set(self.initial_data.keys()) - set(self.fields.keys()))
        if unknown_fields:
            raise serializers.ValidationError({field: "该字段在此接口不可写" for field in unknown_fields})
        return attrs

    class Meta:
        model = Route
        fields = [
            "name",
            "route_type",
            "drone_type_id",
            "total_distance",
            "estimated_duration",
        ]
        extra_kwargs = {
            "name": {"help_text": "航线名称，用于台账展示与任务绑定。"},
            "route_type": {"help_text": "航线类型扩展位。当前仅开放 `0=待扩展`。"},
            "drone_type_id": {"help_text": "适用无人机类型 ID，可为空；用于业务侧做机型约束。"},
            "total_distance": {"help_text": "航线总长度，单位米；可为空。"},
            "estimated_duration": {"help_text": "预计飞行时长，单位秒；可为空。"},
        }
