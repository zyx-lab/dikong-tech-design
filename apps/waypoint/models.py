from django.db import models


class Waypoint(models.Model):
    """航点主表（waypoints）。"""

    route = models.ForeignKey(
        "route.Route",
        on_delete=models.PROTECT,
        related_name="waypoints",
        verbose_name="所属航线",
    )
    sequence = models.PositiveIntegerField("航点序号")
    latitude = models.DecimalField("纬度", max_digits=12, decimal_places=8)
    longitude = models.DecimalField("经度", max_digits=12, decimal_places=8)
    altitude = models.DecimalField("飞行高度（米）", max_digits=10, decimal_places=2)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        db_table = "waypoints"
        ordering = ["route_id", "sequence", "id"]
        default_permissions = ()
        permissions = [
            ("view_waypoint", "可查看航点"),
            ("manage_waypoint", "可管理航点"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["route", "sequence"], name="waypoints_route_seq_unique"),
        ]

    def __str__(self):
        return f"{self.route_id}-{self.sequence}"
