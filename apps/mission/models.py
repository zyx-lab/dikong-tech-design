from django.db import models


class MissionStatus(models.IntegerChoices):
    PENDING = 0, "待执行"
    RUNNING = 1, "执行中"
    PAUSED = 2, "已暂停"
    COMPLETED = 3, "已完成"
    CANCELED = 4, "已取消"
    FAILED = 5, "执行失败"


class Mission(models.Model):
    """巡检任务主记录。"""

    name = models.CharField("任务名称", max_length=100)
    route = models.ForeignKey("route.Route", on_delete=models.PROTECT, related_name="missions", verbose_name="航线")
    route_name = models.CharField("航线名称（冗余）", max_length=100, blank=True, default="")
    drone = models.ForeignKey("drone.Drone", on_delete=models.PROTECT, related_name="missions", verbose_name="无人机")
    drone_name = models.CharField("无人机名称（冗余）", max_length=100, blank=True, default="")
    pilot = models.ForeignKey("access.StaffProfile", on_delete=models.PROTECT, related_name="missions", verbose_name="飞手")
    pilot_name = models.CharField("飞手姓名（冗余）", max_length=50, blank=True, default="")
    scheduled_at = models.DateTimeField("计划执行时间", null=True, blank=True)
    remark = models.CharField("任务备注", max_length=500, blank=True, default="")
    status = models.PositiveSmallIntegerField("任务状态", choices=MissionStatus.choices, default=MissionStatus.PENDING)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        db_table = "missions"
        ordering = ["-id"]
        default_permissions = ()
        permissions = [
            ("view_mission", "可查看任务"),
            ("manage_mission", "可新增与编辑任务"),
        ]

    def __str__(self):
        return f"{self.id}-{self.name}"
