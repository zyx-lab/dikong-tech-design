from datetime import datetime, timedelta
from uuid import uuid4

from django.core.cache import cache
from django.utils import timezone

from apps.access.exceptions import StandardConstraintConflict, StandardForbidden, StandardNotFound
from apps.iam_v2.models import FixedRole
from apps.inspection_v2.drc_contract import DRC_FLIGHT_ACTION_FIELDS
from apps.inspection_v2.services import (
    dock_child_device_state,
    is_dispatcher,
    usable_resource_binding,
)
from apps.inspection_v2.serializers import CAMERA_ACTION_CHOICES
from apps.resource_v2.gateway import DjiGatewayError, dji_connection_gateway
from apps.resource_v2.models import (
    BindingStatus,
    DjiConnection,
    DockResource,
    DroneResource,
    PayloadResource,
    ResourceBinding,
    ResourceType,
)
from apps.resource_v2.services import effective_permissions_for_binding


CAMERA_METHODS = CAMERA_ACTION_CHOICES
DRC_SESSION_CACHE_PREFIX = "drc:mqtt:"
DRC_SESSION_LEASE_PREFIX = "drc:websocket:"


def require_drc_operator(context):
    if context.is_super_admin or is_dispatcher(context) or FixedRole.PILOT in context.role_codes:
        return
    raise StandardForbidden(data={"reasonCode": "CONTROL_ROLE_REQUIRED"})


def _binding(context, resource_type, resource_id):
    binding = usable_resource_binding(context, resource_type, resource_id)
    permissions = set(effective_permissions_for_binding(context, binding))
    if not context.is_super_admin and not permissions.intersection({"use", "dispatch_task"}):
        raise StandardForbidden(data={"reasonCode": "RESOURCE_USE_REQUIRED"})
    return binding


def _child_drone(dock, connection_id):
    payload = dock.last_payload if isinstance(dock.last_payload, dict) else {}
    sn, _online = dock_child_device_state(payload)
    if sn:
        drone = DroneResource.objects.filter(device_sn=sn).first()
        if drone and ResourceBinding.objects.filter(
            resource_type=ResourceType.DRONE,
            resource_object_id=drone.id,
            dji_connection_id=connection_id,
            status=BindingStatus.ACTIVE,
        ).exists():
            return drone
    ids = list(ResourceBinding.objects.filter(
        resource_type=ResourceType.DRONE,
        dji_connection_id=connection_id,
        status=BindingStatus.ACTIVE,
    ).values_list("resource_object_id", flat=True)[:2])
    return DroneResource.objects.filter(pk=ids[0]).first() if len(ids) == 1 else None


def _payloads(context, connection_id):
    result = []
    bindings = ResourceBinding.objects.filter(
        resource_type=ResourceType.PAYLOAD,
        dji_connection_id=connection_id,
        status=BindingStatus.ACTIVE,
    )
    for binding in bindings:
        if not context.is_super_admin and not set(
            effective_permissions_for_binding(context, binding)
        ).intersection({"use", "dispatch_task"}):
            continue
        payload = PayloadResource.objects.filter(pk=binding.resource_object_id, online_status=True).first()
        if payload is None:
            continue
        data = payload.last_payload if isinstance(payload.last_payload, dict) else {}
        payload_index = data.get("payload_index") or data.get("payloadIndex")
        if payload_index:
            result.append({"payloadIndex": str(payload_index), "psdkIndex": None,
                           "model": payload.model, "online": True, "methods": CAMERA_METHODS})
    return result


def _is_dock3_model(model) -> bool:
    return str(model or "").lower().replace(" ", "") in {"3", "dock3"}


_DOCK_ACTION_TARGET_STATES = {
    "debug_mode_open": (("mode_code", "modeCode"), 2),
    "debug_mode_close": (("mode_code", "modeCode"), 0),
    "cover_open": (("cover_state", "coverState"), 1),
    "cover_close": (("cover_state", "coverState"), 0),
}


