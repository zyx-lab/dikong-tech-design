from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.access.models import DirectoryStatus, TimeStampedModel


class FixedRole(models.TextChoices):
    PLATFORM_SUPER_ADMIN = "platform_super_admin", "平台超级管理员"
    DEPARTMENT_ADMIN = "department_admin", "部门管理员"
    TASK_MONITOR_DISPATCHER = "task_monitor_dispatcher", "任务监控调度员"
    PILOT = "pilot", "飞手"
    WORK_ORDER_HANDLER = "work_order_handler", "工单处理员"


ROLE_CODE_ORDER = [choice.value for choice in FixedRole]
PLATFORM_ROLE_CODES = {FixedRole.PLATFORM_SUPER_ADMIN.value}
DEPARTMENT_ROLE_CODES = {
    FixedRole.DEPARTMENT_ADMIN.value,
    FixedRole.TASK_MONITOR_DISPATCHER.value,
    FixedRole.PILOT.value,
    FixedRole.WORK_ORDER_HANDLER.value,
}
DEPARTMENT_OPERATOR_ROLE_CODES = {
    FixedRole.TASK_MONITOR_DISPATCHER.value,
    FixedRole.PILOT.value,
    FixedRole.WORK_ORDER_HANDLER.value,
}
PROTECTED_FROM_DEPARTMENT_ADMIN_ROLE_CODES = {
    FixedRole.PLATFORM_SUPER_ADMIN.value,
    FixedRole.DEPARTMENT_ADMIN.value,
}


class V2Permission(TimeStampedModel):
    code = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=128)
    domain = models.CharField(max_length=64)
    resource = models.CharField(max_length=64)
    action = models.CharField(max_length=64)
    status = models.PositiveSmallIntegerField(choices=DirectoryStatus.choices, default=DirectoryStatus.ACTIVE)
    is_system = models.BooleanField(default=True)

    class Meta:
        db_table = "v2_permissions"
        ordering = ["domain", "resource", "action", "id"]
        indexes = [
            models.Index(fields=["domain", "resource", "status"], name="idx_v2_perm_domain_resource"),
        ]

    def clean(self):
        parts = str(self.code or "").split(":")
        if len(parts) != 3 or not all(parts):
            raise ValidationError({"code": "v2 权限码格式必须为 <domain>:<resource>:<action>"})
        domain, resource, action = parts
        if self.domain and self.domain != domain:
            raise ValidationError({"domain": "domain 必须与权限码第一段一致"})
        if self.resource and self.resource != resource:
            raise ValidationError({"resource": "resource 必须与权限码第二段一致"})
        if self.action and self.action != action:
            raise ValidationError({"action": "action 必须与权限码第三段一致"})
        self.domain = domain
        self.resource = resource
        self.action = action

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class V2Role(TimeStampedModel):
    class DataScope(models.TextChoices):
        ALL = "ALL", "全平台数据"
        DEPT_AND_CHILDREN = "DEPT_AND_CHILDREN", "本部门及下级"
        DEPT_ONLY = "DEPT_ONLY", "仅本部门"
        SELF = "SELF", "本人数据"
        CUSTOM_DEPARTMENTS = "CUSTOM_DEPARTMENTS", "指定部门集合"

    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=128)
    status = models.PositiveSmallIntegerField(choices=DirectoryStatus.choices, default=DirectoryStatus.ACTIVE)
    is_system = models.BooleanField(default=False)
    is_super_admin = models.BooleanField(default=False)
    assignable_by_department_admin = models.BooleanField(default=False)
    data_scope = models.CharField(max_length=32, choices=DataScope.choices, default=DataScope.SELF)
    sort = models.IntegerField(default=100)
    remark = models.TextField(blank=True, default="")

    class Meta:
        db_table = "v2_roles"
        ordering = ["sort", "id"]

    def clean(self):
        if self.is_super_admin:
            self.data_scope = self.DataScope.ALL
            self.assignable_by_department_admin = False
        if self.assignable_by_department_admin and self.data_scope == self.DataScope.ALL:
            raise ValidationError({"data_scope": "部门管理员可分配角色不能使用 ALL 数据范围"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code}:{self.name}"


class V2RoleCustomDepartment(TimeStampedModel):
    role = models.ForeignKey(V2Role, on_delete=models.CASCADE, related_name="custom_departments")
    department = models.ForeignKey("Department", on_delete=models.CASCADE, related_name="custom_scope_roles")

    class Meta:
        db_table = "v2_role_custom_departments"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["role", "department"], name="uniq_v2_role_custom_department"),
        ]


