from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers, status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.access.api_v1.authentication import BearerAuthSessionAuthentication
from apps.access.exceptions import StandardForbidden
from apps.access.models import DirectoryStatus
from apps.api_v1.business_response import BusinessApiResponseMixin, StandardCode, standard_error_payload
from apps.iam_v2.models import Department
from apps.iam_v2.models import ResourceShareGroup, ResourceShareGroupTargetDepartment
from apps.iam_v2.services import (
    is_department_admin,
    is_platform_super_admin,
    require_v2_operation_permission,
    resolve_v2_context,
)
from apps.resource_v2.audit import log_v2_action
from apps.resource_v2.gateway import DjiConnectionGateway
from apps.resource_v2.models import (
    BindingStatus,
    DjiConnection,
    DjiConnectionStatus,
    ResourceBinding,
    ResourceSharePermission,
    ResourceType,
    V2AuditLog,
)
from apps.resource_v2.serializers import (
    AuditLogReadSerializer,
    BindingCreateSerializer,
    BindingReadSerializer,
    DjiConnectionCredentialReadSerializer,
    DjiConnectionReadSerializer,
    DjiConnectionWriteSerializer,
    ShareGroupCreateSerializer,
    ShareGroupReadSerializer,
    ShareGroupResourceCreateSerializer,
    ShareGroupResourceReadSerializer,
    ShareGroupResourceUpdateSerializer,
    ShareGroupTargetCreateSerializer,
    ShareGroupTargetReadSerializer,
    ShareGroupUpdateSerializer,
    V2FlightRecordReadSerializer,
    V2MediaFileReadSerializer,
    V2MissionReadSerializer,
    V2RouteReadSerializer,
    serialize_resource_binding,
    upsert_resource_from_payload,
)
from apps.resource_v2.services import (
    get_resource,
    mark_binding_created,
    mark_binding_unbound,
    require_bind_connection,
    require_manage_connection,
    require_unbind,
    visible_bindings_queryset,
    visible_flight_records_queryset,
    visible_media_files_queryset,
    visible_missions_queryset,
    visible_routes_queryset,
)


class EmptySchemaSerializer(serializers.Serializer):
    pass


class V2ResourceAPIView(BusinessApiResponseMixin, GenericAPIView):
    authentication_classes = [BearerAuthSessionAuthentication]
    serializer_class = EmptySchemaSerializer


def _not_found_response():
    return Response(standard_error_payload(StandardCode.NOT_FOUND, "资源不存在", None), status=status.HTTP_404_NOT_FOUND)


def _duplicate_response(errors=None):
    return Response(standard_error_payload(StandardCode.DUPLICATE, "资源已存在", errors), status=status.HTTP_409_CONFLICT)


def _share_group_or_404(id: int):
    group = ResourceShareGroup.objects.select_related("owner_department", "owner_department__tenant").filter(pk=id).first()
    if group is None:
        return None
    return group


def _require_share_group_owner_admin(context, group: ResourceShareGroup):
    if not (is_department_admin(context) and group.owner_department_id == context.department.id):
        raise StandardForbidden()


