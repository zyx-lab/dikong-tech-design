from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from apps.access.api_base import StrictSerializer
from apps.access.models import DirectoryStatus
from apps.iam_v2.models import ResourceShareGroup, ResourceShareGroupTargetDepartment
from apps.resource_v2.models import (
    DjiConnection,
    DockResource,
    DroneTelemetrySnapshot,
    DroneResource,
    GatewayResource,
    MqttConnectionHealth,
    MqttLatestMessage,
    PayloadResource,
    ResourceBinding,
    ResourceSharePermission,
    ResourceType,
    SHARE_PERMISSION_CHOICES,
    V2AuditLog,
)
from apps.resource_v2.mqtt import read_redis_health
from apps.resource_v2.services import effective_permissions_for_binding, normalize_base_url


SHARE_PERMISSION_ORDER = ["view", "monitor", "dispatch_task", "use", "review_task", "edit_config"]


def normalize_share_permissions(value):
    normalized = []
    seen = set()
    for item in value:
        permission = str(item).strip()
        if not permission or permission in seen:
            continue
        seen.add(permission)
        normalized.append(permission)

    invalid = sorted(set(normalized) - SHARE_PERMISSION_CHOICES)
    if invalid:
        raise serializers.ValidationError(f"不支持的资源共享权限: {', '.join(invalid)}")
    if not normalized:
        raise serializers.ValidationError("资源共享权限不能为空")
    return [permission for permission in SHARE_PERMISSION_ORDER if permission in set(normalized)]


def _payload_string(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _payload_online(payload: dict) -> bool:
    value = payload.get("online_status", payload.get("status", False))
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "online", "active"}
    return False


def _resource_defaults(payload: dict) -> dict:
    return {
        "name": _payload_string(payload, "name", "device_name", "nickname") or _payload_string(payload, "device_sn"),
        "model": _payload_string(payload, "model", "device_model", "product_type", "type"),
        "online_status": _payload_online(payload),
        "firmware_version": _payload_string(payload, "firmware_version", "firmwareVersion"),
        "firmware_status": _payload_string(payload, "firmware_status", "firmwareStatus"),
        "last_payload": payload,
        "last_seen_at": timezone.now(),
    }


def upsert_resource_from_payload(resource_type: str, payload: dict):
    resolved_type = ResourceType(resource_type)
    if resolved_type == ResourceType.PAYLOAD:
        payload_sn = _payload_string(payload, "payload_sn", "payloadSn", "device_sn", "sn")
        if not payload_sn:
            raise serializers.ValidationError({"payload_sn": ["DJI 负载资源缺少稳定 SN"]})
        defaults = _resource_defaults(payload)
        defaults["payload_type"] = _payload_string(payload, "payload_type", "payloadType", "type")
        resource, _created = PayloadResource.objects.update_or_create(payload_sn=payload_sn, defaults=defaults)
        return resource

    device_sn = _payload_string(payload, "device_sn", "dock_sn", "dockSn", "sn")
    if not device_sn:
        raise serializers.ValidationError({"device_sn": ["DJI 资源缺少稳定 SN"]})
    model = {
        ResourceType.DRONE: DroneResource,
        ResourceType.DOCK: DockResource,
        ResourceType.GATEWAY: GatewayResource,
    }[resolved_type]
    resource, _created = model.objects.update_or_create(device_sn=device_sn, defaults=_resource_defaults(payload))
    return resource