def _dock_action_already_applied(dock, action: str) -> bool:
    payload = dock.last_payload if isinstance(dock.last_payload, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    keys, expected = _DOCK_ACTION_TARGET_STATES[action]
    return any(data.get(key) == expected for key in keys)


def drc_capabilities(*, context, dock_id):
    require_drc_operator(context)
    dock = DockResource.objects.filter(pk=dock_id).first()
    if dock is None:
        raise StandardNotFound(data={"reasonCode": "DOCK_NOT_FOUND"})
    binding = _binding(context, ResourceType.DOCK, dock.id)
    drone = _child_drone(dock, binding.dji_connection_id)
    if drone is not None:
        _binding(context, ResourceType.DRONE, drone.id)
    blockers = []
    supported = _is_dock3_model(dock.model)
    if not supported:
        blockers.append({"code": "DOCK_NOT_SUPPORTED", "message": "当前设备不是 Dock 3"})
    if not dock.online_status:
        blockers.append({"code": "DOCK_OFFLINE", "message": "机场不在线"})
    if drone is None:
        blockers.append({"code": "CHILD_DRONE_NOT_FOUND", "message": "无法解析子无人机"})
    elif not drone.online_status:
        blockers.append({"code": "DRONE_OFFLINE", "message": "子无人机不在线"})
    return {
        "dockId": dock.id,
        "droneId": drone.id if drone else None,
        "supported": supported,
        "available": supported and not blockers,
        "blockers": blockers,
        "control": {
            "protocol": "stick_control", "frequency": {"min": 5, "max": 10, "default": 10},
            "channel": {"min": 364, "neutral": 1024, "max": 1684},
            "actions": list(DRC_FLIGHT_ACTION_FIELDS),
        },
        "payloads": _payloads(context, binding.dji_connection_id),
    }


def execute_dock_debug_action(*, context, data):
    require_drc_operator(context)
    dock = DockResource.objects.filter(pk=data["dockId"]).first()
    if dock is None:
        raise StandardNotFound(data={"reasonCode": "DOCK_NOT_FOUND"})
    binding = _binding(context, ResourceType.DOCK, dock.id)
    if not _is_dock3_model(dock.model):
        raise StandardConstraintConflict(
            msg="当前设备不是 Dock 3", data={"reasonCode": "DOCK_NOT_SUPPORTED"}
        )
    if not dock.online_status:
        raise StandardConstraintConflict(
            msg="机场不在线", data={"reasonCode": "DOCK_OFFLINE"}
        )
    if _dock_action_already_applied(dock, data["action"]):
        return {
            "action": data["action"],
            "status": "SUCCEEDED",
            "dockId": dock.id,
            "upstream": {"skipped": True, "reason": "ALREADY_IN_STATE"},
        }
    upstream = dji_connection_gateway(binding.dji_connection).control_dock_debug(
        dock.device_sn, data["action"]
    )
    return {
        "action": data["action"],
        "status": "SUCCEEDED",
        "dockId": dock.id,
        "upstream": upstream,
    }


def execute_drc_flight_action(*, context, data):
    capability = drc_capabilities(context=context, dock_id=data["dockId"])
    if not capability["available"]:
        blocker = capability["blockers"][0]
        raise StandardConstraintConflict(
            msg=blocker["message"], data={"reasonCode": blocker["code"]}
        )
    dock = DockResource.objects.get(pk=data["dockId"])
    binding = _binding(context, ResourceType.DOCK, dock.id)
    gateway = dji_connection_gateway(binding.dji_connection)
    action = data["action"]
    if action == "fly_to_point_stop":
        upstream = gateway.stop_fly_to_point(dock.device_sn)
    else:
        method = {
            "takeoff_to_point": gateway.takeoff_to_point,
            "fly_to_point": gateway.fly_to_point,
            "fly_to_point_update": gateway.update_fly_to_point,
        }[action]
        upstream = method(dock.device_sn, data["_dji_data"])
    return {
        "action": action,
        "status": "SUCCEEDED",
        "dockId": dock.id,
        "droneId": capability["droneId"],
        "upstream": upstream,
    }


def connect_drc(*, context, data):
    capability = drc_capabilities(context=context, dock_id=data["dockId"])
    if not capability["available"]:
        blocker = capability["blockers"][0]
        raise StandardConstraintConflict(
            msg=blocker["message"], data={"reasonCode": blocker["code"]}
        )
    dock = DockResource.objects.get(pk=data["dockId"])
    binding = _binding(context, ResourceType.DOCK, dock.id)
    gateway = dji_connection_gateway(binding.dji_connection)
    client_id = None
    try:
        mqtt = gateway.connect_drc(
            dock_sn=dock.device_sn,
            expire_sec=data["expireSec"],
            client_id=None,
        )
        client_id = mqtt.get("clientId") if isinstance(mqtt, dict) else None
        if not client_id or not all(mqtt.get(key) for key in ("address", "username", "password")):
            raise DjiGatewayError("DRC connect response is incomplete", status_code=502)
        acl = gateway.enter_drc(
            dock_sn=dock.device_sn,
            client_id=client_id,
            expire_sec=data["expireSec"],
            osd_frequency=data["osdFrequency"],
            hsi_frequency=data["hsiFrequency"],
        )
    except DjiGatewayError as exc:
        if client_id:
            try:
                gateway.exit_drc(dock_sn=dock.device_sn, client_id=client_id)
            except DjiGatewayError:
                pass
        raise StandardConstraintConflict(
            msg="上云 API DRC 连接失败", data={"reasonCode": "UPSTREAM_ERROR"}
        ) from exc
    expected_pub = f"thing/product/{dock.device_sn}/drc/down"
    expected_sub = f"thing/product/{dock.device_sn}/drc/up"
    pub = acl.get("pub") if isinstance(acl, dict) else None
    sub = acl.get("sub") if isinstance(acl, dict) else None
    if pub != [expected_pub] or sub != [expected_sub]:
        try:
            gateway.exit_drc(dock_sn=dock.device_sn, client_id=client_id)
        except DjiGatewayError:
            pass
        raise StandardConstraintConflict(
            msg="上云 API 未返回 DRC topic", data={"reasonCode": "UPSTREAM_CONTRACT_ERROR"}
        )
    now = timezone.now()
    expire_time = mqtt.get("expireTime")
    try:
        expires_at = datetime.fromtimestamp(int(expire_time), tz=timezone.get_current_timezone())
    except (TypeError, ValueError, OSError):
        expires_at = now + timedelta(seconds=data["expireSec"])
    ttl = max(1, min(data["expireSec"], int((expires_at - now).total_seconds())))
    session_id = uuid4()
    cache.set(
        _session_key(session_id),
        {
            "userId": context.user.id,
            "dockId": dock.id,
            "droneId": capability["droneId"],
            "connectionId": binding.dji_connection_id,
            "dockSn": dock.device_sn,
            "address": mqtt["address"],
            "username": mqtt["username"],
            "password": mqtt["password"],
            "clientId": client_id,
            "enableTls": bool(mqtt.get("enableTls")),
            "publishTopic": expected_pub,
            "subscribeTopic": expected_sub,
            "expiresAt": expires_at.isoformat(),
        },
        timeout=ttl,
    )
    return {
        "sessionId": session_id,
        "dockId": dock.id,
        "droneId": capability["droneId"],
        "expiresAt": expires_at,
        "webSocketPath": f"/ws/v2/drc/sessions/{session_id}",
    }


def _session_key(session_id):
    return f"{DRC_SESSION_CACHE_PREFIX}{session_id}"


def _lease_key(session_id):
    return f"{DRC_SESSION_LEASE_PREFIX}{session_id}"


def drc_session_config(*, context, session_id):
    require_drc_operator(context)
    config = cache.get(_session_key(session_id))
    if not isinstance(config, dict) or config.get("userId") != context.user.id:
        raise StandardNotFound(data={"reasonCode": "DRC_SESSION_NOT_FOUND"})
    _binding(context, ResourceType.DOCK, config["dockId"])
    return config


def claim_drc_session(*, context, session_id, lease_owner):
    config = drc_session_config(context=context, session_id=session_id)
    expires_at = datetime.fromisoformat(config["expiresAt"])
    ttl = max(1, int((expires_at - timezone.now()).total_seconds()))
    if not cache.add(_lease_key(session_id), lease_owner, timeout=ttl):
        raise StandardConstraintConflict(data={"reasonCode": "DRC_SESSION_IN_USE"})
    return config


def exit_drc(*, context, session_id):
    config = drc_session_config(context=context, session_id=session_id)
    try:
        _exit_upstream(config)
    except DjiGatewayError as exc:
        raise StandardConstraintConflict(
            msg="上云 API DRC 退出失败", data={"reasonCode": "UPSTREAM_ERROR"}
        ) from exc
    _delete_session(session_id)
    return config["dockId"]


def close_drc_session(*, session_id, config, lease_owner):
    try:
        _exit_upstream(config)
    except DjiGatewayError:
        pass
    finally:
        _delete_session(session_id, lease_owner=lease_owner)


def _delete_session(session_id, *, lease_owner=None):
    cache.delete(_session_key(session_id))
    if lease_owner is None or cache.get(_lease_key(session_id)) == lease_owner:
        cache.delete(_lease_key(session_id))


def _exit_upstream(config):
    connection = DjiConnection.objects.filter(pk=config["connectionId"]).first()
    if connection is None:
        raise DjiGatewayError("DRC connection no longer exists", status_code=404)
    dji_connection_gateway(connection).exit_drc(
        dock_sn=config["dockSn"], client_id=config["clientId"]
    )