@extend_schema_view(
    get=extend_schema(operation_id="v2_resource_dji_connections_list"),
    post=extend_schema(operation_id="v2_resource_dji_connections_create"),
)
class DjiConnectionListCreateView(V2ResourceAPIView):
    def get(self, request):
        context = resolve_v2_context(request)
        if not (is_platform_super_admin(context) or is_department_admin(context)):
            raise StandardForbidden()
        queryset = DjiConnection.objects.select_related("owner_department")
        if not is_platform_super_admin(context):
            queryset = queryset.filter(owner_department=context.department)
        queryset = queryset.order_by("-id")
        serializer = DjiConnectionReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)

    def post(self, request):
        context = resolve_v2_context(request)
        serializer = DjiConnectionWriteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        owner_department = context.department
        owner_department_id = serializer.validated_data.get("ownerDepartmentId")
        if owner_department_id:
            if not is_platform_super_admin(context) and owner_department_id != context.department.id:
                raise StandardForbidden()
            owner_department = Department.objects.filter(pk=owner_department_id, status=DirectoryStatus.ACTIVE).first()
            if owner_department is None:
                raise serializers.ValidationError({"ownerDepartmentId": ["部门不存在"]})
        require_manage_connection(context, DjiConnection(owner_department=owner_department))
        serializer.validated_data["owner_department"] = owner_department
        try:
            connection = serializer.save()
        except IntegrityError as exc:
            return _duplicate_response({"detail": str(exc)})
        log_v2_action(
            request=request,
            context=context,
            action="create_dji_connection",
            target_type="dji_connection",
            target_id=connection.id,
            resource_owner_department=connection.owner_department,
            after_data=DjiConnectionReadSerializer(connection).data,
        )
        return Response(DjiConnectionReadSerializer(connection).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(operation_id="v2_resource_dji_connections_retrieve"),
    put=extend_schema(operation_id="v2_resource_dji_connections_update"),
)
class DjiConnectionDetailView(V2ResourceAPIView):
    def _connection(self, id):
        return DjiConnection.objects.select_related("owner_department").filter(pk=id).first()

    def get(self, request, id: int):
        context = resolve_v2_context(request)
        connection = self._connection(id)
        if connection is None:
            return _not_found_response()
        require_manage_connection(context, connection)
        include_credentials = str(request.query_params.get("includeCredentials", "")).lower() == "true"
        if include_credentials:
            log_v2_action(
                request=request,
                context=context,
                action="view_plaintext_dji_credentials",
                target_type="dji_connection",
                target_id=connection.id,
                resource_owner_department=connection.owner_department,
            )
            serializer_class = DjiConnectionCredentialReadSerializer
        else:
            serializer_class = DjiConnectionReadSerializer
        return Response(serializer_class(connection).data, status=status.HTTP_200_OK)

    def put(self, request, id: int):
        context = resolve_v2_context(request)
        connection = self._connection(id)
        if connection is None:
            return _not_found_response()
        require_manage_connection(context, connection)
        serializer = DjiConnectionWriteSerializer(connection, data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        before_data = DjiConnectionReadSerializer(connection).data
        owner_department_id = serializer.validated_data.get("ownerDepartmentId")
        if owner_department_id and owner_department_id != connection.owner_department_id:
            if not is_platform_super_admin(context):
                raise StandardForbidden()
            owner_department = Department.objects.filter(pk=owner_department_id, status=DirectoryStatus.ACTIVE).first()
            if owner_department is None:
                raise serializers.ValidationError({"ownerDepartmentId": ["部门不存在"]})
            serializer.validated_data["owner_department"] = owner_department
        try:
            connection = serializer.save()
        except IntegrityError as exc:
            return _duplicate_response({"detail": str(exc)})
        log_v2_action(
            request=request,
            context=context,
            action="update_dji_connection",
            target_type="dji_connection",
            target_id=connection.id,
            resource_owner_department=connection.owner_department,
            before_data=before_data,
            after_data=DjiConnectionReadSerializer(connection).data,
        )
        return Response(DjiConnectionReadSerializer(connection).data, status=status.HTTP_200_OK)


class DjiConnectionDiscoverView(V2ResourceAPIView):
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        connection = DjiConnection.objects.select_related("owner_department").filter(pk=id).first()
        if connection is None:
            return _not_found_response()
        require_manage_connection(context, connection)
        discovered = DjiConnectionGateway(connection).discover()
        drones = [
            upsert_resource_from_payload(ResourceType.DRONE, payload)
            for payload in discovered.get("drones", [])
            if isinstance(payload, dict)
        ]
        docks = [
            upsert_resource_from_payload(ResourceType.DOCK, payload)
            for payload in discovered.get("docks", [])
            if isinstance(payload, dict)
        ]
        connection.status = DjiConnectionStatus.ACTIVE
        connection.last_checked_at = timezone.now()
        connection.save(update_fields=["status", "last_checked_at", "updated_at"])
        data = {
            "drones": [
                {
                    "id": item.id,
                    "deviceSn": item.device_sn,
                    "name": item.name,
                    "model": item.model,
                    "onlineStatus": item.online_status,
                }
                for item in drones
            ],
            "docks": [
                {
                    "id": item.id,
                    "deviceSn": item.device_sn,
                    "name": item.name,
                    "model": item.model,
                    "onlineStatus": item.online_status,
                }
                for item in docks
            ],
        }
        return Response(data, status=status.HTTP_200_OK)


class ResourceListView(V2ResourceAPIView):
    resource_type = None

    def get(self, request):
        context = resolve_v2_context(request)
        require_v2_operation_permission(context, "view")
        queryset = visible_bindings_queryset(context, resource_type=self.resource_type)
        items = [serialize_resource_binding(binding, context=context) for binding in queryset]
        return Response({"list": items, "total": len(items)}, status=status.HTTP_200_OK)


class DroneResourceListView(ResourceListView):
    resource_type = ResourceType.DRONE


class DockResourceListView(ResourceListView):
    resource_type = ResourceType.DOCK


def _int_query_param(params, name: str):
    value = params.get(name)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise serializers.ValidationError({name: ["必须是整数"]}) from exc


class BusinessReadOnlyView(V2ResourceAPIView):
    serializer_class = EmptySchemaSerializer
    read_serializer_class = None

    def get_queryset_for_context(self, context):
        raise NotImplementedError

    def apply_query_filters(self, queryset, params):
        return queryset

    def get(self, request, id: int | None = None):
        context = resolve_v2_context(request)
        require_v2_operation_permission(context, "view")
        queryset = self.get_queryset_for_context(context)
        if id is not None:
            instance = queryset.filter(pk=id).first()
            if instance is None:
                return _not_found_response()
            return Response(self.read_serializer_class(instance).data, status=status.HTTP_200_OK)
        queryset = self.apply_query_filters(queryset, request.query_params)
        serializer = self.read_serializer_class(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)


class BusinessRouteReadView(BusinessReadOnlyView):
    read_serializer_class = V2RouteReadSerializer

    def get_queryset_for_context(self, context):
        return visible_routes_queryset(context)

    def apply_query_filters(self, queryset, params):
        keywords = str(params.get("keywords") or "").strip()
        if keywords:
            queryset = queryset.filter(name__icontains=keywords)
        return queryset


class BusinessRouteListView(BusinessRouteReadView):
    @extend_schema(operation_id="v2_resource_routes_list", responses=V2RouteReadSerializer)
    def get(self, request):
        return super().get(request)


class BusinessRouteDetailView(BusinessRouteReadView):
    @extend_schema(operation_id="v2_resource_routes_retrieve", responses=V2RouteReadSerializer)
    def get(self, request, id: int):
        return super().get(request, id=id)


class BusinessMissionReadView(BusinessReadOnlyView):
    read_serializer_class = V2MissionReadSerializer

    def get_queryset_for_context(self, context):
        return visible_missions_queryset(context)

    def apply_query_filters(self, queryset, params):
        route_id = _int_query_param(params, "routeId")
        if route_id is not None:
            queryset = queryset.filter(route_id=route_id)
        status_value = _int_query_param(params, "status")
        if status_value is not None:
            queryset = queryset.filter(status=status_value)
        device_sn = str(params.get("deviceSn") or "").strip()
        if device_sn:
            queryset = queryset.filter(Q(device_sn=device_sn) | Q(drone__device_sn=device_sn))
        keywords = str(params.get("keywords") or "").strip()
        if keywords:
            queryset = queryset.filter(
                Q(name__icontains=keywords)
                | Q(route_name__icontains=keywords)
                | Q(drone_name__icontains=keywords)
                | Q(device_sn__icontains=keywords)
            )
        return queryset.distinct()


class BusinessMissionListView(BusinessMissionReadView):
    @extend_schema(operation_id="v2_resource_missions_list", responses=V2MissionReadSerializer)
    def get(self, request):
        return super().get(request)


class BusinessMissionDetailView(BusinessMissionReadView):
    @extend_schema(operation_id="v2_resource_missions_retrieve", responses=V2MissionReadSerializer)
    def get(self, request, id: int):
        return super().get(request, id=id)


class BusinessFlightRecordReadView(BusinessReadOnlyView):
    read_serializer_class = V2FlightRecordReadSerializer

    def get_queryset_for_context(self, context):
        return visible_flight_records_queryset(context)

    def apply_query_filters(self, queryset, params):
        mission_id = _int_query_param(params, "missionId")
        if mission_id is not None:
            queryset = queryset.filter(mission_id=mission_id)
        status_value = _int_query_param(params, "status")
        if status_value is not None:
            queryset = queryset.filter(status=status_value)
        device_sn = str(params.get("deviceSn") or "").strip()
        if device_sn:
            queryset = queryset.filter(
                Q(device_sn=device_sn)
                | Q(drone__device_sn=device_sn)
                | Q(mission__device_sn=device_sn)
                | Q(mission__drone__device_sn=device_sn)
            )
        flight_no = str(params.get("flightNo") or "").strip()
        if flight_no:
            queryset = queryset.filter(flight_no__icontains=flight_no)
        return queryset.distinct()


class BusinessFlightRecordListView(BusinessFlightRecordReadView):
    @extend_schema(operation_id="v2_resource_flight_records_list", responses=V2FlightRecordReadSerializer)
    def get(self, request):
        return super().get(request)


class BusinessFlightRecordDetailView(BusinessFlightRecordReadView):
    @extend_schema(operation_id="v2_resource_flight_records_retrieve", responses=V2FlightRecordReadSerializer)
    def get(self, request, id: int):
        return super().get(request, id=id)


class BusinessMediaFileReadView(BusinessReadOnlyView):
    read_serializer_class = V2MediaFileReadSerializer

    def get_queryset_for_context(self, context):
        return visible_media_files_queryset(context)

    def apply_query_filters(self, queryset, params):
        flight_record_id = _int_query_param(params, "flightRecordId")
        if flight_record_id is not None:
            queryset = queryset.filter(flight_record_id=flight_record_id)
        mission_id = _int_query_param(params, "missionId")
        if mission_id is not None:
            queryset = queryset.filter(mission_id=mission_id)
        media_type = _int_query_param(params, "mediaType")
        if media_type is not None:
            queryset = queryset.filter(media_type=media_type)
        device_sn = str(params.get("deviceSn") or "").strip()
        if device_sn:
            queryset = queryset.filter(
                Q(device_sn=device_sn)
                | Q(flight_record__device_sn=device_sn)
                | Q(flight_record__drone__device_sn=device_sn)
                | Q(mission__device_sn=device_sn)
                | Q(mission__drone__device_sn=device_sn)
            )
        file_name = str(params.get("fileName") or "").strip()
        if file_name:
            queryset = queryset.filter(file_name__icontains=file_name)
        return queryset.distinct()


class BusinessMediaFileListView(BusinessMediaFileReadView):
    @extend_schema(operation_id="v2_resource_media_files_list", responses=V2MediaFileReadSerializer)
    def get(self, request):
        return super().get(request)


class BusinessMediaFileDetailView(BusinessMediaFileReadView):
    @extend_schema(operation_id="v2_resource_media_files_retrieve", responses=V2MediaFileReadSerializer)
    def get(self, request, id: int):
        return super().get(request, id=id)


class BindingListCreateView(V2ResourceAPIView):
    @transaction.atomic
    def post(self, request):
        context = resolve_v2_context(request)
        serializer = BindingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resource_type = serializer.validated_data["resourceType"]
        resource_id = serializer.validated_data["resourceId"]
        connection = (
            DjiConnection.objects.select_for_update()
            .select_related("owner_department")
            .filter(pk=serializer.validated_data["djiConnectionId"])
            .first()
        )
        if connection is None:
            return _not_found_response()
        require_bind_connection(context, connection)
        get_resource(resource_type, resource_id)
        existing = (
            ResourceBinding.objects.select_for_update()
            .select_related("owner_department")
            .filter(resource_type=resource_type, resource_object_id=resource_id, status=BindingStatus.ACTIVE)
            .first()
        )
        if existing is not None:
            return Response(
                standard_error_payload(
                    StandardCode.STATE_CONFLICT,
                    "当前资源已被其他部门绑定",
                    {
                        "occupyingDepartment": {
                            "id": existing.owner_department_id,
                            "name": existing.owner_department.name,
                            "path": existing.owner_department.path,
                        }
                    },
                ),
                status=status.HTTP_409_CONFLICT,
            )
        binding = ResourceBinding.objects.create(
            resource_type=resource_type,
            resource_object_id=resource_id,
            owner_department=connection.owner_department,
            dji_connection=connection,
            status=BindingStatus.ACTIVE,
            bound_by_user=request.user,
            bound_at=timezone.now(),
        )
        mark_binding_created(binding=binding, context=context, request=request)
        return Response(BindingReadSerializer(binding).data, status=status.HTTP_201_CREATED)


class BindingDetailView(V2ResourceAPIView):
    @transaction.atomic
    def delete(self, request, id: int):
        context = resolve_v2_context(request)
        binding = (
            ResourceBinding.objects.select_for_update()
            .select_related("owner_department", "dji_connection")
            .filter(pk=id, status=BindingStatus.ACTIVE)
            .first()
        )
        if binding is None:
            return _not_found_response()
        require_unbind(context, binding)
        mark_binding_unbound(binding=binding, context=context, request=request)
        binding.status = BindingStatus.UNBOUND
        binding.unbound_by_user = request.user
        binding.unbound_at = timezone.now()
        binding.save(update_fields=["status", "unbound_by_user", "unbound_at", "updated_at"])
        return Response(BindingReadSerializer(binding).data, status=status.HTTP_200_OK)


class ShareGroupListCreateView(V2ResourceAPIView):
    def get(self, request):
        context = resolve_v2_context(request)
        queryset = ResourceShareGroup.objects.select_related("owner_department").order_by("-id")
        if is_platform_super_admin(context):
            pass
        elif is_department_admin(context):
            queryset = queryset.filter(owner_department=context.department)
        else:
            raise StandardForbidden()
        serializer = ShareGroupReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request):
        context = resolve_v2_context(request)
        if not is_department_admin(context):
            raise StandardForbidden()
        serializer = ShareGroupCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            group = ResourceShareGroup.objects.create(
                owner_department=context.department,
                name=serializer.validated_data["name"],
            )
        except IntegrityError as exc:
            return _duplicate_response({"detail": str(exc)})
        data = ShareGroupReadSerializer(group).data
        log_v2_action(
            request=request,
            context=context,
            action="create_share_group",
            target_type="resource_share_group",
            target_id=group.id,
            resource_owner_department=group.owner_department,
            after_data=data,
        )
        return Response(data, status=status.HTTP_201_CREATED)


