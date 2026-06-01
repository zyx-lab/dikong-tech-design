from __future__ import annotations

import json
import threading
from urllib.parse import urlparse
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from apps.dji_bff.gateway import DjiGatewayError
from apps.inspection_v2.services import apply_cloud_execution_event, apply_device_status_event, apply_osd_telemetry
from apps.resource_v2.gateway import DjiConnectionGateway
from apps.resource_v2.models import DjiConnection, DjiConnectionStatus


class V2DjiWorker:
    TOPICS = (
        "thing/product/+/osd",
        "thing/product/+/state",
        "thing/product/+/events",
        "thing/product/+/services_reply",
        "sys/product/+/status",
    )

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
            config = DjiConnectionGateway(connection).get_workspace_config()
        except DjiGatewayError:
            return
        mqtt_host, mqtt_port = self._parse_mqtt_addr(config.mqtt_addr)
        if not mqtt_host or not config.mqtt_username or not config.mqtt_password:
            return

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"{self.client_id_prefix}-{connection.id}",
        )
        client.username_pw_set(config.mqtt_username, config.mqtt_password)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.connect(mqtt_host, mqtt_port, keepalive=30)
        while not self.stop_event.is_set():
            client.loop(timeout=1.0)
        client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties):  # pragma: no cover - MQTT callback
        if self._is_connect_failure(reason_code):
            return
        for topic in self.TOPICS:
            client.subscribe(topic)

    def _on_message(self, client, userdata, message):  # pragma: no cover - MQTT callback
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return
        self.handle_message(message.topic, payload)

    def handle_message(self, topic: str, payload: dict) -> dict:
        if topic.endswith("/osd"):
            device_sn = self._device_sn_from_topic(topic)
            if device_sn:
                return apply_osd_telemetry(device_sn=device_sn, payload=payload)
            return {"updated": 0}

        if topic.startswith("sys/product/") and topic.endswith("/status"):
            device_sn = self._device_sn_from_topic(topic)
            if device_sn:
                return apply_device_status_event(device_sn=device_sn, payload=payload)
            return {"updated": 0}

        if topic.endswith("/events") or topic.endswith("/services_reply"):
            job_id = self._job_id_from_payload(payload)
            event_status = self._status_from_payload(payload)
            if job_id and event_status:
                return apply_cloud_execution_event(dji_job_id=job_id, status=event_status, payload=payload)
        return {"updated": 0}

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
