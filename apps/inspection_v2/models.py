from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.access.models import DirectoryStatus, TimeStampedModel
from apps.iam_v2.models import Department
from apps.resource_v2.models import DockResource, DroneResource, PayloadResource, ResourceType
from apps.workforce_v2.models import PilotProfile


class MissionStatus(models.TextChoices):
    PENDING = "PENDING", "待执行"
    RUNNING = "RUNNING", "执行中"
    COMPLETED = "COMPLETED", "已完成"
    CANCELED = "CANCELED", "已取消"
    FAILED = "FAILED", "失败"


class FlightSessionStatus(models.TextChoices):
    RUNNING = "RUNNING", "执行中"
    COMPLETED = "COMPLETED", "已完成"
    CANCELED = "CANCELED", "已取消"
    FAILED = "FAILED", "失败"


class FlightRecordStatus(models.TextChoices):
    COMPLETED = "COMPLETED", "已完成"
    CANCELED = "CANCELED", "已取消"
    FAILED = "FAILED", "失败"


class CloudMediaType(models.TextChoices):
    PHOTO = "PHOTO", "照片"
    VIDEO = "VIDEO", "视频"
    OTHER = "OTHER", "其他"


class WaypointRoute(TimeStampedModel):
    tenant = models.ForeignKey("access.Tenant", on_delete=models.CASCADE, related_name="v2_waypoint_routes")
    owner_department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="v2_waypoint_routes")
    name = models.CharField(max_length=128)
    status = models.PositiveSmallIntegerField(choices=DirectoryStatus.choices, default=DirectoryStatus.ACTIVE)
    default_altitude = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    default_speed = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    remark = models.TextField(blank=True, default="")
    created_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_v2_waypoint_routes",
    )

    class Meta:
        db_table = "v2_waypoint_routes"
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(fields=["owner_department", "name"], name="uniq_v2_route_owner_name"),
        ]

    def __str__(self):
        return f"{self.owner_department_id}:{self.name}"


class Waypoint(TimeStampedModel):
    route = models.ForeignKey(WaypointRoute, on_delete=models.CASCADE, related_name="waypoints")
    sequence = models.PositiveIntegerField()
    latitude = models.DecimalField(max_digits=12, decimal_places=8)
    longitude = models.DecimalField(max_digits=12, decimal_places=8)
    altitude = models.DecimalField(max_digits=10, decimal_places=2)
    speed = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    heading = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    hover_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "v2_waypoints"
        ordering = ["route_id", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["route", "sequence"], name="uniq_v2_route_waypoint_sequence"),
        ]


class InspectionMission(TimeStampedModel):
    tenant = models.ForeignKey("access.Tenant", on_delete=models.CASCADE, related_name="v2_inspection_missions")
    creator_department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="created_v2_missions")
    primary_resource_owner_department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="owned_resource_v2_missions",
    )
    route = models.ForeignKey(WaypointRoute, on_delete=models.PROTECT, related_name="missions")
    route_snapshot = models.JSONField(default=dict)
    name = models.CharField(max_length=128)
    status = models.CharField(max_length=16, choices=MissionStatus.choices, default=MissionStatus.PENDING)
    drone = models.ForeignKey(DroneResource, on_delete=models.PROTECT, related_name="v2_missions")
    dock = models.ForeignKey(DockResource, null=True, blank=True, on_delete=models.PROTECT, related_name="v2_missions")
    payload = models.ForeignKey(PayloadResource, null=True, blank=True, on_delete=models.PROTECT, related_name="v2_missions")
    pilot = models.ForeignKey(PilotProfile, on_delete=models.PROTECT, related_name="v2_missions")
    scheduled_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True, default="")
    failure_reason = models.TextField(blank=True, default="")
    remark = models.TextField(blank=True, default="")
    created_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_v2_inspection_missions",
    )

    class Meta:
        db_table = "v2_inspection_missions"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["tenant", "status"], name="idx_v2_mission_tenant_status"),
            models.Index(fields=["creator_department", "status"], name="idx_v2_mission_creator_status"),
            models.Index(fields=["primary_resource_owner_department", "status"], name="idx_v2_mission_owner_status"),
        ]


class MissionResourceAssignment(TimeStampedModel):
    mission = models.ForeignKey(InspectionMission, on_delete=models.CASCADE, related_name="resource_assignments")
    resource_type = models.CharField(max_length=16, choices=ResourceType.choices)
    resource_object_id = models.BigIntegerField()
    owner_department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="v2_mission_resource_assignments")

    class Meta:
        db_table = "v2_mission_resource_assignments"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["mission", "resource_type", "resource_object_id"],
                name="uniq_v2_mission_resource_assignment",
            ),
        ]
        indexes = [
            models.Index(fields=["resource_type", "resource_object_id"], name="idx_v2_mission_resource"),
            models.Index(fields=["owner_department"], name="idx_v2_mission_resource_owner"),
        ]


