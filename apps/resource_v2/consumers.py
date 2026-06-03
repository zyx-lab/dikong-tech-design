from __future__ import annotations

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone

from apps.access.authentication import sha256_text
from apps.access.models import AuthSession, DirectoryStatus, UserStatus
from apps.iam_v2.models import V2AccountProfile, V2AccountRoleAssignment
from apps.iam_v2.services import V2RequestContext
from apps.resource_v2.models import MqttLatestMessage, ResourceType
from apps.resource_v2.mqtt import MQTT_BROADCAST_GROUP, mqtt_message_envelope
from apps.resource_v2.services import effective_permissions_for_binding, get_resource, visible_bindings_queryset


class DjiMqttConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        token = self._token_from_query()
        self.user_id = await self._authenticate_token(token)
        if self.user_id is None:
            await self.close(code=4401)
            return

        self.device_sns: set[str] = set()
        self.topic_kinds: set[str] = set()
        self.topics: set[str] = set()
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
        await self.send_json(
            {
                "type": "subscription.accepted",
                "deviceSns": subscription["deviceSns"],
                "topicKinds": subscription["topicKinds"],
                "topics": subscription["topics"],
            }
        )
        for message in subscription["latest"]:
            await self.send_json(message)

    async def mqtt_message(self, event):
        message = event.get("message") if isinstance(event, dict) else None
        if not isinstance(message, dict):
            return
        if not self._matches(message):
            return
        await self.send_json(message)

    def _matches(self, message: dict) -> bool:
        if self.device_sns and message.get("deviceSn") not in self.device_sns:
            return False
        if self.topic_kinds and message.get("topicKind") not in self.topic_kinds:
            return False
        if self.topics and message.get("topic") not in self.topics:
            return False
        return True

    def _token_from_query(self) -> str:
        query = self.scope.get("query_string", b"").decode("utf-8", errors="ignore")
        return (parse_qs(query).get("token") or [""])[0]

    @database_sync_to_async
    def _authenticate_token(self, token: str) -> int | None:
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

    @database_sync_to_async
    def _resolve_subscription(self, user_id: int, content: dict) -> dict:
        context = _context_for_user(user_id)
        if context is None:
            return {"deviceSns": [], "topicKinds": [], "topics": [], "latest": []}
        visible_sns = _visible_monitor_device_sns(context)
        requested_sns = _string_list(content.get("deviceSns"))
        device_sns = sorted(set(requested_sns).intersection(visible_sns)) if requested_sns else sorted(visible_sns)
        topic_kinds = sorted(set(_string_list(content.get("topicKinds"))))
        topics = sorted(set(_string_list(content.get("topics"))))

        latest = MqttLatestMessage.objects.filter(device_sn__in=device_sns)
        if topic_kinds:
            latest = latest.filter(topic_kind__in=topic_kinds)
        if topics:
            latest = latest.filter(topic__in=topics)
        return {
            "deviceSns": device_sns,
            "topicKinds": topic_kinds,
            "topics": topics,
            "latest": [mqtt_message_envelope(message) for message in latest.order_by("-received_at", "-id")[:200]],
        }


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
    return V2RequestContext(user=profile.user, profile=profile, department=profile.department, role_codes=role_codes)


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
