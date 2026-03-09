from django.db import models


class FlightRecordStatus(models.IntegerChoices):
    IN_PROGRESS = 0, "飞行中"
    COMPLETED = 1, "已完成"
    ABORTED = 2, "异常终止"


class FlightRecord(models.Model):
    """飞行记录主表（flight_records）。"""

    flight_no = models.CharField("架次编号", max_length=50, unique=True)
    mission = models.ForeignKey(
        "mission.Mission",
        on_delete=models.PROTECT,
        related_name="flight_records",
        null=True,
        blank=True,
        verbose_name="所属任务",
    )
    mission_name = models.CharField("任务名称（冗余）", max_length=100, blank=True, default="")
    route_name = models.CharField("航线名称（冗余）", max_length=100, blank=True, default="")
    airport_name = models.CharField("执行机场名称", max_length=100, blank=True, default="")
    drone = models.ForeignKey(
        "drone.Drone",
        on_delete=models.PROTECT,
        related_name="flight_records",
        null=True,
        blank=True,
        verbose_name="执行无人机",
    )
    drone_name = models.CharField("无人机名称（冗余）", max_length=100, blank=True, default="")
    pilot = models.ForeignKey(
        "access.StaffProfile",
        on_delete=models.PROTECT,
        related_name="flight_records",
        null=True,
        blank=True,
        verbose_name="执行飞手",
    )
    pilot_name = models.CharField("飞手姓名（冗余）", max_length=50, blank=True, default="")
    start_time = models.DateTimeField("开始时间", null=True, blank=True)
    end_time = models.DateTimeField("结束时间", null=True, blank=True)
    flight_duration = models.PositiveIntegerField("飞行时长（秒）", null=True, blank=True)
    photo_count = models.PositiveIntegerField("拍摄照片数量", default=0)
    video_count = models.PositiveIntegerField("录制视频数量", default=0)
    status = models.PositiveSmallIntegerField("状态", choices=FlightRecordStatus.choices, default=FlightRecordStatus.IN_PROGRESS)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        db_table = "flight_records"
        ordering = ["-id"]
        default_permissions = ()
        permissions = [
            ("view_flight_record", "可查看飞行记录"),
            ("manage_flight_record", "可新增与编辑飞行记录"),
        ]

    def __str__(self):
        return f"{self.id}-{self.flight_no}"
