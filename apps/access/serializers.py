from django.contrib.auth.models import Group, Permission
from django.db import transaction
from rest_framework import serializers
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field

from apps.access.models import (
    AuditLog,
    GroupPermissionScope,
    ScopeStatus,
    ScopeType,
    StaffProfile,
    StaffType,
    StaffTypeGroup,
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
