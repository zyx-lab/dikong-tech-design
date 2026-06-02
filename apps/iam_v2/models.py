from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q

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
    status = models.PositiveSmallIntegerField(choices=DirectoryStatus.choices, default=DirectoryStatus.ACTIVE)

    class Meta:
        db_table = "v2_account_profiles"
        ordering = ["id"]

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
