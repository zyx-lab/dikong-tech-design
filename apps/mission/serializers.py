from rest_framework import serializers

from apps.access.models import DirectoryStatus, EmploymentStatus, TenantMemberRoleStatus, TenantMemberStatus
from apps.api_v1.tenant_scope import require_request_tenant
from apps.drone.models import DroneStatus
from apps.mission.models import Mission, MissionStatus
from apps.route.models import RouteStatus


def _pilot_display_name(pilot_member) -> str:
    staff = getattr(pilot_member.user, "staff_profile", None)
    if staff is not None and staff.name:
        return staff.name
    if pilot_member.display_name:
        return pilot_member.display_name
    return pilot_member.user.username


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

        current_tenant = require_request_tenant(self.context)
        instance = getattr(self, "instance", None)
        route = attrs.get("route", instance.route if instance is not None else None)
        drone = attrs.get("drone", instance.drone if instance is not None else None)
        pilot = attrs.get("pilot", instance.pilot if instance is not None else None)

        if route is not None and route.tenant_id != current_tenant.id:
            raise serializers.ValidationError({"route": "仅允许绑定当前租户下的航线"})
        if drone is not None and drone.tenant_id != current_tenant.id:
            raise serializers.ValidationError({"drone": "仅允许绑定当前租户下的无人机"})
        if route is not None and route.status != RouteStatus.ACTIVE:
            raise serializers.ValidationError({"route": "仅允许绑定状态为正常的航线"})
        if drone is not None and drone.status != DroneStatus.ENABLED:
            raise serializers.ValidationError({"drone": "仅允许绑定启用状态无人机"})
        if pilot is not None and pilot.tenant_id != current_tenant.id:
            raise serializers.ValidationError({"pilot": "仅允许绑定当前租户下的成员"})
        if pilot is not None and pilot.status != TenantMemberStatus.ACTIVE:
            raise serializers.ValidationError({"pilot": "仅允许分配给 ACTIVE 成员"})
        if pilot is not None:
            staff = getattr(pilot.user, "staff_profile", None)
            if staff is None:
                raise serializers.ValidationError({"pilot": "pilot 对应账号必须存在 staff_profile"})
            if staff.employment_status != EmploymentStatus.ACTIVE:
                raise serializers.ValidationError({"pilot": "仅允许分配给在职飞手"})
            if not pilot.role_bindings.filter(
                system_role__code="pilot_operator",
                system_role__status=DirectoryStatus.ACTIVE,
                status=TenantMemberRoleStatus.GRANTED,
            ).exists():
                raise serializers.ValidationError({"pilot": "仅允许分配给飞手类型（pilot_operator）"})

        return attrs

    def create(self, validated_data):
        validated_data["route_name"] = validated_data["route"].name
        validated_data["drone_name"] = validated_data["drone"].name
        validated_data["pilot_name"] = _pilot_display_name(validated_data["pilot"])
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
            validated_data["pilot_name"] = _pilot_display_name(pilot)
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
