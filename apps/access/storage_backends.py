from __future__ import annotations

from typing import Any

from storages.backends.s3 import S3Storage

from apps.access.external_call_logging import (
    log_external_call_failed,
    log_external_call_finished,
    log_external_call_started,
)
from apps.access.request_logging import redact_payload


class LoggedS3Storage(S3Storage):
    """S3/MinIO storage backend with structured external-call logging."""

    service_name = "object_storage"

    def _storage_url(self, name: str = "") -> str:
        endpoint = str(getattr(self, "endpoint_url", "") or "").rstrip("/")
        bucket = str(getattr(self, "bucket_name", "") or "").strip("/")
        key = str(name or "").lstrip("/")
        if endpoint and bucket and key:
            return f"{endpoint}/{bucket}/{key}"
        if endpoint and bucket:
            return f"{endpoint}/{bucket}"
        return endpoint or bucket or key

    def _request_payload(self, *, name: str, content: Any = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "bucket": getattr(self, "bucket_name", ""),
            "key": name,
            "endpoint_url": getattr(self, "endpoint_url", ""),
        }
        if content is not None:
            payload["content"] = redact_payload(content)
        return payload

    def _save(self, name, content):
        call = log_external_call_started(
            service=self.service_name,
            operation="save",
            method="PUT",
            url=self._storage_url(name),
            path=str(name),
            request=self._request_payload(name=str(name), content=content),
            attempt=1,
        )
        try:
            saved_name = super()._save(name, content)
        except Exception as exc:
            log_external_call_failed(call, error=exc, attempt=1)
            raise
        log_external_call_finished(call, response={"name": saved_name}, attempt=1)
        return saved_name

    def delete(self, name):
        call = log_external_call_started(
            service=self.service_name,
            operation="delete",
            method="DELETE",
            url=self._storage_url(name),
            path=str(name),
            request=self._request_payload(name=str(name)),
            attempt=1,
        )
        try:
            result = super().delete(name)
        except Exception as exc:
            log_external_call_failed(call, error=exc, attempt=1)
            raise
        log_external_call_finished(call, response={"deleted": True}, attempt=1)
        return result

    def exists(self, name):
        call = log_external_call_started(
            service=self.service_name,
            operation="exists",
            method="HEAD",
            url=self._storage_url(name),
            path=str(name),
            request=self._request_payload(name=str(name)),
            attempt=1,
        )
        try:
            result = super().exists(name)
        except Exception as exc:
            log_external_call_failed(call, error=exc, attempt=1)
            raise
        log_external_call_finished(call, response={"exists": result}, attempt=1)
        return result

    def url(self, name, parameters=None, expire=None, http_method=None):
        call = log_external_call_started(
            service=self.service_name,
            operation="url",
            method=http_method or "GET",
            url=self._storage_url(name),
            path=str(name),
            request={
                **self._request_payload(name=str(name)),
                "parameters": parameters or {},
                "expire": expire,
            },
            attempt=1,
        )
        try:
            result = super().url(name, parameters=parameters, expire=expire, http_method=http_method)
        except Exception as exc:
            log_external_call_failed(call, error=exc, attempt=1)
            raise
        log_external_call_finished(call, response={"url": result}, attempt=1)
        return result
