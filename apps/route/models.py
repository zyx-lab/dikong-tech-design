from django.db import models


class RouteType(models.IntegerChoices):
    PENDING_EXTENSION = 0, "待扩展"


class RouteStatus(models.IntegerChoices):
    DISABLED = 0, "禁用"
    ACTIVE = 1, "正常"


class Route(models.Model):
    """航线台账（V1）。"""

    name = models.CharField("航线名称", max_length=100)
    route_type = models.PositiveSmallIntegerField(
        "航线类型扩展位",
        choices=RouteType.choices,
        default=RouteType.PENDING_EXTENSION,
    )
    drone_type_id = models.BigIntegerField("适用无人机类型 ID", null=True, blank=True)
    total_distance = models.DecimalField(
        "航线总长度(米)",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    estimated_duration = models.PositiveIntegerField("预计飞行时长(秒)", null=True, blank=True)
    waypoint_count = models.PositiveIntegerField("航点数量", null=True, blank=True)
    creator_name = models.CharField("创建人姓名", max_length=50, blank=True, default="")
    status = models.PositiveSmallIntegerField(
        "状态",
        choices=RouteStatus.choices,
        default=RouteStatus.ACTIVE,
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        db_table = "routes"
        ordering = ["-id"]
        default_permissions = ()
        permissions = [
            ("view_route", "可查看航线"),
            ("manage_route", "可新增与编辑航线"),
        ]

    def __str__(self):
        return f"{self.id}-{self.name}"
