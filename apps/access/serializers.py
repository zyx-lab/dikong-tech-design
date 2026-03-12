from uuid import uuid4

from django.contrib.auth.models import Group, Permission
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field

from apps.access.exceptions import BusinessResourceNotFound, BusinessStateConflict
from apps.access.models import (
    AuditLog,
    GroupPermissionScope,
    ScopeStatus,
    ScopeType,
    StaffProfile,
    StaffType,
    StaffTypeGroup,
    SystemRole,
    SystemRoleStatus,
    Tenant,
    TenantMember,
    TenantMemberRole,
    TenantMemberRoleStatus,
    TenantMemberStatus,
    User,
)


class PermissionCodeSerializer(serializers.ModelSerializer):
    code = serializers.SerializerMethodField()

    class Meta:
        model = Permission
        fields = ["id", "name", "code"]

    @extend_schema_field(OpenApiTypes.STR)
    def get_code(self, obj):
        return f"{obj.content_type.app_label}.{obj.codename}"


class StaffProfileNestedSerializer(serializers.ModelSerializer):
    staff_type_name = serializers.CharField(source="staff_type.name", read_only=True)

    class Meta:
        model = StaffProfile
        fields = [
            "id",
            "staff_no",
            "name",
            "phone",
            "email",
            "employment_status",
            "staff_type",
            "staff_type_name",
            "org_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "staff_type_name", "created_at", "updated_at"]


class UserManageSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, trim_whitespace=False)
    staff = StaffProfileNestedSerializer(source="staff_profile", required=False, allow_null=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "password",
            "is_active",
            "is_staff",
            "is_superuser",
            "status",
            "staff",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "is_superuser", "created_at", "updated_at"]

    def validate(self, attrs):
        if "is_superuser" in self.initial_data:
            raise serializers.ValidationError({"is_superuser": "禁止通过 API 修改超级管理员标记，请使用命令行创建"})

        has_staff_field = "staff_profile" in attrs
        staff_data = attrs.get("staff_profile")
        target_is_superuser = attrs.get("is_superuser", self.instance.is_superuser if self.instance else False)
        existing_staff = getattr(self.instance, "staff_profile", None) if self.instance else None

        # superuser 作为 root 账号，不走 staff_type 授权链，禁止绑定 staff。
        if target_is_superuser:
            if has_staff_field:
                raise serializers.ValidationError({"staff": "superuser 账号不允许提交 staff 字段"})
            if existing_staff is not None:
                raise serializers.ValidationError({"staff": "superuser 账号不允许绑定 staff 信息"})
            return attrs

        # 非 superuser 必须最终拥有 staff。
        will_have_staff = False
        if has_staff_field:
            will_have_staff = staff_data is not None
        else:
            will_have_staff = existing_staff is not None

        if not will_have_staff:
            raise serializers.ValidationError({"staff": "非 superuser 账号必须绑定 staff 信息"})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        staff_data = validated_data.pop("staff_profile", None)
        password = validated_data.pop("password", None)
        if not password:
            raise serializers.ValidationError({"password": "创建账号必须提供密码"})

        user = User.objects.create_user(password=password, **validated_data)
        # 一账号一 staff：通过 OneToOne 约束保证不会出现多 staff 绑定。
        if staff_data:
            StaffProfile.objects.create(user=user, **staff_data)
        return user

    @transaction.atomic
    def update(self, instance, validated_data):
        staff_in_payload = "staff_profile" in validated_data
        staff_data = validated_data.pop("staff_profile", serializers.empty)
        password = validated_data.pop("password", None)

        update_fields = []
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
            update_fields.append(attr)

        if password:
            instance.set_password(password)
            update_fields.append("password")

        if update_fields:
            instance.save(update_fields=update_fields)
        else:
            instance.save()

        if staff_in_payload and staff_data is not serializers.empty:
            staff = getattr(instance, "staff_profile", None)
            if staff is None:
                StaffProfile.objects.create(user=instance, **staff_data)
            else:
                staff_update_fields = []
                for attr, value in staff_data.items():
                    setattr(staff, attr, value)
                    staff_update_fields.append(attr)
                if staff_update_fields:
                    staff.save(update_fields=staff_update_fields)
                else:
                    staff.save()

        return instance


class GroupSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Group
        fields = ["id", "name", "permissions"]

    @extend_schema_field({"type": "array", "items": {"type": "string"}})
    def get_permissions(self, obj):
        perms = obj.permissions.select_related("content_type").all()
        return [f"{perm.content_type.app_label}.{perm.codename}" for perm in perms]


class GroupPermissionAssignSerializer(serializers.Serializer):
    permission_codes = serializers.ListField(child=serializers.CharField(), allow_empty=True)

    def validate_permission_codes(self, value):
        unique_values = list(dict.fromkeys(value))
        missing = []
        permissions = []

        for code in unique_values:
            if "." not in code:
                missing.append(code)
                continue
            app_label, codename = code.split(".", 1)
            perm = Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()
            if not perm:
                missing.append(code)
                continue
            permissions.append(perm)

        if missing:
            raise serializers.ValidationError(f"permissions not found: {missing}")

        self._resolved_permissions = permissions
        return unique_values

    def save(self, **kwargs):
        group = self.context["group"]
        group.permissions.set(self._resolved_permissions)
        # 权限移除后，相关 scope 一并删除，避免留下脏配置。
        GroupPermissionScope.objects.filter(group=group).exclude(permission__in=self._resolved_permissions).delete()
        return group


class GroupScopeItemSerializer(serializers.Serializer):
    permission_code = serializers.CharField()
    scope_type = serializers.ChoiceField(choices=ScopeType.choices)
    status = serializers.IntegerField(required=False, default=ScopeStatus.ACTIVE)

    def validate_permission_code(self, value):
        if "." not in value:
            raise serializers.ValidationError("permission_code must be app_label.codename")
        return value


class GroupScopeAssignSerializer(serializers.Serializer):
    items = GroupScopeItemSerializer(many=True)

    @transaction.atomic
    def save(self, **kwargs):
        group = self.context["group"]
        results = []

        for item in self.validated_data["items"]:
            app_label, codename = item["permission_code"].split(".", 1)
            permission = Permission.objects.filter(
                content_type__app_label=app_label,
                codename=codename,
            ).first()
            if not permission:
                raise serializers.ValidationError(f"permission not found: {item['permission_code']}")

            if not group.permissions.filter(id=permission.id).exists():
                raise serializers.ValidationError(
                    f"permission {item['permission_code']} is not assigned to group {group.id}"
                )

            scope_obj, _ = GroupPermissionScope.objects.update_or_create(
                group=group,
                permission=permission,
                defaults={
                    "scope_type": item["scope_type"],
                    "status": item["status"],
                },
            )
            results.append(scope_obj)

        return results


