from rest_framework import serializers

from apps.route.models import RouteStatus
from apps.waypoint.models import Waypoint


class WaypointReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Waypoint
        fields = [
            "id",
            "route",
            "sequence",
            "latitude",
            "longitude",
            "altitude",
            "created_at",
        ]
        read_only_fields = fields


class WaypointWriteSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        unknown_fields = sorted(set(self.initial_data.keys()) - set(self.fields.keys()))
        if unknown_fields:
            raise serializers.ValidationError({field: "该字段在此接口不可写" for field in unknown_fields})

        route = attrs.get("route")
        sequence = attrs.get("sequence")
        if route is not None and route.status != RouteStatus.ACTIVE:
            raise serializers.ValidationError({"route": "仅允许向状态为正常的航线新增航点"})
        if route is not None and sequence is not None:
            if Waypoint.objects.filter(route=route, sequence=sequence).exists():
                raise serializers.ValidationError({"sequence": "同一航线下航点序号不能重复"})
        return attrs

    class Meta:
        model = Waypoint
        fields = [
            "route",
            "sequence",
            "latitude",
            "longitude",
            "altitude",
        ]