class ShareGroupDetailView(V2ResourceAPIView):
    @transaction.atomic
    def put(self, request, id: int):
        context = resolve_v2_context(request)
        group = _share_group_or_404(id)
        if group is None:
            return _not_found_response()
        _require_share_group_owner_admin(context, group)
        serializer = ShareGroupUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before_data = ShareGroupReadSerializer(group).data
        group.name = serializer.validated_data["name"]
        group.status = serializer.validated_data["status"]
        try:
            group.save(update_fields=["name", "status", "updated_at"])
        except IntegrityError as exc:
            return _duplicate_response({"detail": str(exc)})
        data = ShareGroupReadSerializer(group).data
        log_v2_action(
            request=request,
            context=context,
            action="update_share_group",
            target_type="resource_share_group",
            target_id=group.id,
            resource_owner_department=group.owner_department,
            before_data=before_data,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)


class ShareGroupDepartmentListCreateView(V2ResourceAPIView):
    @transaction.atomic
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        group = _share_group_or_404(id)
        if group is None:
            return _not_found_response()
        _require_share_group_owner_admin(context, group)
        serializer = ShareGroupTargetCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        department = Department.objects.filter(
            pk=serializer.validated_data["departmentId"],
            status=DirectoryStatus.ACTIVE,
        ).first()
        if department is None:
            raise serializers.ValidationError({"departmentId": ["目标部门不存在或未启用"]})
        if department.tenant_id != group.owner_department.tenant_id:
            raise serializers.ValidationError({"departmentId": ["目标部门必须与共享组属于同一 tenant"]})
        if ResourceShareGroupTargetDepartment.objects.filter(share_group=group, department=department).exists():
            return _duplicate_response({"departmentId": ["目标部门已在共享组中"]})
        try:
            target = ResourceShareGroupTargetDepartment.objects.create(share_group=group, department=department)
        except IntegrityError as exc:
            return _duplicate_response({"detail": str(exc)})
        data = ShareGroupTargetReadSerializer(target).data
        log_v2_action(
            request=request,
            context=context,
            action="add_share_group_target_department",
            target_type="resource_share_group_target_department",
            target_id=target.id,
            resource_owner_department=group.owner_department,
            after_data=data,
        )
        return Response(data, status=status.HTTP_201_CREATED)


