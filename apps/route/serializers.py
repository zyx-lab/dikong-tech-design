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
            "waypoint_count",
        ]
