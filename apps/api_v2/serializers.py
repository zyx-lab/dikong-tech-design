from rest_framework import serializers

from apps.api_v1.serializers import RejectUnknownFieldsMixin
from apps.api_v1.tenant_scope import require_request_tenant
from apps.dji_bff.models import DjiCloudPlatform
from apps.drone.models import Drone, DroneStatus
from apps.drone.serializers import AvailableDroneReadSerializer, DroneReadSerializer, DroneUpdateSerializer, _payload_string
from apps.flight_record.serializers import FlightRecordDetailSerializer, FlightRecordSummarySerializer
from apps.media_file.serializers import MediaFileBindMissionSerializer, MediaFileReadSerializer
from apps.mission.serializers import MissionCreateSerializer, MissionReadSerializer, MissionUpdateSerializer
from apps.route.serializers import RouteCreateSerializer, RouteReadSerializer, RouteUpdateJsonSerializer, RouteUpdateSerializer


def normalize_base_url(value: str) -> str:
    return str(value or "").strip().rstrip("/")


class DjiCloudPlatformReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = DjiCloudPlatform
        fields = [
            "id",
            "name",
            "base_url",
            "username",
            "login_flag",
            "workspace_id",
            "dji_user_id",
            "dji_username",
            "dji_user_type",
            "mqtt_username",
            "mqtt_addr",
            "is_default",
            "status",
            "last_checked_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class DjiCloudPlatformWriteSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    name = serializers.CharField(max_length=128)
    base_url = serializers.CharField(max_length=500)
    username = serializers.CharField(max_length=128)
    password = serializers.CharField(max_length=256, write_only=True)
    login_flag = serializers.IntegerField(required=False, default=1)
    is_default = serializers.BooleanField(required=False, default=False)

    class Meta:
        model = DjiCloudPlatform
        fields = ["name", "base_url", "username", "password", "login_flag", "is_default"]

    def validate_base_url(self, value):
        normalized = normalize_base_url(value)
        if not normalized.startswith(("http://", "https://")):
            raise serializers.ValidationError("base_url 必须以 http:// 或 https:// 开头")
        return normalized


class DjiCloudPlatformPatchSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    name = serializers.CharField(max_length=128, required=False)
    base_url = serializers.CharField(max_length=500, required=False)
    username = serializers.CharField(max_length=128, required=False)
    password = serializers.CharField(max_length=256, required=False, write_only=True)
    login_flag = serializers.IntegerField(required=False)
    is_default = serializers.BooleanField(required=False)

    class Meta:
        model = DjiCloudPlatform
        fields = ["name", "base_url", "username", "password", "login_flag", "is_default"]

    def validate_base_url(self, value):
        normalized = normalize_base_url(value)
        if not normalized.startswith(("http://", "https://")):
            raise serializers.ValidationError("base_url 必须以 http:// 或 https:// 开头")
        return normalized


class V2DroneReadSerializer(DroneReadSerializer):
    class Meta(DroneReadSerializer.Meta):
        fields = [
            "id",
            "dji_platform",
            "code",
            "name",
            "model",
            "device_sn",
            "status",
            "dji_online",
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

    def _device_index(self, obj):
        if not obj.device_sn or obj.dji_platform_id is None:
            return None
        return obj.dji_platform.device_indexes.filter(device_sn=obj.device_sn).first()


class V2AvailableDroneReadSerializer(AvailableDroneReadSerializer):
    dji_platform = serializers.IntegerField(source="dji_platform_id", read_only=True)

    class Meta(AvailableDroneReadSerializer.Meta):
        fields = [
            "dji_platform",
            "device_sn",
            "name",
            "model",
            "firmware_version",
            "firmware_status",
            "last_seen_at",
        ]
        read_only_fields = fields


class V2DroneClaimSerializer(RejectUnknownFieldsMixin, serializers.ModelSerializer):
    platform_id = serializers.IntegerField(write_only=True, min_value=1)
    name = serializers.CharField(required=False, allow_blank=True)
    model = serializers.CharField(required=False, allow_blank=True)
    org_id = serializers.IntegerField(required=False, allow_null=True)

    class Meta:
        model = Drone
        fields = ["platform_id", "code", "device_sn", "name", "model", "org_id"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        current_tenant = require_request_tenant(self.context)
        platform = DjiCloudPlatform.objects.filter(tenant=current_tenant, id=attrs["platform_id"]).first()
        if platform is None:
            raise serializers.ValidationError({"platform_id": ["DJI 平台不存在"]})

        code = attrs.get("code")
        device_sn = attrs.get("device_sn")
        device_index = platform.device_indexes.filter(device_sn=device_sn).first()
        released_drone = (
            Drone.objects.filter(
                tenant=current_tenant,
                dji_platform=platform,
                device_sn=device_sn,
                status=DroneStatus.RELEASED,
            )
            .order_by("-id")
            .first()
        )

        if device_index is None:
            raise serializers.ValidationError({"device_sn": "该 DJI 平台的已绑定设备池中不存在该 device_sn"})
        if code and Drone.objects.filter(tenant=current_tenant, code=code).exclude(pk=getattr(released_drone, "pk", None)).exists():
            raise serializers.ValidationError({"code": "当前租户下已存在相同业务编码"})

        claimed_drone = (
            Drone.objects.exclude(status=DroneStatus.RELEASED)
            .filter(tenant=current_tenant, dji_platform=platform, device_sn=device_sn)
            .first()
        )
        if claimed_drone is not None:
            raise serializers.ValidationError({"device_sn": "当前租户的该 DJI 平台下已认领该设备"})

        self._platform = platform
        self._device_index = device_index
        self._released_drone_id = getattr(released_drone, "id", None)
        return attrs

    def create(self, validated_data):
        platform = getattr(self, "_platform")
        device_index = getattr(self, "_device_index", None)
        released_drone_id = getattr(self, "_released_drone_id", None)
        validated_data.pop("platform_id", None)
        payload = device_index.last_payload if device_index is not None else {}
        claim_name = (
            validated_data.get("name")
            or _payload_string(payload, "name", "device_name", "nickname")
            or validated_data["code"]
        )
        claim_model = (
            validated_data.get("model")
            or _payload_string(payload, "model", "device_model", "product_type")
            or "unknown"
        )

        if released_drone_id is not None:
            updated_count = Drone.objects.filter(pk=released_drone_id, status=DroneStatus.RELEASED).update(
                tenant=validated_data["tenant"],
                dji_platform=platform,
                code=validated_data["code"],
                name=claim_name,
                model=claim_model,
                device_sn=validated_data["device_sn"],
                org_id=validated_data.get("org_id"),
                status=DroneStatus.CLAIMED,
                dji_online=True,
                created_by_tenant_member_id=validated_data.get("created_by_tenant_member_id"),
            )
            if updated_count:
                return Drone.objects.get(pk=released_drone_id)

        validated_data["dji_platform"] = platform
        validated_data["name"] = claim_name
        validated_data["model"] = claim_model
        validated_data["status"] = DroneStatus.CLAIMED
        validated_data["dji_online"] = True
        return super().create(validated_data)


V2DroneUpdateSerializer = DroneUpdateSerializer


class V2RouteReadSerializer(RouteReadSerializer):
    class Meta(RouteReadSerializer.Meta):
        fields = ["id", "dji_platform", "name", "is_published", "created_at", "updated_at"]
        read_only_fields = fields


class V2RouteCreateSerializer(RouteCreateSerializer):
    platform_id = serializers.IntegerField(write_only=True, min_value=1)

    class Meta(RouteCreateSerializer.Meta):
        fields = ["platform_id", "name", "kmz_file"]

    def create(self, validated_data):
        validated_data.pop("platform_id", None)
        return super().create(validated_data)


class V2RouteUpdateSerializer(RouteUpdateSerializer):
    class Meta(RouteUpdateSerializer.Meta):
        fields = ["name", "kmz_file"]


class V2RouteUpdateJsonSerializer(RouteUpdateJsonSerializer):
    pass


class V2MissionReadSerializer(MissionReadSerializer):
    class Meta(MissionReadSerializer.Meta):
        fields = [
            "id",
            "dji_platform",
            "name",
            "route",
            "route_name",
            "drone",
            "device_sn",
            "drone_name",
            "pilot",
            "pilot_name",
            "scheduled_at",
            "started_at",
            "finished_at",
            "remark",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class V2MissionCreateSerializer(MissionCreateSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        route = attrs.get("route")
        drone = attrs.get("drone")
        if route is not None and drone is not None and route.dji_platform_id != drone.dji_platform_id:
            raise serializers.ValidationError({"dji_platform": ["route 与 drone 必须属于同一 DJI 平台"]})
        if route is not None and route.dji_platform_id is None:
            raise serializers.ValidationError({"route": ["v2 任务只能绑定带 DJI 平台的航线"]})
        if drone is not None and drone.dji_platform_id is None:
            raise serializers.ValidationError({"drone": ["v2 任务只能绑定带 DJI 平台的无人机"]})
        self._platform = route.dji_platform if route is not None else None
        return attrs

    def create(self, validated_data):
        validated_data["dji_platform"] = getattr(self, "_platform", None)
        return super().create(validated_data)


class V2MissionUpdateSerializer(MissionUpdateSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = getattr(self, "instance", None)
        route = attrs.get("route", instance.route if instance is not None else None)
        drone = attrs.get("drone", instance.drone if instance is not None else None)
        if route is not None and drone is not None and route.dji_platform_id != drone.dji_platform_id:
            raise serializers.ValidationError({"dji_platform": ["route 与 drone 必须属于同一 DJI 平台"]})
        if route is not None and route.dji_platform_id is None:
            raise serializers.ValidationError({"route": ["v2 任务只能绑定带 DJI 平台的航线"]})
        if drone is not None and drone.dji_platform_id is None:
            raise serializers.ValidationError({"drone": ["v2 任务只能绑定带 DJI 平台的无人机"]})
        self._platform = route.dji_platform if route is not None else None
        return attrs

    def update(self, instance, validated_data):
        validated_data["dji_platform"] = getattr(self, "_platform", instance.dji_platform)
        return super().update(instance, validated_data)


class V2FlightRecordSummarySerializer(FlightRecordSummarySerializer):
    class Meta(FlightRecordSummarySerializer.Meta):
        fields = ["dji_platform", *FlightRecordSummarySerializer.Meta.fields]
        read_only_fields = fields


class V2FlightRecordDetailSerializer(FlightRecordDetailSerializer):
    class Meta(FlightRecordDetailSerializer.Meta):
        fields = ["dji_platform", *FlightRecordDetailSerializer.Meta.fields]
        read_only_fields = fields


class V2MediaFileReadSerializer(MediaFileReadSerializer):
    class Meta(MediaFileReadSerializer.Meta):
        fields = ["dji_platform", *MediaFileReadSerializer.Meta.fields]
        read_only_fields = fields


V2MediaFileBindMissionSerializer = MediaFileBindMissionSerializer
