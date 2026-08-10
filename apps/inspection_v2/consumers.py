from __future__ import annotations

import asyncio
import json
import time
from urllib.parse import urlparse

import paho.mqtt.client as mqtt
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from apps.access.exceptions import StandardForbidden, StandardNotFound
from apps.inspection_v2.drc_services import close_drc_session, drc_session_config
from apps.resource_v2.consumers import _authenticate_token, _context_for_user, _token_from_scope


NEUTRAL_STICK = {"roll": 1024, "pitch": 1024, "throttle": 1024, "yaw": 1024}
CONTROL_FRAME_FIELDS = {"type", "clientSeq", "sentAt", *NEUTRAL_STICK}


def validate_control_frame(content: dict, *, last_client_seq: int) -> tuple[int, dict]:
    if set(content) != CONTROL_FRAME_FIELDS:
        raise ValueError("INVALID_FIELDS")
    client_seq = content["clientSeq"]
    sent_at = content["sentAt"]
    if type(client_seq) is not int or client_seq <= last_client_seq:
        raise ValueError("CLIENT_SEQ_REPLAY")
    if type(sent_at) is not int or sent_at <= 0:
        raise ValueError("INVALID_SENT_AT")
    frame = {}
    for name in NEUTRAL_STICK:
        value = content[name]
        if type(value) is not int or not 364 <= value <= 1684:
            raise ValueError(f"INVALID_{name.upper()}")
        frame[name] = value
    return client_seq, frame


class DrcSessionConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user_id = await _authenticate_token(_token_from_scope(self.scope))
        if self.user_id is None:
            await self.close(code=4401)
            return

        self.session_id = str(self.scope["url_route"]["kwargs"]["session_id"])
        close_code, self.config = await self._load_config(self.user_id, self.session_id)
        if self.config is None:
            await self.close(code=close_code)
            return

        self.event_loop = asyncio.get_running_loop()
        self.mqtt_connected = False
        self.closing = False
        self.armed = False
        self.sequence = 0
        self.last_client_seq = 0
        self.last_frame_at = 0.0
        try:
            self.mqtt_client, endpoint = self._mqtt_client(self.config)
        except ValueError:
            await self.close(code=1011)
            return

        await self.accept()
        await self.send_json({"type": "session.connecting"})
        try:
            self.mqtt_client.connect_async(endpoint["host"], endpoint["port"], keepalive=10)
            self.mqtt_client.loop_start()
        except Exception:
            await self.send_json({"type": "error", "code": "MQTT_CONNECT_FAILED"})
            await self.close(code=1011)
            return
        self.watchdog_task = asyncio.create_task(self._watchdog())
        self.heartbeat_task = asyncio.create_task(self._heartbeat())

    async def disconnect(self, code):
        del code
        self.closing = True
        for name in ("watchdog_task", "heartbeat_task"):
            task = getattr(self, name, None)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        client = getattr(self, "mqtt_client", None)
        if client is not None:
            if getattr(self, "mqtt_connected", False):
                await self._neutralize()
            client.disconnect()
            client.loop_stop()
        config = getattr(self, "config", None)
        if config is not None:
            await self._close_session(self.session_id, config)

    async def receive_json(self, content, **kwargs):
        del kwargs
        message_type = content.get("type") if isinstance(content, dict) else None
        if message_type == "control.arm" and set(content) == {"type"}:
            if not self.mqtt_connected:
                await self._error("MQTT_NOT_CONNECTED")
                return
            self.armed = True
            self.last_frame_at = self.event_loop.time()
            await self.send_json({"type": "control.state", "armed": True})
            return
        if message_type == "control.disarm" and set(content) == {"type"}:
            await self._neutralize()
            await self.send_json({"type": "control.state", "armed": False})
            return
        if message_type != "control.frame":
            await self._error("UNKNOWN_MESSAGE_TYPE")
            return
        if not self.armed or not self.mqtt_connected:
            await self._error("CONTROL_NOT_ARMED")
            return
        try:
            client_seq, frame = validate_control_frame(content, last_client_seq=self.last_client_seq)
        except ValueError as exc:
            await self._error(str(exc))
            return
        try:
            sequence = self._publish("stick_control", frame)
        except RuntimeError:
            self.mqtt_connected = False
            self.armed = False
            await self._error("MQTT_PUBLISH_FAILED")
            return
        self.last_client_seq = client_seq
        self.last_frame_at = self.event_loop.time()
        await self.send_json({"type": "control.ack", "clientSeq": client_seq, "seq": sequence})

    async def _watchdog(self):
        while True:
            await asyncio.sleep(0.1)
            if self.armed and self.event_loop.time() - self.last_frame_at >= 0.5:
                await self._neutralize()
                await self.send_json({"type": "control.state", "armed": False, "reason": "CONTROL_TIMEOUT"})

    async def _heartbeat(self):
        while True:
            if self.mqtt_connected:
                try:
                    self._publish("heart_beat", {"timestamp": int(time.time() * 1000)})
                except RuntimeError:
                    await self._mqtt_lost()
            await asyncio.sleep(5)

    async def _neutralize(self):
        self.armed = False
        if self.mqtt_connected:
            try:
                self._publish("stick_control", NEUTRAL_STICK)
            except RuntimeError:
                self.mqtt_connected = False

    def _publish(self, method: str, data: dict) -> int:
        self.sequence += 1
        payload = json.dumps(
            {"seq": self.sequence, "method": method, "data": data},
            separators=(",", ":"),
        )
        result = self.mqtt_client.publish(self.config["publishTopic"], payload, qos=1)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError("MQTT_PUBLISH_FAILED")
        return self.sequence

    def _mqtt_client(self, config):
        endpoint = self._mqtt_endpoint(config["address"], config["enableTls"])
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=config["clientId"],
            transport=endpoint["transport"],
        )
        client.username_pw_set(config["username"], config["password"])
        if endpoint["tls"]:
            client.tls_set()
        if endpoint["transport"] == "websockets":
            client.ws_set_options(path=endpoint["path"])
        client.on_connect = self._on_mqtt_connect
        client.on_disconnect = self._on_mqtt_disconnect
        client.on_message = self._on_mqtt_message
        return client, endpoint

    @staticmethod
    def _mqtt_endpoint(address, enable_tls):
        value = str(address or "").strip()
        if "://" not in value:
            value = f"tcp://{value}"
        parsed = urlparse(value)
        scheme = parsed.scheme.lower()
        if not parsed.hostname or scheme not in {"tcp", "mqtt", "ssl", "tls", "mqtts", "ws", "wss"}:
            raise ValueError("INVALID_MQTT_ADDRESS")
        websocket = scheme in {"ws", "wss"}
        tls = bool(enable_tls) or scheme in {"ssl", "tls", "mqtts", "wss"}
        default_port = 443 if scheme == "wss" else 80 if scheme == "ws" else 8883 if tls else 1883
        return {
            "host": parsed.hostname,
            "port": parsed.port or default_port,
            "transport": "websockets" if websocket else "tcp",
            "tls": tls,
            "path": parsed.path or "/mqtt",
        }

    def _on_mqtt_connect(self, client, userdata, flags, reason_code, properties):
        del userdata, flags, properties
        failed = bool(reason_code.is_failure) if hasattr(reason_code, "is_failure") else str(reason_code) not in {"0", "Success"}
        if failed:
            self._schedule(self._mqtt_failed)
            return
        client.subscribe(self.config["subscribeTopic"], qos=1)
        self._schedule(self._mqtt_ready)

    def _on_mqtt_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        del client, userdata, disconnect_flags, reason_code, properties
        if not self.closing:
            self._schedule(self._mqtt_lost)

    def _on_mqtt_message(self, client, userdata, message):
        del client, userdata
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return
        self._schedule(self._forward_mqtt, payload)

    def _schedule(self, callback, *args):
        if not self.closing and not self.event_loop.is_closed():
            self.event_loop.call_soon_threadsafe(lambda: asyncio.create_task(callback(*args)))

    async def _mqtt_ready(self):
        self.mqtt_connected = True
        self.armed = False
        await self.send_json({"type": "session.connected"})

    async def _mqtt_lost(self):
        self.mqtt_connected = False
        self.armed = False
        await self.send_json({"type": "session.disconnected"})

    async def _mqtt_failed(self):
        await self._error("MQTT_CONNECT_FAILED")
        await self.close(code=1011)

    async def _forward_mqtt(self, payload):
        await self.send_json({"type": "drc.message", "message": payload})

    async def _error(self, code):
        await self.send_json({"type": "error", "code": code})

    @database_sync_to_async
    def _load_config(self, user_id, session_id):
        context = _context_for_user(user_id)
        if context is None:
            return 4403, None
        try:
            return 0, drc_session_config(context=context, session_id=session_id)
        except StandardForbidden:
            return 4403, None
        except StandardNotFound:
            return 4404, None

    @database_sync_to_async
    def _close_session(self, session_id, config):
        close_drc_session(session_id=session_id, config=config)
