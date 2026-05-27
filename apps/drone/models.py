from django.db import models
from django.db.models import Q


class DroneStatus(models.TextChoices):
    CLAIMED = "CLAIMED", "已认领"
    RELEASED = "RELEASED", "已释放"


class Drone(models.Model):
    """租户已认领设备。"""

    tenant = models.ForeignKey(
        "access.Tenant",
        on_delete=models.CASCADE,
        related_name="drones",
        verbose_name="租户",
    )
    dji_platform = models.ForeignKey(
        "dji_bff.DjiCloudPlatform",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="drones",
        verbose_name="DJI 平台",
    )
    code = models.CharField("业务编码", max_length=64)
    name = models.CharField("无人机名称", max_length=128)
    model = models.CharField("型号", max_length=128)
    device_sn = models.CharField("出厂序列号", max_length=128)
    status = models.CharField(
        "状态",
        max_length=16,
        choices=DroneStatus.choices,
        default=DroneStatus.CLAIMED,
    )
    dji_online = models.BooleanField("DJI 在线状态", default=False)
    org_id = models.BigIntegerField("组织 ID", null=True, blank=True)
    created_by_tenant_member_id = models.BigIntegerField("创建人 TenantMember ID", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        db_table = "drones"
        ordering = ["-id"]
        # 只保留显式权限码，避免默认 CRUD 权限污染权限矩阵。
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="uniq_drone_tenant_code"),
            models.UniqueConstraint(
                fields=["tenant", "dji_platform", "device_sn"],
                condition=Q(dji_platform__isnull=False) & ~Q(status=DroneStatus.RELEASED),
                name="uniq_drone_tenant_platform_device_sn",
            ),
            models.UniqueConstraint(
                fields=["tenant", "device_sn"],
                condition=Q(dji_platform__isnull=True) & ~Q(status=DroneStatus.RELEASED),
                name="uniq_drone_tenant_device_sn_legacy",
            ),
            models.UniqueConstraint(
                fields=["device_sn"],
                condition=Q(dji_platform__isnull=True) & ~Q(status=DroneStatus.RELEASED),
                name="uniq_drone_device_sn_global_legacy",
            ),
        ]
        permissions = [
            ("view_drone", "可查看无人机"),
            ("manage_drone", "可新增与编辑无人机"),
        ]

    def __str__(self):
        return f"{self.code}-{self.name}"
