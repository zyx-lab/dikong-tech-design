import re
import zipfile

from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from apps.access.api_base import StrictSerializer
from apps.access.models import DirectoryStatus
from apps.inspection_v2.models import (
    CloudMediaFile,
    FlightSession,
    FlightTelemetrySnapshot,
    InspectionFlightRecord,
    InspectionMission,
    MissionCloudExecution,
    MissionExecutionMode,
    MissionResourceAssignment,
    MissionStatus,
    Waypoint,
    WaypointRoute,
    WaypointRouteCloudFile,
    WaylineType,
    route_cover_image_url,
)
from apps.inspection_v2.route_cover_images import RouteCoverImageField, validate_route_cover_upload
from apps.iam_v2.serializers import DepartmentReadSerializer


ROUTE_CLOUD_FILE_SCHEMA = {
    "type": "object",
    "nullable": True,
    "properties": {
        "routeId": {"type": "integer", "readOnly": True},
        "djiConnectionId": {"type": "integer", "readOnly": True},
        "workspaceId": {"type": "string", "readOnly": True},
        "djiFileId": {"type": "string", "readOnly": True},
        "waylineType": {"type": "integer", "readOnly": True},
        "downloadUrl": {"type": "string", "readOnly": True},
        "downloadUrlExpiresAt": {"type": "string", "format": "date-time", "nullable": True, "readOnly": True},
        "uploadedAt": {"type": "string", "format": "date-time", "nullable": True, "readOnly": True},
    },
}


class WaypointReadSerializer(serializers.ModelSerializer):
    hoverSeconds = serializers.IntegerField(source="hover_seconds", read_only=True)

    class Meta:
        model = Waypoint
        fields = ["id", "sequence", "latitude", "longitude", "altitude", "speed", "heading", "hoverSeconds"]
        read_only_fields = fields


class WaypointWriteSerializer(StrictSerializer):
    sequence = serializers.IntegerField(min_value=1)
    latitude = serializers.DecimalField(max_digits=12, decimal_places=8)
    longitude = serializers.DecimalField(max_digits=12, decimal_places=8)
    altitude = serializers.DecimalField(max_digits=10, decimal_places=2)
    speed = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    heading = serializers.DecimalField(max_digits=7, decimal_places=2, required=False, allow_null=True)
    hoverSeconds = serializers.IntegerField(min_value=0, required=False, default=0)


