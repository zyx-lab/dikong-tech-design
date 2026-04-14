from __future__ import annotations

import json
import logging
import os
import sys
import threading
from uuid import uuid4
from urllib.parse import urlparse

from django.conf import settings
from django.db import close_old_connections

from apps.dji_bff.gateway import DjiGateway, DjiGatewayError
from apps.mission.flight_state import flight_state_registry
from apps.mission.models import Mission, MissionStatus

logger = logging.getLogger(__name__)

_WATCHER_LOCK = threading.Lock()
_WATCHER: "DjiMqttWatcher | None" = None


def should_start_in_process_watcher() -> bool:
    if not getattr(settings, "DJI_MQTT_WATCHER_ENABLED", True):
        return False

    argv = list(sys.argv)
    command = argv[1] if len(argv) > 1 and argv[0].endswith("manage.py") else ""
    if command in {
        "test",
        "migrate",
        "makemigrations",
        "collectstatic",
        "shell",
        "dbshell",
        "check",
        "createsuperuser",
        "run_dji_sync_scheduler",
    }:
        return False
    if command == "runserver":
        return os.environ.get("RUN_MAIN") == "true"
    return True


def ensure_dji_mqtt_watcher_started() -> "DjiMqttWatcher | None":
    global _WATCHER

    if not should_start_in_process_watcher():
        return None

    with _WATCHER_LOCK:
        if _WATCHER is not None:
            return _WATCHER
        watcher = DjiMqttWatcher()
        watcher.start()
        _WATCHER = watcher
        return watcher


class DjiMqttWatcher:
    def __init__(self, *, gateway: DjiGateway | None = None, refresh_seconds: int | None = None):
        self.gateway = gateway or DjiGateway()
        self.refresh_seconds = max(1, int(refresh_seconds or getattr(settings, "DJI_MQTT_WATCHER_REFRESH_SECONDS", 5)))
        self.client_id = f"dji-mission-watcher-{uuid4().hex[:12]}"
        self._client = None
        self._stop_event = threading.Event()
        self._connected_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._subscriptions: set[str] = set()
        self._subscription_lock = threading.RLock()
        self._loop_started = False

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="dji-mqtt-watcher", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._client is not None:
            try:
                if self._loop_started:
                    self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                logger.exception("Failed to stop DJI MQTT watcher cleanly")

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self._ensure_client_connected()
                if self._connected_event.is_set():
                    self._refresh_subscriptions()
            except Exception:
                logger.exception("DJI MQTT watcher loop failed")
            self._stop_event.wait(self.refresh_seconds)

    def _ensure_client_connected(self):
        if self._connected_event.is_set():
            return

        config = self.gateway.get_workspace_config()
        mqtt_host, mqtt_port = self._parse_mqtt_addr(config.mqtt_addr)
        if not mqtt_host or not config.mqtt_username or not config.mqtt_password:
            logger.warning("DJI MQTT watcher skipped because MQTT credentials are incomplete")
            return

        client = self._client or self._build_client()
        client.username_pw_set(config.mqtt_username, config.mqtt_password)
        if self._loop_started:
            client.reconnect()
            return
        client.connect(mqtt_host, mqtt_port, keepalive=30)
        client.loop_start()
        self._loop_started = True
        self._client = client

    def _build_client(self):
        import paho.mqtt.client as mqtt

        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client = client
        return client

    def _on_connect(self, client, userdata, flags, reason_code, properties):  # pragma: no cover - callback path
        if int(reason_code) != 0:
            logger.warning("DJI MQTT watcher connect rejected: %s", reason_code)
            self._connected_event.clear()
            return
        self._connected_event.set()
        self._refresh_subscriptions()

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):  # pragma: no cover
        self._connected_event.clear()

    def _on_message(self, client, userdata, message):  # pragma: no cover - callback path
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            logger.warning("DJI MQTT watcher ignored invalid JSON payload on %s", message.topic)
            return

        if message.topic.endswith("/osd"):
            self._handle_osd_message(topic=message.topic, payload=payload)

    def _refresh_subscriptions(self):
        if self._client is None or not self._connected_event.is_set():
            return

        desired_topics = self._subscription_topics(self._target_device_sns())
        with self._subscription_lock:
            current_topics = set(self._subscriptions)
            to_subscribe = sorted(desired_topics - current_topics)
            to_unsubscribe = sorted(current_topics - desired_topics)
            for topic in to_subscribe:
                self._client.subscribe(topic)
            for topic in to_unsubscribe:
                self._client.unsubscribe(topic)
            self._subscriptions = desired_topics

    def _target_device_sns(self) -> set[str]:
        close_old_connections()
        try:
            device_sns = Mission.objects.filter(is_deleted=False, status=MissionStatus.RUNNING).exclude(device_sn="")
            return set(device_sns.values_list("device_sn", flat=True))
        finally:
            close_old_connections()

    @staticmethod
    def _subscription_topics(device_sns: set[str]) -> set[str]:
        topics: set[str] = set()
        for device_sn in device_sns:
            topics.add(f"thing/product/{device_sn}/osd")
            topics.add(f"thing/product/{device_sn}/state")
        return topics

    def _handle_osd_message(self, *, topic: str, payload: dict):
        device_sn = self._device_sn_from_topic(topic, suffix="/osd")
        mode_code = self._mode_code(payload)
        if device_sn and mode_code is not None:
            flight_state_registry.update_from_mode_code(device_sn=device_sn, mode_code=mode_code)

    @staticmethod
    def _device_sn_from_topic(topic: str, *, suffix: str) -> str:
        normalized = str(topic or "").strip()
        if not normalized.endswith(suffix):
            return ""
        segments = normalized.split("/")
        if len(segments) != 4:
            return ""
        return segments[2]

    @staticmethod
    def _mode_code(payload: dict) -> int | None:
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        mode_code = data.get("mode_code")
        if isinstance(mode_code, bool):
            return None
        if isinstance(mode_code, int):
            return mode_code
        if isinstance(mode_code, str) and mode_code.strip():
            try:
                return int(mode_code.strip())
            except ValueError:
                return None
        return None

    @staticmethod
    def _parse_mqtt_addr(addr: str) -> tuple[str, int]:
        normalized = str(addr or "").strip()
        if not normalized:
            return "", 0
        if "://" not in normalized:
            normalized = f"tcp://{normalized}"
        parsed = urlparse(normalized)
        return parsed.hostname or "", parsed.port or 1883
