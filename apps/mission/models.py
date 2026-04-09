from django.core.exceptions import ValidationError
from django.db import models

from apps.access.validation import (
    TenantMemberValidationMessages,
    validate_relation_belongs_to_tenant,
    validate_tenant_member_as_pilot,
)


class MissionStatus(models.IntegerChoices):
    DRONE_UNBOUND = 0, "未绑定无人机"
    DRONE_BOUND = 1, "已绑定无人机"


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
    drone = models.ForeignKey(
        "drone.Drone",
        on_delete=models.PROTECT,
        related_name="missions",
        verbose_name="无人机",
        null=True,
        blank=True,
    )
    device_sn = models.CharField("设备序列号（冗余）", max_length=128, blank=True, default="")
    drone_name = models.CharField("无人机名称（冗余）", max_length=100, blank=True, default="")
    pilot = models.ForeignKey("access.TenantMember", on_delete=models.PROTECT, related_name="missions", verbose_name="飞手成员")
    pilot_name = models.CharField("飞手姓名（冗余）", max_length=50, blank=True, default="")
    scheduled_at = models.DateTimeField("计划执行时间", null=True, blank=True)
    remark = models.CharField("任务备注", max_length=500, blank=True, default="")
    status = models.PositiveSmallIntegerField("任务状态", choices=MissionStatus.choices, default=MissionStatus.DRONE_UNBOUND)
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
        validate_relation_belongs_to_tenant(
            related_obj=self.route if self.route_id else None,
            tenant_id=self.tenant_id,
            field_name="route",
            mismatch_message="route 必须属于当前 tenant",
            error_cls=ValidationError,
        )
        validate_relation_belongs_to_tenant(
            related_obj=self.drone if self.drone_id else None,
            tenant_id=self.tenant_id,
            field_name="drone",
            mismatch_message="drone 必须属于当前 tenant",
            error_cls=ValidationError,
        )
        if self.drone_id:
            self.status = MissionStatus.DRONE_BOUND
            self.device_sn = self.drone.device_sn
            self.drone_name = self.drone.name
        else:
            self.status = MissionStatus.DRONE_UNBOUND
            self.device_sn = ""
            self.drone_name = ""
        validate_tenant_member_as_pilot(
            tenant_member=self.pilot if self.pilot_id else None,
            tenant_id=self.tenant_id,
            field_name="pilot",
            messages=TenantMemberValidationMessages(
                tenant_mismatch="pilot 必须属于当前 tenant",
                inactive_member="仅允许分配给 ACTIVE 成员",
                missing_staff_profile="pilot 对应账号必须存在 staff_profile",
                inactive_employment="仅允许分配给在职飞手",
                missing_role="仅允许分配给飞手类型（pilot_operator）",
            ),
            error_cls=ValidationError,
        )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
