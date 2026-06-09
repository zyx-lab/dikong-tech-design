from __future__ import annotations

from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.access.exceptions import StandardForbidden, StandardNotFound
from apps.access.models import DirectoryStatus
from apps.iam_v2.models import FixedRole
from apps.iam_v2.services import (
    PERMISSION_ORDER,
    is_department_admin,
    is_platform_super_admin,
    role_permissions,
)
from apps.resource_v2.audit import log_v2_action
from apps.resource_v2.models import (
    BindingActionType,
    BindingStatus,
    DjiConnection,
    DjiConnectionStatus,
    DockResource,
    DroneResource,
    GatewayResource,
    PayloadResource,
    ResourceBinding,
    ResourceBindingHistory,
    ResourceSharePermission,
    ResourceType,
)


RESOURCE_MODELS = {
    ResourceType.DRONE: DroneResource,
    ResourceType.DOCK: DockResource,
    ResourceType.GATEWAY: GatewayResource,
    ResourceType.PAYLOAD: PayloadResource,
}


def normalize_base_url(value: str) -> str:
    return str(value or "").strip().rstrip("/")


def resource_model(resource_type: str):
    try:
        return RESOURCE_MODELS[ResourceType(resource_type)]
    except (KeyError, ValueError) as exc:
        raise StandardNotFound() from exc


def get_resource(resource_type: str, resource_id: int):
    model = resource_model(resource_type)
    resource = model.objects.filter(pk=resource_id).first()
    if resource is None:
        raise StandardNotFound()
    return resource


def resource_identifier(resource_type: str, resource_id: int) -> str:
    return str(getattr(get_resource(resource_type, resource_id), "device_sn", resource_id))


def dji_connection_snapshot(connection: DjiConnection) -> dict:
    return {
        "id": connection.id,
        "owner_department_id": connection.owner_department_id,
        "name": connection.name,
        "base_url": connection.base_url,
        "username": connection.username,
        "workspace_id": connection.workspace_id,
    }


def can_manage_connection(context, connection: DjiConnection) -> bool:
    if is_platform_super_admin(context):
        return True
    return is_department_admin(context) and connection.owner_department_id == context.department.id


def require_manage_connection(context, connection: DjiConnection):
    if not can_manage_connection(context, connection):
        raise StandardForbidden()


def require_bind_connection(context, connection: DjiConnection):
    if is_platform_super_admin(context):
        return
    if not (is_department_admin(context) and connection.owner_department_id == context.department.id):
        raise StandardForbidden()


def can_unbind(context, binding: ResourceBinding) -> bool:
    if is_platform_super_admin(context):
        return True
    return is_department_admin(context) and binding.owner_department_id == context.department.id


def require_unbind(context, binding: ResourceBinding):
    if not can_unbind(context, binding):
        raise StandardForbidden()


def visible_bindings_queryset(context, *, resource_type: str) -> QuerySet:
    queryset = (
        ResourceBinding.objects.select_related("owner_department", "dji_connection")
        .filter(status=BindingStatus.ACTIVE, resource_type=resource_type)
        .order_by("-id")
    )
    if is_platform_super_admin(context):
        return queryset

    shared_resource_filter = Q(
        resource_type=resource_type,
        status=BindingStatus.ACTIVE,
        dji_connection__isnull=False,
        resource_object_id__in=ResourceSharePermission.objects.filter(
            share_group__target_departments__department=context.department,
            share_group__status=DirectoryStatus.ACTIVE,
            resource_type=resource_type,
        ).values("resource_object_id"),
    )
    hierarchy_filter = Q(owner_department__path__startswith=context.department.path)
    return queryset.filter(hierarchy_filter | shared_resource_filter).distinct()