class V2RolePermissionGrant(TimeStampedModel):
    class GrantSource(models.TextChoices):
        DIRECT = "DIRECT", "直接授权"
        MENU = "MENU", "菜单授权"

    role = models.ForeignKey(V2Role, on_delete=models.CASCADE, related_name="permission_grants")
    permission = models.ForeignKey(V2Permission, on_delete=models.CASCADE, related_name="role_grants")
    source = models.CharField(max_length=16, choices=GrantSource.choices, default=GrantSource.DIRECT)

    class Meta:
        db_table = "v2_role_permission_grants"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["role", "permission", "source"], name="uniq_v2_role_permission_source"),
        ]

    def __str__(self):
        return f"{self.role.code}:{self.permission.code}:{self.source}"


class V2Menu(TimeStampedModel):
    class MenuType(models.TextChoices):
        DIRECTORY = "DIRECTORY", "目录"
        MENU = "MENU", "菜单"
        BUTTON = "BUTTON", "按钮"

    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    name = models.CharField(max_length=128)
    code = models.CharField(max_length=128, unique=True)
    menu_type = models.CharField(max_length=16, choices=MenuType.choices)
    path = models.CharField(max_length=255, blank=True, default="")
    component = models.CharField(max_length=255, blank=True, default="")
    icon = models.CharField(max_length=64, blank=True, default="")
    sort = models.IntegerField(default=100)
    status = models.PositiveSmallIntegerField(choices=DirectoryStatus.choices, default=DirectoryStatus.ACTIVE)
    is_system = models.BooleanField(default=True)

    class Meta:
        db_table = "v2_menus"
        ordering = ["sort", "id"]
        indexes = [
            models.Index(fields=["parent", "sort"], name="idx_v2_menu_parent_sort"),
            models.Index(fields=["menu_type", "status"], name="idx_v2_menu_type_status"),
        ]

    def clean(self):
        if self.menu_type == self.MenuType.BUTTON and self.parent_id is None:
            raise ValidationError({"parent": "按钮必须归属菜单"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code}:{self.name}"

    @property
    def permission_codes(self) -> list[str]:
        return [binding.permission_code for binding in self.permission_bindings.all()]

    @property
    def active_permission_codes(self) -> set[str]:
        return {binding.permission_code for binding in self.permission_bindings.all() if binding.permission_is_active}


class V2MenuPermissionBinding(TimeStampedModel):
    menu = models.ForeignKey(V2Menu, on_delete=models.CASCADE, related_name="permission_bindings")
    permission = models.ForeignKey(V2Permission, on_delete=models.CASCADE, related_name="menu_bindings")

    class Meta:
        db_table = "v2_menu_permission_bindings"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["menu", "permission"], name="uniq_v2_menu_permission"),
        ]

    @property
    def permission_code(self) -> str:
        return self.permission.code

    @property
    def permission_is_active(self) -> bool:
        return self.permission.status == DirectoryStatus.ACTIVE


class V2RoleMenuGrant(TimeStampedModel):
    role = models.ForeignKey(V2Role, on_delete=models.CASCADE, related_name="menu_grants")
    menu = models.ForeignKey(V2Menu, on_delete=models.CASCADE, related_name="role_grants")

    class Meta:
        db_table = "v2_role_menu_grants"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["role", "menu"], name="uniq_v2_role_menu"),
        ]


