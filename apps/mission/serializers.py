from rest_framework import serializers

from apps.access.models import EmploymentStatus
from apps.drone.models import DroneStatus
from apps.mission.models import Mission, MissionStatus
from apps.route.models import RouteStatus


class MissionReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Mission
        fields = [
            "id",
            "name",
            "route",
            "route_name",
            "drone",
            "drone_name",
            "pilot",
            "pilot_name",
            "scheduled_at",
            "remark",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class MissionWriteSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        unknown_fields = sorted(set(self.initial_data.keys()) - set(self.fields.keys()))
        if unknown_fields:
            raise serializers.ValidationError({field: "该字段在此接口不可写" for field in unknown_fields})

        instance = getattr(self, "instance", None)
        route = attrs.get("route", instance.route if instance is not None else None)
        drone = attrs.get("drone", instance.drone if instance is not None else None)
        pilot = attrs.get("pilot", instance.pilot if instance is not None else None)

        if route is not None and route.status != RouteStatus.ACTIVE:
            raise serializers.ValidationError({"route": "仅允许绑定状态为正常的航线"})
        if drone is not None and drone.status != DroneStatus.ENABLED:
            raise serializers.ValidationError({"drone": "仅允许绑定启用状态无人机"})
        if pilot is not None and pilot.employment_status != EmploymentStatus.ACTIVE:
            raise serializers.ValidationError({"pilot": "仅允许分配给在职飞手"})
        if pilot is not None and pilot.staff_type.code != "pilot_operator":
            raise serializers.ValidationError({"pilot": "仅允许分配给飞手类型（pilot_operator）"})

        return attrs

    def create(self, validated_data):
        validated_data["route_name"] = validated_data["route"].name
        validated_data["drone_name"] = validated_data["drone"].name
        validated_data["pilot_name"] = validated_data["pilot"].name
        validated_data["status"] = MissionStatus.PENDING
        return super().create(validated_data)

    def update(self, instance, validated_data):
        route = validated_data.get("route")
        drone = validated_data.get("drone")
        pilot = validated_data.get("pilot")
        if route is not None:
            validated_data["route_name"] = route.name
        if drone is not None:
            validated_data["drone_name"] = drone.name
        if pilot is not None:
            validated_data["pilot_name"] = pilot.name
        return super().update(instance, validated_data)

    class Meta:
        model = Mission
        fields = [
            "name",
            "route",
            "drone",
            "pilot",
            "scheduled_at",
            "remark",
        ]
