from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import Group as AuthGroup
from django.contrib.auth.models import Permission as AuthPermission
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
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


class DirectoryStatus(models.IntegerChoices):
    DISABLED = 0, "disabled"
    ACTIVE = 1, "active"


class AuthSessionType(models.TextChoices):
    BUSINESS = "BUSINESS", "BUSINESS"
    PLATFORM = "PLATFORM", "PLATFORM"


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
        result = super().save(*args, **kwargs)
        if not self.is_active or self.status != UserStatus.ACTIVE:
            AuthSession.objects.filter(user=self, revoked_at__isnull=True).update(revoked_at=timezone.now())
        return result


class AuthSession(TimeStampedModel):
    """正式 IAM Bearer 会话。"""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="auth_sessions")
    session_type = models.CharField(max_length=16, choices=AuthSessionType.choices)
    access_token_hash = models.CharField(max_length=64, unique=True)
    refresh_token_hash = models.CharField(max_length=64, unique=True)
    access_token_expires_at = models.DateTimeField()
    refresh_token_expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_refreshed_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_ip = models.GenericIPAddressField(null=True, blank=True)
    last_used_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "auth_sessions"
        verbose_name = "认证会话"
        verbose_name_plural = "认证会话"
        default_permissions = ()
        indexes = [
            models.Index(fields=["user", "revoked_at"], name="idx_auth_session_user_revoked"),
            models.Index(fields=["access_token_expires_at"], name="idx_auth_session_access_exp"),
            models.Index(fields=["refresh_token_expires_at"], name="idx_auth_session_refresh_exp"),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.session_type}:{self.id}"