class Department(TimeStampedModel):
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )
    name = models.CharField(max_length=128)
    status = models.PositiveSmallIntegerField(choices=DirectoryStatus.choices, default=DirectoryStatus.ACTIVE)
    path = models.CharField(max_length=500, blank=True, db_index=True)
    depth = models.PositiveIntegerField(default=0)
    created_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_v2_departments",
    )

    class Meta:
        db_table = "v2_departments"
        ordering = ["path", "id"]
        constraints = [
            models.UniqueConstraint(
                models.Value(1),
                condition=Q(parent__isnull=True),
                name="uniq_v2_department_single_root",
            ),
            models.UniqueConstraint(fields=["parent", "name"], name="uniq_v2_department_sibling_name"),
        ]

    def __str__(self):
        return self.path or self.name

    def clean(self):
        if self.parent_id is None and Department.objects.filter(parent__isnull=True).exclude(pk=self.pk).exists():
            raise ValidationError({"parent": "系统只允许一个根部门"})
        if self.pk is not None:
            old_parent_id = Department.objects.filter(pk=self.pk).values_list("parent_id", flat=True).first()
            if old_parent_id != self.parent_id:
                raise ValidationError({"parent": "部门节点暂不支持移动"})

    def _resolved_path_and_depth(self):
        if self.parent_id:
            parent = Department.objects.get(pk=self.parent_id)
            return f"{parent.path}{self.id}/", parent.depth + 1
        return f"/{self.id}/", 0

    def save(self, *args, **kwargs):
        self.full_clean()
        with transaction.atomic():
            super().save(*args, **kwargs)
            resolved_path, resolved_depth = self._resolved_path_and_depth()
            if self.path != resolved_path or self.depth != resolved_depth:
                Department.objects.filter(pk=self.pk).update(path=resolved_path, depth=resolved_depth)
                self.path = resolved_path
                self.depth = resolved_depth


class V2AccountProfile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="v2_account_profile")
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="account_profiles")
    name = models.CharField(max_length=128)
    phone = models.CharField(max_length=32)
    email = models.EmailField(blank=True, default="")
    status = models.PositiveSmallIntegerField(choices=DirectoryStatus.choices, default=DirectoryStatus.ACTIVE)

    class Meta:
        db_table = "v2_account_profiles"
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=Q(phone__gt=""), name="chk_v2_account_phone_not_blank"),
            models.UniqueConstraint(fields=["phone"], name="uniq_v2_account_phone"),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.department_id}"

    @property
    def username(self) -> str:
        return self.user.username

    @property
    def role_codes(self) -> list[str]:
        return list(self.role_assignments.values_list("role_code", flat=True))

    def has_any_role(self, role_codes: list[str]) -> bool:
        return self.role_assignments.filter(role_code__in=role_codes).exists()

    def set_password(self, raw_password: str) -> None:
        self.user.set_password(raw_password)
        self.user.save(update_fields=["password", "updated_at"])


class V2AccountRoleAssignment(TimeStampedModel):
    account_profile = models.ForeignKey(V2AccountProfile, on_delete=models.CASCADE, related_name="role_assignments")
    role_code = models.CharField(max_length=64)
    assigned_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_v2_roles",
    )

    class Meta:
        db_table = "v2_account_role_assignments"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["account_profile", "role_code"], name="uniq_v2_account_role"),
        ]

    def __str__(self):
        return f"{self.account_profile_id}:{self.role_code}"


