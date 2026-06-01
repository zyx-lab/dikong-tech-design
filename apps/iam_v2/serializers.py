from rest_framework import serializers

from apps.access.api_base import StrictSerializer
from apps.access.models import DirectoryStatus, Tenant
from apps.iam_v2.models import Department, FixedRole, V2AccountProfile


FIXED_ROLE_ORDER = [choice.value for choice in FixedRole]
SYSTEM_ROLE_CODES = {FixedRole.PLATFORM_SUPER_ADMIN.value, FixedRole.DEPARTMENT_ADMIN.value}
BUSINESS_ROLE_CODES = {
    FixedRole.TASK_MONITOR_DISPATCHER.value,
    FixedRole.PILOT.value,
    FixedRole.WORK_ORDER_HANDLER.value,
}


def normalize_fixed_role_codes(raw_role_codes) -> list[str]:
    if raw_role_codes is None:
        return []

    normalized = []
    seen = set()
    invalid = []
    for item in raw_role_codes:
        role_code = str(item or "").strip()
        if role_code not in FIXED_ROLE_ORDER:
            invalid.append(role_code)
            continue
        if role_code in seen:
            continue
        seen.add(role_code)
        normalized.append(role_code)

    if invalid:
        raise serializers.ValidationError(f"不支持的 v2 固定角色: {', '.join(invalid)}")
    return [role_code for role_code in FIXED_ROLE_ORDER if role_code in seen]


class DepartmentReadSerializer(serializers.ModelSerializer):
    parentId = serializers.IntegerField(source="parent_id", allow_null=True, read_only=True)
    tenantId = serializers.IntegerField(source="tenant_id", read_only=True)
    createdByUserId = serializers.IntegerField(source="created_by_user_id", allow_null=True, read_only=True)

    class Meta:
        model = Department
        fields = [
            "id",
            "tenantId",
            "parentId",
            "name",
            "status",
            "path",
            "depth",
            "createdByUserId",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class DepartmentCreateSerializer(StrictSerializer):
    name = serializers.CharField(max_length=128)
    tenantId = serializers.IntegerField(required=False, min_value=1)
    parentId = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        parent_id = attrs.get("parentId")
        tenant_id = attrs.get("tenantId")
        parent = None
        tenant = None
        if parent_id:
            parent = Department.objects.filter(pk=parent_id).select_related("tenant").first()
            if parent is None:
                raise serializers.ValidationError({"parentId": ["部门不存在"]})
            tenant = parent.tenant
        elif tenant_id:
            tenant = Tenant.objects.filter(pk=tenant_id).first()
            if tenant is None:
                raise serializers.ValidationError({"tenantId": ["租户不存在"]})
        else:
            context = self.context.get("v2_context")
            tenant = getattr(getattr(context, "department", None), "tenant", None)
        if tenant is None:
            raise serializers.ValidationError({"tenantId": ["创建根部门必须提供 tenantId"]})
        attrs["parent"] = parent
        attrs["tenant"] = tenant
        return attrs

    def create(self, validated_data):
        return Department.objects.create(
            tenant=validated_data["tenant"],
            parent=validated_data.get("parent"),
            name=validated_data["name"],
            created_by_user=self.context.get("request").user if self.context.get("request") else None,
        )


class DepartmentUpdateSerializer(StrictSerializer):
    name = serializers.CharField(max_length=128)
    parentId = serializers.IntegerField(required=False, min_value=1)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if "parentId" in attrs:
            instance = self.context["department"]
            if attrs["parentId"] != instance.parent_id:
                raise serializers.ValidationError({"parentId": ["部门节点暂不支持移动"]})
        return attrs

    def update(self, instance, validated_data):
        instance.name = validated_data["name"]
        instance.save(update_fields=["name", "updated_at"])
        return instance


class FixedRoleSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()


def fixed_role_payloads():
    return [{"code": choice.value, "name": choice.label} for choice in FixedRole]


class AccountCreateSerializer(StrictSerializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=128, write_only=True)
    departmentId = serializers.IntegerField(min_value=1)
    roleCodes = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    status = serializers.ChoiceField(choices=DirectoryStatus.choices, required=False, default=DirectoryStatus.ACTIVE)

    def validate_roleCodes(self, value):
        return normalize_fixed_role_codes(value)


class AccountUpdateSerializer(StrictSerializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=128, required=False, write_only=True)
    departmentId = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(choices=DirectoryStatus.choices)


class AccountRolesReplaceSerializer(StrictSerializer):
    roleCodes = serializers.ListField(child=serializers.CharField(), allow_empty=True)

    def validate_roleCodes(self, value):
        return normalize_fixed_role_codes(value)


class AccountReadSerializer(serializers.ModelSerializer):
    userId = serializers.IntegerField(source="user_id", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    department = serializers.SerializerMethodField()
    roleCodes = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = V2AccountProfile
        fields = ["id", "userId", "username", "status", "department", "roleCodes", "createdAt", "updatedAt"]
        read_only_fields = fields

    def get_department(self, instance):
        return DepartmentReadSerializer(instance.department).data

    def get_roleCodes(self, instance):
        role_codes = set(instance.role_assignments.all().values_list("role_code", flat=True))
        return [role_code for role_code in FIXED_ROLE_ORDER if role_code in role_codes]
