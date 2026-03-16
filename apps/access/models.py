from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import Group as AuthGroup
from django.contrib.auth.models import Permission as AuthPermission
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    """统一的时间基类，避免重复字段。"""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UserStatus(models.IntegerChoices):
    DISABLED = 0, "disabled"
    ACTIVE = 1, "active"


class EmploymentStatus(models.IntegerChoices):
    INACTIVE = 0, "inactive"
    ACTIVE = 1, "active"


class ScopeType(models.TextChoices):
    ALL = "ALL", "ALL"
    OWN = "OWN", "OWN"
    ASSIGNED = "ASSIGNED", "ASSIGNED"


class DirectoryStatus(models.IntegerChoices):
    DISABLED = 0, "disabled"
    ACTIVE = 1, "active"


class TenantStatus(models.IntegerChoices):
    DISABLED = 0, "disabled"
    ACTIVE = 1, "active"


class TenantMemberStatus(models.IntegerChoices):
    INVITED = 0, "invited"
    ACTIVE = 1, "active"
    REJECTED = 2, "rejected"
    EXPIRED = 3, "expired"
    REVOKED = 4, "revoked"
    DISABLED = 5, "disabled"


class TenantMemberRoleStatus(models.IntegerChoices):
    REVOKED = 0, "revoked"
    GRANTED = 1, "granted"


class QualificationRecordStatus(models.IntegerChoices):
    ACTIVE = 1, "active"
    INVALID = 2, "invalid"
    REVOKED = 3, "revoked"