class ShareGroupDepartmentDetailView(V2ResourceAPIView):
    @transaction.atomic
    def delete(self, request, id: int, department_id: int):
        context = resolve_v2_context(request)
        group = _share_group_or_404(id)
        if group is None:
            return _not_found_response()
        _require_share_group_owner_admin(context, group)
        target = (
            ResourceShareGroupTargetDepartment.objects.select_related("department")
            .filter(share_group=group, department_id=department_id)
            .first()
        )
        if target is None:
            return _not_found_response()
        data = ShareGroupTargetReadSerializer(target).data
        target_id = target.id
        target.delete()
        log_v2_action(
            request=request,
            context=context,
            action="remove_share_group_target_department",
            target_type="resource_share_group_target_department",
            target_id=target_id,
            resource_owner_department=group.owner_department,
            before_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)


class ShareGroupResourceListCreateView(V2ResourceAPIView):
    @transaction.atomic
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        group = _share_group_or_404(id)
        if group is None:
            return _not_found_response()
        _require_share_group_owner_admin(context, group)
        serializer = ShareGroupResourceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resource_type = serializer.validated_data["resourceType"]
        resource_id = serializer.validated_data["resourceId"]
        get_resource(resource_type, resource_id)
        binding = (
            ResourceBinding.objects.select_related("owner_department")
            .filter(resource_type=resource_type, resource_object_id=resource_id, status=BindingStatus.ACTIVE)
            .first()
        )
        if binding is None:
            raise serializers.ValidationError({"resourceId": ["资源尚未绑定"]})
        if binding.owner_department_id != group.owner_department_id:
            raise StandardForbidden()
        if ResourceSharePermission.objects.filter(
            share_group=group,
            resource_type=resource_type,
            resource_object_id=resource_id,
        ).exists():
            return _duplicate_response({"resourceId": ["资源已在共享组中"]})
        try:
            resource_share = ResourceSharePermission.objects.create(
                share_group=group,
                resource_type=resource_type,
                resource_object_id=resource_id,
                permissions=serializer.validated_data["permissions"],
            )
        except IntegrityError as exc:
            return _duplicate_response({"detail": str(exc)})
        data = ShareGroupResourceReadSerializer(resource_share).data
        log_v2_action(
            request=request,
            context=context,
            action="share_resource_to_group",
            target_type="resource_share_permission",
            target_id=resource_share.id,
            resource_owner_department=group.owner_department,
            resource_type=resource_type,
            resource_object_id=resource_id,
            after_data=data,
        )
        return Response(data, status=status.HTTP_201_CREATED)


