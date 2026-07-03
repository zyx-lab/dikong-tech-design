from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.access.models import TimeStampedModel
from apps.iam_v2.models import Department


class DjiConnectionStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "启用"
    DISABLED = "DISABLED", "停用"
    ERROR = "ERROR", "异常"


class MqttHealthStatus(models.TextChoices):
    DISCONNECTED = "DISCONNECTED", "未连接"
    CONNECTING = "CONNECTING", "连接中"
    SUBSCRIBED = "SUBSCRIBED", "已订阅"
    MESSAGE_RECEIVED = "MESSAGE_RECEIVED", "已收包"
    ERROR = "ERROR", "异常"


class ResourceType(models.TextChoices):
    DRONE = "drone", "无人机"
    DOCK = "dock", "机场"
    GATEWAY = "gateway", "执行端/网关"
    PAYLOAD = "payload", "负载"


class BindingStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "已绑定"
    UNBOUND = "UNBOUND", "已解绑"


class BindingActionType(models.TextChoices):
    BIND = "bind", "绑定"
    UNBIND = "unbind", "解绑"


SHARE_PERMISSION_CHOICES = {"view", "monitor", "dispatch_task", "use", "review_task", "edit_config"}


class DjiConnection(TimeStampedModel):
    owner_department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="dji_connections")
    name = models.CharField(max_length=128)
    base_url = models.CharField(max_length=500)
    username = models.CharField(max_length=128)
    password = models.CharField(max_length=256)
    login_flag = models.IntegerField(default=1)
    extra_params = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=DjiConnectionStatus.choices, default=DjiConnectionStatus.ACTIVE)
    created_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_v2_dji_connections",
    )
    workspace_id = models.CharField(max_length=128, blank=True, default="")
    dji_user_id = models.CharField(max_length=128, blank=True, default="")
    dji_username = models.CharField(max_length=128, blank=True, default="")
    dji_user_type = models.CharField(max_length=64, blank=True, default="")
    access_token = models.CharField(max_length=512, blank=True, default="")
    mqtt_username = models.CharField(max_length=128, blank=True, default="")
    mqtt_password = models.CharField(max_length=256, blank=True, default="")
    mqtt_addr = models.CharField(max_length=256, blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "v2_dji_connections"
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(fields=["owner_department", "name"], name="uniq_v2_dji_connection_owner_name"),
        ]

    def __str__(self):
        return f"{self.owner_department_id}:{self.name}"