class UserManager(BaseUserManager):
    """自定义用户管理器：仅保留账号域必要逻辑。"""

    use_in_migrations = True

    def _create_user(self, username, password, **extra_fields):
        if not username:
            raise ValueError("The username must be set")
        username = self.model.normalize_username(username)
        user = self.model(username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, username, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        extra_fields.setdefault("is_platform_admin", False)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("status", UserStatus.ACTIVE)
        return self._create_user(username, password, **extra_fields)

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_platform_admin", False)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("status", UserStatus.ACTIVE)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        if extra_fields.get("is_platform_admin") is not False:
            raise ValueError("Superuser must have is_platform_admin=False.")

        return self._create_user(username, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """平台账号模型。"""

    username = models.CharField(max_length=150, unique=True)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_platform_admin = models.BooleanField(default=False)
    status = models.PositiveSmallIntegerField(choices=UserStatus.choices, default=UserStatus.ACTIVE)
    last_login = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    groups = models.ManyToManyField(
        AuthGroup,
        verbose_name=_("groups"),
        blank=True,
        help_text=_("业务上禁止用户直接绑定 Group。"),
        related_name="user_set",
        related_query_name="user",
        db_table="auth_users_groups",
    )
    user_permissions = models.ManyToManyField(
        AuthPermission,
        verbose_name=_("user permissions"),
        blank=True,
        help_text=_("业务上禁止用户直接绑定权限点。"),
        related_name="user_set",
        related_query_name="user",
        db_table="auth_users_user_permissions",
    )

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        db_table = "auth_users"
        verbose_name = "账号"
        verbose_name_plural = "账号"
        default_permissions = ()
        permissions = [
            ("view_user", "可查看账号"),
            ("manage_user_accounts", "可管理账号与人员档案"),
        ]

    def __str__(self):
        return self.username

    def clean(self):
        if self.is_superuser and self.is_platform_admin:
            raise ValidationError({"is_platform_admin": "superuser 与 platform_admin 不能同时为 true"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class StaffProfile(TimeStampedModel):
    """人员全局档案。"""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff_profile")
    name = models.CharField(max_length=64)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    employment_status = models.PositiveSmallIntegerField(
        choices=EmploymentStatus.choices,
        default=EmploymentStatus.ACTIVE,
    )
    org_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = "staff_profiles"
        verbose_name = "人员档案"
        verbose_name_plural = "人员档案"
        default_permissions = ()
        permissions = [
            ("view_staffprofile", "可查看人员档案"),
        ]

    def clean(self):
        if self.user_id and self.user.is_superuser:
            raise ValidationError({"user": "superuser 账号不允许绑定 staff_profile"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name or f"staff_profile:{self.id}"


class AuditLog(models.Model):
    """权限关键动作审计日志。"""

    tenant = models.ForeignKey(
        "Tenant",
        on_delete=models.CASCADE,
        related_name="audit_logs",
        null=True,
        blank=True,
        verbose_name="租户",
    )
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=128)
    target_type = models.CharField(max_length=128)
    target_id = models.CharField(max_length=64, blank=True)
    before_data = models.JSONField(null=True, blank=True)
    after_data = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "auth_audit_logs"
        verbose_name = "审计日志"
        verbose_name_plural = "审计日志"
        default_permissions = ()
        ordering = ["-created_at", "-id"]
        permissions = [
            ("view_auth_audit_logs", "可查看授权审计日志"),
        ]

    def __str__(self):
        return f"{self.action}:{self.target_type}:{self.target_id}"


class Tenant(TimeStampedModel):
    """租户主表。"""

    code = models.CharField(max_length=64, unique=True, verbose_name="租户编码")
    name = models.CharField(max_length=128, verbose_name="租户名称")
    status = models.PositiveSmallIntegerField(choices=TenantStatus.choices, default=TenantStatus.ACTIVE, verbose_name="状态")
    plan = models.CharField(max_length=64, blank=True, verbose_name="套餐")
    remark = models.CharField(max_length=500, blank=True, verbose_name="备注")

    class Meta:
        db_table = "tenants"
        verbose_name = "租户"
        verbose_name_plural = "租户"
        default_permissions = ()
        permissions = [
            ("view_tenant", "可查看租户"),
            ("manage_tenant", "可管理租户"),
        ]

    def __str__(self):
        return f"{self.code}:{self.name}"


class Role(TimeStampedModel):
    """平台级统一角色目录。"""

    code = models.CharField(max_length=64, unique=True, verbose_name="角色编码")
    name = models.CharField(max_length=128, verbose_name="角色名称")
    description = models.CharField(max_length=500, blank=True, verbose_name="角色描述")
    status = models.PositiveSmallIntegerField(choices=DirectoryStatus.choices, default=DirectoryStatus.ACTIVE, verbose_name="状态")

    class Meta:
        db_table = "roles"
        verbose_name = "平台角色"
        verbose_name_plural = "平台角色"
        default_permissions = ()
        permissions = [
            ("view_role", "可查看平台角色"),
            ("manage_role", "可管理平台角色"),
        ]

    def __str__(self):
        return f"{self.code}:{self.name}"


class Permission(TimeStampedModel):
    """平台权限目录。"""

    code = models.CharField(max_length=128, unique=True, verbose_name="权限编码")
    name = models.CharField(max_length=128, verbose_name="权限名称")
    module = models.CharField(max_length=64, verbose_name="模块")
    resource_code = models.CharField(max_length=64, blank=True, verbose_name="资源编码")
    description = models.CharField(max_length=500, blank=True, verbose_name="描述")
    status = models.PositiveSmallIntegerField(choices=DirectoryStatus.choices, default=DirectoryStatus.ACTIVE, verbose_name="状态")

    class Meta:
        db_table = "permissions"
        verbose_name = "平台权限"
        verbose_name_plural = "平台权限"
        default_permissions = ()
        permissions = [
            ("view_permission_catalog", "可查看权限目录"),
            ("manage_permission_catalog", "可管理权限目录"),
        ]
        indexes = [
            models.Index(fields=["module", "status"], name="idx_permission_module_status"),
        ]

    def __str__(self):
        return self.code


class RolePermissionGrant(TimeStampedModel):
    """角色默认权限映射。"""

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="permission_grants")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name="role_grants")
    scope_type = models.CharField(max_length=16, choices=ScopeType.choices, default=ScopeType.ALL)

    class Meta:
        db_table = "role_permission_grants"
        verbose_name = "角色权限映射"
        verbose_name_plural = "角色权限映射"
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["role", "permission"], name="uniq_role_permission_grant"),
        ]

    def __str__(self):
        return f"{self.role.code}:{self.permission.code}:{self.scope_type}"

    def clean(self):
        if not self.permission_id:
            return
        if self.scope_type in {ScopeType.OWN, ScopeType.ASSIGNED} and not self.permission.resource_code:
            raise ValidationError({"permission": "OWN / ASSIGNED 权限映射要求 permission.resource_code 非空"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class QualificationType(TimeStampedModel):
    """平台资质类型目录。"""

    code = models.CharField(max_length=64, unique=True, verbose_name="资质编码")
    name = models.CharField(max_length=128, verbose_name="资质名称")
    description = models.CharField(max_length=500, blank=True, verbose_name="资质描述")
    requires_validity = models.BooleanField(default=False, verbose_name="是否要求有效期")
    payload_schema_json = models.JSONField(null=True, blank=True, verbose_name="扩展字段 schema")
    status = models.PositiveSmallIntegerField(choices=DirectoryStatus.choices, default=DirectoryStatus.ACTIVE, verbose_name="状态")

    class Meta:
        db_table = "qualification_types"
        verbose_name = "资质类型"
        verbose_name_plural = "资质类型"
        default_permissions = ()
        permissions = [
            ("view_qualification_type", "可查看资质类型"),
            ("manage_qualification_type", "可管理资质类型"),
        ]

    def __str__(self):
        return f"{self.code}:{self.name}"


class TenantMember(TimeStampedModel):
    """租户成员关系。"""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tenant_members")
    member_no = models.CharField(max_length=64, blank=True, null=True, verbose_name="工号")
    display_name = models.CharField(max_length=128, blank=True, verbose_name="显示名称")
    invitation_token = models.CharField(max_length=64, unique=False, null=True, blank=True, verbose_name="邀请令牌")
    invited_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sent_tenant_invitations",
        verbose_name="邀请人",
    )
    invited_at = models.DateTimeField(null=True, blank=True, verbose_name="邀请时间")
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name="邀请过期时间")
    responded_at = models.DateTimeField(null=True, blank=True, verbose_name="响应时间")
    joined_at = models.DateTimeField(null=True, blank=True, verbose_name="加入时间")
    status = models.PositiveSmallIntegerField(choices=TenantMemberStatus.choices, default=TenantMemberStatus.INVITED, verbose_name="状态")

    class Meta:
        db_table = "tenant_members"
        verbose_name = "租户成员"
        verbose_name_plural = "租户成员"
        default_permissions = ()
        permissions = [
            ("view_tenant_member", "可查看租户成员"),
            ("manage_tenant_member", "可管理租户成员"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "user"], name="uniq_tenant_user_member"),
            models.UniqueConstraint(
                fields=["tenant", "member_no"],
                condition=Q(member_no__isnull=False) & ~Q(member_no=""),
                name="uniq_tenant_member_no",
            ),
            models.UniqueConstraint(
                fields=["invitation_token"],
                condition=Q(invitation_token__isnull=False) & ~Q(invitation_token=""),
                name="uniq_tenant_member_invitation_token",
            ),
        ]

    def clean(self):
        errors = {}

        if self.user_id and self.user.is_superuser:
            errors["user"] = "superuser 账号不允许绑定 tenant_member"
        if self.user_id and self.user.is_platform_admin:
            errors["user"] = "platform_admin 账号不允许绑定 tenant_member"
        if self.user_id and not StaffProfile.objects.filter(user_id=self.user_id).exists():
            errors["user"] = "tenant_member 绑定的账号必须先存在 staff_profile"

        if self.status == TenantMemberStatus.INVITED:
            if not self.invitation_token:
                errors["invitation_token"] = "INVITED 状态必须提供 invitation_token"
            if self.invited_at is None:
                errors["invited_at"] = "INVITED 状态必须提供 invited_at"
            if self.expires_at is None:
                errors["expires_at"] = "INVITED 状态必须提供 expires_at"
            if self.responded_at is not None:
                errors["responded_at"] = "INVITED 状态不允许写入 responded_at"
            if self.joined_at is not None:
                errors["joined_at"] = "INVITED 状态不允许写入 joined_at"

        if self.status in {TenantMemberStatus.ACTIVE, TenantMemberStatus.DISABLED}:
            if self.responded_at is None:
                errors["responded_at"] = f"{self.get_status_display().upper()} 状态必须提供 responded_at"
            if self.joined_at is None:
                errors["joined_at"] = f"{self.get_status_display().upper()} 状态必须提供 joined_at"
            if self.invitation_token:
                errors["invitation_token"] = f"{self.get_status_display().upper()} 状态不允许保留 invitation_token"
            if self.invited_by_user_id is not None:
                errors["invited_by_user"] = f"{self.get_status_display().upper()} 状态不允许保留 invited_by_user"
            if self.invited_at is not None:
                errors["invited_at"] = f"{self.get_status_display().upper()} 状态不允许保留 invited_at"
            if self.expires_at is not None:
                errors["expires_at"] = f"{self.get_status_display().upper()} 状态不允许保留 expires_at"

        if self.status == TenantMemberStatus.REJECTED:
            if self.responded_at is None:
                errors["responded_at"] = "REJECTED 状态必须提供 responded_at"
            if self.invitation_token:
                errors["invitation_token"] = "REJECTED 状态不允许保留 invitation_token"
            if self.joined_at is not None:
                errors["joined_at"] = "REJECTED 状态不允许写入 joined_at"

        if self.status in {TenantMemberStatus.REVOKED, TenantMemberStatus.EXPIRED}:
            if self.invitation_token:
                errors["invitation_token"] = f"{self.get_status_display().upper()} 状态不允许保留 invitation_token"
            if self.responded_at is not None:
                errors["responded_at"] = f"{self.get_status_display().upper()} 状态不允许写入 responded_at"
            if self.joined_at is not None:
                errors["joined_at"] = f"{self.get_status_display().upper()} 状态不允许写入 joined_at"

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tenant.code}:{self.user.username}"


