from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.access.api_base import StrictSerializer
from apps.access.models import DirectoryStatus
from apps.iam_v2.models import (
    DEPARTMENT_ROLE_CODES,
    ROLE_CODE_ORDER,
    Department,
    FixedRole,
    V2AccountQualification,
    V2AccountProfile,
    V2AccountRoleProfile,
    V2Menu,
    V2MenuPermissionBinding,
    V2Permission,
    V2ProfileType,
    V2Role,
    V2RoleMenuGrant,
    V2RolePermissionGrant,
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


def ordered_v2_role_codes(role_codes) -> list[str]:
    role_set = {str(role_code) for role_code in role_codes}
    dynamic_order = list(V2Role.objects.filter(code__in=role_set).order_by("sort", "id").values_list("code", flat=True))
    included = set(dynamic_order)
    fixed_tail = [role_code for role_code in FIXED_ROLE_ORDER if role_code in role_set and role_code not in included]
    custom_tail = sorted(role_set - included - set(fixed_tail))
    return dynamic_order + fixed_tail + custom_tail


def normalize_v2_role_codes(raw_role_codes) -> list[str]:
    if raw_role_codes is None:
        return []

    normalized = []
    seen = set()
    invalid = []
    active_role_codes = set(V2Role.objects.filter(status=DirectoryStatus.ACTIVE).values_list("code", flat=True))
    for item in raw_role_codes:
        role_code = str(item or "").strip()
        if role_code not in active_role_codes:
            invalid.append(role_code)
            continue
        if role_code in seen:
            continue
        seen.add(role_code)
        normalized.append(role_code)

    if invalid:
        raise serializers.ValidationError(f"不支持的 v2 角色: {', '.join(invalid)}")
    return ordered_v2_role_codes(normalized)


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
    name = serializers.CharField(max_length=128)
    phone = serializers.CharField(max_length=32)
    email = serializers.EmailField(required=False, allow_blank=True)
    departmentId = serializers.IntegerField(min_value=1)
    roleCodes = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    status = serializers.ChoiceField(choices=DirectoryStatus.choices, required=False, default=DirectoryStatus.ACTIVE)

    def validate_roleCodes(self, value):
        return normalize_v2_role_codes(value)


class AccountUpdateSerializer(StrictSerializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=128, required=False, write_only=True)
    name = serializers.CharField(max_length=128)
    phone = serializers.CharField(max_length=32)
    email = serializers.EmailField(required=False, allow_blank=True)
    departmentId = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(choices=DirectoryStatus.choices)


class AccountRolesReplaceSerializer(StrictSerializer):
    roleCodes = serializers.ListField(child=serializers.CharField(), allow_empty=True)

    def validate_roleCodes(self, value):
        return normalize_v2_role_codes(value)


class AccountReadSerializer(serializers.ModelSerializer):
    userId = serializers.IntegerField(source="user_id", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    department = serializers.SerializerMethodField()
    roleCodes = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = V2AccountProfile
        fields = [
            "id",
            "userId",
            "username",
            "name",
            "phone",
            "email",
            "status",
            "department",
            "roleCodes",
            "createdAt",
            "updatedAt",
        ]
        read_only_fields = fields

    @extend_schema_field(DepartmentReadSerializer)
    def get_department(self, instance):
        return DepartmentReadSerializer(instance.department).data

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_roleCodes(self, instance):
        role_codes = set(instance.role_assignments.all().values_list("role_code", flat=True))
        return ordered_v2_role_codes(role_codes)


class V2ProfileTypeReadSerializer(serializers.ModelSerializer):
    roleCode = serializers.CharField(source="role_code", read_only=True)
    isSystem = serializers.BooleanField(source="is_system", read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = V2ProfileType
        fields = ["code", "name", "roleCode", "status", "isSystem", "sort", "remark", "createdAt", "updatedAt"]
        read_only_fields = fields


class V2ProfileTypeWriteSerializer(StrictSerializer):
    code = serializers.CharField(max_length=64)
    name = serializers.CharField(max_length=128)
    status = serializers.ChoiceField(choices=DirectoryStatus.choices, required=False, default=DirectoryStatus.ACTIVE)
    sort = serializers.IntegerField(required=False, default=100)
    remark = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_code(self, value):
        code = str(value or "").strip()
        if not code:
            raise serializers.ValidationError("profileType 编码不能为空")
        if not V2Role.objects.filter(code=code).exists():
            raise serializers.ValidationError("profileType 必须绑定已存在角色")
        return code


class AccountRoleProfileReadSerializer(serializers.ModelSerializer):
    accountProfileId = serializers.IntegerField(source="account_profile_id", read_only=True)
    userId = serializers.IntegerField(source="account_profile.user_id", read_only=True)
    username = serializers.CharField(source="account_profile.user.username", read_only=True)
    profileType = serializers.CharField(source="profile_type", read_only=True)
    displayName = serializers.CharField(source="display_name", read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = V2AccountRoleProfile
        fields = [
            "id",
            "accountProfileId",
            "userId",
            "username",
            "profileType",
            "displayName",
            "level",
            "status",
            "remark",
            "createdAt",
            "updatedAt",
        ]
        read_only_fields = fields


class AccountRoleProfileWriteSerializer(StrictSerializer):
    profileType = serializers.CharField(max_length=64)
    displayName = serializers.CharField(max_length=128)
    level = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    status = serializers.ChoiceField(choices=DirectoryStatus.choices, required=False, default=DirectoryStatus.ACTIVE)
    remark = serializers.CharField(required=False, allow_blank=True, default="")


class AccountRoleProfileUpdateSerializer(StrictSerializer):
    displayName = serializers.CharField(max_length=128)
    level = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    status = serializers.ChoiceField(choices=DirectoryStatus.choices, required=False, default=DirectoryStatus.ACTIVE)
    remark = serializers.CharField(required=False, allow_blank=True, default="")


class AccountQualificationReadSerializer(serializers.ModelSerializer):
    accountProfileId = serializers.IntegerField(source="account_profile_id", read_only=True)
    profileType = serializers.CharField(source="profile_type", read_only=True)
    qualificationType = serializers.CharField(source="qualification_type", read_only=True)
    certificateNo = serializers.CharField(source="certificate_no", read_only=True)
    issuedAt = serializers.DateField(source="issued_at", read_only=True)
    expiresAt = serializers.DateField(source="expires_at", read_only=True)
    isEffective = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = V2AccountQualification
        fields = [
            "id",
            "accountProfileId",
            "profileType",
            "qualificationType",
            "certificateNo",
            "issuedAt",
            "expiresAt",
            "status",
            "remark",
            "isEffective",
            "createdAt",
            "updatedAt",
        ]
        read_only_fields = fields

    def get_isEffective(self, obj) -> bool:
        return obj.is_effective()


class AccountQualificationWriteSerializer(StrictSerializer):
    profileType = serializers.CharField(max_length=64)
    qualificationType = serializers.CharField(max_length=128)
    certificateNo = serializers.CharField(max_length=128)
    issuedAt = serializers.DateField()
    expiresAt = serializers.DateField()
    status = serializers.ChoiceField(choices=DirectoryStatus.choices)
    remark = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["issuedAt"] > attrs["expiresAt"]:
            raise serializers.ValidationError({"expiresAt": ["有效期结束日期不能早于签发日期"]})
        return attrs


class V2PermissionReadSerializer(serializers.ModelSerializer):
    isSystem = serializers.BooleanField(source="is_system", read_only=True)

    class Meta:
        model = V2Permission
        fields = ["id", "code", "name", "domain", "resource", "action", "status", "isSystem", "created_at", "updated_at"]
        read_only_fields = fields


class V2RoleReadSerializer(serializers.ModelSerializer):
    isSystem = serializers.BooleanField(source="is_system", read_only=True)
    isSuperAdmin = serializers.BooleanField(source="is_super_admin", read_only=True)
    assignableByDepartmentAdmin = serializers.BooleanField(source="assignable_by_department_admin", read_only=True)
    dataScope = serializers.CharField(source="data_scope", read_only=True)
    permissionCodes = serializers.SerializerMethodField()
    menuIds = serializers.SerializerMethodField()

    class Meta:
        model = V2Role
        fields = [
            "id",
            "code",
            "name",
            "status",
            "isSystem",
            "isSuperAdmin",
            "assignableByDepartmentAdmin",
            "dataScope",
            "sort",
            "remark",
            "permissionCodes",
            "menuIds",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_permissionCodes(self, instance):
        return list(
            instance.permission_grants.select_related("permission")
            .filter(permission__status=DirectoryStatus.ACTIVE)
            .order_by("permission__domain", "permission__resource", "permission__action", "id")
            .values_list("permission__code", flat=True)
            .distinct()
        )

    @extend_schema_field(serializers.ListField(child=serializers.IntegerField()))
    def get_menuIds(self, instance):
        return list(instance.menu_grants.order_by("menu__sort", "menu_id").values_list("menu_id", flat=True))


class V2RoleWriteSerializer(StrictSerializer):
    code = serializers.CharField(max_length=64)
    name = serializers.CharField(max_length=128)
    status = serializers.ChoiceField(choices=DirectoryStatus.choices, required=False, default=DirectoryStatus.ACTIVE)
    assignableByDepartmentAdmin = serializers.BooleanField(required=False, default=False)
    dataScope = serializers.ChoiceField(choices=V2Role.DataScope.choices, required=False, default=V2Role.DataScope.SELF)
    sort = serializers.IntegerField(required=False, default=100)
    remark = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_code(self, value):
        code = str(value or "").strip()
        if not code:
            raise serializers.ValidationError("角色编码不能为空")
        if code in FIXED_ROLE_ORDER:
            raise serializers.ValidationError("内置角色编码不能通过接口创建或改写")
        return code

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs.get("assignableByDepartmentAdmin") and attrs.get("dataScope") == V2Role.DataScope.ALL:
            raise serializers.ValidationError({"dataScope": ["部门管理员可分配角色不能使用 ALL 数据范围"]})
        return attrs


class V2RoleMenusReplaceSerializer(StrictSerializer):
    menuIds = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=True)


class V2RolePermissionsReplaceSerializer(StrictSerializer):
    permissionIds = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=True)


class V2RoleDataScopeSerializer(StrictSerializer):
    dataScope = serializers.ChoiceField(choices=V2Role.DataScope.choices)
    customDepartmentIds = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, allow_empty=True)


class V2MenuPermissionBindingReadSerializer(serializers.ModelSerializer):
    permissionId = serializers.IntegerField(source="permission_id", read_only=True)
    permissionCode = serializers.CharField(source="permission_code", read_only=True)

    class Meta:
        model = V2MenuPermissionBinding
        fields = ["id", "permissionId", "permissionCode"]
        read_only_fields = fields


class V2MenuReadSerializer(serializers.ModelSerializer):
    parentId = serializers.IntegerField(source="parent_id", allow_null=True, read_only=True)
    type = serializers.CharField(source="menu_type", read_only=True)
    isSystem = serializers.BooleanField(source="is_system", read_only=True)
    permissionCodes = serializers.SerializerMethodField()
    children = serializers.SerializerMethodField()

    class Meta:
        model = V2Menu
        fields = [
            "id",
            "parentId",
            "name",
            "code",
            "type",
            "path",
            "component",
            "icon",
            "sort",
            "status",
            "isSystem",
            "permissionCodes",
            "children",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_permissionCodes(self, instance):
        return instance.permission_codes

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_children(self, instance):
        children = getattr(instance, "_visible_children", None)
        if children is None:
            children = instance.children.filter(status=DirectoryStatus.ACTIVE).order_by("sort", "id")
        return V2MenuReadSerializer(children, many=True, context=self.context).data


class V2MenuWriteSerializer(StrictSerializer):
    parentId = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    name = serializers.CharField(max_length=128)
    code = serializers.CharField(max_length=128)
    type = serializers.ChoiceField(choices=V2Menu.MenuType.choices)
    path = serializers.CharField(required=False, allow_blank=True, default="")
    component = serializers.CharField(required=False, allow_blank=True, default="")
    icon = serializers.CharField(required=False, allow_blank=True, default="")
    sort = serializers.IntegerField(required=False, default=100)
    status = serializers.ChoiceField(choices=DirectoryStatus.choices, required=False, default=DirectoryStatus.ACTIVE)
    permissionIds = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, allow_empty=True)