class V2ProfileType(TimeStampedModel):
    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=128)
    role_code = models.CharField(max_length=64)
    status = models.PositiveSmallIntegerField(choices=DirectoryStatus.choices, default=DirectoryStatus.ACTIVE)
    is_system = models.BooleanField(default=False)
    sort = models.IntegerField(default=100)
    remark = models.TextField(blank=True, default="")

    class Meta:
        db_table = "v2_profile_types"
        ordering = ["sort", "id"]

    def clean(self):
        if self.code != self.role_code:
            raise ValidationError({"role_code": "profileType.code 必须等于绑定角色编码"})
        if self.role_code and not V2Role.objects.filter(code=self.role_code).exists():
            raise ValidationError({"role_code": "profileType 必须绑定已存在角色"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code}:{self.name}"


def _soft_delete_profile_record(record) -> None:
    record.deleted_at = timezone.now()
    record.status = DirectoryStatus.DISABLED
    record.save(update_fields=["deleted_at", "status", "updated_at"])


class V2AccountRoleProfile(TimeStampedModel):
    account_profile = models.ForeignKey(V2AccountProfile, on_delete=models.CASCADE, related_name="role_profiles")
    profile_type = models.CharField(max_length=64)
    display_name = models.CharField(max_length=128)
    level = models.CharField(max_length=64, blank=True, default="")
    status = models.PositiveSmallIntegerField(choices=DirectoryStatus.choices, default=DirectoryStatus.ACTIVE)
    remark = models.TextField(blank=True, default="")
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "v2_account_role_profiles"
        ordering = ["account_profile_id", "profile_type"]
        constraints = [
            models.UniqueConstraint(fields=["account_profile", "profile_type"], name="uniq_v2_account_role_profile"),
        ]
        indexes = [
            models.Index(fields=["profile_type", "status", "deleted_at"], name="idx_v2_role_profile_type"),
        ]

    def soft_delete(self):
        _soft_delete_profile_record(self)

    @property
    def department(self):
        return self.account_profile.department

    def __str__(self):
        return f"{self.account_profile_id}:{self.profile_type}"


class V2AccountQualification(TimeStampedModel):
    account_profile = models.ForeignKey(V2AccountProfile, on_delete=models.CASCADE, related_name="qualifications")
    profile_type = models.CharField(max_length=64)
    qualification_type = models.CharField(max_length=128)
    certificate_no = models.CharField(max_length=128)
    issued_at = models.DateField()
    expires_at = models.DateField()
    status = models.PositiveSmallIntegerField(choices=DirectoryStatus.choices)
    remark = models.TextField(blank=True, default="")
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "v2_account_qualifications"
        ordering = ["account_profile_id", "profile_type", "-expires_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["account_profile", "profile_type", "qualification_type", "certificate_no"],
                name="uniq_v2_account_qualification",
            ),
        ]
        indexes = [
            models.Index(fields=["account_profile", "profile_type", "deleted_at"], name="idx_v2_account_qual_profile"),
        ]

    def clean(self):
        if self.issued_at > self.expires_at:
            raise ValidationError({"expires_at": "有效期结束日期不能早于签发日期"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def is_effective(self, at=None) -> bool:
        today = (at or timezone.now()).date()
        if self.deleted_at is not None:
            return False
        if self.status != DirectoryStatus.ACTIVE:
            return False
        if self.issued_at > today:
            return False
        if self.expires_at < today:
            return False
        return True

    def soft_delete(self):
        _soft_delete_profile_record(self)

    def __str__(self):
        return f"{self.account_profile_id}:{self.profile_type}:{self.qualification_type}:{self.certificate_no}"


class ResourceShareGroup(TimeStampedModel):
    owner_department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="owned_share_groups")
    name = models.CharField(max_length=128)
    status = models.PositiveSmallIntegerField(choices=DirectoryStatus.choices, default=DirectoryStatus.ACTIVE)

    class Meta:
        db_table = "v2_resource_share_groups"
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(fields=["owner_department", "name"], name="uniq_v2_share_group_owner_name"),
        ]

    def __str__(self):
        return f"{self.owner_department_id}:{self.name}"


class ResourceShareGroupTargetDepartment(TimeStampedModel):
    share_group = models.ForeignKey(ResourceShareGroup, on_delete=models.CASCADE, related_name="target_departments")
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="targeted_share_groups")

    class Meta:
        db_table = "v2_resource_share_group_targets"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["share_group", "department"], name="uniq_v2_share_group_target_department"),
        ]

    def clean(self):
        return None

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
