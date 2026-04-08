from django.db import models
from django.db.models import Q


class SyncStatus(models.TextChoices):
    PENDING = "PENDING", "同步中"
    SYNCED = "SYNCED", "同步成功"
    ERROR = "ERROR", "同步失败"


class DjiWorkspaceConfig(models.Model):
    workspace_id = models.CharField("DJI workspace ID", max_length=128, unique=True)
    dji_user_id = models.CharField("DJI user ID", max_length=128, blank=True, default="")
    dji_username = models.CharField("DJI 用户名", max_length=128, blank=True, default="")
    dji_user_type = models.CharField("DJI 用户类型", max_length=64, blank=True, default="")
    access_token = models.CharField("访问令牌", max_length=512, blank=True, default="")
    mqtt_username = models.CharField("MQTT 用户名", max_length=128, blank=True, default="")
    mqtt_password = models.CharField("MQTT 密码", max_length=256, blank=True, default="")
    mqtt_addr = models.CharField("MQTT 地址", max_length=256, blank=True, default="")
    expires_at = models.DateTimeField("访问令牌过期时间", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "dji_workspace_configs"
        ordering = ["-id"]


class DjiDeviceIndex(models.Model):
    device_sn = models.CharField("设备序列号", max_length=128, unique=True)
    last_payload = models.JSONField("最近一次 DJI 原始载荷", default=dict, blank=True)
    last_seen_at = models.DateTimeField("最近见到时间", null=True, blank=True)
    firmware_version = models.CharField("固件版本", max_length=128, blank=True, default="")
    firmware_status = models.CharField("固件状态", max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "dji_device_indexes"
        ordering = ["device_sn"]


class TenantRouteIndex(models.Model):
    tenant = models.ForeignKey("access.Tenant", on_delete=models.CASCADE, related_name="dji_route_indexes")
    route = models.OneToOneField("route.Route", on_delete=models.CASCADE, related_name="dji_index")
    dji_wayline_id = models.CharField("DJI 航线 ID", max_length=128, blank=True, default="")
    download_url = models.CharField("DJI 航线下载地址", max_length=500, blank=True, default="")
    is_published = models.BooleanField("是否已发布", default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenant_route_indexes"
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "dji_wayline_id"],
                condition=Q(dji_wayline_id__isnull=False) & ~Q(dji_wayline_id=""),
                name="uniq_tenant_dji_wayline_id",
            )
        ]


class TenantMediaIndex(models.Model):
    tenant = models.ForeignKey("access.Tenant", on_delete=models.CASCADE, related_name="dji_media_indexes")
    media_file = models.OneToOneField("media_file.MediaFile", on_delete=models.CASCADE, related_name="dji_index")
    dji_file_id = models.CharField("DJI 文件 ID", max_length=128)
    device_sn = models.CharField("设备序列号", max_length=128, blank=True, default="")
    mission = models.ForeignKey(
        "mission.Mission",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dji_media_indexes",
    )
    sync_status = models.CharField("同步状态", max_length=32, choices=SyncStatus.choices, default=SyncStatus.PENDING)
    last_sync_at = models.DateTimeField("最近同步时间", null=True, blank=True)
    error_msg = models.CharField("同步错误", max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenant_media_indexes"
        ordering = ["-id"]
        constraints = [models.UniqueConstraint(fields=["tenant", "dji_file_id"], name="uniq_tenant_dji_file_id")]
