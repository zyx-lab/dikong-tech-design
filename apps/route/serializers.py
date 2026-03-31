from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from apps.api_v1.serializers import RejectUnknownFieldsMixin
from apps.route.models import Route


class RouteWaypointSerializer(RejectUnknownFieldsMixin, serializers.Serializer):
    sequence = serializers.IntegerField(min_value=1)
    latitude = serializers.DecimalField(max_digits=12, decimal_places=8)
    longitude = serializers.DecimalField(max_digits=12, decimal_places=8)
    altitude = serializers.DecimalField(max_digits=10, decimal_places=2)


class RouteReadSerializer(serializers.ModelSerializer):
    is_published = serializers.SerializerMethodField()
    waypoints = serializers.SerializerMethodField()

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
            "is_published",
            "waypoints",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_is_published(self, obj) -> bool:
        return bool(getattr(getattr(obj, "dji_index", None), "is_published", False))

    @extend_schema_field(RouteWaypointSerializer(many=True))
    def get_waypoints(self, obj):
        return RouteWaypointSerializer(obj.waypoint_rows.all(), many=True).data


class RouteWriteSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    waypoints = RouteWaypointSerializer(many=True, required=False)

    class Meta:
        model = Route
        fields = [
            "name",
            "route_type",
            "drone_type_id",
            "total_distance",
            "estimated_duration",
            "waypoints",
        ]
        extra_kwargs = {
            "name": {"help_text": "航线名称。"},
            "route_type": {"help_text": "航线类型扩展位。", "required": False},
            "drone_type_id": {"help_text": "适用无人机类型 ID，可为空。", "required": False},
            "total_distance": {"help_text": "航线总长度，单位米，可为空。", "required": False},
            "estimated_duration": {"help_text": "预计飞行时长，单位秒，可为空。", "required": False},
        }

    def validate_waypoints(self, value):
        sequences = [item["sequence"] for item in value]
        if len(sequences) != len(set(sequences)):
            raise serializers.ValidationError("同一航线下航点序号不能重复")
        return value


class RouteCreateSerializer(RouteWriteSerializer):
    pass


class RouteUpdateSerializer(RouteWriteSerializer):
    pass
