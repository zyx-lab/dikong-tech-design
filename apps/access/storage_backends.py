from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from storages.backends.s3 import S3Storage
from storages.utils import clean_name

from apps.access.external_call_logging import (
    log_external_call_failed,
    log_external_call_finished,
    log_external_call_started,
)
from apps.access.request_logging import current_request_base_url, redact_payload


class LoggedS3Storage(S3Storage):
    """S3/MinIO storage backend with structured external-call logging."""

    service_name = "object_storage"

    @staticmethod
    def _public_endpoint() -> str:
        configured = str(getattr(settings, "OBJECT_STORAGE_PUBLIC_ENDPOINT_URL", "") or "").rstrip("/")
        if configured:
            return configured
        request_base_url = current_request_base_url.get()
        if not request_base_url:
            return ""
        parsed = urlsplit(request_base_url)
        hostname = parsed.hostname
        if not hostname:
            return ""
        port = int(getattr(settings, "OBJECT_STORAGE_PUBLIC_PORT", 9000))
        return f"{parsed.scheme}://{hostname}:{port}"

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

    def _rewrite_public_endpoint_url(self, url: str) -> str:
        public_endpoint = self._public_endpoint()
        internal_endpoint = str(getattr(self, "endpoint_url", "") or "").rstrip("/")
        if not public_endpoint or not internal_endpoint:
            return url
        if url == internal_endpoint:
            return public_endpoint
        if url.startswith(f"{internal_endpoint}/"):
            return f"{public_endpoint}{url[len(internal_endpoint):]}"
        return url

    def _public_presigned_url(self, name, parameters=None, expire=None, http_method=None) -> str | None:
        public_endpoint = self._public_endpoint()
        if not public_endpoint or self.custom_domain or not self.querystring_auth:
            return None
        normalized_name = self._normalize_name(clean_name(name))
        params = (parameters or {}).copy()
        params["Bucket"] = self.bucket_name
        params["Key"] = normalized_name
        if expire is None:
            expire = self.querystring_expire
        client = self._create_session().client(
            "s3",
            region_name=self.region_name,
            use_ssl=self.use_ssl,
            endpoint_url=public_endpoint,
            config=self.client_config,
            verify=self.verify,
        )
        return client.generate_presigned_url("get_object", Params=params, ExpiresIn=expire, HttpMethod=http_method)

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
            result = self._public_presigned_url(name, parameters=parameters, expire=expire, http_method=http_method)
            if result is None:
                result = super().url(name, parameters=parameters, expire=expire, http_method=http_method)
        except Exception as exc:
            log_external_call_failed(call, error=exc, attempt=1)
            raise
        result = self._rewrite_public_endpoint_url(result)
        log_external_call_finished(call, response={"url": result}, attempt=1)
        return result
