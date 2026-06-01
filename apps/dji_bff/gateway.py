from __future__ import annotations

from django.utils import timezone

from apps.dji_bff.models import DjiCloudPlatform, DjiCloudPlatformStatus, DjiWorkspaceConfig
from apps.dji_cloud.gateway import (
    DjiCloudSession,
    DjiGateway as CloudDjiGateway,
    DjiGatewayConfigurationError,
    DjiGatewayError,
    DjiGatewayUpstreamError,
    GatewayResponse,
    urlopen,
)


class DjiGateway(CloudDjiGateway):
    """Legacy DJI BFF adapter backed by the shared DJI Cloud gateway."""

    def __init__(
        self,
        *,
        platform: DjiCloudPlatform | None = None,
        base_url: str | None = None,
        timeout: int | None = None,
    ):
        super().__init__(platform=platform, base_url=base_url, timeout=timeout)

    def _urlopen(self):
        return urlopen

    def _current_config(self) -> DjiWorkspaceConfig | DjiCloudPlatform | DjiCloudSession | None:
        if self.platform is not None:
            if self.platform.pk is not None:
                self.platform.refresh_from_db()
            return self.platform
        return DjiWorkspaceConfig.objects.order_by("-id").first()

    def _save_session(
        self,
        payload,
        *,
        config: DjiWorkspaceConfig | DjiCloudPlatform | None = None,
    ) -> DjiWorkspaceConfig | DjiCloudPlatform:
        if not isinstance(payload, dict):
            raise DjiGatewayUpstreamError("DJI 登录态响应格式不正确", status_code=502, data=payload)

        config = config or self.platform
        workspace_id = self._string_value(payload.get("workspace_id")) or getattr(config, "workspace_id", "")
        access_token = self._string_value(payload.get("access_token"))
        if not workspace_id or not access_token:
            raise DjiGatewayUpstreamError("DJI 登录态响应缺少关键字段", status_code=502, data=payload)

        is_platform_config = isinstance(config, DjiCloudPlatform)
        if not is_platform_config:
            DjiWorkspaceConfig.objects.exclude(pk=getattr(config, "pk", None)).delete()
        workspace_config = config or DjiWorkspaceConfig()
        workspace_config.workspace_id = workspace_id
        workspace_config.dji_user_id = self._string_value(payload.get("user_id"))
        workspace_config.dji_username = self._string_value(payload.get("username"))
        workspace_config.dji_user_type = self._string_value(payload.get("user_type"))
        workspace_config.access_token = access_token
        workspace_config.mqtt_username = self._string_value(payload.get("mqtt_username"))
        workspace_config.mqtt_password = self._string_value(payload.get("mqtt_password"))
        workspace_config.mqtt_addr = self._string_value(payload.get("mqtt_addr"))
        workspace_config.expires_at = self._token_expires_at(access_token)
        if is_platform_config:
            workspace_config.status = DjiCloudPlatformStatus.ACTIVE
            workspace_config.last_checked_at = timezone.now()
        if workspace_config.pk is None:
            workspace_config.save()
        else:
            update_fields = [
                "workspace_id",
                "dji_user_id",
                "dji_username",
                "dji_user_type",
                "access_token",
                "mqtt_username",
                "mqtt_password",
                "mqtt_addr",
                "expires_at",
                "updated_at",
            ]
            if is_platform_config:
                update_fields.extend(["status", "last_checked_at"])
            workspace_config.save(update_fields=update_fields)
        return workspace_config


__all__ = [
    "DjiGateway",
    "DjiGatewayConfigurationError",
    "DjiGatewayError",
    "DjiGatewayUpstreamError",
    "GatewayResponse",
]
