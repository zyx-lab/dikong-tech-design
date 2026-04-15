from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.access.validation import (
    TenantMemberValidationMessages,
    validate_relation_belongs_to_tenant,
    validate_tenant_member_as_pilot,
)


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
    device_sn = models.CharField("设备序列号（冗余）", max_length=128, blank=True, default="")
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
    is_deleted = models.BooleanField("是否已删除", default=False)
    deleted_at = models.DateTimeField("删除时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        db_table = "flight_records"
        ordering = ["-id"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["tenant", "flight_no"], name="uniq_flight_record_tenant_flight_no"),
            models.UniqueConstraint(
                fields=["mission"],
                condition=Q(mission__isnull=False),
                name="uniq_flight_record_mission",
            ),
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

    @classmethod
    def sync_video_count_from_media(cls, *, flight_record):
        if flight_record is None:
            return
        from apps.media_file.models import MediaFile, MediaType

        locked_flight_record = cls.objects.select_for_update().filter(
            pk=flight_record.pk,
            is_deleted=False,
        ).first()
        if locked_flight_record is None:
            return
        locked_flight_record.video_count = MediaFile.objects.filter(
            flight_record=locked_flight_record,
            is_deleted=False,
            media_type=MediaType.VIDEO,
            dji_index__isnull=False,
        ).count()
        locked_flight_record.save(update_fields=["video_count", "updated_at"])

    @classmethod
    def build_snapshot_defaults(cls, *, mission):
        flight_duration = None
        if mission.started_at and mission.finished_at:
            flight_duration = max(int((mission.finished_at - mission.started_at).total_seconds()), 0)

        return {
            "tenant": mission.tenant,
            "flight_no": f"FR-{mission.id}",
            "mission_name": mission.name,
            "route_name": mission.route_name,
            "airport_name": "",
            "drone": mission.drone,
            "device_sn": mission.device_sn,
            "drone_name": mission.drone_name,
            "pilot": mission.pilot,
            "pilot_name": mission.pilot_name,
            "start_time": mission.started_at,
            "end_time": mission.finished_at,
            "flight_duration": flight_duration,
            "photo_count": 0,
            "video_count": 0,
            "status": FlightRecordStatus.COMPLETED,
        }

    @classmethod
    def create_from_completed_mission(cls, *, mission):
        record, _created = cls.objects.get_or_create(
            mission=mission,
            defaults=cls.build_snapshot_defaults(mission=mission),
        )
        return record

    def clean(self):
        if self.pk:
            was_deleted = FlightRecord.objects.filter(pk=self.pk).values_list("is_deleted", flat=True).first()
            if was_deleted and not self.is_deleted:
                raise ValidationError({"is_deleted": "飞行记录软删除后不可恢复"})

        validate_relation_belongs_to_tenant(
            related_obj=self.mission if self.mission_id else None,
            tenant_id=self.tenant_id,
            field_name="mission",
            mismatch_message="mission 必须属于当前 tenant",
            error_cls=ValidationError,
        )
        validate_relation_belongs_to_tenant(
            related_obj=self.drone if self.drone_id else None,
            tenant_id=self.tenant_id,
            field_name="drone",
            mismatch_message="drone 必须属于当前 tenant",
            error_cls=ValidationError,
        )
        if self.start_time and self.end_time and self.end_time < self.start_time:
            raise ValidationError({"end_time": "结束时间不能早于开始时间"})
        if self.is_deleted and self.deleted_at is None:
            raise ValidationError({"deleted_at": "逻辑删除记录必须提供 deleted_at"})
        if not self.is_deleted and self.deleted_at is not None:
            raise ValidationError({"deleted_at": "未删除记录不允许写入 deleted_at"})
        validate_tenant_member_as_pilot(
            tenant_member=self.pilot if self.pilot_id else None,
            tenant_id=self.tenant_id,
            field_name="pilot",
            messages=TenantMemberValidationMessages(
                tenant_mismatch="pilot 必须属于当前 tenant",
                inactive_member="仅允许绑定 ACTIVE 成员",
                missing_staff_profile="pilot 对应账号必须存在 staff_profile",
                inactive_employment="仅允许绑定在职飞手",
                missing_role="仅允许绑定当前租户下的飞手类型（pilot_operator）",
            ),
            error_cls=ValidationError,
        )
        if self.mission_id and self.drone_id and self.mission.drone_id and self.mission.drone_id != self.drone_id:
            raise ValidationError({"drone": "drone 与 mission 绑定关系不一致"})
        if self.mission_id and self.pilot_id and self.mission.pilot_id and self.mission.pilot_id != self.pilot_id:
            raise ValidationError({"pilot": "pilot 与 mission 绑定关系不一致"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
