from django.core.exceptions import ValidationError
from django.db import models

from apps.access.models import DirectoryStatus, EmploymentStatus, TenantMemberRoleStatus, TenantMemberStatus
from apps.drone.models import DroneStatus


class MissionStatus(models.IntegerChoices):
    PENDING = 0, "待执行"
    RUNNING = 1, "执行中"
    PAUSED = 2, "已暂停"
    COMPLETED = 3, "已完成"
    CANCELED = 4, "已取消"
    FAILED = 5, "执行失败"


class Mission(models.Model):
    """巡检任务主记录。"""

    tenant = models.ForeignKey(
        "access.Tenant",
        on_delete=models.CASCADE,
        related_name="missions",
        verbose_name="租户",
    )
    name = models.CharField("任务名称", max_length=100)
    route = models.ForeignKey(
        "route.Route",
        on_delete=models.SET_NULL,
        related_name="missions",
        verbose_name="航线",
        null=True,
        blank=True,
    )
    route_name = models.CharField("航线名称（冗余）", max_length=100, blank=True, default="")
    drone = models.ForeignKey("drone.Drone", on_delete=models.PROTECT, related_name="missions", verbose_name="无人机")
    device_sn = models.CharField("设备序列号（冗余）", max_length=128, blank=True, default="")
    drone_name = models.CharField("无人机名称（冗余）", max_length=100, blank=True, default="")
    pilot = models.ForeignKey("access.TenantMember", on_delete=models.PROTECT, related_name="missions", verbose_name="飞手成员")
    pilot_name = models.CharField("飞手姓名（冗余）", max_length=50, blank=True, default="")
    scheduled_at = models.DateTimeField("计划执行时间", null=True, blank=True)
    remark = models.CharField("任务备注", max_length=500, blank=True, default="")
    status = models.PositiveSmallIntegerField("任务状态", choices=MissionStatus.choices, default=MissionStatus.PENDING)
    dji_job_id = models.CharField("DJI 任务 ID", max_length=128, blank=True, default="")
    is_deleted = models.BooleanField("是否已删除", default=False)
    deleted_at = models.DateTimeField("删除时间", null=True, blank=True)
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

    @property
    def assigned_tenant_member_id(self):
        return self.pilot_id

    def clean(self):
        if self.pk:
            was_deleted = Mission.objects.filter(pk=self.pk).values_list("is_deleted", flat=True).first()
            if was_deleted and not self.is_deleted:
                raise ValidationError({"is_deleted": "任务软删除后不可恢复"})
        if self.is_deleted and self.deleted_at is None:
            raise ValidationError({"deleted_at": "逻辑删除记录必须提供 deleted_at"})
        if not self.is_deleted and self.deleted_at is not None:
            raise ValidationError({"deleted_at": "未删除记录不允许写入 deleted_at"})
        if self.tenant_id and self.route_id and self.route.tenant_id != self.tenant_id:
            raise ValidationError({"route": "route 必须属于当前 tenant"})
        if self.tenant_id and self.drone_id and self.drone.tenant_id != self.tenant_id:
            raise ValidationError({"drone": "drone 必须属于当前 tenant"})
        if self.drone_id and not self.device_sn:
            self.device_sn = self.drone.device_sn
        if self.tenant_id and self.pilot_id and self.pilot.tenant_id != self.tenant_id:
            raise ValidationError({"pilot": "pilot 必须属于当前 tenant"})
        if self.pilot_id and self.pilot.status != TenantMemberStatus.ACTIVE:
            raise ValidationError({"pilot": "仅允许分配给 ACTIVE 成员"})
        if self.pilot_id:
            staff = getattr(self.pilot.user, "staff_profile", None)
            if staff is None:
                raise ValidationError({"pilot": "pilot 对应账号必须存在 staff_profile"})
            if staff.employment_status != EmploymentStatus.ACTIVE:
                raise ValidationError({"pilot": "仅允许分配给在职飞手"})
            if not self.pilot.role_bindings.filter(
                system_role__code="pilot_operator",
                system_role__status=DirectoryStatus.ACTIVE,
                status=TenantMemberRoleStatus.GRANTED,
            ).exists():
                raise ValidationError({"pilot": "仅允许分配给飞手类型（pilot_operator）"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
