from uuid import uuid4

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from apps.access.exceptions import (
    BusinessIdempotentDuplicate,
    BusinessPermissionDenied,
    BusinessResourceNotFound,
    BusinessStateConflict,
)
from apps.access.models import (
    AuditLog,
    DirectoryStatus,
    EmploymentStatus,
    Permission,
    QualificationRecordStatus,
    QualificationType,
    Role,
    RolePermissionGrant,
    ScopeType,
    StaffProfile,
    Tenant,
    TenantMember,
    TenantMemberQualification,
    TenantMemberRole,
    TenantMemberRoleStatus,
    TenantMemberStatus,
    TenantStatus,
    User,
)
from apps.access.services import default_invitation_expiry, expire_stale_tenant_member_invitation


PLATFORM_ONLY_ROLE_CODES = {"platform_admin"}


def _get_actor_tenant_from_request(request):
    if (
        request is None
        or not getattr(request, "user", None)
        or request.user.is_superuser
        or request.user.is_platform_admin
    ):
        return None
    return getattr(request, "tenant_context", None)


def _ensure_role_codes_assignable(role_codes: list[str], request) -> list[str]:
    normalized = list(dict.fromkeys(role_codes))
    valid_codes = set(Role.objects.filter(status=DirectoryStatus.ACTIVE).values_list("code", flat=True))
    invalid = set(normalized) - valid_codes
    if invalid:
        raise serializers.ValidationError(f"非法角色编码: {sorted(invalid)}")

    disallowed = set(normalized) & PLATFORM_ONLY_ROLE_CODES
    if disallowed:
        raise serializers.ValidationError(f"禁止分配平台角色: {sorted(disallowed)}")

    return normalized


def _validate_member_no_unique_in_tenant(*, tenant: Tenant, member_no: str | None, exclude_member_id: int | None = None) -> None:
    normalized_member_no = (member_no or "").strip()
    if not normalized_member_no:
        return

    queryset = TenantMember.objects.filter(tenant=tenant, member_no=normalized_member_no)
    if exclude_member_id is not None:
        queryset = queryset.exclude(id=exclude_member_id)
    if queryset.exists():
        raise serializers.ValidationError({"member_no": "当前租户下已存在相同工号"})


def _ensure_user_can_be_tenant_member(user: User) -> None:
    if user.is_superuser:
        raise serializers.ValidationError({"user_id": "superuser 账号不允许成为租户成员"})
    if user.is_platform_admin:
        raise serializers.ValidationError({"user_id": "platform_admin 账号不允许成为租户成员"})
    if not StaffProfile.objects.filter(user=user).exists():
        raise serializers.ValidationError({"user_id": "账号必须先创建全局 staff_profile 后才能成为租户成员"})


class PermissionCodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["id", "code", "name", "module", "resource_code", "status"]


class StaffProfileNestedSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffProfile
        fields = [
            "id",
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
            "is_platform_admin",
            "status",
            "staff",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "is_superuser", "created_at", "updated_at"]

    def validate(self, attrs):
        request_user = getattr(self.context.get("request"), "user", None)
        if "is_superuser" in self.initial_data:
            raise serializers.ValidationError({"is_superuser": "禁止通过 API 修改超级管理员标记，请使用命令行创建"})
        if "is_platform_admin" in self.initial_data and not (request_user and request_user.is_superuser):
            raise serializers.ValidationError({"is_platform_admin": "禁止通过 API 修改平台管理员标记，仅 superuser 可操作"})

        has_staff_field = "staff_profile" in attrs
        staff_data = attrs.get("staff_profile")
        target_is_superuser = attrs.get("is_superuser", self.instance.is_superuser if self.instance else False)
        target_is_platform_admin = attrs.get(
            "is_platform_admin",
            self.instance.is_platform_admin if self.instance else False,
        )
        existing_staff = getattr(self.instance, "staff_profile", None) if self.instance else None

        if target_is_superuser and target_is_platform_admin:
            raise serializers.ValidationError({"is_platform_admin": "superuser 与 platform_admin 不能同时为 true"})

        if target_is_superuser:
            if has_staff_field:
                raise serializers.ValidationError({"staff": "superuser 账号不允许提交 staff 字段"})
            if existing_staff is not None:
                raise serializers.ValidationError({"staff": "superuser 账号不允许绑定 staff 信息"})
            return attrs

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
            name=name,
            phone=phone,
            employment_status=EmploymentStatus.ACTIVE,
        )
        return user


