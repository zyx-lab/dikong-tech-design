from datetime import datetime

from rest_framework import serializers

from apps.access.models import DirectoryStatus, EmploymentStatus, TenantMemberRoleStatus, TenantMemberStatus
from apps.api_v1.serializers import RejectUnknownFieldsMixin
from apps.api_v1.tenant_scope import require_request_tenant
from apps.mission.models import Mission, MissionStatus


def _pilot_display_name(pilot_member) -> str:
    staff = getattr(pilot_member.user, "staff_profile", None)
    if staff is not None and staff.name:
        return staff.name
    if pilot_member.display_name:
        return pilot_member.display_name
    return pilot_member.user.username


class MissionReadSerializer(serializers.ModelSerializer):
    sync_status = serializers.SerializerMethodField()
    execution_status = serializers.SerializerMethodField()
    last_sync_at = serializers.SerializerMethodField()

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
            "dji_job_id",
            "sync_status",
            "execution_status",
            "last_sync_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_sync_status(self, obj) -> str:
        return getattr(getattr(obj, "dji_index", None), "sync_status", "")

    def get_execution_status(self, obj) -> str:
        return getattr(getattr(obj, "dji_index", None), "execution_status", "")

    def get_last_sync_at(self, obj) -> datetime | None:
        return getattr(getattr(obj, "dji_index", None), "last_sync_at", None)


class MissionCreateSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    dock_sn = serializers.CharField(write_only=True, required=True, allow_blank=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)

        current_tenant = require_request_tenant(self.context)
        route = attrs.get("route")
        drone = attrs.get("drone")
        pilot = attrs.get("pilot")

        if route is not None and route.tenant_id != current_tenant.id:
            raise serializers.ValidationError({"route": "仅允许绑定当前租户下的航线"})
        if drone is not None and drone.tenant_id != current_tenant.id:
            raise serializers.ValidationError({"drone": "仅允许绑定当前租户下的无人机"})
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
        validated_data.pop("dock_sn", "")
        validated_data["route_name"] = validated_data["route"].name
        validated_data["drone_name"] = validated_data["drone"].name
        validated_data["pilot_name"] = _pilot_display_name(validated_data["pilot"])
        validated_data["status"] = MissionStatus.PENDING
        return super().create(validated_data)

    class Meta:
        model = Mission
        fields = [
            "name",
            "route",
            "drone",
            "pilot",
            "dock_sn",
            "scheduled_at",
            "remark",
        ]
        extra_kwargs = {
            "name": {"help_text": "任务名称，用于调度展示和日志定位。"},
            "route": {"help_text": "任务绑定的航线 ID；必须属于当前租户。", "required": True, "allow_null": False},
            "drone": {"help_text": "任务绑定的无人机 ID；必须属于当前租户。"},
            "pilot": {"help_text": "任务绑定的飞手成员 ID；必须为当前租户 ACTIVE 成员且具备 pilot_operator 角色。"},
            "dock_sn": {"help_text": "任务下发时透传给 DJI 的 dock_sn。", "required": True},
            "scheduled_at": {"help_text": "计划执行时间，可为空。"},
            "remark": {"help_text": "任务备注，可为空。"},
        }


class MissionUpdateSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    def validate(self, attrs):
        return super().validate(attrs)

    class Meta:
        model = Mission
        fields = [
            "name",
            "scheduled_at",
            "remark",
        ]
        extra_kwargs = {
            "name": {"help_text": "任务名称。"},
            "scheduled_at": {"help_text": "计划执行时间，可为空。", "required": False},
            "remark": {"help_text": "任务备注，可为空。", "required": False},
        }
