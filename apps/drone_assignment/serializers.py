from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from apps.access.validation import (
    TenantMemberValidationMessages,
    validate_relation_belongs_to_tenant,
    validate_tenant_member_as_pilot,
)
from apps.api_v1.tenant_scope import require_request_tenant
from apps.drone_assignment.models import DroneAssignment, DroneAssignmentStatus


class DroneAssignmentReadSerializer(serializers.ModelSerializer):
    drone_code = serializers.CharField(source="drone.code", read_only=True)
    drone_name = serializers.CharField(source="drone.name", read_only=True)
    member_no = serializers.CharField(source="tenant_member.member_no", read_only=True)
    staff_name = serializers.SerializerMethodField(read_only=True)

    @extend_schema_field(serializers.CharField())
    def get_staff_name(self, obj):
        staff = getattr(obj.tenant_member.user, "staff_profile", None)
        if staff is not None and staff.name:
            return staff.name
        return obj.tenant_member.display_name or obj.tenant_member.user.username

    class Meta:
        model = DroneAssignment
        fields = [
            "id",
            "drone",
            "drone_code",
            "drone_name",
            "tenant_member",
            "member_no",
            "staff_name",
            "status",
            "start_at",
            "end_at",
            "created_by_tenant_member_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class DroneAssignmentCreateSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        drone = attrs["drone"]
        tenant_member = attrs["tenant_member"]
        current_tenant = require_request_tenant(self.context)

        validate_relation_belongs_to_tenant(
            related_obj=drone,
            tenant_id=current_tenant.id,
            field_name="drone",
            mismatch_message="仅允许绑定当前租户下的无人机",
            error_cls=serializers.ValidationError,
        )
        validate_tenant_member_as_pilot(
            tenant_member=tenant_member,
            tenant_id=current_tenant.id,
            field_name="tenant_member",
            messages=TenantMemberValidationMessages(
                tenant_mismatch="仅允许绑定当前租户下的成员",
                inactive_member="仅允许绑定 ACTIVE 成员",
                missing_staff_profile="tenant_member 对应账号必须存在 staff_profile",
                inactive_employment="仅允许分配给在职人员",
                missing_role="仅允许分配给飞手类型（pilot_operator）",
            ),
            error_cls=serializers.ValidationError,
        )

        if DroneAssignment.objects.filter(
            tenant=current_tenant,
            drone=drone,
            tenant_member=tenant_member,
            status=DroneAssignmentStatus.ACTIVE,
        ).exists():
            raise serializers.ValidationError({"non_field_errors": ["该无人机与该飞手已存在生效分配"]})

        return attrs

    def create(self, validated_data):
        # 开始时间由模型自动写入，统一使用 ACTIVE 初始状态。
        validated_data["status"] = DroneAssignmentStatus.ACTIVE
        validated_data["end_at"] = None
        return super().create(validated_data)

    class Meta:
        model = DroneAssignment
        fields = [
            "id",
            "drone",
            "tenant_member",
        ]
        read_only_fields = ["id"]
        extra_kwargs = {
            "drone": {"help_text": "当前租户下要分配的无人机 ID。"},
            "tenant_member": {"help_text": "当前租户下要接收分配的成员 ID；必须是 ACTIVE 且具备 pilot_operator 角色。"},
        }