class ShareGroupResourceDetailView(V2ResourceAPIView):
    def _resource_share(self, *, group, resource_share_id):
        return ResourceSharePermission.objects.filter(share_group=group, pk=resource_share_id).first()

    @transaction.atomic
    def put(self, request, id: int, resource_share_id: int):
        context = resolve_v2_context(request)
        group = _share_group_or_404(id)
        if group is None:
            return _not_found_response()
        _require_share_group_owner_admin(context, group)
        resource_share = self._resource_share(group=group, resource_share_id=resource_share_id)
        if resource_share is None:
            return _not_found_response()
        serializer = ShareGroupResourceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before_data = ShareGroupResourceReadSerializer(resource_share).data
        resource_share.permissions = serializer.validated_data["permissions"]
        resource_share.save(update_fields=["permissions", "updated_at"])
        data = ShareGroupResourceReadSerializer(resource_share).data
        log_v2_action(
            request=request,
            context=context,
            action="update_resource_share_permissions",
            target_type="resource_share_permission",
            target_id=resource_share.id,
            resource_owner_department=group.owner_department,
            resource_type=resource_share.resource_type,
            resource_object_id=resource_share.resource_object_id,
            before_data=before_data,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)

    @transaction.atomic
    def delete(self, request, id: int, resource_share_id: int):
        context = resolve_v2_context(request)
        group = _share_group_or_404(id)
        if group is None:
            return _not_found_response()
        _require_share_group_owner_admin(context, group)
        resource_share = self._resource_share(group=group, resource_share_id=resource_share_id)
        if resource_share is None:
            return _not_found_response()
        data = ShareGroupResourceReadSerializer(resource_share).data
        resource_type = resource_share.resource_type
        resource_object_id = resource_share.resource_object_id
        resource_share.delete()
        log_v2_action(
            request=request,
            context=context,
            action="remove_resource_sharing",
            target_type="resource_share_permission",
            target_id=resource_share_id,
            resource_owner_department=group.owner_department,
            resource_type=resource_type,
            resource_object_id=resource_object_id,
            before_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)


class AuditLogListView(V2ResourceAPIView):
    def get(self, request):
        context = resolve_v2_context(request)
        if not (is_platform_super_admin(context) or is_department_admin(context)):
            raise StandardForbidden()
        queryset = V2AuditLog.objects.select_related("actor_department", "resource_owner_department")
        if not is_platform_super_admin(context):
            queryset = queryset.filter(actor_department=context.department)
        resource_type = request.query_params.get("resourceType")
        resource_id = request.query_params.get("resourceObjectId")
        if resource_type:
            queryset = queryset.filter(resource_type=resource_type)
        if resource_id:
            queryset = queryset.filter(resource_object_id=str(resource_id))
        queryset = queryset.order_by("-created_at", "-id")
        serializer = AuditLogReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)
