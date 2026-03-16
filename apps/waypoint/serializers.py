from rest_framework import serializers

from apps.api_v1.tenant_scope import require_request_tenant
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


class WaypointCreateSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        unknown_fields = sorted(set(self.initial_data.keys()) - set(self.fields.keys()))
        if unknown_fields:
            raise serializers.ValidationError({field: "该字段在此接口不可写" for field in unknown_fields})

        current_tenant = require_request_tenant(self.context)
        route = attrs.get("route")
        sequence = attrs.get("sequence")
        if route is not None and route.tenant_id != current_tenant.id:
            raise serializers.ValidationError({"route": "仅允许绑定当前租户下的航线"})
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


class WaypointPatchSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        unknown_fields = sorted(set(self.initial_data.keys()) - set(self.fields.keys()))
        if unknown_fields:
            raise serializers.ValidationError({field: "该字段在此接口不可写" for field in unknown_fields})

        current_tenant = require_request_tenant(self.context)
        if not attrs:
            raise serializers.ValidationError({"non_field_errors": ["至少提供一个可更新字段"]})

        route = self.instance.route
        if route.tenant_id != current_tenant.id:
            raise serializers.ValidationError({"non_field_errors": ["仅允许更新当前租户下的航点"]})
        if route.status != RouteStatus.ACTIVE:
            raise serializers.ValidationError({"non_field_errors": ["仅允许更新状态为正常的航线下航点"]})

        sequence = attrs.get("sequence", self.instance.sequence)
        if Waypoint.objects.filter(route=route, sequence=sequence).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError({"sequence": "同一航线下航点序号不能重复"})

        return attrs

    class Meta:
        model = Waypoint
        fields = [
            "sequence",
            "latitude",
            "longitude",
            "altitude",
        ]
