from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.access.api_base import StrictSerializer
from apps.access.models import DirectoryStatus
from apps.iam_v2.models import (
    DEPARTMENT_ROLE_CODES,
    ROLE_CODE_ORDER,
    Department,
    FixedRole,
    V2AccountProfile,
)


FIXED_ROLE_ORDER = ROLE_CODE_ORDER
DEPARTMENT_ROLE_ORDER = [role_code for role_code in FIXED_ROLE_ORDER if role_code in DEPARTMENT_ROLE_CODES]


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
    createdByUserId = serializers.IntegerField(source="created_by_user_id", allow_null=True, read_only=True)

    class Meta:
        model = Department
        fields = [
            "id",
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
    parentId = serializers.IntegerField(
        min_value=1,
        error_messages={
            "required": "创建部门必须指定父部门",
            "null": "创建部门必须指定父部门",
        },
    )

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unexpected = sorted(set(data.keys()) - set(self.fields.keys()))
            if unexpected:
                raise serializers.ValidationError({"body": ["请求包含不支持的字段"]})
        return super().to_internal_value(data)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        parent_id = attrs.get("parentId")
        parent = Department.objects.filter(pk=parent_id).first()
        if parent is None:
            raise serializers.ValidationError({"parentId": ["部门不存在"]})
        attrs["parent"] = parent
        return attrs

    def create(self, validated_data):
        return Department.objects.create(
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
    labels = dict(FixedRole.choices)
    return [{"code": role_code, "name": labels[role_code]} for role_code in DEPARTMENT_ROLE_ORDER]


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

    @extend_schema_field(DepartmentReadSerializer)
    def get_department(self, instance):
        return DepartmentReadSerializer(instance.department).data

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_roleCodes(self, instance):
        role_codes = set(instance.role_assignments.all().values_list("role_code", flat=True))
        return [role_code for role_code in FIXED_ROLE_ORDER if role_code in role_codes]
