from __future__ import annotations

import json
from datetime import timezone as dt_timezone
from decimal import Decimal

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.resource_v2.models import (
    DjiConnection,
    DroneResource,
    DroneTelemetrySnapshot,
    MqttConnectionHealth,
    MqttHealthStatus,
    MqttLatestMessage,
)


DEFAULT_MQTT_TOPICS = (
    "thing/product/+/osd",
    "thing/product/+/state",
    "thing/product/+/events",
    "thing/product/+/services_reply",
    "sys/product/+/status",
)

MQTT_BROADCAST_GROUP = "v2.dji.mqtt"


def configured_mqtt_topics() -> tuple[str, ...]:
    extras = str(getattr(settings, "DJI_V2_MQTT_EXTRA_TOPICS", "") or "")
    topics = list(DEFAULT_MQTT_TOPICS)
    for item in extras.split(","):
        topic = item.strip()
        if topic and topic not in topics:
            topics.append(topic)
    return tuple(topics)


def topic_kind(topic: str) -> str:
    normalized = str(topic or "").strip()
    if normalized.startswith("sys/product/") and normalized.endswith("/status"):
        return "status"
    if normalized.endswith("/services_reply"):
        return "services_reply"
    for suffix in ("osd", "state", "events"):
        if normalized.endswith(f"/{suffix}"):
            return suffix
    return "unknown"


def device_sn_from_topic(topic: str) -> str:
    segments = str(topic or "").split("/")
    if len(segments) == 4 and segments[1] == "product":
        return segments[2]
    return ""


def mqtt_message_envelope(message: MqttLatestMessage) -> dict:
    return {
        "type": "mqtt.message",
        "connectionId": message.dji_connection_id,
        "topic": message.topic,
        "topicKind": message.topic_kind,
        "deviceSn": message.device_sn,
        "receivedAt": message.received_at.isoformat(),
        "sequence": message.sequence,
        "rawPayload": message.raw_payload,
    }


def mark_mqtt_health(
    *,
    connection: DjiConnection,
    status: str,
    worker_id: str = "",
    mqtt_addr: str = "",
    subscribed_topics: list[str] | tuple[str, ...] | None = None,
    last_error: str = "",
) -> MqttConnectionHealth:
    now = timezone.now()
    health, _created = MqttConnectionHealth.objects.get_or_create(dji_connection=connection)
    health.status = status
    if worker_id:
        health.worker_id = worker_id
    if mqtt_addr:
        health.mqtt_addr = mqtt_addr
    if subscribed_topics is not None:
        health.subscribed_topics = list(subscribed_topics)
    if status == MqttHealthStatus.CONNECTING:
        health.last_heartbeat_at = now
    if status in {MqttHealthStatus.SUBSCRIBED, MqttHealthStatus.MESSAGE_RECEIVED}:
        health.last_connected_at = health.last_connected_at or now
        health.last_subscribed_at = now if status == MqttHealthStatus.SUBSCRIBED else health.last_subscribed_at
        health.last_heartbeat_at = now
        health.last_error = ""
    if status == MqttHealthStatus.ERROR:
        health.last_error = last_error
        health.last_heartbeat_at = now
    health.save()
    write_redis_health(connection_id=connection.id, status=status, heartbeat_at=now)
    return health


def _redis_url() -> str:
    return str(
        getattr(settings, "DJI_V2_MQTT_REDIS_URL", "")
        or getattr(settings, "CHANNEL_REDIS_URL", "")
        or ""
    ).strip()


def write_redis_health(*, connection_id: int, status: str, heartbeat_at) -> None:
    url = _redis_url()
    if not url:
        return
    try:
        import redis

        ttl = int(getattr(settings, "DJI_V2_MQTT_HEARTBEAT_TTL_SECONDS", 30))
        client = redis.Redis.from_url(url, decode_responses=True)
        client.setex(
            f"v2:dji:mqtt:health:{connection_id}",
            ttl,
            json.dumps({"status": status, "heartbeatAt": heartbeat_at.isoformat()}, ensure_ascii=False),
        )
    except Exception:
        return


