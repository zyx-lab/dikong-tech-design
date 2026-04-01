from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from django.conf import settings
from django.utils import timezone

from apps.dji_bff.models import DjiWorkspaceConfig


@dataclass
class GatewayResponse:
    status_code: int
    headers: dict[str, str]
    data: object


class DjiGatewayError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 500, data=None):
        super().__init__(message)
        self.status_code = status_code
        self.data = data


class DjiGatewayConfigurationError(DjiGatewayError):
    pass


class DjiGatewayUpstreamError(DjiGatewayError):
    pass


class DjiGateway:
    """Mock-friendly DJI gateway with a concrete HTTP implementation."""

    DEFAULT_WAYLINE_TYPE = 0
    DEFAULT_TASK_TYPE = 0
    DEFAULT_RTH_ALTITUDE = 30
    DEFAULT_OUT_OF_CONTROL_ACTION = 0

    def __init__(self, *, base_url: str | None = None, timeout: int | None = None):
        self.base_url = (base_url or getattr(settings, "DJI_UPSTREAM_BASE_URL", "")).rstrip("/")
        self.timeout = timeout or int(getattr(settings, "DJI_UPSTREAM_TIMEOUT_SECONDS", 10))

    def get_live_capacity(self, device_sn: str):
        payload = self._request_json("GET", "/api/v1/manage/live/capacity").data
        items = self._extract_items(payload)
        for item in items:
            if self._matches_device(item, device_sn=device_sn):
                return item
        return {}

    def get_current_user(self):
        return self._request_json("GET", "/api/v1/manage/users/current").data

    def get_current_workspace(self):
        return self._request_json("GET", "/api/v1/manage/workspaces/current").data

    def list_devices(self) -> list[dict]:
        workspace_id = self._workspace_id()
        return self._request_paginated_items(
            f"/api/v1/manage/workspaces/{workspace_id}/devices/bound",
            query={"domain": 0},
        )

    def start_live(self, device_sn: str, **kwargs):
        payload = {"device_sn": device_sn}
        payload.update(kwargs)
        return self._request_json("POST", "/api/v1/manage/live/streams/start", data=payload).data

    def stop_live(self, device_sn: str, **kwargs):
        payload = {"device_sn": device_sn}
        payload.update(kwargs)
        return self._request_json("POST", "/api/v1/manage/live/streams/stop", data=payload).data

    def set_live_video_quality(self, device_sn: str, **kwargs):
        payload = {"device_sn": device_sn}
        payload.update(kwargs)
        return self._request_json("POST", "/api/v1/manage/live/streams/update", data=payload).data

    def set_live_video_source(self, device_sn: str, **kwargs):
        payload = {"device_sn": device_sn}
        payload.update(kwargs)
        return self._request_json("POST", "/api/v1/manage/live/streams/switch", data=payload).data

    def upload_route(self, *, route_name: str, file_obj):
        workspace_id = self._workspace_id()
        fields = {"name": route_name}
        files = {"file": (getattr(file_obj, "name", f"{uuid.uuid4().hex}.kmz"), file_obj.read())}
        payload = self._request_multipart(
            "POST",
            f"/api/v1/wayline/workspaces/{workspace_id}/waylines/files/upload",
            fields=fields,
            files=files,
        ).data
        if isinstance(payload, dict) and payload.get("dji_wayline_id"):
            return payload
        raise DjiGatewayUpstreamError("上传航线后未返回 dji_wayline_id", status_code=502, data=payload)

    def get_duplicate_route_names(self, names: list[str]) -> list[str]:
        workspace_id = self._workspace_id()
        normalized_names = [name.strip() for name in names if isinstance(name, str) and name.strip()]
        if not normalized_names:
            return []
        payload = self._request_json(
            "GET",
            f"/api/v1/wayline/workspaces/{workspace_id}/waylines/duplicate-names?"
            f"{urlencode([('name', name) for name in normalized_names])}",
        ).data
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, str) and item]
        if isinstance(payload, dict):
            for key in ("list", "items", "names"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, str) and item]
        return []

    def get_route_download_url(self, dji_wayline_id: str):
        workspace_id = self._workspace_id()
        response = self._request_json(
            "GET",
            f"/api/v1/wayline/workspaces/{workspace_id}/waylines/{dji_wayline_id}/url",
            follow_redirects=False,
        )
        if "Location" in response.headers:
            return response.headers["Location"]
        payload = response.data
        if isinstance(payload, dict):
            for key in ("url", "download_url", "downloadUrl"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value
        raise DjiGatewayUpstreamError("未获取到航线下载地址", status_code=502, data=payload)

    def delete_route(self, dji_wayline_id: str):
        workspace_id = self._workspace_id()
        return self._request_json(
            "DELETE",
            f"/api/v1/wayline/workspaces/{workspace_id}/waylines/{dji_wayline_id}",
        ).data

    def create_mission(self, *, mission_name: str, file_id: str, dock_sn: str | None = None):
        workspace_id = self._workspace_id()
        payload = {
            "name": mission_name,
            "fileId": file_id,
            "dockSn": dock_sn or "",
            "waylineType": self.DEFAULT_WAYLINE_TYPE,
            "taskType": self.DEFAULT_TASK_TYPE,
            "rthAltitude": self.DEFAULT_RTH_ALTITUDE,
            "outOfControlAction": self.DEFAULT_OUT_OF_CONTROL_ACTION,
        }
        response = self._request_json(
            "POST",
            f"/api/v1/wayline/workspaces/{workspace_id}/flight-tasks",
            data=payload,
        ).data
        if isinstance(response, dict) and response.get("dji_job_id"):
            return response
        if isinstance(response, dict) and response.get("job_id"):
            return {"dji_job_id": response["job_id"]}
        raise DjiGatewayUpstreamError("创建 DJI 任务后未返回 dji_job_id", status_code=502, data=response)

    def cancel_mission(self, dji_job_id: str):
        workspace_id = self._workspace_id()
        return self._request_json(
            "DELETE",
            f"/api/v1/wayline/workspaces/{workspace_id}/jobs?{urlencode({'job_id': dji_job_id})}",
        ).data

    def list_jobs(self) -> list[dict]:
        workspace_id = self._workspace_id()
        return self._request_paginated_items(f"/api/v1/wayline/workspaces/{workspace_id}/jobs")

    def get_media_url(self, dji_file_id: str):
        workspace_id = self._workspace_id()
        response = self._request_json(
            "GET",
            f"/api/v1/media/workspaces/{workspace_id}/files/{dji_file_id}/url",
            follow_redirects=False,
        )
        if "Location" in response.headers:
            return response.headers["Location"]
        payload = response.data
        if isinstance(payload, dict):
            for key in ("url", "download_url", "downloadUrl"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value
        raise DjiGatewayUpstreamError("未获取到媒体下载地址", status_code=502, data=payload)

    def list_media_files(self) -> list[dict]:
        workspace_id = self._workspace_id()
        return self._request_paginated_items(f"/api/v1/media/workspaces/{workspace_id}/files")

    def _workspace_id(self) -> str:
        config = self._ensure_authenticated()
        if not config.workspace_id:
            raise DjiGatewayConfigurationError("DJI workspace 未配置", status_code=500)
        return config.workspace_id

    def _current_config(self) -> DjiWorkspaceConfig | None:
        return DjiWorkspaceConfig.objects.order_by("-id").first()

    def _ensure_authenticated(self) -> DjiWorkspaceConfig:
        config = self._current_config()
        if config is None or not config.access_token or not config.workspace_id:
            return self._login_session(config=config)
        if config.expires_at is not None and config.expires_at <= timezone.now():
            try:
                return self._refresh_session(config)
            except DjiGatewayUpstreamError:
                return self._login_session(config=config)
        return config

    def _reauthenticate(self) -> DjiWorkspaceConfig:
        config = self._current_config()
        if config is not None and config.access_token:
            try:
                return self._refresh_session(config)
            except DjiGatewayUpstreamError:
                pass
        return self._login_session(config=config)

    def _login_session(self, *, config: DjiWorkspaceConfig | None = None) -> DjiWorkspaceConfig:
        username, password = self._configured_credentials()
        payload = {
            "username": username,
            "password": password,
            "flag": self._configured_login_flag(),
        }
        response = self._request(
            "POST",
            "/api/v1/manage/login",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(content_type="application/json"),
            follow_redirects=True,
        )
        return self._save_session(response.data, config=config)

    def _refresh_session(self, config: DjiWorkspaceConfig) -> DjiWorkspaceConfig:
        if not config.access_token:
            raise DjiGatewayUpstreamError("DJI access_token 缺失，无法续期", status_code=401)
        response = self._request(
            "POST",
            "/api/v1/manage/token/refresh",
            data=None,
            headers=self._headers(content_type="application/json", auth_token=config.access_token),
            follow_redirects=True,
        )
        return self._save_session(response.data, config=config)

    def _save_session(self, payload, *, config: DjiWorkspaceConfig | None = None) -> DjiWorkspaceConfig:
        if not isinstance(payload, dict):
            raise DjiGatewayUpstreamError("DJI 登录态响应格式不正确", status_code=502, data=payload)

        workspace_id = self._string_value(payload.get("workspace_id")) or getattr(config, "workspace_id", "")
        access_token = self._string_value(payload.get("access_token"))
        if not workspace_id or not access_token:
            raise DjiGatewayUpstreamError("DJI 登录态响应缺少关键字段", status_code=502, data=payload)

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
        if workspace_config.pk is None:
            workspace_config.save()
        else:
            workspace_config.save(
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
                    "updated_at",
                ]
            )
        return workspace_config

    def _configured_credentials(self) -> tuple[str, str]:
        username = str(getattr(settings, "DJI_UPSTREAM_USERNAME", "") or "").strip()
        password = str(getattr(settings, "DJI_UPSTREAM_PASSWORD", "") or "")
        if not username or not password:
            raise DjiGatewayConfigurationError("DJI upstream 账号或密码未配置", status_code=500)
        return username, password

    def _configured_login_flag(self) -> int:
        raw = getattr(settings, "DJI_UPSTREAM_LOGIN_FLAG", 1)
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise DjiGatewayConfigurationError("DJI_UPSTREAM_LOGIN_FLAG 配置无效", status_code=500) from exc

    def _headers(self, *, content_type: str | None = None, auth_token: str | None = None) -> dict[str, str]:
        if not self.base_url:
            raise DjiGatewayConfigurationError("DJI_UPSTREAM_BASE_URL 未配置", status_code=500)

        headers = {"Accept": "application/json"}
        if auth_token:
            headers["x-auth-token"] = auth_token
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        data=None,
        follow_redirects: bool = True,
        authenticate: bool = True,
    ) -> GatewayResponse:
        body = None
        auth_token = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
        if authenticate:
            auth_token = self._ensure_authenticated().access_token
        headers = self._headers(content_type="application/json", auth_token=auth_token)
        try:
            return self._request(method, path, data=body, headers=headers, follow_redirects=follow_redirects)
        except DjiGatewayUpstreamError as exc:
            if not authenticate or not self._is_auth_error(exc):
                raise
            headers = self._headers(content_type="application/json", auth_token=self._reauthenticate().access_token)
            return self._request(method, path, data=body, headers=headers, follow_redirects=follow_redirects)

    def _request_paginated_items(self, path: str, *, query: dict | None = None, page_size: int = 100) -> list[dict]:
        items: list[dict] = []
        page = 1
        query = {} if query is None else dict(query)

        while True:
            page_query = dict(query)
            page_query["page"] = page
            page_query["page_size"] = page_size
            payload = self._request_json("GET", self._with_query(path, page_query)).data
            page_items = self._extract_items(payload)
            items.extend(page_items)
            pagination = self._extract_pagination(payload)
            if not pagination:
                break

            total = self._int_value(pagination.get("total"))
            current_page = self._int_value(pagination.get("page")) or page
            current_page_size = self._int_value(pagination.get("page_size")) or page_size
            if total is not None and len(items) >= total:
                break
            if not page_items or current_page_size <= 0:
                break
            page = current_page + 1

        return items

    def _request_multipart(self, method: str, path: str, *, fields: dict[str, str], files: dict[str, tuple[str, bytes]]) -> GatewayResponse:
        boundary = f"----DjiBoundary{uuid.uuid4().hex}"
        body = bytearray()
        for key, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")
        for key, (filename, content) in files.items():
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(
                f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode("utf-8")
            )
            body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
            body.extend(content)
            body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))
        headers = self._headers(
            content_type=f"multipart/form-data; boundary={boundary}",
            auth_token=self._ensure_authenticated().access_token,
        )
        try:
            return self._request(method, path, data=bytes(body), headers=headers, follow_redirects=True)
        except DjiGatewayUpstreamError as exc:
            if not self._is_auth_error(exc):
                raise
            headers = self._headers(
                content_type=f"multipart/form-data; boundary={boundary}",
                auth_token=self._reauthenticate().access_token,
            )
            return self._request(method, path, data=bytes(body), headers=headers, follow_redirects=True)

    def _request(self, method: str, path: str, *, data: bytes | None, headers: dict[str, str], follow_redirects: bool) -> GatewayResponse:
        url = f"{self.base_url}{path}"
        request = Request(url=url, data=data, headers=headers, method=method)
        opener = None if follow_redirects else build_opener(_NoRedirectHandler())
        try:
            open_fn = urlopen if opener is None else opener.open
            with open_fn(request, timeout=self.timeout) as response:
                raw_body = response.read()
                return GatewayResponse(
                    status_code=response.status,
                    headers=dict(response.headers.items()),
                    data=self._parse_body(raw_body),
                )
        except HTTPError as exc:
            payload = self._parse_body(exc.read())
            raise DjiGatewayUpstreamError("DJI upstream request failed", status_code=exc.code, data=payload) from exc
        except URLError as exc:
            raise DjiGatewayUpstreamError("DJI upstream unreachable", status_code=502) from exc

    @staticmethod
    def _parse_body(raw_body: bytes):
        if not raw_body:
            return {}
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {"raw": raw_body.decode("utf-8", errors="ignore")}
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    @staticmethod
    def _is_auth_error(exc: DjiGatewayUpstreamError) -> bool:
        return exc.status_code == 401

    @staticmethod
    def _token_expires_at(access_token: str):
        if not isinstance(access_token, str) or access_token.count(".") < 2:
            return None
        try:
            payload_segment = access_token.split(".")[1]
            payload_segment += "=" * (-len(payload_segment) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_segment.encode("ascii")).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        exp = payload.get("exp")
        if isinstance(exp, bool):
            return None
        try:
            exp_value = int(exp)
        except (TypeError, ValueError):
            return None
        return datetime.fromtimestamp(exp_value, tz=dt_timezone.utc)

    @staticmethod
    def _string_value(value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

    @staticmethod
    def _extract_items(payload) -> list[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("list", "items", "devices"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _extract_pagination(payload) -> dict | None:
        if isinstance(payload, dict):
            pagination = payload.get("pagination")
            if isinstance(pagination, dict):
                return pagination
        return None

    @staticmethod
    def _int_value(value) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return int(value.strip())
            except ValueError:
                return None
        return None

    @staticmethod
    def _with_query(path: str, query: dict) -> str:
        split = urlsplit(path)
        existing = parse_qsl(split.query, keep_blank_values=True)
        merged = [(key, value) for key, value in existing if key not in query]
        for key, value in query.items():
            if isinstance(value, (list, tuple)):
                merged.extend((key, item) for item in value)
            else:
                merged.append((key, value))
        return urlunsplit((split.scheme, split.netloc, split.path, urlencode(merged, doseq=True), split.fragment))

    @staticmethod
    def _matches_device(payload: dict, *, device_sn: str) -> bool:
        candidates = [
            payload.get("device_sn"),
            payload.get("sn"),
            payload.get("deviceSn"),
        ]
        return device_sn in {value for value in candidates if isinstance(value, str)}


class _NoRedirectHandler(HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        fp.status = code
        fp.code = code
        return fp

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302
