from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import Group, Permission, PermissionsMixin
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


class StaffTypeStatus(models.IntegerChoices):
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
    - 人员身份事实（name/phone/email/staff_type）统一放在 StaffProfile。
    - groups/user_permissions 通过信号强制禁止直接改动。
    - 业务授权只能走 staff_type -> group -> permission(scope)。
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
        default_permissions = ()
        permissions = [
            ("view_user", "可查看账号"),
            ("manage_user_accounts", "可管理账号与人员档案"),
        ]

    def __str__(self):
        return self.username


class StaffType(TimeStampedModel):
    """身份类型：承载岗位分类，不承载具体人。"""

    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=64)
    description = models.CharField(max_length=255, blank=True)
    status = models.PositiveSmallIntegerField(choices=StaffTypeStatus.choices, default=StaffTypeStatus.ACTIVE)

    class Meta:
        db_table = "staff_types"
        default_permissions = ()

    def __str__(self):
        return f"{self.code}:{self.name}"


class StaffProfile(TimeStampedModel):
    """人员身份档案。

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
    staff_type = models.ForeignKey(StaffType, on_delete=models.PROTECT, related_name="staff_profiles")
    org_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = "staff_profiles"
        default_permissions = ()
        permissions = [
            ("view_staffprofile", "可查看人员档案"),
        ]

    def __str__(self):
        return f"{self.staff_no}-{self.name}"


class StaffTypeGroup(TimeStampedModel):
    """身份类型 -> 能力模块（Group）的映射。"""

    staff_type = models.ForeignKey(StaffType, on_delete=models.CASCADE, related_name="group_links")
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="staff_type_links")
    status = models.PositiveSmallIntegerField(choices=ScopeStatus.choices, default=ScopeStatus.ACTIVE)

    class Meta:
        db_table = "staff_type_groups"
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["staff_type", "group"], name="uniq_staff_type_group"),
        ]
        permissions = [
            ("manage_auth_groups", "可管理能力组与权限"),
            ("manage_auth_scopes", "可管理权限范围策略"),
            ("manage_staff_type_groups", "可管理身份类型能力组映射"),
        ]

    def __str__(self):
        return f"{self.staff_type_id}:{self.group_id}"


class GroupPermissionScope(TimeStampedModel):
    """Group 中每个权限点的数据范围策略。"""

    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="permission_scopes")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name="group_scopes")
    scope_type = models.CharField(max_length=16, choices=ScopeType.choices)
    status = models.PositiveSmallIntegerField(choices=ScopeStatus.choices, default=ScopeStatus.ACTIVE)

    class Meta:
        db_table = "auth_group_permission_scopes"
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["group", "permission"], name="uniq_group_permission_scope"),
        ]


class AuditLog(models.Model):
    """权限关键动作审计日志。"""

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
        default_permissions = ()
        ordering = ["-created_at", "-id"]
        permissions = [
            ("view_auth_audit_logs", "可查看授权审计日志"),
        ]

    def __str__(self):
        return f"{self.action}:{self.target_type}:{self.target_id}"