def read_redis_health(connection_id: int) -> dict:
    url = _redis_url()
    if not url:
        return {}
    try:
        import redis

        client = redis.Redis.from_url(url, decode_responses=True)
        payload = client.get(f"v2:dji:mqtt:health:{connection_id}")
        return json.loads(payload) if payload else {}
    except Exception:
        return {}


def publish_mqtt_envelope(envelope: dict) -> bool:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return False
    async_to_sync(channel_layer.group_send)(
        MQTT_BROADCAST_GROUP,
        {
            "type": "mqtt.message",
            "message": envelope,
        },
    )
    return True


def record_mqtt_message(*, connection: DjiConnection, topic: str, payload: dict, received_at=None) -> dict:
    received_at = received_at or timezone.now()
    normalized_payload = payload if isinstance(payload, dict) else {"value": payload}
    normalized_topic = str(topic or "").strip()
    kind = topic_kind(normalized_topic)
    device_sn = device_sn_from_topic(normalized_topic)

    with transaction.atomic():
        latest = (
            MqttLatestMessage.objects.select_for_update()
            .filter(dji_connection=connection, topic=normalized_topic, device_sn=device_sn)
            .first()
        )
        sequence = (latest.sequence + 1) if latest is not None else 1
        message, _created = MqttLatestMessage.objects.update_or_create(
            dji_connection=connection,
            topic=normalized_topic,
            device_sn=device_sn,
            defaults={
                "topic_kind": kind,
                "received_at": received_at,
                "sequence": sequence,
                "raw_payload": normalized_payload,
            },
        )
        health, _created = MqttConnectionHealth.objects.select_for_update().get_or_create(dji_connection=connection)
        health.status = MqttHealthStatus.MESSAGE_RECEIVED
        health.last_message_at = received_at
        health.last_heartbeat_at = received_at
        health.last_error = ""
        health.message_count += 1
        health.save(
            update_fields=[
                "status",
                "last_message_at",
                "last_heartbeat_at",
                "last_error",
                "message_count",
                "updated_at",
            ]
        )

    write_redis_health(connection_id=connection.id, status=MqttHealthStatus.MESSAGE_RECEIVED, heartbeat_at=received_at)
    envelope = mqtt_message_envelope(message)
    publish_mqtt_envelope(envelope)
    return envelope


def osd_reported_at(payload: dict):
    timestamp = payload.get("timestamp")
    if isinstance(timestamp, (int, float)):
        seconds = timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
        return timezone.datetime.fromtimestamp(seconds, tz=dt_timezone.utc)
    return timezone.now()


def osd_battery_percent(data: dict):
    battery = data.get("battery")
    if isinstance(battery, dict):
        value = battery.get("capacity_percent") or battery.get("percent") or battery.get("battery_percent")
    else:
        value = data.get("battery_percent") or data.get("capacity_percent")
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decimal_or_none(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def upsert_drone_telemetry_from_osd(
    *,
    connection: DjiConnection | None,
    device_sn: str,
    payload: dict | None,
) -> DroneTelemetrySnapshot | None:
    payload = payload if isinstance(payload, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    drone = DroneResource.objects.filter(device_sn=device_sn).first()
    if drone is None:
        return None
    snapshot, _created = DroneTelemetrySnapshot.objects.update_or_create(
        drone=drone,
        defaults={
            "dji_connection": connection,
            "latitude": _decimal_or_none(data.get("latitude")),
            "longitude": _decimal_or_none(data.get("longitude")),
            "altitude": _decimal_or_none(data.get("altitude", data.get("height"))),
            "speed": _decimal_or_none(data.get("speed", data.get("horizontal_speed"))),
            "heading": _decimal_or_none(data.get("heading", data.get("attitude_head"))),
            "battery_percent": osd_battery_percent(data),
            "reported_at": osd_reported_at(payload),
            "raw_payload": payload,
        },
    )
    return snapshot
