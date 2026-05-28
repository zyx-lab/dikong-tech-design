from rest_framework import serializers
from rest_framework.reverse import reverse

from apps.api_v1.serializers import RejectUnknownFieldsMixin
from apps.api_v1.tenant_scope import require_request_tenant
from apps.dji_bff.models import DjiCloudPlatform, TenantRouteIndex
from apps.drone.models import Drone, DroneStatus
from apps.drone.serializers import AvailableDroneReadSerializer, DroneReadSerializer, DroneUpdateSerializer, _payload_string
from apps.flight_record.serializers import FlightRecordDetailSerializer, FlightRecordSummarySerializer
from apps.media_file.models import MediaFile, MediaType
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


class V2RouteDispatchReadSerializer(serializers.ModelSerializer):
    dji_platform = serializers.IntegerField(source="dji_platform_id", read_only=True)

    class Meta:
        model = TenantRouteIndex
        fields = [
            "dji_platform",
            "workspace_id",
            "dji_wayline_id",
            "download_url",
            "is_published",
            "updated_at",
        ]
        read_only_fields = fields


class V2RouteReadSerializer(RouteReadSerializer):
    dispatches = serializers.SerializerMethodField()

    class Meta(RouteReadSerializer.Meta):
        fields = ["id", "name", "kmz_file", "is_published", "dispatches", "created_at", "updated_at"]
        read_only_fields = fields

    def get_is_published(self, obj) -> bool:
        return obj.dji_indexes.filter(dji_platform__isnull=False, is_published=True).exists()

    def get_dispatches(self, obj) -> list[dict]:
        queryset = obj.dji_indexes.filter(dji_platform__isnull=False).order_by("dji_platform_id", "id")
        return V2RouteDispatchReadSerializer(queryset, many=True, context=self.context).data


class V2RouteCreateSerializer(RouteCreateSerializer):
    class Meta(RouteCreateSerializer.Meta):
        fields = ["name", "kmz_file"]

    def create(self, validated_data):
        return serializers.ModelSerializer.create(self, validated_data)


class V2RouteUpdateSerializer(RouteUpdateSerializer):
    class Meta(RouteUpdateSerializer.Meta):
        fields = ["name", "kmz_file"]

    def update(self, instance, validated_data):
        return serializers.ModelSerializer.update(self, instance, validated_data)


class V2RouteUpdateJsonSerializer(RouteUpdateJsonSerializer):
    pass


class V2RouteDispatchSerializer(RejectUnknownFieldsMixin, serializers.Serializer):
    platform_id = serializers.IntegerField(write_only=True, min_value=1)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        current_tenant = require_request_tenant(self.context)
        platform = DjiCloudPlatform.objects.filter(tenant=current_tenant, id=attrs["platform_id"]).first()
        if platform is None:
            raise serializers.ValidationError({"platform_id": ["必须提供当前租户下有效的 DJI 平台 ID"]})
        attrs["platform"] = platform
        return attrs


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
        current_tenant = require_request_tenant(self.context)
        route = attrs.get("route")
        drone = attrs.get("drone")
        if drone is not None and drone.dji_platform_id is None:
            raise serializers.ValidationError({"drone": ["v2 任务只能绑定带 DJI 平台的无人机"]})
        if route is not None and drone is not None:
            route_dispatched = TenantRouteIndex.objects.filter(
                tenant=current_tenant,
                route=route,
                dji_platform=drone.dji_platform,
                is_published=True,
            ).exists()
            if not route_dispatched:
                raise serializers.ValidationError({"route": ["航线必须先下发到无人机所属 DJI 平台"]})
        self._platform = drone.dji_platform if drone is not None else None
        return attrs

    def create(self, validated_data):
        validated_data["dji_platform"] = getattr(self, "_platform", None)
        return super().create(validated_data)


class V2MissionUpdateSerializer(MissionUpdateSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        current_tenant = require_request_tenant(self.context)
        instance = getattr(self, "instance", None)
        route = attrs.get("route", instance.route if instance is not None else None)
        drone = attrs.get("drone", instance.drone if instance is not None else None)
        if drone is not None and drone.dji_platform_id is None:
            raise serializers.ValidationError({"drone": ["v2 任务只能绑定带 DJI 平台的无人机"]})
        if route is not None and drone is not None:
            route_dispatched = TenantRouteIndex.objects.filter(
                tenant=current_tenant,
                route=route,
                dji_platform=drone.dji_platform,
                is_published=True,
            ).exists()
            if not route_dispatched:
                raise serializers.ValidationError({"route": ["航线必须先下发到无人机所属 DJI 平台"]})
        self._platform = drone.dji_platform if drone is not None else None
        return attrs

    def update(self, instance, validated_data):
        validated_data["dji_platform"] = getattr(self, "_platform", instance.dji_platform)
        return super().update(instance, validated_data)


class V2FlightRecordSummarySerializer(FlightRecordSummarySerializer):
    class Meta(FlightRecordSummarySerializer.Meta):
        fields = ["dji_platform", *FlightRecordSummarySerializer.Meta.fields]
        read_only_fields = fields


class V2FlightRecordMediaFileSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()
    playback_url = serializers.SerializerMethodField()
    preview_url = serializers.SerializerMethodField()

    class Meta:
        model = MediaFile
        fields = [
            "id",
            "media_type",
            "file_name",
            "thumbnail_url",
            "captured_at",
            "download_url",
            "playback_url",
            "preview_url",
        ]
        read_only_fields = fields

    def get_download_url(self, obj: MediaFile) -> str:
        return reverse("v2-media-file-download", kwargs={"pk": obj.id})

    def get_playback_url(self, obj: MediaFile) -> str:
        if obj.media_type != MediaType.VIDEO:
            return ""
        return reverse("v2-media-file-playback-url", kwargs={"pk": obj.id})

    def get_preview_url(self, obj: MediaFile) -> str:
        if obj.media_type != MediaType.PHOTO:
            return ""
        return reverse("v2-media-file-preview-url", kwargs={"pk": obj.id})


class V2FlightRecordDetailSerializer(FlightRecordDetailSerializer):
    media_files = serializers.SerializerMethodField()

    class Meta(FlightRecordDetailSerializer.Meta):
        fields = ["dji_platform", *FlightRecordDetailSerializer.Meta.fields]
        read_only_fields = fields

    def get_media_files(self, obj) -> list[dict]:
        media_queryset = obj.media_files.filter(
            dji_platform=obj.dji_platform,
            is_deleted=False,
            dji_index__isnull=False,
        ).order_by("-captured_at", "-id")
        return V2FlightRecordMediaFileSerializer(media_queryset, many=True, context=self.context).data


class V2MediaFileReadSerializer(MediaFileReadSerializer):
    class Meta(MediaFileReadSerializer.Meta):
        fields = ["dji_platform", *MediaFileReadSerializer.Meta.fields]
        read_only_fields = fields


V2MediaFileBindMissionSerializer = MediaFileBindMissionSerializer
