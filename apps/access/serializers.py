from uuid import uuid4

from django.contrib.auth.models import Group, Permission
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field

from apps.access.exceptions import (
    BusinessIdempotentDuplicate,
    BusinessPermissionDenied,
    BusinessResourceNotFound,
    BusinessStateConflict,
)
from apps.access.models import (
    AuditLog,
    EmploymentStatus,
    GroupPermissionScope,
    ScopeStatus,
    ScopeType,
    StaffProfile,
    SystemRole,
    SystemRoleGroup,
    SystemRoleStatus,
    Tenant,
    TenantMember,
    TenantMemberAttributeStatus,
    TenantMemberPosition,
    TenantMemberQualification,
    TenantMemberRole,
    TenantMemberRoleStatus,
    TenantMemberStatus,
    TenantStatus,
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
    class Meta:
        model = StaffProfile
        fields = [
            "id",
            "staff_no",
            "name",
            "phone",
            "email",
            "employment_status",
            "org_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


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

        # superuser 作为 root 账号，不绑定全局 staff 档案。
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


class BaseUserRegisterSerializer(serializers.Serializer):
    def _generate_staff_no(self) -> str:
        while True:
            staff_no = f"REG-{uuid4().hex[:12].upper()}"
            if not StaffProfile.objects.filter(staff_no=staff_no).exists():
                return staff_no

    def _validate_non_empty(self, attrs, fields: list[str]):
        for field in fields:
            value = attrs.get(field)
            if isinstance(value, str) and not value.strip():
                raise serializers.ValidationError({field: "该字段不能为空"})
        return attrs

    def _check_duplicate_username(self, username: str) -> str:
        if User.objects.filter(username=username).exists():
            raise BusinessIdempotentDuplicate(
                "username already exists",
                business_detail_code="USERNAME_ALREADY_EXISTS",
            )
        return username

    def _check_duplicate_phone(self, phone: str) -> str:
        if User.objects.filter(username=phone).exists() or StaffProfile.objects.filter(phone=phone).exists():
            raise BusinessIdempotentDuplicate(
                "phone already exists",
                business_detail_code="PHONE_ALREADY_EXISTS",
            )
        return phone

    def _create_registered_user(self, *, username: str, password: str, name: str, phone: str) -> User:
        user = User.objects.create_user(username=username, password=password, is_staff=False)
        StaffProfile.objects.create(
            user=user,
            staff_no=self._generate_staff_no(),
            name=name,
            phone=phone,
            employment_status=EmploymentStatus.ACTIVE,
        )
        return user


class UserSelfRegisterSerializer(BaseUserRegisterSerializer):
    """平台注册账号。"""

    username = serializers.CharField(max_length=150, trim_whitespace=True)
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    name = serializers.CharField(max_length=64, trim_whitespace=True)
    phone = serializers.CharField(max_length=32, trim_whitespace=True)

    def validate_username(self, value):
        return self._check_duplicate_username(value)

    def validate(self, attrs):
        return self._validate_non_empty(attrs, ["username", "password", "name", "phone"])

    @transaction.atomic
    def create(self, validated_data):
        password = validated_data["password"]
        return self._create_registered_user(
            username=validated_data["username"],
            password=password,
            name=validated_data["name"],
            phone=validated_data["phone"],
        )


class UserPhoneRegisterSerializer(BaseUserRegisterSerializer):
    """手机号注册平台账号。"""

    phone = serializers.CharField(max_length=32, trim_whitespace=True)
    sms_code = serializers.CharField(max_length=16, trim_whitespace=True)

    def validate_phone(self, value):
        return self._check_duplicate_phone(value)

    def validate(self, attrs):
        attrs = self._validate_non_empty(attrs, ["phone", "sms_code"])
        if attrs["sms_code"] != "123456":
            raise serializers.ValidationError({"sms_code": "mock 短信验证码固定为 123456"})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        phone = validated_data["phone"]
        return self._create_registered_user(
            username=phone,
            password=uuid4().hex,
            name=f"手机用户{phone[-4:]}",
            phone=phone,
        )


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


class TenantMemberPositionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TenantMemberPosition
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


class TenantMemberQualificationSerializer(serializers.ModelSerializer):
    payload = serializers.JSONField(required=False)

    class Meta:
        model = TenantMemberQualification
        fields = [
            "id",
            "code",
            "name",
            "description",
            "status",
            "valid_until",
            "payload",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TenantMemberAttributeMixin:
    def _sync_positions(self, member: TenantMember, positions_data: list[dict]) -> None:
        codes = [item["code"] for item in positions_data]
        member.positions.exclude(code__in=codes).delete()
        for item in positions_data:
            TenantMemberPosition.objects.update_or_create(
                tenant_member=member,
                code=item["code"],
                defaults={
                    "name": item["name"],
                    "description": item.get("description", ""),
                    "status": item.get("status", TenantMemberAttributeStatus.ACTIVE),
                },
            )

    def _sync_qualifications(self, member: TenantMember, qualifications_data: list[dict]) -> None:
        codes = [item["code"] for item in qualifications_data]
        member.qualifications.exclude(code__in=codes).delete()
        for item in qualifications_data:
            TenantMemberQualification.objects.update_or_create(
                tenant_member=member,
                code=item["code"],
                defaults={
                    "name": item["name"],
                    "description": item.get("description", ""),
                    "status": item.get("status", TenantMemberAttributeStatus.ACTIVE),
                    "valid_until": item.get("valid_until"),
                    "payload": item.get("payload", {}),
                },
            )

    def sync_member_attributes(
        self,
        member: TenantMember,
        *,
        positions_data=serializers.empty,
        qualifications_data=serializers.empty,
    ) -> None:
        if positions_data is not serializers.empty:
            self._sync_positions(member, positions_data)
        if qualifications_data is not serializers.empty:
            self._sync_qualifications(member, qualifications_data)


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


class CurrentUserTenantSerializer(serializers.ModelSerializer):
    tenant_id = serializers.IntegerField(source="tenant.id", read_only=True)
    tenant_code = serializers.CharField(source="tenant.code", read_only=True)
    tenant_name = serializers.CharField(source="tenant.name", read_only=True)
    roles = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = TenantMember
        fields = ["tenant_id", "tenant_code", "tenant_name", "roles"]

    @extend_schema_field({"type": "array", "items": {"type": "string"}})
    def get_roles(self, obj):
        return list(
            obj.role_bindings.filter(status=TenantMemberRoleStatus.ACTIVE)
            .order_by("id")
            .values_list("system_role__code", flat=True)
        )


class CurrentUserInvitationSerializer(serializers.ModelSerializer):
    member_id = serializers.IntegerField(source="id", read_only=True)
    tenant_id = serializers.IntegerField(source="tenant.id", read_only=True)
    tenant_code = serializers.CharField(source="tenant.code", read_only=True)
    tenant_name = serializers.CharField(source="tenant.name", read_only=True)
    roles = serializers.SerializerMethodField(read_only=True)
    positions = TenantMemberPositionSerializer(many=True, read_only=True)
    qualifications = TenantMemberQualificationSerializer(many=True, read_only=True)

    class Meta:
        model = TenantMember
        fields = [
            "member_id",
            "tenant_id",
            "tenant_code",
            "tenant_name",
            "display_name",
            "roles",
            "positions",
            "qualifications",
            "invitation_token",
        ]

    @extend_schema_field({"type": "array", "items": {"type": "string"}})
    def get_roles(self, obj):
        return list(
            obj.role_bindings.filter(status=TenantMemberRoleStatus.ACTIVE)
            .order_by("id")
            .values_list("system_role__code", flat=True)
        )


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


class TenantSetPlanSerializer(serializers.Serializer):
    """租户套餐配置。"""

    plan = serializers.CharField(max_length=64, allow_blank=False, trim_whitespace=True)

    @transaction.atomic
    def create(self, validated_data):
        tenant = self.context["tenant"]
        new_plan = validated_data["plan"]
        if tenant.plan == new_plan:
            raise BusinessIdempotentDuplicate(
                "tenant plan unchanged",
                business_detail_code="TENANT_PLAN_UNCHANGED",
            )

        tenant.plan = new_plan
        tenant.save(update_fields=["plan", "updated_at"])
        return tenant


class SystemRoleSerializer(serializers.ModelSerializer):
    """平台固定角色序列化器"""

    groups = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = SystemRole
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
            SystemRoleGroup.objects.filter(system_role=obj, status=ScopeStatus.ACTIVE)
            .select_related("group")
            .order_by("group_id")
        )
        return [{"id": link.group_id, "name": link.group.name} for link in links]


class SystemRoleGroupAssignSerializer(serializers.Serializer):
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
        system_role = self.context["system_role"]
        group_ids = [group.id for group in self._groups]

        if group_ids:
            SystemRoleGroup.objects.filter(system_role=system_role).exclude(group_id__in=group_ids).update(status=ScopeStatus.DISABLED)
        else:
            SystemRoleGroup.objects.filter(system_role=system_role).update(status=ScopeStatus.DISABLED)

        for group in self._groups:
            SystemRoleGroup.objects.update_or_create(
                system_role=system_role,
                group=group,
                defaults={"status": ScopeStatus.ACTIVE},
            )

        return system_role


class TenantMemberSerializer(TenantMemberAttributeMixin, serializers.ModelSerializer):
    """租户成员序列化器"""

    username = serializers.CharField(source="user.username", read_only=True)
    tenant_code = serializers.CharField(source="tenant.code", read_only=True)
    roles = serializers.SerializerMethodField(read_only=True)
    positions = TenantMemberPositionSerializer(many=True, required=False)
    qualifications = TenantMemberQualificationSerializer(many=True, required=False)

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
            "positions",
            "qualifications",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "tenant",
            "tenant_code",
            "user",
            "username",
            "status",
            "joined_at",
            "roles",
            "created_at",
            "updated_at",
        ]

    @extend_schema_field({"type": "array", "items": {"type": "string"}})
    def get_roles(self, obj):
        """获取成员绑定的角色编码列表"""
        return list(
            obj.role_bindings.filter(status=TenantMemberRoleStatus.ACTIVE)
            .values_list("system_role__code", flat=True)
        )

    @transaction.atomic
    def update(self, instance, validated_data):
        positions_data = validated_data.pop("positions", serializers.empty)
        qualifications_data = validated_data.pop("qualifications", serializers.empty)

        update_fields = []
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
            update_fields.append(attr)

        if update_fields:
            instance.save(update_fields=update_fields + ["updated_at"])
        else:
            instance.save()

        self.sync_member_attributes(
            instance,
            positions_data=positions_data,
            qualifications_data=qualifications_data,
        )
        return instance


class TenantMemberCreateSerializer(TenantMemberAttributeMixin, serializers.ModelSerializer):
    """租户成员创建序列化器"""

    user_id = serializers.IntegerField(write_only=True)
    role_codes = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        write_only=True,
        allow_empty=True,
    )
    positions = TenantMemberPositionSerializer(many=True, required=False)
    qualifications = TenantMemberQualificationSerializer(many=True, required=False)

    class Meta:
        model = TenantMember
        fields = [
            "user_id",
            "display_name",
            "staff_no",
            "phone",
            "email",
            "role_codes",
            "positions",
            "qualifications",
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
        positions_data = validated_data.pop("positions", [])
        qualifications_data = validated_data.pop("qualifications", [])
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

        self.sync_member_attributes(
            member,
            positions_data=positions_data,
            qualifications_data=qualifications_data,
        )
        return member


class TenantInitializeAdminSerializer(TenantMemberAttributeMixin, serializers.Serializer):
    """初始化租户管理员。"""

    user_id = serializers.IntegerField(required=True)
    display_name = serializers.CharField(max_length=128)
    staff_no = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    positions = TenantMemberPositionSerializer(many=True, required=False)
    qualifications = TenantMemberQualificationSerializer(many=True, required=False)

    @transaction.atomic
    def create(self, validated_data):
        tenant = self.context["tenant"]
        positions_data = validated_data.pop("positions", [])
        qualifications_data = validated_data.pop("qualifications", [])
        if tenant.status != TenantStatus.ENABLED:
            raise BusinessStateConflict("tenant status invalid", business_detail_code="TENANT_STATUS_INVALID")

        user = User.objects.filter(id=validated_data["user_id"]).first()
        if user is None:
            raise BusinessResourceNotFound("user not found", business_detail_code="USER_NOT_FOUND")

        tenant_admin_role = SystemRole.objects.filter(code="tenant_admin", status=SystemRoleStatus.ACTIVE).first()
        if tenant_admin_role is None:
            raise BusinessStateConflict(
                "tenant_admin role not configured",
                business_detail_code="TENANT_ADMIN_ROLE_NOT_CONFIGURED",
            )

        existing_admin = (
            TenantMemberRole.objects.select_related("tenant_member")
            .filter(
                tenant_member__tenant=tenant,
                tenant_member__status=TenantMemberStatus.ACTIVE,
                system_role=tenant_admin_role,
                status=TenantMemberRoleStatus.ACTIVE,
            )
            .first()
        )
        if existing_admin is not None:
            raise BusinessIdempotentDuplicate(
                "tenant admin already initialized",
                business_detail_code="TENANT_ADMIN_ALREADY_INITIALIZED",
            )

        member = TenantMember.objects.filter(tenant=tenant, user=user).first()
        joined_at = timezone.now()
        if member is None:
            member = TenantMember.objects.create(
                tenant=tenant,
                user=user,
                display_name=validated_data["display_name"],
                staff_no=validated_data.get("staff_no", ""),
                phone=validated_data.get("phone", ""),
                email=validated_data.get("email", ""),
                invitation_token=None,
                status=TenantMemberStatus.ACTIVE,
                joined_at=joined_at,
            )
        else:
            member.display_name = validated_data["display_name"]
            member.staff_no = validated_data.get("staff_no", "")
            member.phone = validated_data.get("phone", "")
            member.email = validated_data.get("email", "")
            member.invitation_token = None
            member.status = TenantMemberStatus.ACTIVE
            member.joined_at = member.joined_at or joined_at
            member.save(
                update_fields=[
                    "display_name",
                    "staff_no",
                    "phone",
                    "email",
                    "invitation_token",
                    "status",
                    "joined_at",
                    "updated_at",
                ]
            )

        TenantMemberRole.objects.update_or_create(
            tenant_member=member,
            system_role=tenant_admin_role,
            defaults={"status": TenantMemberRoleStatus.ACTIVE},
        )
        self.sync_member_attributes(
            member,
            positions_data=positions_data,
            qualifications_data=qualifications_data,
        )
        return member


class TenantMemberInviteSerializer(TenantMemberAttributeMixin, serializers.Serializer):
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
    positions = TenantMemberPositionSerializer(many=True, required=False)
    qualifications = TenantMemberQualificationSerializer(many=True, required=False)

    def validate_roles(self, value):
        valid_codes = set(SystemRole.objects.filter(status=SystemRoleStatus.ACTIVE).values_list("code", flat=True))
        invalid = set(value) - valid_codes
        if invalid:
            raise serializers.ValidationError(f"Invalid role codes: {invalid}")
        return value

    @transaction.atomic
    def create(self, validated_data):
        positions_data = validated_data.pop("positions", [])
        qualifications_data = validated_data.pop("qualifications", [])
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

        self.sync_member_attributes(
            member,
            positions_data=positions_data,
            qualifications_data=qualifications_data,
        )
        return member


class TenantMemberConfirmInvitationSerializer(serializers.Serializer):
    """租户成员确认邀请序列化器。"""

    invitation_token = serializers.CharField(max_length=64, required=True)

    @transaction.atomic
    def create(self, validated_data):
        request = self.context["request"]
        member = (
            TenantMember.objects.select_related("tenant", "user")
            .prefetch_related("role_bindings__system_role")
            .filter(invitation_token=validated_data["invitation_token"])
            .first()
        )
        if member is None:
            raise BusinessResourceNotFound("invitation not found", business_detail_code="INVITATION_NOT_FOUND")

        if member.user_id != request.user.id:
            raise BusinessPermissionDenied("invitation does not belong to current user", business_detail_code="INVITATION_NOT_ALLOWED")

        if member.status == TenantMemberStatus.ACTIVE:
            raise BusinessIdempotentDuplicate(
                "invitation already confirmed",
                business_detail_code="INVITATION_ALREADY_CONFIRMED",
            )

        if member.status != TenantMemberStatus.PENDING:
            raise BusinessStateConflict("invitation is not pending", business_detail_code="INVITATION_STATUS_INVALID")

        member.status = TenantMemberStatus.ACTIVE
        member.joined_at = timezone.now()
        member.save(update_fields=["status", "joined_at", "updated_at"])
        return member


class TenantMemberRejectInvitationSerializer(serializers.Serializer):
    """租户成员拒绝邀请序列化器。"""

    invitation_token = serializers.CharField(max_length=64, required=True)

    @transaction.atomic
    def create(self, validated_data):
        request = self.context["request"]
        member = (
            TenantMember.objects.select_related("tenant", "user")
            .prefetch_related("role_bindings__system_role")
            .filter(invitation_token=validated_data["invitation_token"])
            .first()
        )
        if member is None:
            raise BusinessResourceNotFound("invitation not found", business_detail_code="INVITATION_NOT_FOUND")

        if member.user_id != request.user.id:
            raise BusinessPermissionDenied("invitation does not belong to current user", business_detail_code="INVITATION_NOT_ALLOWED")

        if member.status != TenantMemberStatus.PENDING:
            raise BusinessStateConflict("invitation is not pending", business_detail_code="INVITATION_STATUS_INVALID")

        payload = {
            "tenant": member.tenant,
            "member_id": member.id,
            "tenant_id": member.tenant_id,
            "user_id": member.user_id,
            "status": member.status,
        }
        member.delete()
        return payload


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
