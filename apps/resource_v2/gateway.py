from django.utils import timezone

from apps.dji_bff.gateway import DjiGateway, DjiGatewayUpstreamError
from apps.resource_v2.models import DjiConnection, DjiConnectionStatus, ResourceType


RESOURCE_DOMAINS = {
    ResourceType.DRONE: 0,
    ResourceType.DOCK: 3,
}


class DjiConnectionGateway(DjiGateway):
    def __init__(self, connection: DjiConnection):
        self.connection = connection
        super().__init__(platform=connection)

    def _current_config(self):
        if self.connection.pk is not None:
            self.connection.refresh_from_db()
        return self.connection

    def _save_session(self, payload, *, config=None):
        if not isinstance(payload, dict):
            raise DjiGatewayUpstreamError("DJI 登录态响应格式不正确", status_code=502, data=payload)

        connection = config or self.connection
        workspace_id = self._string_value(payload.get("workspace_id")) or getattr(connection, "workspace_id", "")
        access_token = self._string_value(payload.get("access_token"))
        if not workspace_id or not access_token:
            raise DjiGatewayUpstreamError("DJI 登录态响应缺少关键字段", status_code=502, data=payload)

        connection.workspace_id = workspace_id
        connection.dji_user_id = self._string_value(payload.get("user_id"))
        connection.dji_username = self._string_value(payload.get("username"))
        connection.dji_user_type = self._string_value(payload.get("user_type"))
        connection.access_token = access_token
        connection.mqtt_username = self._string_value(payload.get("mqtt_username"))
        connection.mqtt_password = self._string_value(payload.get("mqtt_password"))
        connection.mqtt_addr = self._string_value(payload.get("mqtt_addr"))
        connection.expires_at = self._token_expires_at(access_token)
        connection.status = DjiConnectionStatus.ACTIVE
        connection.last_checked_at = timezone.now()
        connection.save(
            update_fields=[
                "workspace_id",
                "dji_user_id",
                "dji_username",
                "dji_user_type",
                "access_token",
                "mqtt_username",
                "mqtt_password",
                "mqtt_addr",
                "expires_at",
                "status",
                "last_checked_at",
                "updated_at",
            ]
        )
        return connection

    def list_resources(self, resource_type: str) -> list[dict]:
        workspace_id = self._workspace_id()
        domain = RESOURCE_DOMAINS[ResourceType(resource_type)]
        return self._request_paginated_items(
            f"/api/v1/manage/workspaces/{workspace_id}/devices/bound",
            query={"domain": domain},
        )

    def list_gateways(self) -> list[dict]:
        workspace_id = self._workspace_id()
        return self._request_paginated_items(f"/api/v1/manage/workspaces/{workspace_id}/devices")

    @staticmethod
    def _payloads_from_devices(devices: list[dict]) -> list[dict]:
        payloads = []
        seen = set()
        for device in devices:
            if not isinstance(device, dict):
                continue
            candidates = []
            for key in ("payloads", "payloads_list", "payloadsList", "payload_list"):
                value = device.get(key)
                if isinstance(value, list):
                    candidates.extend(item for item in value if isinstance(item, dict))
            single_payload = device.get("payload")
            if isinstance(single_payload, dict):
                candidates.append(single_payload)
            for payload in candidates:
                payload_sn = str(
                    payload.get("payload_sn")
                    or payload.get("payloadSn")
                    or payload.get("device_sn")
                    or payload.get("sn")
                    or ""
                ).strip()
                if not payload_sn or payload_sn in seen:
                    continue
                seen.add(payload_sn)
                payloads.append(payload)
        return payloads

    def discover(self) -> dict[str, list[dict]]:
        drones = self.list_resources(ResourceType.DRONE)
        docks = self.list_resources(ResourceType.DOCK)
        return {
            "drones": drones,
            "docks": docks,
            "gateways": self.list_gateways(),
            "payloads": self._payloads_from_devices([*drones, *docks]),
        }
