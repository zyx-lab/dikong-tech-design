from __future__ import annotations

import json
import logging
import traceback
import uuid
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.conf import settings


current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)

SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "passwd",
    "pwd",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "x-auth-token",
    "client_secret",
    "secret",
    "mqtt_password",
}


def truncate_text(value: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(value) <= max_chars:
        return value
    if max_chars <= 3:
        return value[:max_chars]
    return value[: max_chars - 3] + "..."


def _max_body_chars() -> int:
    return max(0, int(getattr(settings, "DJANGO_LOG_BODY_MAX_CHARS", 20_000)))


def _max_header_chars() -> int:
    return max(0, int(getattr(settings, "DJANGO_LOG_HEADER_MAX_CHARS", 4_096)))


def _is_sensitive_key(key: Any) -> bool:
    key_text = str(key).strip().lower()
    if not key_text:
        return False
    if key_text in SENSITIVE_KEYS:
        return True
    if key_text.startswith("x-auth-"):
        return True
    if "authorization" in key_text:
        return True
    if "cookie" in key_text:
        return True
    if "password" in key_text:
        return True
    if key_text.endswith("_token") or key_text.endswith("-token"):
        return True
    if key_text.endswith("_secret") or key_text.endswith("-secret"):
        return True
    if key_text == "token":
        return True
    return False


def _is_json_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float))


def _summarize_binary(value: bytes | bytearray | memoryview) -> dict[str, Any]:
    binary = bytes(value)
    return {
        "type": "binary",
        "size": len(binary),
    }


def _summarize_file(value: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "type": value.__class__.__name__,
    }
    name = getattr(value, "name", None)
    if name:
        summary["name"] = truncate_text(str(name), _max_header_chars())
    size = getattr(value, "size", None)
    if isinstance(size, int):
        summary["size"] = size
    content_type = getattr(value, "content_type", None)
    if content_type:
        summary["content_type"] = truncate_text(str(content_type), _max_header_chars())
    return summary


def redact_payload(value: Any, *, max_text_chars: int | None = None) -> Any:
    resolved_max_text_chars = _max_body_chars() if max_text_chars is None else max(0, int(max_text_chars))

    if _is_json_scalar(value):
        return value
    if isinstance(value, str):
        return truncate_text(value, resolved_max_text_chars)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _summarize_binary(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return redact_payload(asdict(value), max_text_chars=resolved_max_text_chars)
    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = redact_payload(item, max_text_chars=resolved_max_text_chars)
        return redacted
    if isinstance(value, list):
        return [redact_payload(item, max_text_chars=resolved_max_text_chars) for item in value]
    if isinstance(value, tuple):
        return [redact_payload(item, max_text_chars=resolved_max_text_chars) for item in value]
    if isinstance(value, set):
        return [redact_payload(item, max_text_chars=resolved_max_text_chars) for item in sorted(value, key=str)]
    if hasattr(value, "read"):
        return _summarize_file(value)
    return truncate_text(str(value), resolved_max_text_chars)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _summarize_binary(value)
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return asdict(value)
    return str(value)


def log_json(logger: logging.Logger, level: int, event: str, **payload: Any) -> None:
    request_id = payload.pop("request_id", None)
    trace_id = payload.pop("trace_id", None)
    current_request = current_request_id.get()
    resolved_request_id = request_id or trace_id or current_request
    resolved_trace_id = trace_id or request_id or current_request

    record = {
        "timestamp": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "level": logging.getLevelName(level),
        "logger": logger.name,
        "event": event,
        **redact_payload(payload),
    }
    if resolved_request_id is not None:
        record["request_id"] = resolved_request_id
    if resolved_trace_id is not None:
        record["trace_id"] = resolved_trace_id
    logger.log(level, json.dumps(record, ensure_ascii=False, default=_json_default))


def _resolve_client_ip(request) -> str | None:
    if request is None:
        return None

    meta = getattr(request, "META", {}) or {}
    forwarded_for = meta.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return meta.get("REMOTE_ADDR")


def _request_headers(request) -> dict[str, Any]:
    headers = getattr(request, "headers", None)
    if headers is None:
        meta = getattr(request, "META", {}) or {}
        return {
            key[5:].replace("_", "-").title(): value
            for key, value in meta.items()
            if key.startswith("HTTP_")
        }
    return dict(headers.items())


def _response_headers(response) -> dict[str, Any]:
    headers = getattr(response, "headers", None)
    if headers is None:
        return {}
    return dict(headers.items())


def build_request_context(request) -> dict[str, Any]:
    meta = getattr(request, "META", {}) or {}
    user = getattr(request, "user", None)
    tenant = getattr(request, "tenant_context", None)
    request_id = getattr(request, "request_id", None) or current_request_id.get()
    trace_id = getattr(request, "trace_id", None) or request_id
    return {
        "request_id": request_id,
        "trace_id": trace_id,
        "method": getattr(request, "method", None),
        "path": getattr(request, "path", None),
        "query_string": meta.get("QUERY_STRING", ""),
        "tenant_code": getattr(request, "tenant_context_code", None),
        "tenant_id": getattr(tenant, "id", None),
        "user_id": getattr(user, "id", None),
        "username": getattr(user, "username", None),
        "remote_addr": _resolve_client_ip(request),
        "user_agent": meta.get("HTTP_USER_AGENT", ""),
    }


def build_request_log_payload(request) -> dict[str, Any]:
    meta = getattr(request, "META", {}) or {}
    body = getattr(request, "data", None)
    if body is None and hasattr(request, "body"):
        raw_body = getattr(request, "body", b"")
        if raw_body not in (None, b"", ""):
            body = raw_body
    return {
        "method": getattr(request, "method", None),
        "path": getattr(request, "path", None),
        "full_path": getattr(request, "get_full_path", lambda: None)(),
        "query_string": meta.get("QUERY_STRING", ""),
        "content_type": meta.get("CONTENT_TYPE", ""),
        "content_length": meta.get("CONTENT_LENGTH", ""),
        "headers": redact_payload(_request_headers(request), max_text_chars=_max_header_chars()),
        "body": redact_payload(body, max_text_chars=_max_body_chars()),
    }


def build_response_log_payload(response) -> dict[str, Any]:
    body = getattr(response, "data", None)
    if body is None and hasattr(response, "content"):
        raw_content = getattr(response, "content", b"")
        if raw_content not in (None, b"", ""):
            body = raw_content
    return {
        "status_code": getattr(response, "status_code", None),
        "headers": redact_payload(_response_headers(response), max_text_chars=_max_header_chars()),
        "body": redact_payload(body, max_text_chars=_max_body_chars()),
    }


def build_exception_log_payload(exc: BaseException) -> dict[str, Any]:
    stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return {
        "type": exc.__class__.__name__,
        "message": str(exc),
        "stack": stack,
    }
