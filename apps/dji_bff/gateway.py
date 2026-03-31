from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from django.conf import settings

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
        payload = self._request_json(
            "GET",
            f"/api/v1/manage/workspaces/{workspace_id}/devices/bound",
        ).data
        return self._extract_items(payload)

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
        payload = {"name": mission_name}
        payload["fileId"] = file_id
        if dock_sn:
            payload["dockSn"] = dock_sn
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
        payload = self._request_json(
            "GET",
            f"/api/v1/wayline/workspaces/{workspace_id}/jobs",
        ).data
        return self._extract_items(payload)

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
        payload = self._request_json(
            "GET",
            f"/api/v1/media/workspaces/{workspace_id}/files",
        ).data
        return self._extract_items(payload)

    def _workspace_id(self) -> str:
        config = DjiWorkspaceConfig.objects.order_by("-id").first()
        if config is None or not config.workspace_id:
            raise DjiGatewayConfigurationError("DJI workspace 未配置", status_code=500)
        return config.workspace_id

    def _headers(self, *, content_type: str | None = None) -> dict[str, str]:
        if not self.base_url:
            raise DjiGatewayConfigurationError("DJI_UPSTREAM_BASE_URL 未配置", status_code=500)

        config = DjiWorkspaceConfig.objects.order_by("-id").first()
        headers = {"Accept": "application/json"}
        if config is not None and config.access_token:
            headers["x-auth-token"] = config.access_token
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _request_json(self, method: str, path: str, *, data=None, follow_redirects: bool = True) -> GatewayResponse:
        body = None
        headers = self._headers(content_type="application/json")
        if data is not None:
            body = json.dumps(data).encode("utf-8")
        return self._request(method, path, data=body, headers=headers, follow_redirects=follow_redirects)

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
        headers = self._headers(content_type=f"multipart/form-data; boundary={boundary}")
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
    def _matches_device(payload: dict, *, device_sn: str) -> bool:
        candidates = [
            payload.get("device_sn"),
            payload.get("sn"),
            payload.get("deviceSn"),
        ]
        children = payload.get("children")
        if isinstance(children, list):
            for child in children:
                if not isinstance(child, dict):
                    continue
                candidates.extend([child.get("device_sn"), child.get("sn"), child.get("deviceSn")])
        return device_sn in {value for value in candidates if isinstance(value, str)}


class _NoRedirectHandler(HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        fp.status = code
        fp.code = code
        return fp

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302