class StaffTypeSerializer(serializers.ModelSerializer):
    groups = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = StaffType
        fields = [
            "id",
            "code",
            "name",
            "description",
            "status",
            "groups",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    @extend_schema_field({"type": "array", "items": {"type": "object"}})
    def get_groups(self, obj):
        links = (
            StaffTypeGroup.objects.filter(staff_type=obj, status=ScopeStatus.ACTIVE)
            .select_related("group")
            .order_by("group_id")
        )
        return [{"id": link.group_id, "name": link.group.name} for link in links]

    @transaction.atomic
    def create(self, validated_data):
        return StaffType.objects.create(**validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if validated_data:
            instance.save(update_fields=list(validated_data.keys()))
        else:
            instance.save()

        return instance


class StaffTypeGroupAssignSerializer(serializers.Serializer):
    group_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=True)

    def validate_group_ids(self, value):
        unique_values = list(dict.fromkeys(value))
        groups = list(Group.objects.filter(id__in=unique_values))
        found = {group.id for group in groups}
        missing = [gid for gid in unique_values if gid not in found]
        if missing:
            raise serializers.ValidationError(f"groups not found: {missing}")
        self._groups = groups
        return unique_values

    @transaction.atomic
    def save(self, **kwargs):
        staff_type = self.context["staff_type"]
        group_ids = [group.id for group in self._groups]

        if group_ids:
            StaffTypeGroup.objects.filter(staff_type=staff_type).exclude(group_id__in=group_ids).update(status=ScopeStatus.DISABLED)
        else:
            StaffTypeGroup.objects.filter(staff_type=staff_type).update(status=ScopeStatus.DISABLED)

        for group in self._groups:
            StaffTypeGroup.objects.update_or_create(
                staff_type=staff_type,
                group=group,
                defaults={"status": ScopeStatus.ACTIVE},
            )

        return staff_type


class AuditLogSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source="actor_user.username", read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "actor_user",
            "actor_username",
            "action",
            "target_type",
            "target_id",
            "before_data",
            "after_data",
            "ip",
            "request_id",
            "created_at",
        ]


class MePermissionSerializer(serializers.Serializer):
    permission = serializers.CharField()
    scope = serializers.CharField(allow_null=True)
    enabled = serializers.BooleanField()


class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = [
            "id",
            "code",
            "name",
            "status",
            "plan",
            "remark",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class SystemRoleSerializer(serializers.ModelSerializer):
    """平台固定角色序列化器"""

    class Meta:
        model = SystemRole
        fields = [
            "id",
            "code",
            "name",
            "description",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TenantMemberSerializer(serializers.ModelSerializer):
    """租户成员序列化器"""

    username = serializers.CharField(source="user.username", read_only=True)
    tenant_code = serializers.CharField(source="tenant.code", read_only=True)
    roles = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = TenantMember
        fields = [
            "id",
            "tenant",
            "tenant_code",
            "user",
            "username",
            "display_name",
            "staff_no",
            "phone",
            "email",
            "status",
            "joined_at",
            "roles",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "tenant_code", "username", "joined_at", "created_at", "updated_at"]

    @extend_schema_field({"type": "array", "items": {"type": "string"}})
    def get_roles(self, obj):
        """获取成员绑定的角色编码列表"""
        return list(
            obj.role_bindings.filter(status=TenantMemberRoleStatus.ACTIVE)
            .values_list("system_role__code", flat=True)
        )


class TenantMemberCreateSerializer(serializers.ModelSerializer):
    """租户成员创建序列化器"""

    user_id = serializers.IntegerField(write_only=True)
    role_codes = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        write_only=True,
        allow_empty=True,
    )

    class Meta:
        model = TenantMember
        fields = [
            "user_id",
            "display_name",
            "staff_no",
            "phone",
            "email",
            "role_codes",
        ]

    def validate_user_id(self, value):
        user = User.objects.filter(id=value).first()
        if not user:
            raise serializers.ValidationError("User not found")
        # 检查用户是否已在该租户中
        tenant = self.context.get("tenant")
        if tenant and TenantMember.objects.filter(tenant=tenant, user=user).exists():
            raise serializers.ValidationError("User is already a member of this tenant")
        return value

    def validate_role_codes(self, value):
        valid_codes = set(SystemRole.objects.filter(status=SystemRoleStatus.ACTIVE).values_list("code", flat=True))
        invalid = set(value) - valid_codes
        if invalid:
            raise serializers.ValidationError(f"Invalid role codes: {invalid}")
        return value

    @transaction.atomic
    def create(self, validated_data):
        user_id = validated_data.pop("user_id")
        role_codes = validated_data.pop("role_codes", [])
        user = User.objects.get(id=user_id)
        tenant = self.context["tenant"]

        member = TenantMember.objects.create(
            tenant=tenant,
            user=user,
            display_name=validated_data["display_name"],
            staff_no=validated_data.get("staff_no", ""),
            phone=validated_data.get("phone", ""),
            email=validated_data.get("email", ""),
            status=TenantMemberStatus.ACTIVE,
            joined_at=timezone.now(),
        )

        # 绑定角色
        for code in role_codes:
            role = SystemRole.objects.get(code=code)
            TenantMemberRole.objects.create(
                tenant_member=member,
                system_role=role,
                status=TenantMemberRoleStatus.ACTIVE,
            )

        return member


class TenantMemberInviteSerializer(serializers.Serializer):
    """租户成员邀请序列化器。"""

    tenant_id = serializers.IntegerField(required=True)
    user_id = serializers.IntegerField(required=True)
    display_name = serializers.CharField(max_length=128)
    staff_no = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    roles = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )

    def validate_roles(self, value):
        valid_codes = set(SystemRole.objects.filter(status=SystemRoleStatus.ACTIVE).values_list("code", flat=True))
        invalid = set(value) - valid_codes
        if invalid:
            raise serializers.ValidationError(f"Invalid role codes: {invalid}")
        return value

    @transaction.atomic
    def create(self, validated_data):
        tenant = Tenant.objects.filter(id=validated_data["tenant_id"]).first()
        if tenant is None:
            raise BusinessResourceNotFound("tenant not found", business_detail_code="TENANT_NOT_FOUND")

        user = User.objects.filter(id=validated_data["user_id"]).first()
        if user is None:
            raise BusinessResourceNotFound("user not found", business_detail_code="USER_NOT_FOUND")

        if TenantMember.objects.filter(tenant=tenant, user=user).exists():
            raise BusinessStateConflict("tenant member already exists", business_detail_code="TENANT_MEMBER_EXISTS")

        member = TenantMember.objects.create(
            tenant=tenant,
            user=user,
            display_name=validated_data["display_name"],
            staff_no=validated_data.get("staff_no", ""),
            phone=validated_data.get("phone", ""),
            email=validated_data.get("email", ""),
            invitation_token=uuid4().hex,
            status=TenantMemberStatus.PENDING,
            joined_at=None,
        )

        for code in validated_data.get("roles", []):
            role = SystemRole.objects.get(code=code)
            TenantMemberRole.objects.create(
                tenant_member=member,
                system_role=role,
                status=TenantMemberRoleStatus.ACTIVE,
            )

        return member


