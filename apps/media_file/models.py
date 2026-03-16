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

    def clean(self):
        if self.tenant_id and self.flight_record_id and self.flight_record.tenant_id != self.tenant_id:
            raise ValidationError({"flight_record": "flight_record 必须属于当前 tenant"})
        if self.is_deleted and self.deleted_at is None:
            raise ValidationError({"deleted_at": "逻辑删除记录必须提供 deleted_at"})
        if not self.is_deleted and self.deleted_at is not None:
            raise ValidationError({"deleted_at": "未删除记录不允许写入 deleted_at"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
