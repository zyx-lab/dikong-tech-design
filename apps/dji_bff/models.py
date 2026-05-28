from django.db import models
from django.db.models import Q


class SyncStatus(models.TextChoices):
    PENDING = "PENDING", "同步中"
    SYNCED = "SYNCED", "同步成功"
    ERROR = "ERROR", "同步失败"


class DjiCloudPlatformStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "启用"
    DISABLED = "DISABLED", "停用"
    ERROR = "ERROR", "异常"


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


class DjiCloudPlatform(models.Model):
    tenant = models.ForeignKey("access.Tenant", on_delete=models.CASCADE, related_name="dji_cloud_platforms")
    name = models.CharField("平台名称", max_length=128)
    base_url = models.CharField("DJI API Base URL", max_length=500)
    username = models.CharField("DJI 登录用户名", max_length=128)
    password = models.CharField("DJI 登录密码", max_length=256)
    login_flag = models.IntegerField("DJI 登录 flag", default=1)
    workspace_id = models.CharField("DJI workspace ID", max_length=128, blank=True, default="")
    dji_user_id = models.CharField("DJI user ID", max_length=128, blank=True, default="")
    dji_username = models.CharField("DJI 用户名", max_length=128, blank=True, default="")
    dji_user_type = models.CharField("DJI 用户类型", max_length=64, blank=True, default="")
    access_token = models.CharField("访问令牌", max_length=512, blank=True, default="")
    mqtt_username = models.CharField("MQTT 用户名", max_length=128, blank=True, default="")
    mqtt_password = models.CharField("MQTT 密码", max_length=256, blank=True, default="")
    mqtt_addr = models.CharField("MQTT 地址", max_length=256, blank=True, default="")
    expires_at = models.DateTimeField("访问令牌过期时间", null=True, blank=True)
    is_default = models.BooleanField("是否默认平台", default=False)
    status = models.CharField(
        "平台状态",
        max_length=16,
        choices=DjiCloudPlatformStatus.choices,
        default=DjiCloudPlatformStatus.ACTIVE,
    )
    last_checked_at = models.DateTimeField("最近连接检测时间", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "dji_cloud_platforms"
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "name"], name="uniq_dji_cloud_platform_tenant_name"),
            models.UniqueConstraint(
                fields=["tenant"],
                condition=Q(is_default=True),
                name="uniq_dji_cloud_platform_tenant_default",
            ),
        ]

    def __str__(self):
        return f"{self.tenant_id}:{self.name}"


class DjiDeviceIndex(models.Model):
    dji_platform = models.ForeignKey(
        "dji_bff.DjiCloudPlatform",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="device_indexes",
        verbose_name="DJI 平台",
    )
    device_sn = models.CharField("设备序列号", max_length=128)
    last_payload = models.JSONField("最近一次 DJI 原始载荷", default=dict, blank=True)
    last_seen_at = models.DateTimeField("最近见到时间", null=True, blank=True)
    firmware_version = models.CharField("固件版本", max_length=128, blank=True, default="")
    firmware_status = models.CharField("固件状态", max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "dji_device_indexes"
        ordering = ["device_sn"]
        constraints = [
            models.UniqueConstraint(
                fields=["dji_platform", "device_sn"],
                condition=Q(dji_platform__isnull=False),
                name="uniq_dji_device_index_platform_sn",
            ),
            models.UniqueConstraint(
                fields=["device_sn"],
                condition=Q(dji_platform__isnull=True),
                name="uniq_dji_device_index_legacy_sn",
            ),
        ]


class TenantRouteIndex(models.Model):
    tenant = models.ForeignKey("access.Tenant", on_delete=models.CASCADE, related_name="dji_route_indexes")
    route = models.ForeignKey("route.Route", on_delete=models.CASCADE, related_name="dji_indexes")
    dji_platform = models.ForeignKey(
        "dji_bff.DjiCloudPlatform",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="route_indexes",
        verbose_name="DJI 平台",
    )
    workspace_id = models.CharField("DJI workspace ID", max_length=128, blank=True, default="")
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
                fields=["route"],
                condition=Q(dji_platform__isnull=True),
                name="uniq_route_legacy_index",
            ),
            models.UniqueConstraint(
                fields=["route", "dji_platform"],
                condition=Q(dji_platform__isnull=False),
                name="uniq_route_platform_index",
            ),
            models.UniqueConstraint(
                fields=["dji_platform", "dji_wayline_id"],
                condition=Q(dji_platform__isnull=False) & Q(dji_wayline_id__isnull=False) & ~Q(dji_wayline_id=""),
                name="uniq_platform_dji_wayline_id",
            ),
            models.UniqueConstraint(
                fields=["tenant", "dji_wayline_id"],
                condition=Q(dji_platform__isnull=True) & Q(dji_wayline_id__isnull=False) & ~Q(dji_wayline_id=""),
                name="uniq_tenant_dji_wayline_id_legacy",
            )
        ]


class TenantMediaIndex(models.Model):
    tenant = models.ForeignKey("access.Tenant", on_delete=models.CASCADE, related_name="dji_media_indexes")
    media_file = models.OneToOneField("media_file.MediaFile", on_delete=models.CASCADE, related_name="dji_index")
    dji_platform = models.ForeignKey(
        "dji_bff.DjiCloudPlatform",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="media_indexes",
        verbose_name="DJI 平台",
    )
    workspace_id = models.CharField("DJI workspace ID", max_length=128, blank=True, default="")
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
        constraints = [
            models.UniqueConstraint(
                fields=["dji_platform", "dji_file_id"],
                condition=Q(dji_platform__isnull=False),
                name="uniq_platform_dji_file_id",
            ),
            models.UniqueConstraint(
                fields=["tenant", "dji_file_id"],
                condition=Q(dji_platform__isnull=True),
                name="uniq_tenant_dji_file_id_legacy",
            ),
        ]
