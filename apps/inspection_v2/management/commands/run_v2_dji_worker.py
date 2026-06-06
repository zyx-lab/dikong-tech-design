from __future__ import annotations

import json
import threading
from urllib.parse import urlparse
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from apps.dji_cloud.gateway import DjiGatewayError
from apps.inspection_v2.services import apply_cloud_execution_event, apply_device_status_event, apply_osd_telemetry
from apps.resource_v2.gateway import DjiConnectionGateway
from apps.resource_v2.models import DjiConnection, DjiConnectionStatus, MqttHealthStatus
from apps.resource_v2.mqtt import DEFAULT_MQTT_TOPICS, configured_mqtt_topics, mark_mqtt_health, record_mqtt_message


class V2DjiWorker:
    TOPICS = DEFAULT_MQTT_TOPICS

    def __init__(self, *, stop_event: threading.Event | None = None):
        self.stop_event = stop_event or threading.Event()
        self.client_id_prefix = f"v2-dji-worker-{uuid4().hex[:10]}"

    def run_once(self) -> dict[str, int]:
        return {"connections": DjiConnection.objects.filter(status=DjiConnectionStatus.ACTIVE).count()}

    def run_forever(self, *, reconnect_seconds: int = 5) -> None:
        while not self.stop_event.is_set():
            connections = list(DjiConnection.objects.filter(status=DjiConnectionStatus.ACTIVE).order_by("id"))
            threads = [
                threading.Thread(target=self._run_connection, args=(connection,), daemon=True)
                for connection in connections
            ]
            for thread in threads:
                thread.start()
            while any(thread.is_alive() for thread in threads) and not self.stop_event.is_set():
                self.stop_event.wait(1)
            close_old_connections()
            self.stop_event.wait(reconnect_seconds)

    def _run_connection(self, connection: DjiConnection) -> None:
        import paho.mqtt.client as mqtt

        try:
            mark_mqtt_health(
                connection=connection,
                status=MqttHealthStatus.CONNECTING,
                worker_id=f"{self.client_id_prefix}-{connection.id}",
            )
            config = DjiConnectionGateway(connection).get_workspace_config()
            mark_mqtt_health(
                connection=connection,
                status=MqttHealthStatus.CONNECTING,
                worker_id=f"{self.client_id_prefix}-{connection.id}",
                mqtt_addr=config.mqtt_addr,
            )
        except DjiGatewayError as exc:
            mark_mqtt_health(connection=connection, status=MqttHealthStatus.ERROR, last_error=str(exc))
            return
        mqtt_host, mqtt_port = self._parse_mqtt_addr(config.mqtt_addr)
        if not mqtt_host or not config.mqtt_username or not config.mqtt_password:
            mark_mqtt_health(
                connection=connection,
                status=MqttHealthStatus.ERROR,
                mqtt_addr=config.mqtt_addr,
                last_error="MQTT credentials are incomplete",
            )
            return

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"{self.client_id_prefix}-{connection.id}",
        )
        client.user_data_set({"connection_id": connection.id})
        client.username_pw_set(config.mqtt_username, config.mqtt_password)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        try:
            client.connect(mqtt_host, mqtt_port, keepalive=30)
        except Exception as exc:
            mark_mqtt_health(
                connection=connection,
                status=MqttHealthStatus.ERROR,
                mqtt_addr=config.mqtt_addr,
                last_error=str(exc),
            )
            return
        while not self.stop_event.is_set():
            try:
                result_code = client.loop(timeout=1.0)
            except Exception as exc:
                mark_mqtt_health(
                    connection=connection,
                    status=MqttHealthStatus.ERROR,
                    mqtt_addr=config.mqtt_addr,
                    last_error=str(exc),
                )
                break
            if result_code != mqtt.MQTT_ERR_SUCCESS:
                mark_mqtt_health(
                    connection=connection,
                    status=MqttHealthStatus.ERROR,
                    mqtt_addr=config.mqtt_addr,
                    last_error=f"MQTT loop returned {result_code}",
                )
                break
        client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties):  # pragma: no cover - MQTT callback
        connection = self._connection_from_userdata(userdata)
        if self._is_connect_failure(reason_code):
            if connection is not None:
                mark_mqtt_health(connection=connection, status=MqttHealthStatus.ERROR, last_error=str(reason_code))
            return
        topics = configured_mqtt_topics()
        for topic in topics:
            client.subscribe(topic)
        if connection is not None:
            mark_mqtt_health(
                connection=connection,
                status=MqttHealthStatus.SUBSCRIBED,
                subscribed_topics=topics,
            )

    def _on_message(self, client, userdata, message):  # pragma: no cover - MQTT callback
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return
        self.handle_message(message.topic, payload, connection=self._connection_from_userdata(userdata))

    def handle_message(self, topic: str, payload: dict, *, connection: DjiConnection | None = None) -> dict:
        message = None
        if connection is not None:
            message = record_mqtt_message(connection=connection, topic=topic, payload=payload)

        if topic.endswith("/osd"):
            device_sn = self._device_sn_from_topic(topic)
            if device_sn:
                result = (
                    apply_osd_telemetry(device_sn=device_sn, payload=payload, dji_connection=connection)
                    if connection is not None
                    else apply_osd_telemetry(device_sn=device_sn, payload=payload)
                )
                return self._with_message(result, message)
            return self._with_message({"updated": 0}, message)

        if topic.startswith("sys/product/") and topic.endswith("/status"):
            device_sn = self._device_sn_from_topic(topic)
            if device_sn:
                return self._with_message(apply_device_status_event(device_sn=device_sn, payload=payload), message)
            return self._with_message({"updated": 0}, message)

        if topic.endswith("/events") or topic.endswith("/services_reply"):
            job_id = self._job_id_from_payload(payload)
            event_status = self._status_from_payload(payload)
            if job_id and event_status:
                return self._with_message(
                    apply_cloud_execution_event(dji_job_id=job_id, status=event_status, payload=payload),
                    message,
                )
        return self._with_message({"updated": 0}, message)

    @staticmethod
    def _with_message(result, message: dict | None) -> dict:
        payload = result if isinstance(result, dict) else {}
        if message is not None:
            payload = dict(payload)
            payload["message"] = message
        return payload

    @staticmethod
    def _connection_from_userdata(userdata) -> DjiConnection | None:
        if not isinstance(userdata, dict):
            return None
        connection_id = userdata.get("connection_id")
        if not connection_id:
            return None
        return DjiConnection.objects.filter(pk=connection_id).first()

    @staticmethod
    def _device_sn_from_topic(topic: str) -> str:
        segments = str(topic or "").split("/")
        if len(segments) != 4:
            return ""
        return segments[2]

    @classmethod
    def _job_id_from_payload(cls, payload: dict) -> str:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        for source in (output, data, payload):
            for key in ("job_id", "jobId", "flight_id", "flightId"):
                value = source.get(key)
                if value not in (None, ""):
                    return str(value).strip()
        return ""

    @staticmethod
    def _status_from_payload(payload: dict) -> str:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        for source in (output, data, payload):
            for key in ("status", "result", "state"):
                value = source.get(key)
                if value not in (None, ""):
                    return str(value).strip()
        return ""

    @staticmethod
    def _parse_mqtt_addr(addr: str) -> tuple[str, int]:
        normalized = str(addr or "").strip()
        if not normalized:
            return "", 0
        if "://" not in normalized:
            normalized = f"tcp://{normalized}"
        parsed = urlparse(normalized)
        return parsed.hostname or "", parsed.port or 1883

    @staticmethod
    def _is_connect_failure(reason_code) -> bool:
        if hasattr(reason_code, "is_failure"):
            return bool(reason_code.is_failure)
        try:
            return int(reason_code) != 0
        except (TypeError, ValueError):
            return str(reason_code or "").strip().lower() not in {"0", "success"}


class Command(BaseCommand):
    help = "运行 v2 DJI 任务闭环 worker，消费 OSD/progress MQTT 消息。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="只检查一次可用连接并退出。")
        parser.add_argument("--reconnect-seconds", type=int, default=5, help="连接退出后的重连间隔秒数。")

    def handle(self, *args, **options):
        reconnect_seconds = options["reconnect_seconds"]
        if reconnect_seconds < 0:
            raise CommandError("--reconnect-seconds 不能小于 0")
        worker = V2DjiWorker()
        if options["once"]:
            summary = worker.run_once()
            self.stdout.write(self.style.SUCCESS(f"v2 DJI worker checked: {summary}"))
            return
        worker.run_forever(reconnect_seconds=reconnect_seconds)
