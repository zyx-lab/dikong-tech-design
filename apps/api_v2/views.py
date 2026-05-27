from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.models import ScopeType
from apps.access.api_v1.authentication import BearerAuthSessionAuthentication
from apps.access.api_v1.context import resolve_tenant_request_context
from apps.access.exceptions import StandardForbidden
from apps.api_v1.business_response import (
    BusinessApiResponseMixin,
    StandardCode,
    standard_error_payload,
    validation_error_payload,
)
from apps.dji_bff.gateway import DjiGateway, DjiGatewayError
from apps.dji_bff.models import DjiCloudPlatform, DjiCloudPlatformStatus, DjiDeviceIndex, SyncStatus, TenantMediaIndex
from apps.dji_bff.tasks import (
    _device_is_online,
    _device_sn_from_payload,
    _file_name,
    _flight_record_for_mission,
    _int,
    _match_mission_for_media,
    _media_type,
    _string,
    _sync_video_count_for_flight_record,
)
from apps.drone.models import Drone, DroneStatus
from apps.drone.views import DroneViewSet, _drone_validate_or_respond
from apps.flight_record.models import FlightRecord
from apps.flight_record.views import FlightRecordViewSet
from apps.media_file.models import MediaFile, MediaType
from apps.media_file.views import MediaFileViewSet
from apps.mission.models import Mission, MissionStatus
from apps.mission.views import MissionViewSet
from apps.route.views import (
    RouteViewSet,
    _require_kmz_file,
    _sync_route_index,
    _upload_route_to_upstream,
)

from .serializers import (
    DjiCloudPlatformPatchSerializer,
    DjiCloudPlatformReadSerializer,
    DjiCloudPlatformWriteSerializer,
    V2AvailableDroneReadSerializer,
    V2DroneClaimSerializer,
    V2DroneReadSerializer,
    V2DroneUpdateSerializer,
    V2FlightRecordDetailSerializer,
    V2FlightRecordSummarySerializer,
    V2MediaFileBindMissionSerializer,
    V2MediaFileReadSerializer,
    V2MissionCreateSerializer,
    V2MissionReadSerializer,
    V2MissionUpdateSerializer,
    V2RouteCreateSerializer,
    V2RouteReadSerializer,
    V2RouteUpdateJsonSerializer,
    V2RouteUpdateSerializer,
)


def _require_tenant_admin(request):
    context = resolve_tenant_request_context(request)
    if "tenant_admin" not in context.role_codes:
        raise StandardForbidden()
    return context


def _platform_queryset(context):
    return DjiCloudPlatform.objects.filter(tenant=context.tenant).order_by("-is_default", "-id")


def _get_platform_or_404(context, platform_id: int):
    return _platform_queryset(context).filter(id=platform_id).first()


def _not_found_response():
    return Response(standard_error_payload(StandardCode.NOT_FOUND, "资源不存在", None), status=status.HTTP_404_NOT_FOUND)


def _duplicate_response(exc: IntegrityError):
    text = str(exc).lower()
    if "uniq_dji_cloud_platform_tenant_default" in text:
        errors = {"is_default": ["当前租户已有默认 DJI 平台"]}
    else:
        errors = {"name": ["当前租户下已存在同名 DJI 平台"]}
    return Response(standard_error_payload(StandardCode.DUPLICATE, "资源已存在", errors), status=status.HTTP_409_CONFLICT)


def _set_default(platform: DjiCloudPlatform):
    DjiCloudPlatform.objects.filter(tenant=platform.tenant, is_default=True).exclude(pk=platform.pk).update(
        is_default=False,
        updated_at=timezone.now(),
    )
    if not platform.is_default:
        platform.is_default = True
        platform.save(update_fields=["is_default", "updated_at"])


def _datetime_value(payload: dict, *keys: str):
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            parsed = parse_datetime(value.strip())
            if parsed is not None:
                if timezone.is_naive(parsed):
                    return timezone.make_aware(parsed, timezone.get_current_timezone())
                return parsed
    return None


def _platform_for_tenant_or_none(*, tenant, platform_id):
    try:
        normalized_platform_id = int(platform_id)
    except (TypeError, ValueError):
        return None
    return DjiCloudPlatform.objects.filter(tenant=tenant, id=normalized_platform_id).first()