class RouteReadSerializer(serializers.ModelSerializer):
    ownerDepartmentId = serializers.IntegerField(source="owner_department_id", read_only=True)
    defaultAltitude = serializers.DecimalField(source="default_altitude", max_digits=10, decimal_places=2, allow_null=True, read_only=True)
    defaultSpeed = serializers.DecimalField(source="default_speed", max_digits=10, decimal_places=2, allow_null=True, read_only=True)
    coverImageUrl = serializers.SerializerMethodField()
    djiFile = serializers.SerializerMethodField()
    waypoints = WaypointReadSerializer(many=True, read_only=True)

    class Meta:
        model = WaypointRoute
        fields = [
            "id",
            "ownerDepartmentId",
            "name",
            "status",
            "defaultAltitude",
            "defaultSpeed",
            "coverImageUrl",
            "djiFile",
            "remark",
            "waypoints",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_coverImageUrl(self, instance: WaypointRoute) -> str:
        return route_cover_image_url(instance)

    @extend_schema_field(ROUTE_CLOUD_FILE_SCHEMA)
    def get_djiFile(self, instance: WaypointRoute) -> dict | None:
        try:
            cloud_file = instance.cloud_file
        except WaypointRouteCloudFile.DoesNotExist:
            return None
        return dict(RouteCloudFileReadSerializer(cloud_file).data)


class RouteDeleteResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    deleted = serializers.BooleanField()


class RouteBaseWriteSerializer(StrictSerializer):
    name = serializers.CharField(max_length=128)
    status = serializers.ChoiceField(choices=DirectoryStatus.choices, required=False)
    coverImage = RouteCoverImageField(required=False, allow_empty_file=False, write_only=True)
    remark = serializers.CharField(required=False, allow_blank=True)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            has_empty_cover = "coverImage" in data and (data.get("coverImage") == "" or data.get("coverImage") is None)
        else:
            has_empty_cover = False
        if isinstance(data, dict) and has_empty_cover:
            data = {key: data.get(key) for key in data.keys()}
            data.pop("coverImage", None)
        return super().to_internal_value(data)

    def validate_coverImage(self, value):
        return validate_route_cover_upload(value)


def validate_route_kmz_upload(value):
    name = str(getattr(value, "name", "") or "").lower()
    if not name.endswith(".kmz"):
        raise serializers.ValidationError("只支持上传 .kmz 文件")
    try:
        value.seek(0)
        is_valid_zip = zipfile.is_zipfile(value)
    finally:
        try:
            value.seek(0)
        except Exception:  # noqa: BLE001 - upload wrappers expose inconsistent seek behavior.
            pass
    if not is_valid_zip:
        raise serializers.ValidationError("请上传有效的 KMZ/ZIP 文件")
    return value


class RouteCreateSerializer(RouteBaseWriteSerializer):
    djiConnectionId = serializers.IntegerField(min_value=1)
    kmzFile = serializers.FileField()

    def validate_kmzFile(self, value):
        return validate_route_kmz_upload(value)


class RouteMetadataUpdateSerializer(StrictSerializer):
    name = serializers.CharField(max_length=128, required=False)
    status = serializers.ChoiceField(choices=DirectoryStatus.choices, required=False)
    coverImage = RouteCoverImageField(required=False, allow_empty_file=False, write_only=True)
    remark = serializers.CharField(required=False, allow_blank=True)

    def to_internal_value(self, data):
        if isinstance(data, dict) and ("coverImage" in data and (data.get("coverImage") == "" or data.get("coverImage") is None)):
            data = {key: data.get(key) for key in data.keys()}
            data.pop("coverImage", None)
        return super().to_internal_value(data)

    def validate_coverImage(self, value):
        return validate_route_cover_upload(value)


class RouteKmzUpdateSerializer(StrictSerializer):
    name = serializers.CharField(max_length=128, required=False)
    status = serializers.ChoiceField(choices=DirectoryStatus.choices, required=False)
    coverImage = RouteCoverImageField(required=False, allow_empty_file=False, write_only=True)
    remark = serializers.CharField(required=False, allow_blank=True)
    djiConnectionId = serializers.IntegerField(min_value=1)
    kmzFile = serializers.FileField()

    def to_internal_value(self, data):
        if isinstance(data, dict):
            has_empty_cover = "coverImage" in data and (data.get("coverImage") == "" or data.get("coverImage") is None)
        else:
            has_empty_cover = False
        if isinstance(data, dict) and has_empty_cover:
            data = {key: data.get(key) for key in data.keys()}
            data.pop("coverImage", None)
        return super().to_internal_value(data)

    def validate_coverImage(self, value):
        return validate_route_cover_upload(value)

    def validate_kmzFile(self, value):
        return validate_route_kmz_upload(value)


class RouteCloudFileReadSerializer(serializers.ModelSerializer):
    routeId = serializers.IntegerField(source="route_id", read_only=True)
    djiConnectionId = serializers.IntegerField(source="dji_connection_id", read_only=True)
    workspaceId = serializers.CharField(source="workspace_id", read_only=True)
    djiFileId = serializers.CharField(source="dji_file_id", read_only=True)
    waylineType = serializers.IntegerField(source="wayline_type", read_only=True)
    downloadUrl = serializers.CharField(source="download_url", read_only=True)
    downloadUrlExpiresAt = serializers.DateTimeField(source="download_url_expires_at", allow_null=True, read_only=True)
    uploadedAt = serializers.DateTimeField(source="uploaded_at", allow_null=True, read_only=True)

    class Meta:
        model = WaypointRouteCloudFile
        fields = [
            "routeId",
            "djiConnectionId",
            "workspaceId",
            "djiFileId",
            "waylineType",
            "downloadUrl",
            "downloadUrlExpiresAt",
            "uploadedAt",
        ]
        read_only_fields = fields


class MissionResourceAssignmentReadSerializer(serializers.ModelSerializer):
    resourceType = serializers.CharField(source="resource_type", read_only=True)
    resourceId = serializers.IntegerField(source="resource_object_id", read_only=True)
    ownerDepartmentId = serializers.IntegerField(source="owner_department_id", read_only=True)

    class Meta:
        model = MissionResourceAssignment
        fields = ["id", "resourceType", "resourceId", "ownerDepartmentId"]
        read_only_fields = fields


class MissionCloudExecutionReadSerializer(serializers.ModelSerializer):
    djiConnectionId = serializers.IntegerField(source="dji_connection_id", read_only=True)
    routeCloudFileId = serializers.IntegerField(source="route_cloud_file_id", read_only=True)
    workspaceId = serializers.CharField(source="workspace_id", read_only=True)
    executionMode = serializers.CharField(source="execution_mode", read_only=True)
    djiJobId = serializers.CharField(source="dji_job_id", read_only=True)
    executorSn = serializers.CharField(source="executor_sn", read_only=True)
    droneSn = serializers.CharField(source="drone_sn", read_only=True)
    progressPercent = serializers.IntegerField(source="progress_percent", read_only=True)
    liveStatus = serializers.CharField(source="live_status", read_only=True)
    liveVideoId = serializers.CharField(source="live_video_id", read_only=True)
    liveUrlType = serializers.IntegerField(source="live_url_type", read_only=True)
    liveVideoQuality = serializers.IntegerField(source="live_video_quality", read_only=True)
    liveUrls = serializers.JSONField(source="live_urls", read_only=True)
    liveStartedAt = serializers.DateTimeField(source="live_started_at", allow_null=True, read_only=True)
    liveStoppedAt = serializers.DateTimeField(source="live_stopped_at", allow_null=True, read_only=True)
    liveErrorMessage = serializers.CharField(source="live_error_message", read_only=True)
    lastEventAt = serializers.DateTimeField(source="last_event_at", allow_null=True, read_only=True)
    errorCode = serializers.CharField(source="error_code", read_only=True)
    errorMessage = serializers.CharField(source="error_message", read_only=True)

    class Meta:
        model = MissionCloudExecution
        fields = [
            "id",
            "djiConnectionId",
            "routeCloudFileId",
            "workspaceId",
            "executionMode",
            "djiJobId",
            "executorSn",
            "droneSn",
            "status",
            "progressPercent",
            "liveStatus",
            "liveVideoId",
            "liveUrlType",
            "liveVideoQuality",
            "liveUrls",
            "liveStartedAt",
            "liveStoppedAt",
            "liveErrorMessage",
            "lastEventAt",
            "errorCode",
            "errorMessage",
        ]
        read_only_fields = fields


class InspectionPilotAccountSummarySerializer(serializers.Serializer):
    accountProfileId = serializers.IntegerField()
    userId = serializers.IntegerField()
    username = serializers.CharField()
    name = serializers.CharField()
    department = DepartmentReadSerializer()


class MissionReadSerializer(serializers.ModelSerializer):
    creatorDepartmentId = serializers.IntegerField(source="creator_department_id", read_only=True)
    primaryResourceOwnerDepartmentId = serializers.IntegerField(source="primary_resource_owner_department_id", read_only=True)
    routeId = serializers.IntegerField(source="route_id", read_only=True)
    routeName = serializers.CharField(source="route.name", read_only=True)
    droneId = serializers.IntegerField(source="drone_id", read_only=True)
    droneDeviceSn = serializers.CharField(source="drone.device_sn", read_only=True)
    droneName = serializers.CharField(source="drone.name", read_only=True)
    dockId = serializers.IntegerField(source="dock_id", allow_null=True, read_only=True)
    executorId = serializers.IntegerField(source="executor_id", allow_null=True, read_only=True)
    payloadId = serializers.IntegerField(source="payload_id", allow_null=True, read_only=True)
    executionMode = serializers.CharField(source="execution_mode", read_only=True)
    pilot = serializers.SerializerMethodField()
    scheduledAt = serializers.DateTimeField(source="scheduled_at", allow_null=True, read_only=True)
    startedAt = serializers.DateTimeField(source="started_at", allow_null=True, read_only=True)
    finishedAt = serializers.DateTimeField(source="finished_at", allow_null=True, read_only=True)
    canceledAt = serializers.DateTimeField(source="canceled_at", allow_null=True, read_only=True)
    routeSnapshot = serializers.JSONField(source="route_snapshot", read_only=True)
    resourceAssignments = MissionResourceAssignmentReadSerializer(source="resource_assignments", many=True, read_only=True)
    cloudExecution = MissionCloudExecutionReadSerializer(source="cloud_execution", allow_null=True, read_only=True)

    class Meta:
        model = InspectionMission
        fields = [
            "id",
            "creatorDepartmentId",
            "primaryResourceOwnerDepartmentId",
            "name",
            "status",
            "routeId",
            "routeName",
            "routeSnapshot",
            "droneId",
            "droneDeviceSn",
            "droneName",
            "dockId",
            "executorId",
            "payloadId",
            "executionMode",
            "pilot",
            "scheduledAt",
            "startedAt",
            "finishedAt",
            "canceledAt",
            "cancel_reason",
            "failure_reason",
            "remark",
            "resourceAssignments",
            "cloudExecution",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(InspectionPilotAccountSummarySerializer)
    def get_pilot(self, obj) -> dict:
        account = obj.pilot_account_profile
        return {
            "accountProfileId": account.id,
            "userId": account.user_id,
            "username": account.user.username,
            "name": account.name,
            "department": DepartmentReadSerializer(account.department).data,
        }


class MissionWriteSerializer(StrictSerializer):
    name = serializers.CharField(max_length=128)
    routeId = serializers.IntegerField(min_value=1)
    droneId = serializers.IntegerField(min_value=1)
    pilotAccountProfileId = serializers.IntegerField(min_value=1)
    dockId = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    executorId = serializers.IntegerField(
        min_value=1,
        required=False,
        allow_null=True,
        help_text="Pilot2 手动执行端/遥控器资源 ID；与 dockId 必须二选一。"
    )
    payloadId = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    scheduledAt = serializers.DateTimeField(required=False, allow_null=True)
    remark = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        has_dock = attrs.get("dockId") is not None
        has_executor = attrs.get("executorId") is not None
        if has_dock == has_executor:
            raise serializers.ValidationError({"dockId": ["dockId 与 executorId 必须二选一"], "executorId": ["dockId 与 executorId 必须二选一"]})
        return attrs


class MissionCloseSerializer(StrictSerializer):
    reason = serializers.CharField(required=False, allow_blank=True)


class MissionPreflightReasonSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    detail = serializers.JSONField(required=False)


class MissionPreflightCheckItemSerializer(serializers.Serializer):
    code = serializers.CharField()
    label = serializers.CharField()
    status = serializers.ChoiceField(choices=["PASS", "FAIL", "WARNING", "SKIPPED"])
    message = serializers.CharField()
    detail = serializers.JSONField(required=False)


class MissionPreflightExecutionSerializer(serializers.Serializer):
    executionMode = serializers.ChoiceField(choices=MissionExecutionMode.choices, allow_blank=True)
    routeId = serializers.IntegerField()
    droneId = serializers.IntegerField()
    droneSn = serializers.CharField()
    executorId = serializers.IntegerField(allow_null=True)
    executorSn = serializers.CharField(allow_blank=True)
    dockId = serializers.IntegerField(allow_null=True)
    payloadId = serializers.IntegerField(allow_null=True)
    djiConnectionId = serializers.IntegerField(allow_null=True)
    workspaceId = serializers.CharField(allow_blank=True)
    routeDjiFileId = serializers.CharField(allow_blank=True)
    selectedLiveVideoId = serializers.CharField(allow_blank=True)


class MissionPreflightCheckResponseSerializer(serializers.Serializer):
    missionId = serializers.IntegerField()
    canStart = serializers.BooleanField()
    status = serializers.ChoiceField(choices=["READY", "BLOCKED"])
    blockingReasons = MissionPreflightReasonSerializer(many=True)
    warnings = MissionPreflightReasonSerializer(many=True)
    checks = MissionPreflightCheckItemSerializer(many=True)
    execution = MissionPreflightExecutionSerializer()


class TelemetrySnapshotReadSerializer(serializers.ModelSerializer):
    batteryPercent = serializers.IntegerField(source="battery_percent", allow_null=True, read_only=True)
    reportedAt = serializers.DateTimeField(source="reported_at", read_only=True)

    class Meta:
        model = FlightTelemetrySnapshot
        fields = ["latitude", "longitude", "altitude", "speed", "heading", "batteryPercent", "reportedAt"]
        read_only_fields = fields


class ActiveFlightReadSerializer(serializers.ModelSerializer):
    missionId = serializers.IntegerField(source="mission_id", read_only=True)
    missionName = serializers.CharField(source="mission.name", read_only=True)
    routeName = serializers.CharField(source="mission.route.name", read_only=True)
    droneId = serializers.IntegerField(source="drone_id", read_only=True)
    droneDeviceSn = serializers.CharField(source="drone.device_sn", read_only=True)
    droneName = serializers.CharField(source="drone.name", read_only=True)
    dockId = serializers.IntegerField(source="dock_id", allow_null=True, read_only=True)
    payloadId = serializers.IntegerField(source="payload_id", allow_null=True, read_only=True)
    pilot = serializers.SerializerMethodField()
    startedAt = serializers.DateTimeField(source="started_at", read_only=True)
    telemetry = TelemetrySnapshotReadSerializer(source="telemetry_snapshot", read_only=True)
    liveStatus = serializers.SerializerMethodField()
    liveVideoId = serializers.SerializerMethodField()
    liveUrls = serializers.SerializerMethodField()

    class Meta:
        model = FlightSession
        fields = [
            "id",
            "missionId",
            "missionName",
            "routeName",
            "droneId",
            "droneDeviceSn",
            "droneName",
            "dockId",
            "payloadId",
            "pilot",
            "status",
            "startedAt",
            "telemetry",
            "liveStatus",
            "liveVideoId",
            "liveUrls",
        ]
        read_only_fields = fields

    @extend_schema_field(InspectionPilotAccountSummarySerializer)
    def get_pilot(self, obj) -> dict:
        account = obj.mission.pilot_account_profile
        return {
            "accountProfileId": account.id,
            "userId": account.user_id,
            "username": account.user.username,
            "name": account.name,
            "department": DepartmentReadSerializer(account.department).data,
        }

    def _cloud_execution(self, obj):
        try:
            return obj.mission.cloud_execution
        except MissionCloudExecution.DoesNotExist:
            return None

    def get_liveStatus(self, obj) -> str:
        execution = self._cloud_execution(obj)
        return execution.live_status if execution is not None else ""

    def get_liveVideoId(self, obj) -> str:
        execution = self._cloud_execution(obj)
        return execution.live_video_id if execution is not None else ""

    def get_liveUrls(self, obj) -> dict:
        execution = self._cloud_execution(obj)
        return execution.live_urls if execution is not None else {}


class TelemetrySnapshotWriteSerializer(StrictSerializer):
    sessionId = serializers.IntegerField(min_value=1)
    latitude = serializers.DecimalField(max_digits=12, decimal_places=8, required=False, allow_null=True)
    longitude = serializers.DecimalField(max_digits=12, decimal_places=8, required=False, allow_null=True)
    altitude = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    speed = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    heading = serializers.DecimalField(max_digits=7, decimal_places=2, required=False, allow_null=True)
    batteryPercent = serializers.IntegerField(min_value=0, max_value=100, required=False, allow_null=True)
    reportedAt = serializers.DateTimeField(required=False)


class FlightRecordReadSerializer(serializers.ModelSerializer):
    flightNo = serializers.CharField(source="flight_no", read_only=True)
    missionId = serializers.IntegerField(source="mission_id", read_only=True)
    creatorDepartmentId = serializers.IntegerField(source="creator_department_id", read_only=True)
    primaryResourceOwnerDepartmentId = serializers.IntegerField(source="primary_resource_owner_department_id", read_only=True)
    missionName = serializers.CharField(source="mission_name", read_only=True)
    routeName = serializers.CharField(source="route_name", read_only=True)
    droneDeviceSn = serializers.CharField(source="drone_device_sn", read_only=True)
    droneName = serializers.CharField(source="drone_name", read_only=True)
    pilotName = serializers.CharField(source="pilot_name", read_only=True)
    startTime = serializers.DateTimeField(source="start_time", read_only=True)
    endTime = serializers.DateTimeField(source="end_time", read_only=True)
    flightDuration = serializers.IntegerField(source="flight_duration", read_only=True)
    photoCount = serializers.IntegerField(source="photo_count", read_only=True)
    videoCount = serializers.IntegerField(source="video_count", read_only=True)
    abnormalReason = serializers.CharField(source="abnormal_reason", read_only=True)

    class Meta:
        model = InspectionFlightRecord
        fields = [
            "id",
            "flightNo",
            "missionId",
            "creatorDepartmentId",
            "primaryResourceOwnerDepartmentId",
            "missionName",
            "routeName",
            "droneDeviceSn",
            "droneName",
            "pilotName",
            "startTime",
            "endTime",
            "flightDuration",
            "photoCount",
            "videoCount",
            "status",
            "remark",
            "abnormalReason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class FlightRecordUpdateSerializer(StrictSerializer):
    remark = serializers.CharField(required=False, allow_blank=True)
    abnormalReason = serializers.CharField(required=False, allow_blank=True)


class CloudMediaFileReadSerializer(serializers.ModelSerializer):
    flightRecordId = serializers.IntegerField(source="flight_record_id", allow_null=True, read_only=True)
    missionId = serializers.IntegerField(source="mission_id", allow_null=True, read_only=True)
    workspaceId = serializers.CharField(source="workspace_id", read_only=True)
    deviceSn = serializers.CharField(source="device_sn", read_only=True)
    djiJobId = serializers.CharField(source="dji_job_id", read_only=True)
    cloudFileId = serializers.CharField(source="cloud_file_id", read_only=True)
    objectKey = serializers.CharField(source="object_key", read_only=True)
    fingerprint = serializers.CharField(read_only=True)
    fileGroupId = serializers.CharField(source="file_group_id", read_only=True)
    mediaType = serializers.CharField(source="media_type", read_only=True)
    fileName = serializers.CharField(source="file_name", read_only=True)
    thumbnailUrl = serializers.CharField(source="thumbnail_url", read_only=True)
    previewUrl = serializers.CharField(source="preview_url", read_only=True)
    downloadUrl = serializers.CharField(source="download_url", read_only=True)
    playbackUrl = serializers.CharField(source="playback_url", read_only=True)
    fileSize = serializers.IntegerField(source="file_size", allow_null=True, read_only=True)
    capturedAt = serializers.DateTimeField(source="captured_at", allow_null=True, read_only=True)

    class Meta:
        model = CloudMediaFile
        fields = [
            "id",
            "flightRecordId",
            "missionId",
            "workspaceId",
            "deviceSn",
            "djiJobId",
            "cloudFileId",
            "objectKey",
            "fingerprint",
            "fileGroupId",
            "mediaType",
            "fileName",
            "thumbnailUrl",
            "previewUrl",
            "downloadUrl",
            "playbackUrl",
            "fileSize",
            "capturedAt",
            "created_at",
        ]
        read_only_fields = fields


class CloudMediaFileUrlRefreshSerializer(StrictSerializer):
    urlType = serializers.ChoiceField(choices=["download", "preview", "playback"], required=False, default="download")


CAMERA_ACTION_CHOICES = [
    "camera_mode_switch",
    "camera_photo_take",
    "camera_recording_start",
    "camera_recording_stop",
    "camera_focal_length_set",
    "camera_aim",
    "gimbal_reset",
]
CAMERA_TYPE_CHOICES = ["wide", "zoom", "ir"]
FOCAL_CAMERA_TYPE_CHOICES = ["zoom", "ir"]
PAYLOAD_INDEX_PATTERN = re.compile(r"^\d+-\d+-\d+$")


class CameraActionSerializer(StrictSerializer):
    missing = object()

    droneId = serializers.IntegerField(min_value=1)
    executorId = serializers.IntegerField(min_value=1)
    payloadIndex = serializers.CharField(required=False, allow_blank=True)
    payload_index = serializers.CharField(required=False, allow_blank=True, write_only=True)
    action = serializers.ChoiceField(choices=CAMERA_ACTION_CHOICES)
    cameraMode = serializers.IntegerField(required=False)
    camera_mode = serializers.IntegerField(required=False, write_only=True)
    cameraType = serializers.ChoiceField(choices=CAMERA_TYPE_CHOICES, required=False)
    camera_type = serializers.ChoiceField(choices=CAMERA_TYPE_CHOICES, required=False, write_only=True)
    zoomFactor = serializers.FloatField(required=False)
    zoom_factor = serializers.FloatField(required=False, write_only=True)
    locked = serializers.BooleanField(required=False)
    x = serializers.FloatField(required=False, min_value=0, max_value=1)
    y = serializers.FloatField(required=False, min_value=0, max_value=1)
    resetMode = serializers.IntegerField(required=False)
    reset_mode = serializers.IntegerField(required=False, write_only=True)

    def _alias(self, attrs: dict, camel: str, snake: str):
        camel_value = attrs.get(camel, self.missing)
        snake_value = attrs.get(snake, self.missing)
        if camel_value is not self.missing and snake_value is not self.missing and camel_value != snake_value:
            raise serializers.ValidationError({camel: [f"{camel} 与 {snake} 不能同时传入不同值"]})
        if camel_value is not self.missing:
            return camel_value
        if snake_value is not self.missing:
            return snake_value
        return self.missing

    def validate(self, attrs):
        attrs = super().validate(attrs)
        payload_index = self._alias(attrs, "payloadIndex", "payload_index")
        if payload_index is self.missing or not str(payload_index).strip():
            raise serializers.ValidationError({"payloadIndex": ["该字段是必填项。"]})
        payload_index = str(payload_index).strip()
        if not PAYLOAD_INDEX_PATTERN.match(payload_index):
            raise serializers.ValidationError({"payloadIndex": ["格式必须为 type-subtype-index，例如 88-0-0"]})

        action = attrs["action"]
        dji_data = {"payload_index": payload_index}

        if action == "camera_mode_switch":
            camera_mode = self._alias(attrs, "cameraMode", "camera_mode")
            if camera_mode is self.missing:
                raise serializers.ValidationError({"cameraMode": ["camera_mode_switch 需要 cameraMode"]})
            if camera_mode not in {0, 1, 2, 3}:
                raise serializers.ValidationError({"cameraMode": ["取值必须是 0、1、2 或 3"]})
            dji_data["camera_mode"] = camera_mode

        if action == "camera_focal_length_set":
            camera_type = self._alias(attrs, "cameraType", "camera_type")
            zoom_factor = self._alias(attrs, "zoomFactor", "zoom_factor")
            if camera_type is self.missing:
                raise serializers.ValidationError({"cameraType": ["camera_focal_length_set 需要 cameraType"]})
            if camera_type not in FOCAL_CAMERA_TYPE_CHOICES:
                raise serializers.ValidationError({"cameraType": ["camera_focal_length_set 只支持 zoom 或 ir"]})
            if zoom_factor is self.missing:
                raise serializers.ValidationError({"zoomFactor": ["camera_focal_length_set 需要 zoomFactor"]})
            max_zoom = 20 if camera_type == "ir" else 200
            if zoom_factor < 2 or zoom_factor > max_zoom:
                raise serializers.ValidationError({"zoomFactor": [f"{camera_type} 变焦倍率必须在 2 到 {max_zoom} 之间"]})
            dji_data["camera_type"] = camera_type
            dji_data["zoom_factor"] = zoom_factor

        if action == "camera_aim":
            camera_type = self._alias(attrs, "cameraType", "camera_type")
            if camera_type is self.missing:
                raise serializers.ValidationError({"cameraType": ["camera_aim 需要 cameraType"]})
            missing = [field for field in ("locked", "x", "y") if field not in attrs]
            if missing:
                raise serializers.ValidationError({field: ["camera_aim 需要该字段"] for field in missing})
            dji_data["camera_type"] = camera_type
            dji_data["locked"] = attrs["locked"]
            dji_data["x"] = attrs["x"]
            dji_data["y"] = attrs["y"]

        if action == "gimbal_reset":
            reset_mode = self._alias(attrs, "resetMode", "reset_mode")
            if reset_mode is self.missing:
                raise serializers.ValidationError({"resetMode": ["gimbal_reset 需要 resetMode"]})
            if reset_mode not in {0, 1, 2, 3}:
                raise serializers.ValidationError({"resetMode": ["取值必须是 0、1、2 或 3"]})
            dji_data["reset_mode"] = reset_mode

        attrs["_payload_index"] = payload_index
        attrs["_dji_data"] = dji_data
        return attrs


class CameraActionResponseSerializer(serializers.Serializer):
    operationId = serializers.IntegerField()
    status = serializers.CharField()
    action = serializers.CharField()
    droneId = serializers.IntegerField()
    droneSn = serializers.CharField()
    executorId = serializers.IntegerField()
    gatewaySn = serializers.CharField()
    payloadIndex = serializers.CharField()
    upstream = serializers.JSONField()


class LiveActionSerializer(StrictSerializer):
    droneId = serializers.IntegerField(min_value=1)
    video_id = serializers.CharField(required=False, allow_blank=True)
    videoId = serializers.CharField(required=False, allow_blank=True)
    video_type = serializers.ChoiceField(choices=["wide", "zoom", "ir", "normal"], required=False)
    videoType = serializers.ChoiceField(choices=["wide", "zoom", "ir", "normal"], required=False)
    url_type = serializers.IntegerField(required=False)
    urlType = serializers.IntegerField(required=False)
    video_quality = serializers.IntegerField(required=False)
    videoQuality = serializers.IntegerField(required=False)


class LiveCapacityQuerySerializer(StrictSerializer):
    droneId = serializers.IntegerField(min_value=1)