def _shared_permissions_for_department(context, binding: ResourceBinding) -> list[str] | None:
    share = (
        ResourceSharePermission.objects.filter(
            share_group__target_departments__department=context.department,
            share_group__status=DirectoryStatus.ACTIVE,
            resource_type=binding.resource_type,
            resource_object_id=binding.resource_object_id,
        )
        .order_by("id")
        .first()
    )
    if share is None:
        return None
    share_permissions = set(share.permissions or [])
    base = set(role_permissions(context.role_codes))
    return [permission for permission in PERMISSION_ORDER if permission in share_permissions and permission in base]


def effective_permissions_for_binding(context, binding: ResourceBinding) -> list[str]:
    if is_platform_super_admin(context):
        return role_permissions([FixedRole.PLATFORM_SUPER_ADMIN])

    base_permissions = set(role_permissions(context.role_codes))
    hierarchy_visible = binding.owner_department.path.startswith(context.department.path)
    if hierarchy_visible:
        if binding.owner_department_id != context.department.id:
            base_permissions.discard("bind")
            base_permissions.discard("unbind")
        return [permission for permission in PERMISSION_ORDER if permission in base_permissions]

    shared = _shared_permissions_for_department(context, binding)
    return shared or []


def sync_connection_resources_from_upstream(connection: DjiConnection) -> dict:
    from apps.resource_v2.gateway import DjiConnectionGateway
    from apps.resource_v2.serializers import upsert_resource_from_payload

    discovered = DjiConnectionGateway(connection).discover()
    resources = {"drones": [], "docks": [], "gateways": [], "payloads": []}
    for payload in discovered.get("drones", []):
        if isinstance(payload, dict):
            resources["drones"].append(upsert_resource_from_payload(ResourceType.DRONE, payload))
    for payload in discovered.get("docks", []):
        if isinstance(payload, dict):
            resources["docks"].append(upsert_resource_from_payload(ResourceType.DOCK, payload))
    for payload in discovered.get("gateways", []):
        if isinstance(payload, dict):
            resources["gateways"].append(upsert_resource_from_payload(ResourceType.GATEWAY, payload))
    for payload in discovered.get("payloads", []):
        if isinstance(payload, dict):
            resources["payloads"].append(upsert_resource_from_payload(ResourceType.PAYLOAD, payload))
    connection.status = DjiConnectionStatus.ACTIVE
    connection.last_checked_at = timezone.now()
    connection.save(update_fields=["status", "last_checked_at", "updated_at"])
    return resources


def create_binding_history(*, binding: ResourceBinding, context, action_type: str, previous_department=None, new_department=None):
    return ResourceBindingHistory.objects.create(
        action_type=action_type,
        resource_type=binding.resource_type,
        resource_object_id=binding.resource_object_id,
        resource_identifier=resource_identifier(binding.resource_type, binding.resource_object_id),
        previous_department=previous_department,
        new_department=new_department,
        dji_connection_snapshot=dji_connection_snapshot(binding.dji_connection),
        actor_user=context.user,
        actor_department=context.department,
    )


def mark_binding_created(*, binding: ResourceBinding, context, request):
    create_binding_history(
        binding=binding,
        context=context,
        action_type=BindingActionType.BIND,
        previous_department=None,
        new_department=binding.owner_department,
    )
    log_v2_action(
        request=request,
        context=context,
        action="bind_resource",
        target_type="resource_binding",
        target_id=binding.id,
        resource_owner_department=binding.owner_department,
        resource_type=binding.resource_type,
        resource_object_id=binding.resource_object_id,
        after_data={"binding_id": binding.id},
    )


def mark_binding_unbound(*, binding: ResourceBinding, context, request):
    previous_department = binding.owner_department
    create_binding_history(
        binding=binding,
        context=context,
        action_type=BindingActionType.UNBIND,
        previous_department=previous_department,
        new_department=None,
    )
    log_v2_action(
        request=request,
        context=context,
        action="unbind_resource",
        target_type="resource_binding",
        target_id=binding.id,
        resource_owner_department=previous_department,
        resource_type=binding.resource_type,
        resource_object_id=binding.resource_object_id,
        before_data={"binding_id": binding.id},
    )