def _device_index_defaults(payload: dict, *, now):
    return {
        "last_payload": payload,
        "last_seen_at": _datetime_value(payload, "last_seen_at", "lastSeenAt", "updated_at", "updatedAt") or now,
        "firmware_version": _string(payload, "firmware_version", "firmwareVersion"),
        "firmware_status": _string(payload, "firmware_status", "firmwareStatus"),
    }


def refresh_platform_device_indexes(platform: DjiCloudPlatform) -> set[str]:
    seen_device_sns: set[str] = set()
    online_device_sns: set[str] = set()
    now = timezone.now()

    for payload in DjiGateway(platform=platform).list_devices():
        if not isinstance(payload, dict):
            continue
        domain = _int(payload, "domain")
        if domain not in (None, 0):
            continue
        device_sn = _device_sn_from_payload(payload)
        if not device_sn:
            continue
        DjiDeviceIndex.objects.update_or_create(
            dji_platform=platform,
            device_sn=device_sn,
            defaults=_device_index_defaults(payload, now=now),
        )
        seen_device_sns.add(device_sn)
        if _device_is_online(payload):
            online_device_sns.add(device_sn)

    DjiDeviceIndex.objects.filter(dji_platform=platform).exclude(device_sn__in=seen_device_sns).delete()
    claimed_drones = Drone.objects.filter(dji_platform=platform, status=DroneStatus.CLAIMED)
    claimed_drones.filter(device_sn__in=online_device_sns).update(dji_online=True, updated_at=now)
    claimed_drones.exclude(device_sn__in=online_device_sns).update(dji_online=False, updated_at=now)
    return online_device_sns


def refresh_platform_media_indexes(*, tenant, platform: DjiCloudPlatform) -> None:
    gateway = DjiGateway(platform=platform)
    workspace_id = platform.workspace_id
    if not workspace_id and hasattr(gateway, "_workspace_id"):
        workspace_id = gateway._workspace_id()
    now = timezone.now()

    for payload in gateway.list_media_files():
        if not isinstance(payload, dict):
            continue

        dji_file_id = _string(payload, "file_id", "fileId", "id")
        device_sn = _device_sn_from_payload(payload)
        if not dji_file_id or not device_sn:
            continue

        claimed_drone = (
            Drone.objects.filter(
                tenant=tenant,
                dji_platform=platform,
                device_sn=device_sn,
                status=DroneStatus.CLAIMED,
            )
            .select_related("tenant")
            .first()
        )
        if claimed_drone is None:
            continue

        media_fields = {
            "tenant": tenant,
            "dji_platform": platform,
            "device_sn": device_sn,
            "media_type": _media_type(payload),
            "file_name": _file_name(payload),
            "file_url": f"dji://{dji_file_id}",
            "thumbnail_url": _string(payload, "thumbnail_url", "thumbnailUrl"),
            "file_size": _int(payload, "file_size", "fileSize"),
            "latitude": payload.get("latitude"),
            "longitude": payload.get("longitude"),
            "captured_at": _datetime_value(payload, "captured_at", "capturedAt", "create_time", "createTime"),
        }
        matched_mission = _match_mission_for_media(
            tenant=tenant,
            device_sn=device_sn,
            captured_at=media_fields["captured_at"],
        )
        if matched_mission is not None and matched_mission.dji_platform_id != platform.id:
            matched_mission = None

        with transaction.atomic():
            media_index = (
                TenantMediaIndex.objects.select_related("media_file")
                .filter(tenant=tenant, dji_platform=platform, dji_file_id=dji_file_id)
                .first()
            )
            if media_index is None:
                resolved_flight_record = _flight_record_for_mission(mission=matched_mission)
                media_file = MediaFile.objects.create(
                    **media_fields,
                    mission=matched_mission,
                    flight_record=resolved_flight_record,
                )
                TenantMediaIndex.objects.create(
                    tenant=tenant,
                    dji_platform=platform,
                    media_file=media_file,
                    workspace_id=workspace_id,
                    dji_file_id=dji_file_id,
                    device_sn=device_sn,
                    mission=matched_mission,
                    sync_status=SyncStatus.SYNCED,
                    last_sync_at=media_fields["captured_at"] or now,
                    error_msg="",
                )
                _sync_video_count_for_flight_record(flight_record=resolved_flight_record)
            else:
                media_file = media_index.media_file
                previous_flight_record = media_file.flight_record
                resolved_mission = media_index.mission or media_file.mission or matched_mission
                if resolved_mission is not None and resolved_mission.dji_platform_id != platform.id:
                    resolved_mission = None
                resolved_flight_record = previous_flight_record or _flight_record_for_mission(mission=resolved_mission)
                for field, value in media_fields.items():
                    setattr(media_file, field, value)
                media_file.mission = resolved_mission
                media_file.flight_record = resolved_flight_record
                media_file.save(
                    update_fields=[
                        "tenant",
                        "dji_platform",
                        "mission",
                        "flight_record",
                        "device_sn",
                        "media_type",
                        "file_name",
                        "file_url",
                        "thumbnail_url",
                        "file_size",
                        "latitude",
                        "longitude",
                        "captured_at",
                    ]
                )
                media_index.workspace_id = workspace_id
                media_index.mission = resolved_mission
                media_index.device_sn = device_sn
                media_index.sync_status = SyncStatus.SYNCED
                media_index.last_sync_at = media_fields["captured_at"] or now
                media_index.error_msg = ""
                media_index.save(
                    update_fields=[
                        "workspace_id",
                        "mission",
                        "device_sn",
                        "sync_status",
                        "last_sync_at",
                        "error_msg",
                        "updated_at",
                    ]
                )
                _sync_video_count_for_flight_record(flight_record=previous_flight_record)
                if resolved_flight_record != previous_flight_record:
                    _sync_video_count_for_flight_record(flight_record=resolved_flight_record)


