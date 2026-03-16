from django.core.exceptions import ValidationError
from django.db import models

from apps.access.models import DirectoryStatus, EmploymentStatus, TenantMemberRoleStatus, TenantMemberStatus


class FlightRecordStatus(models.IntegerChoices):
    IN_PROGRESS = 0, "飞行中"
    COMPLETED = 1, "已完成"
    ABORTED = 2, "异常终止"


class FlightRecord(models.Model):
    """飞行记录主表（flight_records）。"""

    tenant = models.ForeignKey(
        "access.Tenant",
        on_delete=models.CASCADE,
        related_name="flight_records",
        verbose_name="租户",
    )
    flight_no = models.CharField("架次编号", max_length=50)
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
        "access.TenantMember",
        on_delete=models.PROTECT,
        related_name="flight_records",
        null=True,
        blank=True,
        verbose_name="执行飞手成员",
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
        constraints = [
            models.UniqueConstraint(fields=["tenant", "flight_no"], name="uniq_flight_record_tenant_flight_no"),
        ]
        permissions = [
            ("view_flight_record", "可查看飞行记录"),
            ("manage_flight_record", "可新增与编辑飞行记录"),
        ]

    def __str__(self):
        return f"{self.id}-{self.flight_no}"

    @property
    def assigned_tenant_member_id(self):
        return self.pilot_id

    def clean(self):
        if self.pk:
            current_status = FlightRecord.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            allowed_transitions = {
                FlightRecordStatus.IN_PROGRESS: {FlightRecordStatus.COMPLETED, FlightRecordStatus.ABORTED},
                FlightRecordStatus.COMPLETED: set(),
                FlightRecordStatus.ABORTED: set(),
            }
            if current_status is not None and self.status != current_status and self.status not in allowed_transitions.get(current_status, set()):
                raise ValidationError({"status": "当前飞行记录状态不允许执行该变更"})

        if self.tenant_id and self.mission_id and self.mission.tenant_id != self.tenant_id:
            raise ValidationError({"mission": "mission 必须属于当前 tenant"})
        if self.tenant_id and self.drone_id and self.drone.tenant_id != self.tenant_id:
            raise ValidationError({"drone": "drone 必须属于当前 tenant"})
        if self.start_time and self.end_time and self.end_time < self.start_time:
            raise ValidationError({"end_time": "结束时间不能早于开始时间"})
        if self.tenant_id and self.pilot_id and self.pilot.tenant_id != self.tenant_id:
            raise ValidationError({"pilot": "pilot 必须属于当前 tenant"})
        if self.pilot_id and self.pilot.status != TenantMemberStatus.ACTIVE:
            raise ValidationError({"pilot": "仅允许绑定 ACTIVE 成员"})
        if self.pilot_id:
            staff = getattr(self.pilot.user, "staff_profile", None)
            if staff is None:
                raise ValidationError({"pilot": "pilot 对应账号必须存在 staff_profile"})
            if staff.employment_status != EmploymentStatus.ACTIVE:
                raise ValidationError({"pilot": "仅允许绑定在职飞手"})
            if not self.pilot.role_bindings.filter(
                system_role__code="pilot_operator",
                system_role__status=DirectoryStatus.ACTIVE,
                status=TenantMemberRoleStatus.GRANTED,
            ).exists():
                raise ValidationError({"pilot": "仅允许绑定当前租户下的飞手类型（pilot_operator）"})
        if self.mission_id and self.drone_id and self.mission.drone_id and self.mission.drone_id != self.drone_id:
            raise ValidationError({"drone": "drone 与 mission 绑定关系不一致"})
        if self.mission_id and self.pilot_id and self.mission.pilot_id and self.mission.pilot_id != self.pilot_id:
            raise ValidationError({"pilot": "pilot 与 mission 绑定关系不一致"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
