from django.db import models
from django.db.models import Q


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
        null=True,
        blank=True,
    )
    drone = models.ForeignKey("drone.Drone", on_delete=models.CASCADE, related_name="assignments", verbose_name="无人机")
    staff = models.ForeignKey(
        "access.StaffProfile",
        on_delete=models.PROTECT,
        related_name="drone_assignments",
        verbose_name="飞手",
    )
    status = models.CharField(
        "分配状态",
        max_length=16,
        choices=DroneAssignmentStatus.choices,
        default=DroneAssignmentStatus.ACTIVE,
    )
    start_at = models.DateTimeField("开始时间", auto_now_add=True)
    end_at = models.DateTimeField("结束时间", null=True, blank=True)
    created_by_staff_id = models.BigIntegerField("创建人 Staff ID", null=True, blank=True)
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
                fields=["drone", "staff"],
                condition=Q(status=DroneAssignmentStatus.ACTIVE),
                name="uniq_active_drone_staff_assignment",
            )
        ]

    def __str__(self):
        return f"drone={self.drone_id}, staff={self.staff_id}, status={self.status}"
