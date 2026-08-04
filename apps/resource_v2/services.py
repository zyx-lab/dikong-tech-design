from __future__ import annotations

from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.access.exceptions import StandardForbidden, StandardNotFound
from apps.access.models import DirectoryStatus
from apps.iam_v2.models import FixedRole
from apps.iam_v2.services import (
    PERMISSION_ORDER,
    department_in_context_scope,
    department_tree_filter,
    is_department_admin,
    is_platform_super_admin,
    role_permissions,
)
from apps.audit_v2.services import log_v2_action
from apps.resource_v2.models import (
    BindingActionType,
    BindingStatus,
    CameraResource,
    DjiConnection,
    DjiConnectionStatus,
    DockResource,
    DroneResource,
    GatewayResource,
    MqttConnectionHealth,
    MqttHealthStatus,
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
    ResourceType.CAMERA: CameraResource,
}


# DJI 上游把同一台机场在 workspace 视角下既算 dock(domain=3) 又算 gateway(domain=2)，
# 导致 v2_dock_resources 和 v2_gateway_resources 里会有同 device_sn 的两行。
# 绑定一台时必须同时绑姐妹行，否则会出现"半绑"——dock 视角已绑、gateway 视角未绑，
# 或反之。下面这张表标出哪些类型之间存在姐妹关系。drone/payload/camera 不参与联动。
_SISTER_RESOURCE_TYPE = {
    ResourceType.DOCK: ResourceType.GATEWAY,
    ResourceType.GATEWAY: ResourceType.DOCK,
}


def find_sister_resource(resource_type: str, device_sn: str):
    """查找 (resource_type, device_sn) 对应的姐妹资源。

    仅 dock ↔ gateway 之间存在姐妹关系（同 SN 在另一张资源表里的行）。
    其它类型返回 (None, None)；SN 为空或姐妹行不存在时也返回 (None, None)。
    """
    sister_type = _SISTER_RESOURCE_TYPE.get(ResourceType(resource_type))
    if sister_type is None or not device_sn:
        return None, None
    sister_model = RESOURCE_MODELS[sister_type]
    sister_row = sister_model.objects.filter(device_sn=device_sn).first()
    if sister_row is None:
        return None, None
    return sister_type, sister_row


def find_sister_binding(binding: ResourceBinding):
    """查找 binding 在另一张资源表里的姐妹绑定（同 SN、当前 ACTIVE）。

    仅对 dock / gateway binding 有意义；其它返回 None。资源行已被删除时返回 None。
    """
    sister_type = _SISTER_RESOURCE_TYPE.get(ResourceType(binding.resource_type))
    if sister_type is None:
        return None
    primary = get_resource(binding.resource_type, binding.resource_object_id)
    sister_model = RESOURCE_MODELS[sister_type]
    sister_row_id = sister_model.objects.filter(device_sn=primary.device_sn).values_list("id", flat=True).first()
    if sister_row_id is None:
        return None
    return ResourceBinding.objects.filter(
        resource_type=sister_type,
        resource_object_id=sister_row_id,
        status=BindingStatus.ACTIVE,
    ).first()


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


def dji_connection_snapshot(connection: DjiConnection | None) -> dict:
    if connection is None:
        return {}
    return {
        "id": connection.id,
        "owner_department_id": connection.owner_department_id,
        "name": connection.name,
        "base_url": connection.base_url,
        "username": connection.username,
        "workspace_id": connection.workspace_id,
    }


def invalidate_dji_connection_session(connection: DjiConnection) -> None:
    connection.access_token = ""
    connection.workspace_id = ""
    connection.dji_user_id = ""
    connection.dji_username = ""
    connection.dji_user_type = ""
    connection.mqtt_addr = ""
    connection.mqtt_username = ""
    connection.mqtt_password = ""
    connection.expires_at = None
    connection.last_checked_at = None
    connection.save(
        update_fields=[
            "access_token",
            "workspace_id",
            "dji_user_id",
            "dji_username",
            "dji_user_type",
            "mqtt_addr",
            "mqtt_username",
            "mqtt_password",
            "expires_at",
            "last_checked_at",
            "updated_at",
        ]
    )
    MqttConnectionHealth.objects.update_or_create(
        dji_connection=connection,
        defaults={
            "status": MqttHealthStatus.CONNECTING,
            "mqtt_addr": "",
            "subscribed_topics": [],
            "last_connected_at": None,
            "last_subscribed_at": None,
            "last_error": "",
            "last_heartbeat_at": timezone.now(),
        },
    )


def can_manage_connection(context, connection: DjiConnection) -> bool:
    if is_platform_super_admin(context):
        return True
    return is_department_admin(context) and connection.owner_department_id == context.department_id


def require_manage_connection(context, connection: DjiConnection):
    if not can_manage_connection(context, connection):
        raise StandardForbidden()


def require_bind_connection(context, connection: DjiConnection):
    if is_platform_super_admin(context):
        return
    if not (is_department_admin(context) and connection.owner_department_id == context.department_id):
        raise StandardForbidden()


def can_unbind(context, binding: ResourceBinding) -> bool:
    if is_platform_super_admin(context):
        return True
    return is_department_admin(context) and binding.owner_department_id == context.department_id


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
        resource_object_id__in=ResourceSharePermission.objects.filter(
            share_group__target_departments__department=context.department,
            share_group__status=DirectoryStatus.ACTIVE,
            resource_type=resource_type,
        ).values("resource_object_id"),
    )
    hierarchy_filter = department_tree_filter(context, "owner_department")
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
    hierarchy_visible = department_in_context_scope(context, binding.owner_department)
    if hierarchy_visible:
        if binding.owner_department_id != context.department_id:
            base_permissions.discard("bind")
            base_permissions.discard("unbind")
        return [permission for permission in PERMISSION_ORDER if permission in base_permissions]

    shared = _shared_permissions_for_department(context, binding)
    return shared or []


def sync_connection_resources_from_upstream(connection: DjiConnection) -> dict:
    from apps.resource_v2.gateway import dji_connection_gateway
    from apps.resource_v2.serializers import upsert_resource_from_payload

    discovered = dji_connection_gateway(connection).discover()
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