class TenantMemberQualification(TimeStampedModel):
    """租户成员资质记录。"""

    tenant_member = models.ForeignKey(TenantMember, on_delete=models.CASCADE, related_name="qualifications")
    qualification_type = models.ForeignKey(QualificationType, on_delete=models.PROTECT, related_name="member_records")
    certificate_no = models.CharField(max_length=128, blank=True, verbose_name="证书编号")
    level = models.CharField(max_length=64, blank=True, verbose_name="等级")
    status = models.PositiveSmallIntegerField(
        choices=QualificationRecordStatus.choices,
        default=QualificationRecordStatus.ACTIVE,
        verbose_name="状态",
    )
    issued_at = models.DateField(null=True, blank=True, verbose_name="发证日期")
    valid_from = models.DateField(null=True, blank=True, verbose_name="有效开始日期")
    valid_until = models.DateField(null=True, blank=True, verbose_name="有效截止日期")
    issuer = models.CharField(max_length=128, blank=True, verbose_name="发证机构")
    payload_json = models.JSONField(default=dict, blank=True, verbose_name="扩展信息")

    class Meta:
        db_table = "tenant_member_qualifications"
        verbose_name = "租户成员资质"
        verbose_name_plural = "租户成员资质"
        default_permissions = ()
        indexes = [
            models.Index(fields=["tenant_member", "qualification_type"], name="idx_tm_qualification_type"),
            models.Index(fields=["tenant_member", "status"], name="idx_tm_qualification_status"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(valid_from__isnull=True) | Q(valid_until__isnull=True) | Q(valid_from__lte=models.F("valid_until")),
                name="chk_tm_qualification_valid_range",
            ),
        ]

    @property
    def code(self) -> str:
        return self.qualification_type.code

    @property
    def name(self) -> str:
        return self.qualification_type.name

    def __str__(self):
        return f"{self.tenant_member_id}:{self.qualification_type.code}"

    def clean(self):
        if not self.qualification_type_id:
            return

        qualification_type = self.qualification_type
        if qualification_type.status != DirectoryStatus.ACTIVE:
            raise ValidationError({"qualification_type": "资质类型已停用"})

        if qualification_type.requires_validity:
            if self.valid_from is None:
                raise ValidationError({"valid_from": "该资质类型要求提供 valid_from"})
            if self.valid_until is None:
                raise ValidationError({"valid_until": "该资质类型要求提供 valid_until"})

        if self.valid_from and self.valid_until and self.valid_from > self.valid_until:
            raise ValidationError({"valid_until": "valid_until 不能早于 valid_from"})

        payload_json = self.payload_json or {}
        schema = qualification_type.payload_schema_json or {}
        for required_key in schema.get("required", []):
            if required_key not in payload_json or payload_json[required_key] in (None, ""):
                raise ValidationError({"payload_json": f"缺少必填字段: {required_key}"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class TenantMemberRole(TimeStampedModel):
    """租户成员与平台角色的绑定关系。"""

    tenant_member = models.ForeignKey(TenantMember, on_delete=models.CASCADE, related_name="role_bindings")
    system_role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="member_bindings")
    status = models.PositiveSmallIntegerField(
        choices=TenantMemberRoleStatus.choices,
        default=TenantMemberRoleStatus.GRANTED,
        verbose_name="状态",
    )
    assigned_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_tenant_member_roles",
        verbose_name="分配人",
    )
    assigned_at = models.DateTimeField(null=True, blank=True, verbose_name="分配时间")

    class Meta:
        db_table = "tenant_member_roles"
        verbose_name = "成员角色绑定"
        verbose_name_plural = "成员角色绑定"
        default_permissions = ()
        permissions = [
            ("assign_tenant_member_role", "可分配成员角色"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["tenant_member", "system_role"], name="uniq_member_role"),
        ]

    @property
    def role(self):
        return self.system_role

    def __str__(self):
        return f"{self.tenant_member_id}:{self.system_role.code}"

    def clean(self):
        if not self.system_role_id:
            return

        if self.system_role.status != DirectoryStatus.ACTIVE:
            raise ValidationError({"system_role": "角色已停用，不能分配给租户成员"})
        if self.system_role.code == "platform_admin":
            raise ValidationError({"system_role": "platform_admin 角色不允许分配给租户成员"})
        if self.status == TenantMemberRoleStatus.GRANTED and self.assigned_at is None:
            raise ValidationError({"assigned_at": "GRANTED 状态必须提供 assigned_at"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


# 兼容旧代码的别名，后续逐步移除。
SystemRole = Role
SystemRoleStatus = DirectoryStatus
