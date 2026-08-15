from __future__ import annotations

import json
from datetime import timezone as dt_timezone
from decimal import Decimal
from hashlib import sha256

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.resource_v2.models import (
    DjiConnection,
    DroneResource,
    DroneTelemetrySnapshot,
    HmsAlert,
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


def record_hms_alerts(*, connection: DjiConnection | None, topic: str, payload: dict, received_at=None) -> dict:
    if connection is None or not isinstance(payload, dict):
        return {"updated": 0}
    gateway_sn = device_sn_from_topic(topic)
    data = payload.get("data")
    alerts = data.get("list") if isinstance(data, dict) else None
    if not str(topic).startswith("thing/product/") or not str(topic).endswith("/events") or not gateway_sn or not isinstance(alerts, list):
        return {"updated": 0}
    if any(not isinstance(item, dict) or not str(item.get("code") or "").strip() for item in alerts):
        return {"updated": 0}

    received_at = received_at or timezone.now()
    from_sn = str(payload.get("from") or "").strip() or gateway_sn
    normalized = {}
    for item in alerts:
        code = str(item["code"]).strip()
        identity = {
            "code": code,
            "device_type": item.get("device_type", item.get("deviceType")),
            "in_the_sky": item.get("in_the_sky", item.get("inTheSky")),
            "args": item.get("args"),
        }
        alarm_key = sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        normalized[alarm_key] = item

    scope = {
        "dji_connection": connection,
        "gateway_sn": gateway_sn,
        "from_sn": from_sn,
    }
    with transaction.atomic():
        active = {
            alert.alarm_key: alert
            for alert in HmsAlert.objects.select_for_update().filter(**scope, resolved_at__isnull=True)
        }
        for alarm_key, item in normalized.items():
            device_type = item.get("device_type", item.get("deviceType"))
            device_domain = device_type.get("domain") if isinstance(device_type, dict) else None
            defaults = {
                "code": str(item["code"]).strip(),
                "device_domain": _integer_or_none(device_domain),
                "level": _integer_or_none(item.get("level")),
                "module": _integer_or_none(item.get("module")),
                "raw_item": item,
                "last_reported_at": received_at,
            }
            alert = active.get(alarm_key)
            if alert is None:
                HmsAlert.objects.create(
                    **scope,
                    alarm_key=alarm_key,
                    first_reported_at=received_at,
                    **defaults,
                )
            else:
                for field, value in defaults.items():
                    setattr(alert, field, value)
                alert.save(update_fields=[*defaults, "updated_at"])
        HmsAlert.objects.filter(**scope, resolved_at__isnull=True).exclude(
            alarm_key__in=normalized
        ).update(resolved_at=received_at, updated_at=received_at)
    return {"updated": len(normalized)}


def osd_reported_at(payload: dict):
    timestamp = payload.get("timestamp")
    if isinstance(timestamp, (int, float)):
        try:
            seconds = timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
            return timezone.datetime.fromtimestamp(seconds, tz=dt_timezone.utc)
        except (OverflowError, OSError, ValueError):
            pass
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
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None
    except Exception:
        return None


def _integer_or_none(value):
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _nonnegative_integer(value):
    parsed = _integer_or_none(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _nonnegative_decimal(value):
    parsed = _decimal_or_none(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _max_value(previous, current):
    if current is None:
        return previous
    if previous is None:
        return current
    return max(previous, current)


def _battery_cycles(data: dict, previous: list) -> list | None:
    battery = data.get("battery")
    if not isinstance(battery, dict) or "batteries" not in battery:
        return None
    batteries = battery.get("batteries")
    if not isinstance(batteries, list):
        return None
    known_cycles = {
        str(item.get("sn") or "").strip(): _nonnegative_integer(item.get("loopTimes"))
        for item in previous
        if isinstance(item, dict) and str(item.get("sn") or "").strip()
    }
    result = []
    for item in batteries:
        if not isinstance(item, dict):
            continue
        sn = str(item.get("sn") or "").strip()
        loop_times = _nonnegative_integer(item.get("loop_times", item.get("loopTimes")))
        if not sn or loop_times is None:
            continue
        entry = {
            "sn": sn,
            "index": _nonnegative_integer(item.get("index")),
            "loopTimes": _max_value(known_cycles.get(sn), loop_times),
        }
        result.append(entry)
    return result


def upsert_drone_telemetry_from_osd(
    *,
    connection: DjiConnection | None,
    device_sn: str,
    payload: dict | None,
) -> DroneTelemetrySnapshot | None:
    payload = payload if isinstance(payload, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    reported_at = osd_reported_at(payload)
    with transaction.atomic():
        drone = DroneResource.objects.select_for_update().filter(device_sn=device_sn).first()
        if drone is None:
            return None
        snapshot = DroneTelemetrySnapshot.objects.select_for_update().filter(drone=drone).first()
        if snapshot is not None and reported_at < snapshot.reported_at:
            return snapshot
        defaults = {
            "dji_connection": connection,
            "latitude": _decimal_or_none(data.get("latitude")),
            "longitude": _decimal_or_none(data.get("longitude")),
            "altitude": _decimal_or_none(data.get("altitude", data.get("height"))),
            "speed": _decimal_or_none(data.get("speed", data.get("horizontal_speed"))),
            "heading": _decimal_or_none(data.get("heading", data.get("attitude_head"))),
            "battery_percent": osd_battery_percent(data),
            "total_flight_time": _max_value(
                snapshot.total_flight_time if snapshot else None,
                _nonnegative_integer(data.get("total_flight_time", data.get("totalFlightTime"))),
            ),
            "total_flight_distance": _max_value(
                snapshot.total_flight_distance if snapshot else None,
                _nonnegative_decimal(data.get("total_flight_distance", data.get("totalFlightDistance"))),
            ),
            "total_flight_sorties": _max_value(
                snapshot.total_flight_sorties if snapshot else None,
                _nonnegative_integer(data.get("total_flight_sorties", data.get("totalFlightSorties"))),
            ),
            "reported_at": reported_at,
            "raw_payload": payload,
        }
        cycles = _battery_cycles(data, snapshot.battery_cycles if snapshot else [])
        if cycles is not None:
            defaults["battery_cycles"] = cycles
        snapshot, _created = DroneTelemetrySnapshot.objects.update_or_create(drone=drone, defaults=defaults)
    return snapshot
