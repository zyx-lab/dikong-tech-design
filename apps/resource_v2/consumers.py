from __future__ import annotations

import asyncio
from urllib.parse import parse_qs

import websockets
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone

from apps.access.authentication import sha256_text
from apps.access.models import AuthSession, DirectoryStatus, UserStatus
from apps.iam_v2.models import V2AccountProfile, V2AccountRoleAssignment
from apps.iam_v2.services import V2RequestContext, active_roles_for_codes, data_scopes_for_roles, permission_codes_for_roles
from apps.resource_v2.models import CameraResource, ResourceType
from apps.resource_v2.mqtt import MQTT_BROADCAST_GROUP
from apps.resource_v2.services import effective_permissions_for_binding, get_resource, visible_bindings_queryset


def _token_from_scope(scope) -> str:
    query = scope.get("query_string", b"").decode("utf-8", errors="ignore")
    return (parse_qs(query).get("token") or [""])[0]


@database_sync_to_async
def _authenticate_token(token: str) -> int | None:
    if not token:
        return None
    session = (
        AuthSession.objects.select_related("user")
        .filter(
            access_token_hash=sha256_text(token),
            revoked_at__isnull=True,
            access_token_expires_at__gt=timezone.now(),
        )
        .first()
    )
    if session is None:
        return None
    user = session.user
    if not user.is_active or user.status != UserStatus.ACTIVE:
        return None
    return user.id


class DjiMqttConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user_id = await _authenticate_token(_token_from_scope(self.scope))
        if self.user_id is None:
            await self.close(code=4401)
            return

        self.device_sns: set[str] = set()
        self.topic_kinds: set[str] = set()
        self.topics: set[str] = set()
        self.subscribed = False
        await self.channel_layer.group_add(MQTT_BROADCAST_GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        del code
        if getattr(self, "channel_layer", None) is not None:
            await self.channel_layer.group_discard(MQTT_BROADCAST_GROUP, self.channel_name)

    async def receive_json(self, content, **kwargs):
        del kwargs
        if content.get("type") != "subscribe":
            await self.send_json({"type": "error", "code": "unsupported_message_type"})
            return

        subscription = await self._resolve_subscription(self.user_id, content)
        if not subscription["deviceSns"]:
            await self.send_json({"type": "subscription.error", "code": "no_visible_devices"})
            return

        self.device_sns = set(subscription["deviceSns"])
        self.topic_kinds = set(subscription["topicKinds"])
        self.topics = set(subscription["topics"])
        self.subscribed = True
        await self.send_json(
            {
                "type": "subscription.accepted",
                "deviceSns": subscription["deviceSns"],
                "topicKinds": subscription["topicKinds"],
                "topics": subscription["topics"],
            }
        )

    async def mqtt_message(self, event):
        message = event.get("message") if isinstance(event, dict) else None
        if not isinstance(message, dict):
            return
        if not self._matches(message):
            return
        await self.send_json(message)

    def _matches(self, message: dict) -> bool:
        if not getattr(self, "subscribed", False):
            return False
        if self.device_sns and message.get("deviceSn") not in self.device_sns:
            return False
        if self.topic_kinds and message.get("topicKind") not in self.topic_kinds:
            return False
        if self.topics and message.get("topic") not in self.topics:
            return False
        return True

    @database_sync_to_async
    def _resolve_subscription(self, user_id: int, content: dict) -> dict:
        context = _context_for_user(user_id)
        if context is None:
            return {"deviceSns": [], "topicKinds": [], "topics": []}
        visible_sns = _visible_monitor_device_sns(context)
        requested_sns = _string_list(content.get("deviceSns"))
        device_sns = sorted(set(requested_sns).intersection(visible_sns)) if requested_sns else sorted(visible_sns)
        topic_kinds = sorted(set(_string_list(content.get("topicKinds"))))
        topics = sorted(set(_string_list(content.get("topics"))))
        return {
            "deviceSns": device_sns,
            "topicKinds": topic_kinds,
            "topics": topics,
        }


class CameraResultsConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user_id = await _authenticate_token(_token_from_scope(self.scope))
        if self.user_id is None:
            await self.close(code=4401)
            return

        camera_id = self.scope.get("url_route", {}).get("kwargs", {}).get("id")
        config = await self._camera_config(self.user_id, camera_id)
        if config is None:
            await self.close(code=4404)
            return

        try:
            # ponytail: one upstream subscription per viewer; add shared fan-out only when concurrent load requires it.
            self.upstream = await websockets.connect(
                config["results_ws_url"],
                extra_headers={"X-API-Key": config["api_key"]},
                ping_interval=20,
                ping_timeout=20,
            )
        except Exception:
            await self.close(code=1011)
            return

        await self.accept()
        self.forward_task = asyncio.create_task(self._forward_results())

    async def disconnect(self, code):
        del code
        task = getattr(self, "forward_task", None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        upstream = getattr(self, "upstream", None)
        if upstream is not None:
            await upstream.close()

    async def receive(self, text_data=None, bytes_data=None):
        del text_data, bytes_data

    async def _forward_results(self):
        try:
            async for message in self.upstream:
                if isinstance(message, bytes):
                    await self.send(bytes_data=message)
                else:
                    await self.send(text_data=message)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.close(code=1011)

    @database_sync_to_async
    def _camera_config(self, user_id: int, camera_id: int | None) -> dict | None:
        context = _context_for_user(user_id)
        if context is None or camera_id is None:
            return None
        binding = visible_bindings_queryset(context, resource_type=ResourceType.CAMERA).filter(resource_object_id=camera_id).first()
        if binding is None:
            return None
        camera = CameraResource.objects.filter(pk=camera_id).first()
        if camera is None:
            return None
        return {"results_ws_url": camera.results_ws_url, "api_key": camera.api_key}


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized = []
    for item in value:
        text = str(item or "").strip()
        if text:
            normalized.append(text)
    return normalized


def _context_for_user(user_id: int) -> V2RequestContext | None:
    profile = (
        V2AccountProfile.objects.select_related("user", "department")
        .filter(user_id=user_id, status=DirectoryStatus.ACTIVE, department__status=DirectoryStatus.ACTIVE)
        .first()
    )
    if profile is None:
        return None
    role_codes = list(
        V2AccountRoleAssignment.objects.filter(account_profile=profile)
        .order_by("id")
        .values_list("role_code", flat=True)
    )
    roles = active_roles_for_codes(role_codes)
    return V2RequestContext(
        user=profile.user,
        profile=profile,
        department=profile.department,
        role_codes=role_codes,
        permissions=frozenset(permission_codes_for_roles(roles)),
        data_scopes=data_scopes_for_roles(roles),
        is_super_admin=any(role.is_super_admin for role in roles),
    )


def _visible_monitor_device_sns(context: V2RequestContext) -> set[str]:
    device_sns: set[str] = set()
    for resource_type in (ResourceType.DRONE, ResourceType.DOCK, ResourceType.GATEWAY, ResourceType.PAYLOAD):
        for binding in visible_bindings_queryset(context, resource_type=resource_type):
            if "monitor" not in set(effective_permissions_for_binding(context, binding)):
                continue
            resource = get_resource(resource_type, binding.resource_object_id)
            device_sn = getattr(resource, "device_sn", "")
            if device_sn:
                device_sns.add(device_sn)
    return device_sns