class MqttConnectionHealth(TimeStampedModel):
    dji_connection = models.OneToOneField(DjiConnection, on_delete=models.CASCADE, related_name="mqtt_health")
    status = models.CharField(max_length=32, choices=MqttHealthStatus.choices, default=MqttHealthStatus.DISCONNECTED)
    worker_id = models.CharField(max_length=128, blank=True, default="")
    mqtt_addr = models.CharField(max_length=256, blank=True, default="")
    subscribed_topics = models.JSONField(default=list, blank=True)
    last_connected_at = models.DateTimeField(null=True, blank=True)
    last_subscribed_at = models.DateTimeField(null=True, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    message_count = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = "v2_mqtt_connection_health"
        ordering = ["-updated_at", "-id"]


class DroneResource(TimeStampedModel):
    device_sn = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=128, blank=True, default="")
    model = models.CharField(max_length=128, blank=True, default="")
    online_status = models.BooleanField(default=False)
    firmware_version = models.CharField(max_length=128, blank=True, default="")
    firmware_status = models.CharField(max_length=64, blank=True, default="")
    last_payload = models.JSONField(default=dict, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "v2_drone_resources"
        ordering = ["device_sn"]

    def __str__(self):
        return self.device_sn


class DroneTelemetrySnapshot(TimeStampedModel):
    drone = models.OneToOneField(DroneResource, on_delete=models.CASCADE, related_name="latest_telemetry")
    dji_connection = models.ForeignKey(DjiConnection, null=True, blank=True, on_delete=models.SET_NULL, related_name="drone_telemetry_snapshots")
    latitude = models.DecimalField(max_digits=12, decimal_places=8, null=True, blank=True)
    longitude = models.DecimalField(max_digits=12, decimal_places=8, null=True, blank=True)
    altitude = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    speed = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    heading = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    battery_percent = models.PositiveIntegerField(null=True, blank=True)
    reported_at = models.DateTimeField()
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "v2_drone_telemetry_snapshots"
        ordering = ["-reported_at", "-id"]


class DockResource(TimeStampedModel):
    device_sn = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=128, blank=True, default="")
    model = models.CharField(max_length=128, blank=True, default="")
    online_status = models.BooleanField(default=False)
    firmware_version = models.CharField(max_length=128, blank=True, default="")
    firmware_status = models.CharField(max_length=64, blank=True, default="")
    last_payload = models.JSONField(default=dict, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "v2_dock_resources"
        ordering = ["device_sn"]

    def __str__(self):
        return self.device_sn


class GatewayResource(TimeStampedModel):
    device_sn = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=128, blank=True, default="")
    model = models.CharField(max_length=128, blank=True, default="")
    online_status = models.BooleanField(default=False)
    firmware_version = models.CharField(max_length=128, blank=True, default="")
    firmware_status = models.CharField(max_length=64, blank=True, default="")
    last_payload = models.JSONField(default=dict, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "v2_gateway_resources"
        ordering = ["device_sn"]

    def __str__(self):
        return self.device_sn


class PayloadResource(TimeStampedModel):
    payload_sn = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=128, blank=True, default="")
    model = models.CharField(max_length=128, blank=True, default="")
    payload_type = models.CharField(max_length=64, blank=True, default="")
    online_status = models.BooleanField(default=False)
    firmware_version = models.CharField(max_length=128, blank=True, default="")
    firmware_status = models.CharField(max_length=64, blank=True, default="")
    last_payload = models.JSONField(default=dict, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "v2_payload_resources"
        ordering = ["payload_sn"]

    @property
    def device_sn(self):
        return self.payload_sn

    def __str__(self):
        return self.payload_sn


class MqttLatestMessage(TimeStampedModel):
    dji_connection = models.ForeignKey(DjiConnection, on_delete=models.CASCADE, related_name="mqtt_latest_messages")
    topic = models.CharField(max_length=512)
    topic_kind = models.CharField(max_length=64, blank=True, default="")
    device_sn = models.CharField(max_length=128, blank=True, default="")
    received_at = models.DateTimeField()
    sequence = models.PositiveBigIntegerField(default=0)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "v2_mqtt_latest_messages"
        ordering = ["-received_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["dji_connection", "topic", "device_sn"], name="uniq_v2_mqtt_latest_connection_topic_device"),
        ]
        indexes = [
            models.Index(fields=["dji_connection", "topic_kind", "device_sn"], name="idx_v2_mqtt_latest_filter"),
            models.Index(fields=["device_sn", "received_at"], name="idx_v2_mqtt_latest_device_time"),
        ]


class ResourceBinding(TimeStampedModel):
    resource_type = models.CharField(max_length=16, choices=ResourceType.choices)
    resource_object_id = models.BigIntegerField()
    owner_department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="resource_bindings")
    dji_connection = models.ForeignKey(DjiConnection, on_delete=models.PROTECT, related_name="resource_bindings")
    status = models.CharField(max_length=16, choices=BindingStatus.choices, default=BindingStatus.ACTIVE)
    bound_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bound_v2_resources",
    )
    bound_at = models.DateTimeField(null=True, blank=True)
    unbound_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="unbound_v2_resources",
    )
    unbound_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "v2_resource_bindings"
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["resource_type", "resource_object_id"],
                condition=Q(status=BindingStatus.ACTIVE),
                name="uniq_v2_active_resource_binding",
            ),
        ]
        indexes = [
            models.Index(fields=["resource_type", "resource_object_id", "status"], name="idx_v2_binding_resource"),
            models.Index(fields=["owner_department", "status"], name="idx_v2_binding_owner_status"),
        ]

    def __str__(self):
        return f"{self.resource_type}:{self.resource_object_id}:{self.status}"

    @property
    def dji_workspace_id(self) -> str:
        return self.dji_connection.workspace_id

    @property
    def dji_connection_name(self) -> str:
        return self.dji_connection.name

    @property
    def owner_department_name(self) -> str:
        return self.owner_department.name

    @property
    def owner_department_path(self) -> str:
        return self.owner_department.path


class ResourceBindingHistory(models.Model):
    action_type = models.CharField(max_length=16, choices=BindingActionType.choices)
    resource_type = models.CharField(max_length=16, choices=ResourceType.choices)
    resource_object_id = models.BigIntegerField()
    resource_identifier = models.CharField(max_length=128)
    previous_department = models.ForeignKey(
        Department,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="previous_binding_histories",
    )
    new_department = models.ForeignKey(
        Department,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="new_binding_histories",
    )
    dji_connection_snapshot = models.JSONField(default=dict, blank=True)
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="v2_binding_histories",
    )
    actor_department = models.ForeignKey(
        Department,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="actor_binding_histories",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "v2_resource_binding_histories"
        ordering = ["-created_at", "-id"]


class ResourceSharePermission(TimeStampedModel):
    share_group = models.ForeignKey("iam_v2.ResourceShareGroup", on_delete=models.CASCADE, related_name="resource_permissions")
    resource_type = models.CharField(max_length=16, choices=ResourceType.choices)
    resource_object_id = models.BigIntegerField()
    permissions = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "v2_resource_share_permissions"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["share_group", "resource_type", "resource_object_id"], name="uniq_v2_share_group_resource"),
        ]

    def clean(self):
        if not isinstance(self.permissions, list):
            raise ValidationError({"permissions": "资源共享权限必须是列表"})
        invalid = sorted(set(self.permissions or []) - SHARE_PERMISSION_CHOICES)
        if invalid:
            raise ValidationError({"permissions": f"不支持的资源共享权限: {', '.join(invalid)}"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
