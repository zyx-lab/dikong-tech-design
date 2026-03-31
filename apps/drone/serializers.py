from datetime import datetime
from typing import Any

from rest_framework import serializers

from apps.api_v1.serializers import RejectUnknownFieldsMixin
from apps.api_v1.tenant_scope import require_request_tenant
from apps.dji_bff.models import DjiDeviceIndex
from apps.drone.models import Drone


def _payload_string(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    children = payload.get("children")
    if isinstance(children, list):
        for child in children:
            if not isinstance(child, dict):
                continue
            for key in keys:
                value = child.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return ""


def _device_index_for_sn(device_sn: str) -> DjiDeviceIndex | None:
    if not device_sn:
        return None
    return DjiDeviceIndex.objects.filter(device_sn=device_sn).first()


class DroneReadSerializer(serializers.ModelSerializer):
    last_seen_at = serializers.SerializerMethodField()
    last_payload = serializers.SerializerMethodField()
    firmware_version = serializers.SerializerMethodField()
    firmware_status = serializers.SerializerMethodField()

    class Meta:
        model = Drone
        fields = [
            "id",
            "code",
            "name",
            "model",
            "device_sn",
            "status",
            "org_id",
            "created_by_tenant_member_id",
            "last_seen_at",
            "firmware_version",
            "firmware_status",
            "last_payload",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def _device_index(self, obj) -> DjiDeviceIndex | None:
        return _device_index_for_sn(obj.device_sn)

    def get_last_seen_at(self, obj) -> datetime | None:
        device_index = self._device_index(obj)
        return device_index.last_seen_at if device_index is not None else None

    def get_last_payload(self, obj) -> dict[str, Any]:
        device_index = self._device_index(obj)
        return device_index.last_payload if device_index is not None else {}

    def get_firmware_version(self, obj) -> str:
        device_index = self._device_index(obj)
        return device_index.firmware_version if device_index is not None else ""

    def get_firmware_status(self, obj) -> str:
        device_index = self._device_index(obj)
        return device_index.firmware_status if device_index is not None else ""


class DroneClaimSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    name = serializers.CharField(required=False, allow_blank=True)
    model = serializers.CharField(required=False, allow_blank=True)
    org_id = serializers.IntegerField(required=False, allow_null=True)

    class Meta:
        model = Drone
        fields = [
            "code",
            "device_sn",
            "name",
            "model",
            "org_id",
        ]
        extra_kwargs = {
            "code": {"help_text": "租户内展示编码，租户内唯一。"},
            "device_sn": {"help_text": "已绑定 DJI 平台的共享设备序列号。"},
            "name": {"required": False, "help_text": "本地展示名称，可为空。"},
            "model": {"required": False, "help_text": "本地展示型号，可为空。"},
            "org_id": {"required": False, "help_text": "业务组织 ID，可为空。"},
        }

    def validate(self, attrs):
        attrs = super().validate(attrs)

        current_tenant = require_request_tenant(self.context)
        code = attrs.get("code")
        device_sn = attrs.get("device_sn")
        device_index = _device_index_for_sn(device_sn)

        if device_index is None:
            raise serializers.ValidationError({"device_sn": "已绑定设备池中不存在该 device_sn"})

        if code and Drone.objects.filter(tenant=current_tenant, code=code).exists():
            raise serializers.ValidationError({"code": "当前租户下已存在相同业务编码"})

        claimed_drone = Drone.objects.filter(device_sn=device_sn).first()
        if claimed_drone is not None:
            if claimed_drone.tenant_id == current_tenant.id:
                raise serializers.ValidationError({"device_sn": "当前租户下已认领该设备"})
            raise serializers.ValidationError({"device_sn": "该设备已被其他租户认领"})

        self._device_index = device_index
        return attrs

    def create(self, validated_data):
        device_index = getattr(self, "_device_index", None)
        payload = device_index.last_payload if device_index is not None else {}
        validated_data["name"] = (
            validated_data.get("name")
            or _payload_string(payload, "name", "device_name", "nickname")
            or validated_data["code"]
        )
        validated_data["model"] = (
            validated_data.get("model")
            or _payload_string(payload, "model", "device_model", "product_type")
            or "unknown"
        )
        return super().create(validated_data)


class DroneUpdateSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    org_id = serializers.IntegerField(required=False, allow_null=True)

    class Meta:
        model = Drone
        fields = [
            "code",
            "name",
            "model",
            "org_id",
        ]
        extra_kwargs = {
            "code": {"help_text": "租户内展示编码，租户内唯一。"},
            "name": {"help_text": "本地展示名称。"},
            "model": {"help_text": "本地展示型号。"},
            "org_id": {"help_text": "业务组织 ID，可为空。"},
        }

    def validate(self, attrs):
        attrs = super().validate(attrs)

        current_tenant = require_request_tenant(self.context)
        instance = getattr(self, "instance", None)
        code = attrs.get("code", instance.code if instance is not None else None)

        if code and Drone.objects.filter(tenant=current_tenant, code=code).exclude(pk=getattr(instance, "pk", None)).exists():
            raise serializers.ValidationError({"code": "当前租户下已存在相同业务编码"})
        return attrs


class AvailableDroneReadSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    model = serializers.SerializerMethodField()

    class Meta:
        model = DjiDeviceIndex
        fields = [
            "device_sn",
            "name",
            "model",
            "firmware_version",
            "firmware_status",
            "last_seen_at",
        ]
        read_only_fields = fields

    def get_name(self, obj) -> str:
        payload = obj.last_payload or {}
        return _payload_string(payload, "name", "device_name", "nickname") or obj.device_sn

    def get_model(self, obj) -> str:
        payload = obj.last_payload or {}
        return _payload_string(payload, "model", "device_model", "product_type") or "unknown"


class DroneLiveStartSerializer(serializers.Serializer):
    camera_index = serializers.CharField(help_text="camera.index，来自 live/capacity 返回。")
    video_index = serializers.CharField(help_text="video.index，来自 live/capacity 返回。")
    url_type = serializers.IntegerField(required=False, default=1, help_text="直播 URL 类型，默认 1。")


class DroneLiveStopSerializer(serializers.Serializer):
    video_id = serializers.CharField(required=False, allow_blank=True, help_text="要停止的 video_id，可为空。")


class DroneLiveVideoQualitySerializer(serializers.Serializer):
    video_id = serializers.CharField(required=False, allow_blank=True, help_text="要调整的 video_id，可为空。")
    quality = serializers.CharField(help_text="目标画质，例如 720p、1080p。")


class DroneLiveVideoSourceSerializer(serializers.Serializer):
    video_id = serializers.CharField(help_text="DJI 实测 video_id，格式 {device_sn}/{camera.index}/{video.index}。")
    videoType = serializers.CharField(help_text="上游视频源类型，保持 DJI 原始语义。")