class DjiConnectionReadSerializer(serializers.ModelSerializer):
    ownerDepartmentId = serializers.IntegerField(source="owner_department_id", read_only=True)
    baseUrl = serializers.CharField(source="base_url", read_only=True)
    loginFlag = serializers.IntegerField(source="login_flag", read_only=True)
    workspaceId = serializers.CharField(source="workspace_id", read_only=True)
    lastCheckedAt = serializers.DateTimeField(source="last_checked_at", allow_null=True, read_only=True)

    class Meta:
        model = DjiConnection
        fields = [
            "id",
            "ownerDepartmentId",
            "name",
            "baseUrl",
            "username",
            "loginFlag",
            "status",
            "workspaceId",
            "lastCheckedAt",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class DjiConnectionCredentialReadSerializer(DjiConnectionReadSerializer):
    password = serializers.CharField(read_only=True)

    class Meta(DjiConnectionReadSerializer.Meta):
        fields = DjiConnectionReadSerializer.Meta.fields + ["password"]
        read_only_fields = fields


class DjiConnectionWriteSerializer(StrictSerializer):
    name = serializers.CharField(max_length=128)
    baseUrl = serializers.CharField(max_length=500)
    username = serializers.CharField(max_length=128)
    password = serializers.CharField(max_length=256)
    loginFlag = serializers.IntegerField(required=False, default=1)
    ownerDepartmentId = serializers.IntegerField(required=False, min_value=1)
    extraParams = serializers.JSONField(required=False)

    def validate_baseUrl(self, value):
        normalized = normalize_base_url(value)
        if not normalized.startswith(("http://", "https://")):
            raise serializers.ValidationError("baseUrl 必须以 http:// 或 https:// 开头")
        return normalized

    def create(self, validated_data):
        return DjiConnection.objects.create(
            owner_department=validated_data["owner_department"],
            name=validated_data["name"],
            base_url=validated_data["baseUrl"],
            username=validated_data["username"],
            password=validated_data["password"],
            login_flag=validated_data.get("loginFlag", 1),
            extra_params=validated_data.get("extraParams", {}),
            created_by_user=self.context["request"].user,
        )

    def update(self, instance, validated_data):
        owner_department = validated_data.get("owner_department")
        if owner_department is not None:
            instance.owner_department = owner_department
        instance.name = validated_data["name"]
        instance.base_url = validated_data["baseUrl"]
        instance.username = validated_data["username"]
        instance.password = validated_data["password"]
        instance.login_flag = validated_data.get("loginFlag", instance.login_flag)
        instance.extra_params = validated_data.get("extraParams", instance.extra_params)
        update_fields = ["name", "base_url", "username", "password", "login_flag", "extra_params", "updated_at"]
        if owner_department is not None:
            update_fields.append("owner_department")
        instance.save(update_fields=update_fields)
        return instance


class ResourceReadSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    resourceType = serializers.CharField()
    bindingId = serializers.IntegerField()
    djiConnectionId = serializers.IntegerField()
    djiConnectionName = serializers.CharField()
    deviceSn = serializers.CharField()
    name = serializers.CharField()
    model = serializers.CharField()
    onlineStatus = serializers.BooleanField()
    lastSeenAt = serializers.DateTimeField(required=False, allow_null=True)
    latestTelemetry = serializers.DictField(required=False, allow_null=True)
    ownerDepartment = serializers.DictField()
    effectivePermissions = serializers.ListField(child=serializers.CharField())


class MqttConnectionHealthReadSerializer(serializers.ModelSerializer):
    connectionId = serializers.IntegerField(source="dji_connection_id", read_only=True)
    connectionName = serializers.CharField(source="dji_connection.name", read_only=True)
    ownerDepartmentId = serializers.IntegerField(source="dji_connection.owner_department_id", read_only=True)
    workerId = serializers.CharField(source="worker_id", read_only=True)
    mqttAddr = serializers.CharField(source="mqtt_addr", read_only=True)
    subscribedTopics = serializers.JSONField(source="subscribed_topics", read_only=True)
    lastConnectedAt = serializers.DateTimeField(source="last_connected_at", allow_null=True, read_only=True)
    lastSubscribedAt = serializers.DateTimeField(source="last_subscribed_at", allow_null=True, read_only=True)
    lastMessageAt = serializers.DateTimeField(source="last_message_at", allow_null=True, read_only=True)
    lastHeartbeatAt = serializers.DateTimeField(source="last_heartbeat_at", allow_null=True, read_only=True)
    lastError = serializers.CharField(source="last_error", read_only=True)
    messageCount = serializers.IntegerField(source="message_count", read_only=True)
    redisState = serializers.SerializerMethodField()

    class Meta:
        model = MqttConnectionHealth
        fields = [
            "connectionId",
            "connectionName",
            "ownerDepartmentId",
            "status",
            "workerId",
            "mqttAddr",
            "subscribedTopics",
            "lastConnectedAt",
            "lastSubscribedAt",
            "lastMessageAt",
            "lastHeartbeatAt",
            "lastError",
            "messageCount",
            "redisState",
        ]
        read_only_fields = fields

    def get_redisState(self, obj) -> dict:
        return read_redis_health(obj.dji_connection_id)


class MqttLatestMessageReadSerializer(serializers.ModelSerializer):
    connectionId = serializers.IntegerField(source="dji_connection_id", read_only=True)
    topicKind = serializers.CharField(source="topic_kind", read_only=True)
    deviceSn = serializers.CharField(source="device_sn", read_only=True)
    receivedAt = serializers.DateTimeField(source="received_at", read_only=True)
    rawPayload = serializers.JSONField(source="raw_payload", read_only=True)

    class Meta:
        model = MqttLatestMessage
        fields = ["connectionId", "topic", "topicKind", "deviceSn", "receivedAt", "sequence", "rawPayload"]
        read_only_fields = fields


class BindingCreateSerializer(StrictSerializer):
    resourceType = serializers.ChoiceField(choices=ResourceType.choices)
    resourceId = serializers.IntegerField(min_value=1)
    djiConnectionId = serializers.IntegerField(min_value=1)


class BindingReadSerializer(serializers.ModelSerializer):
    resourceType = serializers.CharField(source="resource_type", read_only=True)
    resourceId = serializers.IntegerField(source="resource_object_id", read_only=True)
    ownerDepartmentId = serializers.IntegerField(source="owner_department_id", read_only=True)
    djiConnectionId = serializers.IntegerField(source="dji_connection_id", read_only=True)

    class Meta:
        model = ResourceBinding
        fields = [
            "id",
            "resourceType",
            "resourceId",
            "ownerDepartmentId",
            "djiConnectionId",
            "status",
            "bound_at",
            "unbound_at",
        ]
        read_only_fields = fields


class AuditLogReadSerializer(serializers.ModelSerializer):
    actorDepartmentId = serializers.IntegerField(source="actor_department_id", allow_null=True, read_only=True)
    resourceOwnerDepartmentId = serializers.IntegerField(source="resource_owner_department_id", allow_null=True, read_only=True)
    resourceType = serializers.CharField(source="resource_type", read_only=True)
    resourceObjectId = serializers.CharField(source="resource_object_id", read_only=True)
    targetType = serializers.CharField(source="target_type", read_only=True)
    targetId = serializers.CharField(source="target_id", read_only=True)

    class Meta:
        model = V2AuditLog
        fields = [
            "id",
            "action",
            "actorDepartmentId",
            "resourceOwnerDepartmentId",
            "resourceType",
            "resourceObjectId",
            "targetType",
            "targetId",
            "before_data",
            "after_data",
            "created_at",
        ]
        read_only_fields = fields


class ShareGroupCreateSerializer(StrictSerializer):
    ownerDepartmentId = serializers.IntegerField(required=False, min_value=1)
    name = serializers.CharField(max_length=128)


class ShareGroupUpdateSerializer(StrictSerializer):
    name = serializers.CharField(max_length=128)
    status = serializers.ChoiceField(choices=DirectoryStatus.choices)


class ShareGroupTargetCreateSerializer(StrictSerializer):
    departmentId = serializers.IntegerField(min_value=1)


class ShareGroupResourceCreateSerializer(StrictSerializer):
    resourceType = serializers.ChoiceField(choices=ResourceType.choices)
    resourceId = serializers.IntegerField(min_value=1)
    permissions = serializers.ListField(child=serializers.CharField(), allow_empty=False)

    def validate_permissions(self, value):
        return normalize_share_permissions(value)


class ShareGroupResourceUpdateSerializer(StrictSerializer):
    permissions = serializers.ListField(child=serializers.CharField(), allow_empty=False)

    def validate_permissions(self, value):
        return normalize_share_permissions(value)


class ShareGroupReadSerializer(serializers.ModelSerializer):
    ownerDepartmentId = serializers.IntegerField(source="owner_department_id", read_only=True)

    class Meta:
        model = ResourceShareGroup
        fields = ["id", "ownerDepartmentId", "name", "status", "created_at", "updated_at"]
        read_only_fields = fields


class ShareGroupTargetReadSerializer(serializers.ModelSerializer):
    shareGroupId = serializers.IntegerField(source="share_group_id", read_only=True)
    departmentId = serializers.IntegerField(source="department_id", read_only=True)
    departmentName = serializers.CharField(source="department.name", read_only=True)
    departmentPath = serializers.CharField(source="department.path", read_only=True)

    class Meta:
        model = ResourceShareGroupTargetDepartment
        fields = ["id", "shareGroupId", "departmentId", "departmentName", "departmentPath", "created_at"]
        read_only_fields = fields


class ShareGroupResourceReadSerializer(serializers.ModelSerializer):
    shareGroupId = serializers.IntegerField(source="share_group_id", read_only=True)
    resourceType = serializers.CharField(source="resource_type", read_only=True)
    resourceId = serializers.IntegerField(source="resource_object_id", read_only=True)

    class Meta:
        model = ResourceSharePermission
        fields = ["id", "shareGroupId", "resourceType", "resourceId", "permissions", "created_at", "updated_at"]
        read_only_fields = fields


def _telemetry_is_stale(snapshot: DroneTelemetrySnapshot) -> bool:
    freshness_seconds = max(0, int(getattr(settings, "DJI_MQTT_OSD_FRESHNESS_SECONDS", 10)))
    return (timezone.now() - snapshot.reported_at).total_seconds() > freshness_seconds


def _drone_latest_telemetry(resource) -> dict | None:
    try:
        snapshot = resource.latest_telemetry
    except DroneTelemetrySnapshot.DoesNotExist:
        return None
    return {
        "latitude": str(snapshot.latitude) if snapshot.latitude is not None else None,
        "longitude": str(snapshot.longitude) if snapshot.longitude is not None else None,
        "altitude": str(snapshot.altitude) if snapshot.altitude is not None else None,
        "speed": str(snapshot.speed) if snapshot.speed is not None else None,
        "heading": str(snapshot.heading) if snapshot.heading is not None else None,
        "batteryPercent": snapshot.battery_percent,
        "reportedAt": snapshot.reported_at,
        "isStale": _telemetry_is_stale(snapshot),
        "rawPayload": snapshot.raw_payload,
    }


def serialize_resource_binding(binding: ResourceBinding, *, context):
    resource_model = {
        ResourceType.DRONE: DroneResource,
        ResourceType.DOCK: DockResource,
        ResourceType.GATEWAY: GatewayResource,
        ResourceType.PAYLOAD: PayloadResource,
    }[ResourceType(binding.resource_type)]
    resource = resource_model.objects.get(pk=binding.resource_object_id)
    payload = {
        "id": resource.id,
        "resourceType": binding.resource_type,
        "bindingId": binding.id,
        "djiConnectionId": binding.dji_connection_id,
        "djiConnectionName": binding.dji_connection.name,
        "deviceSn": resource.device_sn,
        "name": resource.name,
        "model": resource.model,
        "onlineStatus": resource.online_status,
        "lastSeenAt": resource.last_seen_at,
        "ownerDepartment": {
            "id": binding.owner_department_id,
            "name": binding.owner_department.name,
            "path": binding.owner_department.path,
        },
        "effectivePermissions": effective_permissions_for_binding(context, binding),
    }
    if binding.resource_type == ResourceType.DRONE:
        payload["latestTelemetry"] = _drone_latest_telemetry(resource)
    return payload
