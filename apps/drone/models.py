from django.db import models
from django.db.models import Q

from apps.access.models import StaffProfile


class DroneStatus(models.TextChoices):
    ENABLED = "ENABLED", "启用"
    DISABLED = "DISABLED", "停用"
    MAINTENANCE = "MAINTENANCE", "维护中"
    RETIRED = "RETIRED", "已退役"


class DroneAssignmentStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "生效中"
    INACTIVE = "INACTIVE", "已失效"


class Drone(models.Model):
    """无人机台账（V1）。

    说明：
    - 当前只做台账管理，不做资源指派。
    - created_by_staff_id 仅用于审计和未来 OWN 范围扩展。
    """

    code = models.CharField("业务编码", max_length=64, unique=True)
    name = models.CharField("无人机名称", max_length=128)
    model = models.CharField("型号", max_length=128)
    serial_no = models.CharField("出厂序列号", max_length=128, unique=True)
    status = models.CharField(
        "状态",
        max_length=16,
        choices=DroneStatus.choices,
        default=DroneStatus.DISABLED,
    )
    org_id = models.BigIntegerField("组织 ID", null=True, blank=True)
    created_by_staff_id = models.BigIntegerField("创建人 Staff ID", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        db_table = "drones"
        ordering = ["-id"]
        # 只保留显式权限码，避免默认 CRUD 权限污染权限矩阵。
        default_permissions = ()
        permissions = [
            ("view_drone", "可查看无人机"),
            ("manage_drone", "可新增与编辑无人机"),
            ("change_drone_status", "可变更无人机状态"),
        ]

    def __str__(self):
        return f"{self.code}-{self.name}"


class DroneAssignment(models.Model):
    """无人机与飞手的关联关系。

    说明：
    - 用于支撑 `drone.view_drone` 的 ASSIGNED 范围过滤。
    - 不做物理删除，取消分配时改为 INACTIVE 并写 end_at。
    """

    drone = models.ForeignKey(Drone, on_delete=models.CASCADE, related_name="assignments", verbose_name="无人机")
    staff = models.ForeignKey(
        StaffProfile,
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
