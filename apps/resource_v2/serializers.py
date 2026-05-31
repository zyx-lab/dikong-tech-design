from django.utils import timezone
from rest_framework import serializers

from apps.access.api_v1.base import StrictSerializer
from apps.access.models import DirectoryStatus
from apps.flight_record.models import FlightRecord
from apps.iam_v2.models import ResourceShareGroup, ResourceShareGroupTargetDepartment
from apps.media_file.models import MediaFile
from apps.mission.flight_state import resolve_mission_display_status
from apps.mission.models import Mission
from apps.resource_v2.models import (
    DjiConnection,
    DockResource,
    DroneResource,
    ResourceBinding,
    ResourceSharePermission,
    ResourceType,
    SHARE_PERMISSION_CHOICES,
    V2AuditLog,
)
from apps.resource_v2.services import effective_permissions_for_binding, normalize_base_url
from apps.route.models import Route


SHARE_PERMISSION_ORDER = ["view", "monitor", "dispatch_task", "review_task", "edit_config"]


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
    device_sn = _payload_string(payload, "device_sn", "dock_sn", "dockSn", "sn")
    if not device_sn:
        raise serializers.ValidationError({"device_sn": ["DJI 资源缺少稳定 SN"]})
    model = DroneResource if resource_type == ResourceType.DRONE else DockResource
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
    deviceSn = serializers.CharField()
    name = serializers.CharField()
    model = serializers.CharField()
    onlineStatus = serializers.BooleanField()
    ownerDepartment = serializers.DictField()
    effectivePermissions = serializers.ListField(child=serializers.CharField())


class V2RouteReadSerializer(serializers.ModelSerializer):
    isPublished = serializers.SerializerMethodField(method_name="get_is_published")
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = Route
        fields = ["id", "name", "isPublished", "createdAt", "updatedAt"]
        read_only_fields = fields

    def get_is_published(self, obj) -> bool:
        return bool(getattr(getattr(obj, "dji_index", None), "is_published", False))


class V2MissionReadSerializer(serializers.ModelSerializer):
    routeId = serializers.IntegerField(source="route_id", allow_null=True, read_only=True)
    routeName = serializers.CharField(source="route_name", read_only=True)
    droneId = serializers.IntegerField(source="drone_id", allow_null=True, read_only=True)
    deviceSn = serializers.CharField(source="device_sn", read_only=True)
    droneName = serializers.CharField(source="drone_name", read_only=True)
    pilotId = serializers.IntegerField(source="pilot_id", read_only=True)
    pilotName = serializers.CharField(source="pilot_name", read_only=True)
    scheduledAt = serializers.DateTimeField(source="scheduled_at", allow_null=True, read_only=True)
    startedAt = serializers.DateTimeField(source="started_at", allow_null=True, read_only=True)
    finishedAt = serializers.DateTimeField(source="finished_at", allow_null=True, read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)
    status = serializers.SerializerMethodField()

    class Meta:
        model = Mission
        fields = [
            "id",
            "name",
            "routeId",
            "routeName",
            "droneId",
            "deviceSn",
            "droneName",
            "pilotId",
            "pilotName",
            "scheduledAt",
            "startedAt",
            "finishedAt",
            "remark",
            "status",
            "createdAt",
            "updatedAt",
        ]
        read_only_fields = fields

    def get_status(self, obj) -> int:
        return resolve_mission_display_status(obj)


class V2FlightRecordReadSerializer(serializers.ModelSerializer):
    flightNo = serializers.CharField(source="flight_no", read_only=True)
    missionId = serializers.IntegerField(source="mission_id", allow_null=True, read_only=True)
    missionName = serializers.CharField(source="mission_name", read_only=True)
    routeName = serializers.CharField(source="route_name", read_only=True)
    airportName = serializers.CharField(source="airport_name", read_only=True)
    droneId = serializers.IntegerField(source="drone_id", allow_null=True, read_only=True)
    deviceSn = serializers.CharField(source="device_sn", read_only=True)
    droneName = serializers.CharField(source="drone_name", read_only=True)
    pilotId = serializers.IntegerField(source="pilot_id", allow_null=True, read_only=True)
    pilotName = serializers.CharField(source="pilot_name", read_only=True)
    startTime = serializers.DateTimeField(source="start_time", allow_null=True, read_only=True)
    endTime = serializers.DateTimeField(source="end_time", allow_null=True, read_only=True)
    flightDuration = serializers.IntegerField(source="flight_duration", allow_null=True, read_only=True)
    photoCount = serializers.IntegerField(source="photo_count", read_only=True)
    videoCount = serializers.IntegerField(source="video_count", read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = FlightRecord
        fields = [
            "id",
            "flightNo",
            "missionId",
            "missionName",
            "routeName",
            "airportName",
            "droneId",
            "deviceSn",
            "droneName",
            "pilotId",
            "pilotName",
            "startTime",
            "endTime",
            "flightDuration",
            "photoCount",
            "videoCount",
            "status",
            "createdAt",
            "updatedAt",
        ]
        read_only_fields = fields


class V2MediaFileReadSerializer(serializers.ModelSerializer):
    flightRecordId = serializers.IntegerField(source="flight_record_id", allow_null=True, read_only=True)
    missionId = serializers.IntegerField(source="mission_id", allow_null=True, read_only=True)
    deviceSn = serializers.CharField(source="device_sn", read_only=True)
    mediaType = serializers.IntegerField(source="media_type", read_only=True)
    fileName = serializers.CharField(source="file_name", read_only=True)
    thumbnailUrl = serializers.CharField(source="thumbnail_url", read_only=True)
    fileSize = serializers.IntegerField(source="file_size", allow_null=True, read_only=True)
    capturedAt = serializers.DateTimeField(source="captured_at", allow_null=True, read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = MediaFile
        fields = [
            "id",
            "flightRecordId",
            "missionId",
            "deviceSn",
            "mediaType",
            "fileName",
            "thumbnailUrl",
            "fileSize",
            "latitude",
            "longitude",
            "capturedAt",
            "createdAt",
        ]
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


def serialize_resource_binding(binding: ResourceBinding, *, context):
    resource_model = DroneResource if binding.resource_type == ResourceType.DRONE else DockResource
    resource = resource_model.objects.get(pk=binding.resource_object_id)
    return {
        "id": resource.id,
        "resourceType": binding.resource_type,
        "bindingId": binding.id,
        "deviceSn": resource.device_sn,
        "name": resource.name,
        "model": resource.model,
        "onlineStatus": resource.online_status,
        "ownerDepartment": {
            "id": binding.owner_department_id,
            "name": binding.owner_department.name,
            "path": binding.owner_department.path,
        },
        "effectivePermissions": effective_permissions_for_binding(context, binding),
    }
