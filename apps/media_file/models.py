from django.core.exceptions import ValidationError
from django.db import models


class MediaType(models.IntegerChoices):
    PHOTO = 1, "照片"
    VIDEO = 2, "视频"


class MediaFile(models.Model):
    """媒体文件主表（media_files）。"""

    tenant = models.ForeignKey(
        "access.Tenant",
        on_delete=models.CASCADE,
        related_name="media_files",
        verbose_name="租户",
    )
    flight_record = models.ForeignKey(
        "flight_record.FlightRecord",
        on_delete=models.PROTECT,
        related_name="media_files",
        null=True,
        blank=True,
        verbose_name="关联飞行记录",
    )
    mission = models.ForeignKey(
        "mission.Mission",
        on_delete=models.SET_NULL,
        related_name="media_files",
        null=True,
        blank=True,
        verbose_name="关联任务",
    )
    device_sn = models.CharField("设备序列号（冗余）", max_length=128, blank=True, default="")
    media_type = models.PositiveSmallIntegerField("媒体类型", choices=MediaType.choices)
    file_name = models.CharField("文件名", max_length=255)
    file_url = models.CharField("文件URL", max_length=500)
    thumbnail_url = models.CharField("缩略图URL", max_length=500, blank=True, default="")
    file_size = models.BigIntegerField("文件大小（字节）", null=True, blank=True)
    latitude = models.DecimalField("拍摄位置-纬度", max_digits=12, decimal_places=8, null=True, blank=True)
    longitude = models.DecimalField("拍摄位置-经度", max_digits=12, decimal_places=8, null=True, blank=True)
    captured_at = models.DateTimeField("拍摄时间", null=True, blank=True)
    is_deleted = models.BooleanField("是否已删除", default=False)
    deleted_at = models.DateTimeField("删除时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        db_table = "media_files"
        ordering = ["-id"]
        default_permissions = ()
        permissions = [
            ("view_media_file", "可查看媒体文件"),
            ("manage_media_file", "可管理媒体文件"),
        ]

    def __str__(self):
        return f"{self.id}-{self.file_name}"

    @property
    def assigned_tenant_member_id(self):
        if self.flight_record_id is None or self.flight_record is None:
            return None
        return self.flight_record.pilot_id

    def clean(self):
        if self.pk:
            was_deleted = MediaFile.objects.filter(pk=self.pk).values_list("is_deleted", flat=True).first()
            if was_deleted and not self.is_deleted:
                raise ValidationError({"is_deleted": "媒体软删除后不可恢复"})
        if self.tenant_id and self.flight_record_id and self.flight_record.tenant_id != self.tenant_id:
            raise ValidationError({"flight_record": "flight_record 必须属于当前 tenant"})
        if self.tenant_id and self.mission_id and self.mission.tenant_id != self.tenant_id:
            raise ValidationError({"mission": "mission 必须属于当前 tenant"})
        if self.flight_record_id and self.mission_id and self.flight_record.mission_id and self.flight_record.mission_id != self.mission_id:
            raise ValidationError({"mission": "mission 与 flight_record 绑定关系不一致"})
        if self.is_deleted and self.deleted_at is None:
            raise ValidationError({"deleted_at": "逻辑删除记录必须提供 deleted_at"})
        if not self.is_deleted and self.deleted_at is not None:
            raise ValidationError({"deleted_at": "未删除记录不允许写入 deleted_at"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