class FlightSession(TimeStampedModel):
    mission = models.OneToOneField(InspectionMission, on_delete=models.CASCADE, related_name="flight_session")
    status = models.CharField(max_length=16, choices=FlightSessionStatus.choices, default=FlightSessionStatus.RUNNING)
    drone = models.ForeignKey(DroneResource, on_delete=models.PROTECT, related_name="v2_flight_sessions")
    dock = models.ForeignKey(DockResource, null=True, blank=True, on_delete=models.PROTECT, related_name="v2_flight_sessions")
    payload = models.ForeignKey(PayloadResource, null=True, blank=True, on_delete=models.PROTECT, related_name="v2_flight_sessions")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    started_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="started_v2_flight_sessions",
    )
    ended_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ended_v2_flight_sessions",
    )

    class Meta:
        db_table = "v2_flight_sessions"
        ordering = ["-started_at", "-id"]
        indexes = [
            models.Index(fields=["status", "drone"], name="idx_v2_session_status_drone"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["drone"],
                condition=Q(status=FlightSessionStatus.RUNNING),
                name="uniq_v2_running_session_drone",
            ),
            models.UniqueConstraint(
                fields=["dock"],
                condition=Q(status=FlightSessionStatus.RUNNING, dock__isnull=False),
                name="uniq_v2_running_session_dock",
            ),
            models.UniqueConstraint(
                fields=["payload"],
                condition=Q(status=FlightSessionStatus.RUNNING, payload__isnull=False),
                name="uniq_v2_running_session_payload",
            ),
        ]


class FlightTelemetrySnapshot(TimeStampedModel):
    session = models.OneToOneField(FlightSession, on_delete=models.CASCADE, related_name="telemetry_snapshot")
    latitude = models.DecimalField(max_digits=12, decimal_places=8, null=True, blank=True)
    longitude = models.DecimalField(max_digits=12, decimal_places=8, null=True, blank=True)
    altitude = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    speed = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    heading = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    battery_percent = models.PositiveIntegerField(null=True, blank=True)
    reported_at = models.DateTimeField()
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "v2_flight_telemetry_snapshots"


class InspectionFlightRecord(TimeStampedModel):
    tenant = models.ForeignKey("access.Tenant", on_delete=models.CASCADE, related_name="v2_flight_records")
    mission = models.OneToOneField(InspectionMission, on_delete=models.PROTECT, related_name="flight_record")
    session = models.OneToOneField(FlightSession, on_delete=models.PROTECT, related_name="flight_record")
    flight_no = models.CharField(max_length=64, unique=True)
    creator_department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="created_v2_flight_records")
    primary_resource_owner_department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="owned_resource_v2_flight_records",
    )
    mission_name = models.CharField(max_length=128)
    route_name = models.CharField(max_length=128)
    drone_device_sn = models.CharField(max_length=128)
    drone_name = models.CharField(max_length=128, blank=True, default="")
    pilot_name = models.CharField(max_length=128)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    flight_duration = models.PositiveIntegerField(default=0)
    photo_count = models.PositiveIntegerField(default=0)
    video_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=FlightRecordStatus.choices, default=FlightRecordStatus.COMPLETED)
    remark = models.TextField(blank=True, default="")
    abnormal_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "v2_inspection_flight_records"
        ordering = ["-end_time", "-id"]
        indexes = [
            models.Index(fields=["tenant", "status"], name="idx_v2_record_tenant_status"),
            models.Index(fields=["drone_device_sn", "start_time", "end_time"], name="idx_v2_record_drone_time"),
        ]


class CloudMediaFile(TimeStampedModel):
    tenant = models.ForeignKey("access.Tenant", on_delete=models.CASCADE, related_name="v2_cloud_media_files")
    flight_record = models.ForeignKey(
        InspectionFlightRecord,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="media_files",
    )
    mission = models.ForeignKey(
        InspectionMission,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="media_files",
    )
    device_sn = models.CharField(max_length=128)
    cloud_file_id = models.CharField(max_length=256)
    media_type = models.CharField(max_length=16, choices=CloudMediaType.choices, default=CloudMediaType.OTHER)
    file_name = models.CharField(max_length=256, blank=True, default="")
    thumbnail_url = models.CharField(max_length=1000, blank=True, default="")
    preview_url = models.CharField(max_length=1000, blank=True, default="")
    download_url = models.CharField(max_length=1000, blank=True, default="")
    playback_url = models.CharField(max_length=1000, blank=True, default="")
    file_size = models.BigIntegerField(null=True, blank=True)
    captured_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "v2_cloud_media_files"
        ordering = ["-captured_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "cloud_file_id"], name="uniq_v2_cloud_media_file"),
        ]
        indexes = [
            models.Index(fields=["device_sn", "captured_at"], name="idx_v2_media_device_time"),
        ]
