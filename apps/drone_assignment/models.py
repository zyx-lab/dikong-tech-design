from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.access.models import DirectoryStatus, EmploymentStatus, TenantMemberRoleStatus, TenantMemberStatus
class DroneAssignmentStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "生效中"
    INACTIVE = "INACTIVE", "已失效"


class DroneAssignment(models.Model):
    """无人机与飞手的关联关系。"""

    tenant = models.ForeignKey(
        "access.Tenant",
        on_delete=models.CASCADE,
        related_name="drone_assignments",
        verbose_name="租户",
    )
    drone = models.ForeignKey("drone.Drone", on_delete=models.CASCADE, related_name="assignments", verbose_name="无人机")
    tenant_member = models.ForeignKey(
        "access.TenantMember",
        on_delete=models.PROTECT,
        related_name="drone_assignments",
        verbose_name="成员",
    )
    status = models.CharField(
        "分配状态",
        max_length=16,
        choices=DroneAssignmentStatus.choices,
        default=DroneAssignmentStatus.ACTIVE,
    )
    start_at = models.DateTimeField("开始时间", auto_now_add=True)
    end_at = models.DateTimeField("结束时间", null=True, blank=True)
    created_by_tenant_member_id = models.BigIntegerField("创建人 TenantMember ID", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        db_table = "drone_assignments"
        ordering = ["-id"]
        default_permissions = ()
        permissions = [
            ("manage_drone_assignment", "可管理无人机分配"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["drone", "tenant_member"],
                condition=Q(status=DroneAssignmentStatus.ACTIVE),
                name="uniq_active_drone_tenant_member_assignment",
            )
        ]

    def __str__(self):
        return f"drone={self.drone_id}, tenant_member={self.tenant_member_id}, status={self.status}"

    def clean(self):
        if self.tenant_id and self.drone_id and self.drone.tenant_id != self.tenant_id:
            raise ValidationError({"drone": "drone 必须属于当前 tenant"})
        if self.tenant_id and self.tenant_member_id and self.tenant_member.tenant_id != self.tenant_id:
            raise ValidationError({"tenant_member": "tenant_member 必须属于当前 tenant"})
        if self.tenant_member_id and self.tenant_member.status != TenantMemberStatus.ACTIVE:
            raise ValidationError({"tenant_member": "仅允许分配给 ACTIVE 成员"})
        if self.tenant_member_id:
            staff = getattr(self.tenant_member.user, "staff_profile", None)
            if staff is None:
                raise ValidationError({"tenant_member": "tenant_member 对应账号必须存在 staff_profile"})
            if staff.employment_status != EmploymentStatus.ACTIVE:
                raise ValidationError({"tenant_member": "仅允许分配给在职人员"})
            if not self.tenant_member.role_bindings.filter(
                system_role__code="pilot_operator",
                system_role__status=DirectoryStatus.ACTIVE,
                status=TenantMemberRoleStatus.GRANTED,
            ).exists():
                raise ValidationError({"tenant_member": "仅允许分配给飞手类型（pilot_operator）"})
        if self.tenant_id and self.created_by_tenant_member_id is not None:
            from apps.access.models import TenantMember

            if not TenantMember.objects.filter(id=self.created_by_tenant_member_id, tenant_id=self.tenant_id).exists():
                raise ValidationError({"created_by_tenant_member_id": "创建人必须属于当前 tenant"})
        if self.status == DroneAssignmentStatus.ACTIVE and self.end_at is not None:
            raise ValidationError({"end_at": "ACTIVE 分配不允许写入 end_at"})
        if self.status == DroneAssignmentStatus.INACTIVE and self.end_at is None:
            raise ValidationError({"end_at": "INACTIVE 分配必须提供 end_at"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
