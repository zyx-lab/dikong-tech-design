from apps.access.exceptions import StandardConstraintConflict, StandardForbidden, StandardNotFound
from apps.iam_v2.models import FixedRole
from apps.inspection_v2.services import is_dispatcher, usable_resource_binding
from apps.resource_v2.gateway import DjiGatewayError, dji_connection_gateway
from apps.resource_v2.models import (
    BindingStatus,
    DockResource,
    DroneResource,
    PayloadResource,
    ResourceBinding,
    ResourceType,
)
from apps.resource_v2.services import effective_permissions_for_binding


CAMERA_METHODS = [
    "camera_mode_switch", "camera_photo_take", "camera_photo_stop",
    "camera_recording_start", "camera_recording_stop", "camera_screen_drag",
    "camera_aim", "camera_focal_length_set", "gimbal_reset", "camera_look_at",
    "camera_screen_split", "photo_storage_set", "video_storage_set",
    "camera_exposure_mode_set", "camera_exposure_set", "camera_focus_mode_set",
    "camera_focus_value_set", "camera_point_focus_action", "ir_metering_mode_set",
    "ir_metering_point_set", "ir_metering_area_set",
]
SPEAKER_METHODS = [
    "speaker_play_volume_set", "speaker_play_mode_set", "speaker_play_stop",
    "speaker_replay", "speaker_tts_play_start",
]


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
    sn = payload.get("child_device_sn") or payload.get("childDeviceSn") or payload.get("drone_sn")
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
        psdk_index = data.get("psdk_index") if "psdk_index" in data else data.get("psdkIndex")
        if payload_index:
            result.append({"payloadIndex": str(payload_index), "psdkIndex": None,
                           "model": payload.model, "online": True, "methods": CAMERA_METHODS})
        elif type(psdk_index) is int and 0 <= psdk_index <= 3:
            result.append({"payloadIndex": None, "psdkIndex": psdk_index,
                           "model": payload.model, "online": True, "methods": SPEAKER_METHODS})
    return result


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
    supported = dock.model.lower().replace(" ", "") == "dock3"
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
        },
        "payloads": _payloads(context, binding.dji_connection_id),
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
            client_id=data.get("clientId"),
        )
        client_id = mqtt.get("clientId") if isinstance(mqtt, dict) else None
        if not client_id:
            raise DjiGatewayError("DRC connect response missing clientId", status_code=502)
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
    pub = acl.get("pub") if isinstance(acl, dict) else None
    sub = acl.get("sub") if isinstance(acl, dict) else None
    if not pub or not sub:
        try:
            gateway.exit_drc(dock_sn=dock.device_sn, client_id=client_id)
        except DjiGatewayError:
            pass
        raise StandardConstraintConflict(
            msg="上云 API 未返回 DRC topic", data={"reasonCode": "UPSTREAM_CONTRACT_ERROR"}
        )
    return {
        "dockId": dock.id,
        "droneId": capability["droneId"],
        "mqtt": mqtt,
        "publishTopic": pub[0],
        "subscribeTopic": sub[0],
    }


def exit_drc(*, context, data):
    require_drc_operator(context)
    dock = DockResource.objects.filter(pk=data["dockId"]).first()
    if dock is None:
        raise StandardNotFound(data={"reasonCode": "DOCK_NOT_FOUND"})
    binding = _binding(context, ResourceType.DOCK, dock.id)
    try:
        dji_connection_gateway(binding.dji_connection).exit_drc(
            dock_sn=dock.device_sn, client_id=data["clientId"]
        )
    except DjiGatewayError as exc:
        raise StandardConstraintConflict(
            msg="上云 API DRC 退出失败", data={"reasonCode": "UPSTREAM_ERROR"}
        ) from exc
    return {"status": "CLOSED"}