class TenantMemberRoleSerializer(serializers.ModelSerializer):
    """成员角色绑定序列化器"""

    role_code = serializers.CharField(source="system_role.code", read_only=True)
    role_name = serializers.CharField(source="system_role.name", read_only=True)

    class Meta:
        model = TenantMemberRole
        fields = [
            "id",
            "tenant_member",
            "system_role",
            "role_code",
            "role_name",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "role_code", "role_name", "created_at", "updated_at"]


class TenantMemberRoleAssignSerializer(serializers.Serializer):
    """成员角色分配序列化器"""

    role_codes = serializers.ListField(
        child=serializers.CharField(),
        required=True,
    )

    def validate_role_codes(self, value):
        valid_codes = set(SystemRole.objects.filter(status=SystemRoleStatus.ACTIVE).values_list("code", flat=True))
        invalid = set(value) - valid_codes
        if invalid:
            raise serializers.ValidationError(f"Invalid role codes: {invalid}")
        return value

    @transaction.atomic
    def save(self, **kwargs):
        member = self.context["tenant_member"]
        role_codes = self.validated_data["role_codes"]

        # 先禁用所有现有角色
        member.role_bindings.update(status=TenantMemberRoleStatus.DISABLED)

        # 启用或创建新角色
        for code in role_codes:
            role = SystemRole.objects.get(code=code)
            binding, _ = TenantMemberRole.objects.update_or_create(
                tenant_member=member,
                system_role=role,
                defaults={"status": TenantMemberRoleStatus.ACTIVE},
            )

        return member