class V2APIView(BusinessApiResponseMixin, APIView):
    authentication_classes = [BearerAuthSessionAuthentication]


class DjiPlatformListCreateView(V2APIView):
    def get(self, request):
        context = _require_tenant_admin(request)
        queryset = _platform_queryset(context)
        serializer = DjiCloudPlatformReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request):
        context = _require_tenant_admin(request)
        serializer = DjiCloudPlatformWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        is_default = bool(serializer.validated_data.get("is_default"))
        if not DjiCloudPlatform.objects.filter(tenant=context.tenant).exists():
            is_default = True
        try:
            platform = serializer.save(tenant=context.tenant, is_default=is_default)
            if platform.is_default:
                _set_default(platform)
        except IntegrityError as exc:
            return _duplicate_response(exc)
        return Response(DjiCloudPlatformReadSerializer(platform).data, status=status.HTTP_201_CREATED)


class DjiPlatformDetailView(V2APIView):
    def get(self, request, platform_id: int):
        context = _require_tenant_admin(request)
        platform = _get_platform_or_404(context, platform_id)
        if platform is None:
            return _not_found_response()
        return Response(DjiCloudPlatformReadSerializer(platform).data, status=status.HTTP_200_OK)

    @transaction.atomic
    def patch(self, request, platform_id: int):
        context = _require_tenant_admin(request)
        platform = _get_platform_or_404(context, platform_id)
        if platform is None:
            return _not_found_response()
        serializer = DjiCloudPlatformPatchSerializer(platform, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            platform = serializer.save()
            if serializer.validated_data.get("is_default") is True:
                _set_default(platform)
        except IntegrityError as exc:
            return _duplicate_response(exc)
        return Response(DjiCloudPlatformReadSerializer(platform).data, status=status.HTTP_200_OK)


class DjiPlatformTestConnectionView(V2APIView):
    def post(self, request, platform_id: int):
        context = _require_tenant_admin(request)
        platform = _get_platform_or_404(context, platform_id)
        if platform is None:
            return _not_found_response()
        try:
            workspace = DjiGateway(platform=platform).get_current_workspace()
        except DjiGatewayError as exc:
            platform.status = DjiCloudPlatformStatus.ERROR
            platform.last_checked_at = timezone.now()
            platform.save(update_fields=["status", "last_checked_at", "updated_at"])
            return Response(
                validation_error_payload({"detail": [str(exc)]}, msg="DJI 平台连接失败"),
                status=status.HTTP_400_BAD_REQUEST,
            )
        platform.refresh_from_db()
        platform.status = DjiCloudPlatformStatus.ACTIVE
        platform.last_checked_at = timezone.now()
        platform.save(update_fields=["status", "last_checked_at", "updated_at"])
        return Response(
            {
                "ok": True,
                "workspace": workspace if isinstance(workspace, dict) else {},
                "platform": DjiCloudPlatformReadSerializer(platform).data,
            },
            status=status.HTTP_200_OK,
        )


class DjiPlatformSetDefaultView(V2APIView):
    @transaction.atomic
    def post(self, request, platform_id: int):
        context = _require_tenant_admin(request)
        platform = _get_platform_or_404(context, platform_id)
        if platform is None:
            return _not_found_response()
        if platform.status == DjiCloudPlatformStatus.DISABLED:
            raise serializers.ValidationError({"status": ["已停用平台不能设为默认"]})
        _set_default(platform)
        platform.refresh_from_db()
        return Response(DjiCloudPlatformReadSerializer(platform).data, status=status.HTTP_200_OK)


class DjiPlatformDisableView(V2APIView):
    @transaction.atomic
    def post(self, request, platform_id: int):
        context = _require_tenant_admin(request)
        platform = _get_platform_or_404(context, platform_id)
        if platform is None:
            return _not_found_response()
        platform.status = DjiCloudPlatformStatus.DISABLED
        platform.is_default = False
        platform.save(update_fields=["status", "is_default", "updated_at"])
        return Response(DjiCloudPlatformReadSerializer(platform).data, status=status.HTTP_200_OK)


class V2DroneViewSet(DroneViewSet):
    def get_serializer_class(self):
        serializer_map = {
            "create": V2DroneClaimSerializer,
            "update": V2DroneUpdateSerializer,
        }
        return serializer_map.get(self.action, V2DroneReadSerializer)

    def get_queryset(self):
        queryset = super().get_queryset().filter(dji_platform__isnull=False).select_related("dji_platform")
        platform_id = self.request.query_params.get("platform_id")
        if platform_id:
            queryset = queryset.filter(dji_platform_id=platform_id)
        return queryset

    @action(detail=False, methods=["get"], url_path="available")
    def available(self, request, *args, **kwargs):
        decision = getattr(request, "_authz_decision", None)
        if decision is not None and decision.scope != ScopeType.ALL:
            return Response({"list": [], "total": 0}, status=status.HTTP_200_OK)

        tenant = self.get_current_tenant()
        platform = _platform_for_tenant_or_none(tenant=tenant, platform_id=request.query_params.get("platform_id"))
        if platform is None:
            return Response(
                validation_error_payload({"platform_id": ["必须提供当前租户下有效的 DJI 平台 ID"]}),
                status=status.HTTP_400_BAD_REQUEST,
            )

        refresh_platform_device_indexes(platform)
        claimed_device_sns = list(
            Drone.objects.exclude(status=DroneStatus.RELEASED)
            .filter(tenant=tenant, dji_platform=platform)
            .values_list("device_sn", flat=True)
        )
        queryset = (
            DjiDeviceIndex.objects.filter(dji_platform=platform)
            .exclude(device_sn__in=claimed_device_sns)
            .order_by("device_sn")
        )
        page = self.paginate_queryset(queryset)
        serializer = V2AvailableDroneReadSerializer(page, many=True, context={"request": request})
        return self.get_paginated_response(serializer.data)

    def _refresh_queryset_platforms(self, queryset):
        platform_ids = list(queryset.values_list("dji_platform_id", flat=True).distinct())
        platforms = DjiCloudPlatform.objects.filter(id__in=[item for item in platform_ids if item])
        for platform in platforms:
            refresh_platform_device_indexes(platform)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        self._refresh_queryset_platforms(queryset)
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        error_response = _drone_validate_or_respond(self, serializer)
        if error_response is not None:
            return error_response

        try:
            drone = self.perform_create(serializer)
        except IntegrityError as exc:
            return self._duplicate_integrity_response(serializer, exc)
        return Response(V2DroneReadSerializer(drone, context={"request": request}).data, status=status.HTTP_201_CREATED)


class V2RouteViewSet(RouteViewSet):
    def get_serializer_class(self):
        if self.action == "create":
            return V2RouteCreateSerializer
        if self.action == "update":
            return V2RouteUpdateSerializer
        return V2RouteReadSerializer

    def get_queryset(self):
        queryset = super().get_queryset().filter(dji_platform__isnull=False)
        platform_id = self.request.query_params.get("platform_id")
        if platform_id:
            queryset = queryset.filter(dji_platform_id=platform_id)
        return queryset

    def get_parsers(self):
        parser_classes = list(self.parser_classes)
        if "put" in getattr(self, "action_map", {}) and "post" not in getattr(self, "action_map", {}):
            from rest_framework import parsers

            parser_classes = [parsers.JSONParser, *parser_classes]
        return [parser() for parser in parser_classes]

    def _platform_from_create_serializer(self, serializer):
        tenant = self.get_current_tenant()
        platform_id = serializer.validated_data.get("platform_id")
        platform = _platform_for_tenant_or_none(tenant=tenant, platform_id=platform_id)
        if platform is None:
            raise serializers.ValidationError({"platform_id": ["必须提供当前租户下有效的 DJI 平台 ID"]})
        return platform

    def _gateway_for_route(self, route=None):
        return DjiGateway(platform=getattr(route, "dji_platform", None))

    def perform_create(self, serializer, *, gateway: DjiGateway, cleanup_state: dict[str, object]):
        tenant = self.get_current_tenant()
        platform = self._platform_from_create_serializer(serializer)
        kmz_file = _require_kmz_file(serializer)

        serializer.validated_data.pop("platform_id", None)
        route = serializer.save(tenant=tenant, dji_platform=platform)
        cleanup_state["route_id"] = route.id
        dji_wayline_id, download_url = _upload_route_to_upstream(
            gateway=gateway,
            route_id=route.id,
            route_name=route.name,
            kmz_file=kmz_file,
        )
        cleanup_state["wayline_id"] = dji_wayline_id
        download_url = self._verify_uploaded_route_file(
            gateway=gateway,
            dji_wayline_id=dji_wayline_id,
            download_url=download_url,
        )
        _sync_route_index(
            tenant=tenant,
            route=route,
            dji_platform=platform,
            dji_wayline_id=dji_wayline_id,
            download_url=download_url,
            workspace_id=gateway._workspace_id(),
        )
        cleanup_state["wayline_id"] = ""
        cleanup_state["route_id"] = None
        return route

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        platform = self._platform_from_create_serializer(serializer)
        gateway = DjiGateway(platform=platform)
        cleanup_state: dict[str, object] = {"wayline_id": "", "route_id": None}
        try:
            route = self.perform_create(serializer, gateway=gateway, cleanup_state=cleanup_state)
        except Exception:
            self._cleanup_uploaded_wayline_after_failure(gateway=gateway, wayline_id=cleanup_state["wayline_id"])
            self._cleanup_created_route_after_failure(route_id=cleanup_state["route_id"])
            raise
        return Response(V2RouteReadSerializer(route, context={"request": request}).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer, *, gateway: DjiGateway | None = None, cleanup_state: dict[str, str]):
        return super().perform_update(serializer, gateway=gateway, cleanup_state=cleanup_state)


class V2MissionViewSet(MissionViewSet):
    def get_serializer_class(self):
        if self.action == "create":
            return V2MissionCreateSerializer
        if self.action == "update":
            return V2MissionUpdateSerializer
        return V2MissionReadSerializer

    def get_queryset(self):
        queryset = super().get_queryset().filter(dji_platform__isnull=False)
        platform_id = self.request.query_params.get("platform_id")
        if platform_id:
            queryset = queryset.filter(dji_platform_id=platform_id)
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=False)
        if serializer.errors:
            return Response(validation_error_payload(serializer.errors), status=status.HTTP_400_BAD_REQUEST)

        mission = self.perform_create(serializer)
        return Response(V2MissionReadSerializer(mission, context={"request": request}).data, status=status.HTTP_201_CREATED)