class UserSelfRegisterSerializer(BaseUserRegisterSerializer):
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
        return self._create_registered_user(
            username=validated_data["username"],
            password=validated_data["password"],
            name=validated_data["name"],
            phone=validated_data["phone"],
        )


class UserPhoneRegisterSerializer(BaseUserRegisterSerializer):
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


class TenantMemberQualificationSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    qualification_type_code = serializers.CharField(max_length=64)
    qualification_type_name = serializers.CharField(read_only=True)
    certificate_no = serializers.CharField(max_length=128, allow_blank=True, required=False)
    level = serializers.CharField(max_length=64, allow_blank=True, required=False)
    status = serializers.ChoiceField(choices=QualificationRecordStatus.choices, required=False, default=QualificationRecordStatus.ACTIVE)
    issued_at = serializers.DateField(required=False, allow_null=True)
    valid_from = serializers.DateField(required=False, allow_null=True)
    valid_until = serializers.DateField(required=False, allow_null=True)
    issuer = serializers.CharField(max_length=128, allow_blank=True, required=False)
    payload_json = serializers.JSONField(required=False)

    def to_representation(self, instance):
        return {
            "id": instance.id,
            "qualification_type_code": instance.qualification_type.code,
            "qualification_type_name": instance.qualification_type.name,
            "certificate_no": instance.certificate_no,
            "level": instance.level,
            "status": instance.status,
            "issued_at": instance.issued_at,
            "valid_from": instance.valid_from,
            "valid_until": instance.valid_until,
            "issuer": instance.issuer,
            "payload_json": instance.payload_json,
            "created_at": instance.created_at,
            "updated_at": instance.updated_at,
        }

    def validate(self, attrs):
        code = attrs["qualification_type_code"]
        qualification_type = QualificationType.objects.filter(code=code).first()
        if qualification_type is None:
            raise serializers.ValidationError({"qualification_type_code": "资质类型不存在"})
        if qualification_type.status != DirectoryStatus.ACTIVE:
            raise serializers.ValidationError({"qualification_type_code": "资质类型已停用"})

        valid_from = attrs.get("valid_from")
        valid_until = attrs.get("valid_until")
        if qualification_type.requires_validity:
            if valid_from is None:
                raise serializers.ValidationError({"valid_from": "该资质类型要求提供 valid_from"})
            if valid_until is None:
                raise serializers.ValidationError({"valid_until": "该资质类型要求提供 valid_until"})
        if valid_from and valid_until and valid_from > valid_until:
            raise serializers.ValidationError({"valid_until": "valid_until 不能早于 valid_from"})

        payload_json = attrs.get("payload_json", {}) or {}
        schema = qualification_type.payload_schema_json or {}
        for required_key in schema.get("required", []):
            if required_key not in payload_json or payload_json[required_key] in (None, ""):
                raise serializers.ValidationError({"payload_json": f"缺少必填字段: {required_key}"})

        attrs["_qualification_type"] = qualification_type
        attrs["payload_json"] = payload_json
        return attrs


class TenantMemberAttributeMixin:
    def _replace_member_qualifications(self, member: TenantMember, qualifications_data: list[dict]) -> None:
        member.qualifications.all().delete()
        for item in qualifications_data:
            qualification_type = item["_qualification_type"]
            TenantMemberQualification.objects.create(
                tenant_member=member,
                qualification_type=qualification_type,
                certificate_no=item.get("certificate_no", ""),
                level=item.get("level", ""),
                status=item.get("status", QualificationRecordStatus.ACTIVE),
                issued_at=item.get("issued_at"),
                valid_from=item.get("valid_from"),
                valid_until=item.get("valid_until"),
                issuer=item.get("issuer", ""),
                payload_json=item.get("payload_json", {}),
            )

    def sync_member_attributes(self, member: TenantMember, *, qualifications_data=serializers.empty) -> None:
        if qualifications_data is not serializers.empty:
            self._replace_member_qualifications(member, qualifications_data)


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


