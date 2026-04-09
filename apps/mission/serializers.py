from rest_framework import serializers

from apps.access.validation import (
    TenantMemberValidationMessages,
    validate_relation_belongs_to_tenant,
    validate_tenant_member_as_pilot,
)
from apps.api_v1.serializers import RejectUnknownFieldsMixin
from apps.api_v1.tenant_scope import require_request_tenant
from apps.mission.models import Mission


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
            "device_sn",
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


class MissionCreateSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)

        current_tenant = require_request_tenant(self.context)
        route = attrs.get("route")
        drone = attrs.get("drone")
        pilot = attrs.get("pilot")

        validate_relation_belongs_to_tenant(
            related_obj=route,
            tenant_id=current_tenant.id,
            field_name="route",
            mismatch_message="仅允许绑定当前租户下的航线",
            error_cls=serializers.ValidationError,
        )
        validate_relation_belongs_to_tenant(
            related_obj=drone,
            tenant_id=current_tenant.id,
            field_name="drone",
            mismatch_message="仅允许绑定当前租户下的无人机",
            error_cls=serializers.ValidationError,
        )
        validate_tenant_member_as_pilot(
            tenant_member=pilot,
            tenant_id=current_tenant.id,
            field_name="pilot",
            messages=TenantMemberValidationMessages(
                tenant_mismatch="仅允许绑定当前租户下的成员",
                inactive_member="仅允许分配给 ACTIVE 成员",
                missing_staff_profile="pilot 对应账号必须存在 staff_profile",
                inactive_employment="仅允许分配给在职飞手",
                missing_role="仅允许分配给飞手类型（pilot_operator）",
            ),
            error_cls=serializers.ValidationError,
        )

        return attrs

    def create(self, validated_data):
        validated_data["route_name"] = validated_data["route"].name
        validated_data["pilot_name"] = _pilot_display_name(validated_data["pilot"])
        return super().create(validated_data)

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
        extra_kwargs = {
            "name": {"help_text": "任务名称，用于调度展示和日志定位。"},
            "route": {"help_text": "任务绑定的航线 ID；必须属于当前租户。", "required": True, "allow_null": False},
            "drone": {"help_text": "任务绑定的无人机 ID；必须属于当前租户。", "required": False, "allow_null": True},
            "pilot": {"help_text": "任务绑定的飞手成员 ID；必须为当前租户 ACTIVE 成员且具备 pilot_operator 角色。"},
            "scheduled_at": {"help_text": "计划执行时间，可为空。"},
            "remark": {"help_text": "任务备注，可为空。"},
        }


class MissionUpdateSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Mission
        fields = [
            "name",
            "drone",
            "scheduled_at",
            "remark",
        ]
        extra_kwargs = {
            "name": {"help_text": "任务名称。"},
            "drone": {"help_text": "任务绑定的无人机 ID；可为空以解绑。", "required": False, "allow_null": True},
            "scheduled_at": {"help_text": "计划执行时间，可为空。", "required": False},
            "remark": {"help_text": "任务备注，可为空。", "required": False},
        }
