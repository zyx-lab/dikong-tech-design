from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import Group, Permission, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
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


class TenantMemberAttributeStatus(models.IntegerChoices):
    DISABLED = 0, "disabled"
    ACTIVE = 1, "active"


class ScopeType(models.TextChoices):
    ALL = "ALL", "ALL"
    OWN = "OWN", "OWN"
    ASSIGNED = "ASSIGNED", "ASSIGNED"


class ScopeStatus(models.IntegerChoices):
    DISABLED = 0, "disabled"
    ACTIVE = 1, "active"


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
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("status", UserStatus.ACTIVE)
        return self._create_user(username, password, **extra_fields)

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("status", UserStatus.ACTIVE)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(username, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """平台账号模型。

    说明：
    - 用户模型只承载“账号域”字段：登录、启用状态、后台状态。
    - 人员身份事实（name/phone/email）统一放在 StaffProfile。
    - groups/user_permissions 通过信号强制禁止直接改动。
    - 多租户授权统一走 tenant_member -> system_role -> group -> permission(scope)。
    """

    username = models.CharField(max_length=150, unique=True)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    status = models.PositiveSmallIntegerField(choices=UserStatus.choices, default=UserStatus.ACTIVE)
    last_login = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    groups = models.ManyToManyField(
        Group,
        verbose_name=_("groups"),
        blank=True,
        help_text=_("业务上禁止用户直接绑定 Group。"),
        related_name="user_set",
        related_query_name="user",
        db_table="auth_users_groups",
    )
    user_permissions = models.ManyToManyField(
        Permission,
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


class StaffProfile(TimeStampedModel):
    """人员全局档案。

    关键约束：一账号一 Staff。
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff_profile")
    staff_no = models.CharField(max_length=64, unique=True)
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
        # superuser 作为 root 账号，不绑定全局 StaffProfile。
        if self.user_id and self.user.is_superuser:
            raise ValidationError({"user": "superuser 账号不允许绑定 staff_profile"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.staff_no}-{self.name}"


class GroupPermissionScope(TimeStampedModel):
    """Group 中每个权限点的数据范围策略。"""

    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="permission_scopes")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name="group_scopes")
    scope_type = models.CharField(max_length=16, choices=ScopeType.choices)
    status = models.PositiveSmallIntegerField(choices=ScopeStatus.choices, default=ScopeStatus.ACTIVE)

    class Meta:
        db_table = "auth_group_permission_scopes"
        verbose_name = "能力组权限范围"
        verbose_name_plural = "能力组权限范围"
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["group", "permission"], name="uniq_group_permission_scope"),
        ]
        permissions = [
            ("manage_auth_groups", "可管理能力组与权限"),
            ("manage_auth_scopes", "可管理权限范围策略"),
        ]


class AuditLog(models.Model):
    """权限关键动作审计日志。

    说明：
    - tenant 为空时表示平台级审计（如租户创建、套餐变更）
    - tenant 有值时表示租户级审计（如成员管理、角色分配）
    """

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


class TenantStatus(models.IntegerChoices):
    DISABLED = 0, "disabled"
    ENABLED = 1, "enabled"


class TenantMemberStatus(models.IntegerChoices):
    """租户成员状态"""
    PENDING = 0, "pending"  # 待激活（待租户邀请）
    ACTIVE = 1, "active"    # 已激活（已加入租户）
    DISABLED = 2, "disabled"  # 已禁用


class TenantMemberRoleStatus(models.IntegerChoices):
    """成员角色绑定状态"""
    DISABLED = 0, "disabled"
    ACTIVE = 1, "active"


class SystemRoleStatus(models.IntegerChoices):
    """平台固定角色状态"""
    DISABLED = 0, "disabled"
    ACTIVE = 1, "active"


class Tenant(TimeStampedModel):
    """租户主表，表示一个买家。

    平台级模型，不需要 tenant_id。
    """

    code = models.CharField(max_length=64, unique=True, verbose_name="租户编码")
    name = models.CharField(max_length=128, verbose_name="租户名称")
    status = models.PositiveSmallIntegerField(choices=TenantStatus.choices, default=TenantStatus.ENABLED, verbose_name="状态")
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


class SystemRole(TimeStampedModel):
    """平台固定角色目录。

    7个固定角色由平台统一定义和维护，所有租户共用同一套角色模板。
    平台级模型，不需要 tenant_id。
    """

    code = models.CharField(max_length=64, unique=True, verbose_name="角色编码")
    name = models.CharField(max_length=128, verbose_name="角色名称")
    description = models.CharField(max_length=500, blank=True, verbose_name="角色描述")
    status = models.PositiveSmallIntegerField(choices=SystemRoleStatus.choices, default=SystemRoleStatus.ACTIVE, verbose_name="状态")

    class Meta:
        db_table = "system_roles"
        verbose_name = "平台固定角色"
        verbose_name_plural = "平台固定角色"
        default_permissions = ()
        permissions = [
            ("view_system_role", "可查看固定角色"),
            ("manage_system_role", "可管理固定角色"),
        ]

    def __str__(self):
        return f"{self.code}:{self.name}"


class SystemRoleGroup(TimeStampedModel):
    """固定角色 -> 能力模块（Group）的映射。"""

    system_role = models.ForeignKey(SystemRole, on_delete=models.CASCADE, related_name="group_links")
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="system_role_links")
    status = models.PositiveSmallIntegerField(choices=ScopeStatus.choices, default=ScopeStatus.ACTIVE)

    class Meta:
        db_table = "system_role_groups"
        verbose_name = "固定角色能力组映射"
        verbose_name_plural = "固定角色能力组映射"
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["system_role", "group"], name="uniq_system_role_group"),
        ]

    def __str__(self):
        return f"{self.system_role_id}:{self.group_id}"


class TenantMember(TimeStampedModel):
    """租户成员身份。

    表示某个 User 在某个 Tenant 中的成员身份。
    租户级模型，通过 tenant_id 关联租户。
    """

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tenant_members")
    display_name = models.CharField(max_length=128, verbose_name="显示名称")
    staff_no = models.CharField(max_length=64, blank=True, verbose_name="工号")
    phone = models.CharField(max_length=32, blank=True, verbose_name="手机")
    email = models.EmailField(blank=True, verbose_name="邮箱")
    invitation_token = models.CharField(max_length=64, unique=True, null=True, blank=True, verbose_name="邀请令牌")
    status = models.PositiveSmallIntegerField(choices=TenantMemberStatus.choices, default=TenantMemberStatus.PENDING, verbose_name="状态")
    joined_at = models.DateTimeField(null=True, blank=True, verbose_name="加入时间")

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
        ]

    def __str__(self):
        return f"{self.tenant.code}:{self.user.username}"


class TenantMemberPosition(TimeStampedModel):
    """租户成员岗位。

    岗位是租户内属性，不参与权限判定，只用于业务校验和业务展示。
    """

    tenant_member = models.ForeignKey(TenantMember, on_delete=models.CASCADE, related_name="positions")
    code = models.CharField(max_length=64, verbose_name="岗位编码")
    name = models.CharField(max_length=128, verbose_name="岗位名称")
    description = models.CharField(max_length=255, blank=True, verbose_name="岗位描述")
    status = models.PositiveSmallIntegerField(
        choices=TenantMemberAttributeStatus.choices,
        default=TenantMemberAttributeStatus.ACTIVE,
        verbose_name="状态",
    )

    class Meta:
        db_table = "tenant_member_positions"
        verbose_name = "租户成员岗位"
        verbose_name_plural = "租户成员岗位"
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["tenant_member", "code"], name="uniq_tenant_member_position_code"),
        ]

    def __str__(self):
        return f"{self.tenant_member_id}:{self.code}"


class TenantMemberQualification(TimeStampedModel):
    """租户成员业务资质。

    资质同样不进入授权矩阵，只用于租户内业务约束，例如飞手可执行资格校验。
    """

    tenant_member = models.ForeignKey(TenantMember, on_delete=models.CASCADE, related_name="qualifications")
    code = models.CharField(max_length=64, verbose_name="资质编码")
    name = models.CharField(max_length=128, verbose_name="资质名称")
    description = models.CharField(max_length=255, blank=True, verbose_name="资质描述")
    status = models.PositiveSmallIntegerField(
        choices=TenantMemberAttributeStatus.choices,
        default=TenantMemberAttributeStatus.ACTIVE,
        verbose_name="状态",
    )
    valid_until = models.DateField(null=True, blank=True, verbose_name="有效期至")
    payload = models.JSONField(default=dict, blank=True, verbose_name="扩展信息")

    class Meta:
        db_table = "tenant_member_qualifications"
        verbose_name = "租户成员资质"
        verbose_name_plural = "租户成员资质"
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["tenant_member", "code"], name="uniq_tenant_member_qualification_code"),
        ]

    def __str__(self):
        return f"{self.tenant_member_id}:{self.code}"


class TenantMemberRole(TimeStampedModel):
    """租户成员与固定角色的绑定关系。"""

    tenant_member = models.ForeignKey(TenantMember, on_delete=models.CASCADE, related_name="role_bindings")
    system_role = models.ForeignKey(SystemRole, on_delete=models.CASCADE, related_name="member_bindings")
    status = models.PositiveSmallIntegerField(choices=TenantMemberRoleStatus.choices, default=TenantMemberRoleStatus.ACTIVE, verbose_name="状态")

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

    def __str__(self):
        return f"{self.tenant_member_id}:{self.system_role.code}"