class V2FlightRecordViewSet(FlightRecordViewSet):
    def get_serializer_class(self):
        if self.action == "retrieve":
            return V2FlightRecordDetailSerializer
        return V2FlightRecordSummarySerializer

    def get_queryset(self):
        queryset = super().get_queryset().filter(dji_platform__isnull=False)
        platform_id = self.request.query_params.get("platform_id")
        if platform_id:
            queryset = queryset.filter(dji_platform_id=platform_id)
        return queryset


class V2MediaFileViewSet(MediaFileViewSet):
    serializer_class = V2MediaFileReadSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        if self.action == "bind_mission":
            return V2MediaFileBindMissionSerializer
        return V2MediaFileReadSerializer

    def get_queryset(self):
        queryset = (
            self.scope_queryset_to_tenant(MediaFile.objects.select_related("flight_record", "mission", "dji_index", "dji_platform"))
            .filter(is_deleted=False, dji_platform__isnull=False, dji_index__isnull=False)
            .order_by("-id")
        )
        params = self.request.query_params
        platform_id = params.get("platform_id")
        if platform_id:
            queryset = queryset.filter(dji_platform_id=platform_id)
        for query_key, model_field in (
            ("flight_record_id", "flight_record_id"),
            ("mission_id", "mission_id"),
            ("device_sn", "device_sn"),
            ("media_type", "media_type"),
            ("file_name", "file_name__icontains"),
        ):
            value = params.get(query_key)
            if value:
                queryset = queryset.filter(**{model_field: value})
        return self.apply_scope(queryset)

    def _request_platform(self, request):
        tenant = self.get_current_tenant()
        platform = _platform_for_tenant_or_none(tenant=tenant, platform_id=request.query_params.get("platform_id"))
        if platform is None:
            raise serializers.ValidationError({"platform_id": ["必须提供当前租户下有效的 DJI 平台 ID"]})
        return platform

    def list(self, request, *args, **kwargs):
        platform = self._request_platform(request)
        refresh_platform_media_indexes(tenant=self.get_current_tenant(), platform=platform)
        queryset = self.filter_queryset(self.get_queryset().filter(dji_platform=platform))
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=["get"])
    def download(self, request, *args, **kwargs):
        from django.http import HttpResponseRedirect

        media_file = self.get_object()
        download_url = DjiGateway(platform=media_file.dji_platform).get_media_url(media_file.dji_index.dji_file_id)
        return HttpResponseRedirect(download_url)

    @action(detail=True, methods=["get"])
    def playback(self, request, *args, **kwargs):
        from django.http import HttpResponseRedirect

        media_file = self.get_object()
        if media_file.media_type != MediaType.VIDEO:
            return Response(
                validation_error_payload({"media_type": ["该媒体不支持 playback"]}),
                status=status.HTTP_400_BAD_REQUEST,
            )
        playback_url = DjiGateway(platform=media_file.dji_platform).get_media_playback_url(media_file.dji_index.dji_file_id)
        return HttpResponseRedirect(playback_url)

    @action(detail=True, methods=["get"], url_path="playback-url")
    def playback_url(self, request, *args, **kwargs):
        media_file = self.get_object()
        if media_file.media_type != MediaType.VIDEO:
            return Response(
                validation_error_payload({"media_type": ["该媒体不支持 playback"]}),
                status=status.HTTP_400_BAD_REQUEST,
            )
        playback_url = DjiGateway(platform=media_file.dji_platform).get_media_playback_url(media_file.dji_index.dji_file_id)
        return Response({"playback_url": playback_url}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="preview-url")
    def preview_url(self, request, *args, **kwargs):
        media_file = self.get_object()
        if media_file.media_type != MediaType.PHOTO:
            return Response(
                validation_error_payload({"media_type": ["该媒体不支持 preview"]}),
                status=status.HTTP_400_BAD_REQUEST,
            )
        preview_url = DjiGateway(platform=media_file.dji_platform).get_media_preview_url(media_file.dji_index.dji_file_id)
        return Response({"preview_url": preview_url}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="bind-mission")
    @transaction.atomic
    def bind_mission(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mission = serializer.validated_data["mission"]
        media_ids = serializer.validated_data["media_file_ids"]

        queryset = self.apply_scope(
            self.scope_queryset_to_tenant(
                MediaFile.objects.select_related("mission", "flight_record", "dji_index", "dji_platform")
            ).filter(is_deleted=False, dji_index__isnull=False, dji_platform=mission.dji_platform, id__in=media_ids)
        )
        media_files = list(queryset.select_for_update().order_by("id"))
        if len(media_files) != len(media_ids):
            return Response(
                validation_error_payload({"media_file_ids": ["存在不存在、已删除、无权限或 DJI 平台不匹配的媒体记录"]}),
                status=status.HTTP_400_BAD_REQUEST,
            )

        mismatched_ids = [item.id for item in media_files if item.device_sn != mission.device_sn]
        if mismatched_ids:
            return Response(
                validation_error_payload({"media_file_ids": [f"以下媒体 device_sn 不匹配: {mismatched_ids}"]}),
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated_ids = []
        for media_file in media_files:
            media_file.mission = mission
            media_file.save(update_fields=["mission"])
            if getattr(media_file, "dji_index", None) is not None:
                media_file.dji_index.mission = mission
                media_file.dji_index.save(update_fields=["mission", "updated_at"])
            updated_ids.append(media_file.id)

        return Response(
            {"mission_id": mission.id, "media_file_ids": updated_ids, "updated_count": len(updated_ids)},
            status=status.HTTP_200_OK,
        )