class RolePermissionGrantSummarySerializer(serializers.Serializer):
    permission = serializers.CharField()
    scope_type = serializers.CharField()


class CurrentUserTenantSerializer(serializers.ModelSerializer):
    tenant_id = serializers.IntegerField(source="tenant.id", read_only=True)
    tenant_code = serializers.CharField(source="tenant.code", read_only=True)
    tenant_name = serializers.CharField(source="tenant.name", read_only=True)
    roles = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = TenantMember
        fields = ["tenant_id", "tenant_code", "tenant_name", "roles"]

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_roles(self, obj):
        return list(
            obj.role_bindings.filter(status=TenantMemberRoleStatus.GRANTED)
            .order_by("id")
            .values_list("system_role__code", flat=True)
        )


class CurrentUserInvitationSerializer(serializers.ModelSerializer):
    member_id = serializers.IntegerField(source="id", read_only=True)
    tenant_id = serializers.IntegerField(source="tenant.id", read_only=True)
    tenant_code = serializers.CharField(source="tenant.code", read_only=True)
    tenant_name = serializers.CharField(source="tenant.name", read_only=True)
    roles = serializers.SerializerMethodField(read_only=True)
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
            "qualifications",
            "invitation_token",
            "invited_at",
            "expires_at",
        ]

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_roles(self, obj):
        return list(
            obj.role_bindings.filter(status=TenantMemberRoleStatus.GRANTED)
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


class RoleSerializer(serializers.ModelSerializer):
    permission_grants = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Role
        fields = [
            "id",
            "code",
            "name",
            "description",
            "status",
            "permission_grants",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    @extend_schema_field(RolePermissionGrantSummarySerializer(many=True))
    def get_permission_grants(self, obj):
        grants = obj.permission_grants.select_related("permission").order_by("permission__code")
        return [
            {
                "permission": item.permission.code,
                "scope_type": item.scope_type,
            }
            for item in grants
        ]


class TenantMemberSerializer(TenantMemberAttributeMixin, serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    tenant_code = serializers.CharField(source="tenant.code", read_only=True)
    roles = serializers.SerializerMethodField(read_only=True)
    qualifications = TenantMemberQualificationSerializer(many=True, required=False)

    class Meta:
        model = TenantMember
        fields = [
            "id",
            "tenant",
            "tenant_code",
            "user",
            "username",
            "member_no",
            "display_name",
            "status",
            "invitation_token",
            "invited_at",
            "expires_at",
            "responded_at",
            "joined_at",
            "roles",
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
            "invitation_token",
            "invited_at",
            "expires_at",
            "responded_at",
            "joined_at",
            "roles",
            "created_at",
            "updated_at",
        ]

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_roles(self, obj):
        return list(
            obj.role_bindings.filter(status=TenantMemberRoleStatus.GRANTED)
            .order_by("id")
            .values_list("system_role__code", flat=True)
        )

    def validate(self, attrs):
        member_no = attrs.get("member_no", self.instance.member_no if self.instance else "")
        if member_no and self.instance and self.instance.status != TenantMemberStatus.ACTIVE:
            raise serializers.ValidationError({"member_no": "只有 ACTIVE 成员允许设置 member_no"})
        _validate_member_no_unique_in_tenant(
            tenant=self.instance.tenant,
            member_no=member_no,
            exclude_member_id=self.instance.id if self.instance else None,
        )
        return attrs

    @transaction.atomic
    def update(self, instance, validated_data):
        qualifications_data = validated_data.pop("qualifications", serializers.empty)

        if "member_no" in validated_data:
            new_member_no = validated_data["member_no"]
            if new_member_no and instance.status != TenantMemberStatus.ACTIVE:
                raise serializers.ValidationError({"member_no": "只有 ACTIVE 成员允许设置 member_no"})

        update_fields = []
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
            update_fields.append(attr)

        if update_fields:
            instance.save(update_fields=update_fields + ["updated_at"])
        else:
            instance.save()

        self.sync_member_attributes(instance, qualifications_data=qualifications_data)
        return instance


class TenantMemberCreateSerializer(TenantMemberAttributeMixin, serializers.Serializer):
    tenant_id = serializers.IntegerField(write_only=True)
    user_id = serializers.IntegerField(write_only=True)
    display_name = serializers.CharField(max_length=128, required=False, allow_blank=True)
    member_no = serializers.CharField(max_length=64, required=False, allow_blank=True)
    role_codes = serializers.ListField(child=serializers.CharField(), required=False, allow_empty=True)
    qualifications = TenantMemberQualificationSerializer(many=True, required=False)

    def validate_tenant_id(self, value):
        tenant = Tenant.objects.filter(id=value).first()
        if tenant is None:
            raise serializers.ValidationError("租户不存在")
        if tenant.status != TenantStatus.ACTIVE:
            raise serializers.ValidationError("租户未启用")
        request_tenant = _get_actor_tenant_from_request(self.context.get("request"))
        if request_tenant is not None and tenant.id != request_tenant.id:
            raise serializers.ValidationError("tenant_id 必须等于当前租户")
        self._tenant = tenant
        return value

    def validate_user_id(self, value):
        user = User.objects.filter(id=value).first()
        if not user:
            raise serializers.ValidationError("用户不存在")
        _ensure_user_can_be_tenant_member(user)
        self._user = user
        return value

    def validate_role_codes(self, value):
        try:
            return _ensure_role_codes_assignable(value, self.context.get("request"))
        except serializers.ValidationError as exc:
            raise serializers.ValidationError(exc.detail[0] if isinstance(exc.detail, list) else exc.detail)

    def validate(self, attrs):
        tenant = getattr(self, "_tenant", None)
        user = getattr(self, "_user", None)
        if tenant and user and TenantMember.objects.filter(tenant=tenant, user=user).exists():
            raise serializers.ValidationError({"user_id": "该用户已是当前租户成员"})
        _validate_member_no_unique_in_tenant(tenant=tenant, member_no=attrs.get("member_no"))
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        tenant = self._tenant
        user = self._user
        role_codes = validated_data.get("role_codes", [])
        qualifications_data = validated_data.get("qualifications", [])
        member = TenantMember.objects.create(
            tenant=tenant,
            user=user,
            display_name=validated_data.get("display_name", ""),
            member_no=validated_data.get("member_no") or None,
            status=TenantMemberStatus.ACTIVE,
            responded_at=timezone.now(),
            joined_at=timezone.now(),
        )
        for code in role_codes:
            role = Role.objects.get(code=code)
            TenantMemberRole.objects.create(
                tenant_member=member,
                system_role=role,
                status=TenantMemberRoleStatus.GRANTED,
                assigned_by_user=getattr(self.context.get("request"), "user", None),
                assigned_at=timezone.now(),
            )
        self.sync_member_attributes(member, qualifications_data=qualifications_data)
        return member


class TenantInitializeAdminSerializer(TenantMemberAttributeMixin, serializers.Serializer):
    user_id = serializers.IntegerField(required=True)
    display_name = serializers.CharField(max_length=128, required=False, allow_blank=True)
    member_no = serializers.CharField(required=False, allow_blank=True)
    qualifications = TenantMemberQualificationSerializer(many=True, required=False)

    @transaction.atomic
    def create(self, validated_data):
        tenant = self.context["tenant"]
        qualifications_data = validated_data.pop("qualifications", [])
        if tenant.status != TenantStatus.ACTIVE:
            raise BusinessStateConflict("tenant status invalid", business_detail_code="TENANT_STATUS_INVALID")

        user = User.objects.filter(id=validated_data["user_id"]).first()
        if user is None:
            raise BusinessResourceNotFound("user not found", business_detail_code="USER_NOT_FOUND")
        _ensure_user_can_be_tenant_member(user)

        tenant_admin_role = Role.objects.filter(code="tenant_admin", status=DirectoryStatus.ACTIVE).first()
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
                status=TenantMemberRoleStatus.GRANTED,
            )
            .first()
        )
        if existing_admin is not None:
            raise BusinessIdempotentDuplicate(
                "tenant admin already initialized",
                business_detail_code="TENANT_ADMIN_ALREADY_INITIALIZED",
            )

        member = TenantMember.objects.filter(tenant=tenant, user=user).first()
        requested_member_no = validated_data.get("member_no") or None
        _validate_member_no_unique_in_tenant(
            tenant=tenant,
            member_no=requested_member_no,
            exclude_member_id=member.id if member is not None else None,
        )
        if member is None:
            member = TenantMember.objects.create(
                tenant=tenant,
                user=user,
                display_name=validated_data.get("display_name", ""),
                member_no=requested_member_no,
                status=TenantMemberStatus.ACTIVE,
                responded_at=timezone.now(),
                joined_at=timezone.now(),
            )
        else:
            member.display_name = validated_data.get("display_name", member.display_name)
            member.member_no = requested_member_no or member.member_no
            member.status = TenantMemberStatus.ACTIVE
            member.invitation_token = None
            member.invited_by_user = None
            member.invited_at = None
            member.expires_at = None
            member.responded_at = member.responded_at or timezone.now()
            member.joined_at = member.joined_at or timezone.now()
            member.save()

        TenantMemberRole.objects.update_or_create(
            tenant_member=member,
            system_role=tenant_admin_role,
            defaults={
                "status": TenantMemberRoleStatus.GRANTED,
                "assigned_by_user": getattr(self.context.get("request"), "user", None),
                "assigned_at": timezone.now(),
            },
        )
        self.sync_member_attributes(member, qualifications_data=qualifications_data)
        return member


class TenantMemberInviteSerializer(TenantMemberAttributeMixin, serializers.Serializer):
    tenant_id = serializers.IntegerField(required=True)
    user_id = serializers.IntegerField(required=True)
    display_name = serializers.CharField(max_length=128, required=False, allow_blank=True)
    role_codes = serializers.ListField(child=serializers.CharField(), required=False, allow_empty=True)
    roles = serializers.ListField(child=serializers.CharField(), required=False, allow_empty=True)
    qualifications = TenantMemberQualificationSerializer(many=True, required=False)

    def validate(self, attrs):
        role_codes = attrs.get("role_codes")
        if role_codes is None:
            role_codes = attrs.get("roles", [])
        attrs["role_codes"] = list(dict.fromkeys(role_codes))
        attrs.pop("roles", None)

        tenant = Tenant.objects.filter(id=attrs["tenant_id"]).first()
        if tenant is None:
            raise BusinessResourceNotFound("tenant not found", business_detail_code="TENANT_NOT_FOUND")
        if tenant.status != TenantStatus.ACTIVE:
            raise BusinessStateConflict("tenant status invalid", business_detail_code="TENANT_STATUS_INVALID")
        request_tenant = _get_actor_tenant_from_request(self.context.get("request"))
        if request_tenant is not None and tenant.id != request_tenant.id:
            raise serializers.ValidationError({"tenant_id": "tenant_id 必须等于当前租户"})

        user = User.objects.filter(id=attrs["user_id"]).first()
        if user is None:
            raise BusinessResourceNotFound("user not found", business_detail_code="USER_NOT_FOUND")
        _ensure_user_can_be_tenant_member(user)

        if TenantMember.objects.filter(tenant=tenant, user=user).exists():
            raise BusinessStateConflict("tenant member already exists", business_detail_code="TENANT_MEMBER_EXISTS")

        try:
            attrs["role_codes"] = _ensure_role_codes_assignable(attrs["role_codes"], self.context.get("request"))
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({"role_codes": exc.detail[0] if isinstance(exc.detail, list) else exc.detail})

        attrs["_tenant"] = tenant
        attrs["_user"] = user
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        qualifications_data = validated_data.get("qualifications", [])
        request = self.context.get("request")
        now = timezone.now()
        tenant = validated_data["_tenant"]
        user = validated_data["_user"]
        member = TenantMember.objects.create(
            tenant=tenant,
            user=user,
            display_name=validated_data.get("display_name", ""),
            invitation_token=uuid4().hex,
            invited_by_user=request.user if request and request.user.is_authenticated else None,
            invited_at=now,
            expires_at=default_invitation_expiry(now),
            status=TenantMemberStatus.INVITED,
        )

        for code in validated_data["role_codes"]:
            role = Role.objects.get(code=code)
            TenantMemberRole.objects.create(
                tenant_member=member,
                system_role=role,
                status=TenantMemberRoleStatus.GRANTED,
                assigned_by_user=request.user if request and request.user.is_authenticated else None,
                assigned_at=now,
            )

        self.sync_member_attributes(member, qualifications_data=qualifications_data)
        return member


class TenantMemberConfirmInvitationSerializer(serializers.Serializer):
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
        member = expire_stale_tenant_member_invitation(member)
        if member is None:
            raise BusinessResourceNotFound("invitation not found", business_detail_code="INVITATION_NOT_FOUND")

        if member.user_id != request.user.id:
            raise BusinessPermissionDenied("invitation does not belong to current user", business_detail_code="INVITATION_NOT_ALLOWED")

        if member.status != TenantMemberStatus.INVITED:
            raise BusinessStateConflict("invitation is not invited", business_detail_code="INVITATION_STATUS_INVALID")

        now = timezone.now()
        member.status = TenantMemberStatus.ACTIVE
        member.invitation_token = None
        member.invited_by_user = None
        member.invited_at = None
        member.expires_at = None
        member.responded_at = now
        member.joined_at = now
        member.save()
        return member


class TenantMemberRejectInvitationSerializer(serializers.Serializer):
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
        member = expire_stale_tenant_member_invitation(member)
        if member is None:
            raise BusinessResourceNotFound("invitation not found", business_detail_code="INVITATION_NOT_FOUND")

        if member.user_id != request.user.id:
            raise BusinessPermissionDenied("invitation does not belong to current user", business_detail_code="INVITATION_NOT_ALLOWED")

        if member.status != TenantMemberStatus.INVITED:
            raise BusinessStateConflict("invitation is not invited", business_detail_code="INVITATION_STATUS_INVALID")

        member.status = TenantMemberStatus.REJECTED
        member.invitation_token = None
        member.responded_at = timezone.now()
        member.save(update_fields=["status", "invitation_token", "responded_at", "updated_at"])
        return member


class TenantMemberRoleSerializer(serializers.ModelSerializer):
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
            "assigned_by_user",
            "assigned_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "role_code", "role_name", "created_at", "updated_at"]


class TenantMemberRoleAssignSerializer(serializers.Serializer):
    role_codes = serializers.ListField(child=serializers.CharField(), required=True)

    def validate_role_codes(self, value):
        try:
            return _ensure_role_codes_assignable(value, self.context.get("request"))
        except serializers.ValidationError as exc:
            raise serializers.ValidationError(exc.detail[0] if isinstance(exc.detail, list) else exc.detail)

    @transaction.atomic
    def save(self, **kwargs):
        member = self.context["tenant_member"]
        request = self.context.get("request")
        role_codes = self.validated_data["role_codes"]
        now = timezone.now()

        desired_role_ids = set(Role.objects.filter(code__in=role_codes).values_list("id", flat=True))
        member.role_bindings.exclude(system_role_id__in=desired_role_ids).update(status=TenantMemberRoleStatus.REVOKED, updated_at=now)

        for role in Role.objects.filter(id__in=desired_role_ids):
            TenantMemberRole.objects.update_or_create(
                tenant_member=member,
                system_role=role,
                defaults={
                    "status": TenantMemberRoleStatus.GRANTED,
                    "assigned_by_user": request.user if request and request.user.is_authenticated else None,
                    "assigned_at": now,
                },
            )

        return member
