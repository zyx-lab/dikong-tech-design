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


class V2AccountRoleAssignment(TimeStampedModel):
    account_profile = models.ForeignKey(V2AccountProfile, on_delete=models.CASCADE, related_name="role_assignments")
    role_code = models.CharField(max_length=64, choices=FixedRole.choices)
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


class V2AccountQualification(TimeStampedModel):
    account_profile = models.ForeignKey(V2AccountProfile, on_delete=models.CASCADE, related_name="qualifications")
    role_code = models.CharField(max_length=64, choices=FixedRole.choices)
    qualification_type = models.CharField(max_length=128)
    certificate_no = models.CharField(max_length=128)
    issued_at = models.DateField()
    expires_at = models.DateField()
    status = models.PositiveSmallIntegerField(choices=DirectoryStatus.choices)
    remark = models.TextField()

    class Meta:
        db_table = "v2_account_qualifications"
        ordering = ["-expires_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(role_code__in=sorted(DEPARTMENT_ROLE_CODES)),
                name="chk_v2_account_qualification_role",
            ),
            models.UniqueConstraint(
                fields=["account_profile", "role_code", "qualification_type", "certificate_no"],
                name="uniq_v2_account_qualification",
            ),
        ]

    def clean(self):
        if self.role_code not in DEPARTMENT_ROLE_CODES:
            raise ValidationError({"role_code": "资质只能归属 v2 部门业务角色"})
        if self.issued_at > self.expires_at:
            raise ValidationError({"expires_at": "有效期结束日期不能早于签发日期"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def is_effective(self, at=None) -> bool:
        today = (at or timezone.now()).date()
        if self.status != DirectoryStatus.ACTIVE:
            return False
        if self.issued_at > today:
            return False
        if self.expires_at < today:
            return False
        return True

    def __str__(self):
        return f"{self.account_profile_id}:{self.role_code}:{self.qualification_type}:{self.certificate_no}"


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
