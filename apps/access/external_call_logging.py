from __future__ import annotations

import logging
import time
import traceback
import uuid
from dataclasses import dataclass
from typing import Any

from apps.access.request_logging import log_json, redact_payload


logger = logging.getLogger("apps.access.external_call")


@dataclass
class ExternalCallLogContext:
    call_id: str
    service: str
    operation: str
    method: str
    url: str
    path: str
    started_at: float


def _duration_ms(call: ExternalCallLogContext) -> int:
    return max(0, int((time.monotonic() - call.started_at) * 1000))


def _base_payload(call: ExternalCallLogContext, *, attempt: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "call_id": call.call_id,
        "service": call.service,
        "operation": call.operation,
        "method": call.method,
        "url": call.url,
        "path": call.path,
        "duration_ms": _duration_ms(call),
    }
    if attempt is not None:
        payload["attempt"] = attempt
    return payload


def _error_payload(error: Any) -> dict[str, Any]:
    if isinstance(error, BaseException):
        return {
            "type": error.__class__.__name__,
            "message": str(error),
            "stack": "".join(traceback.format_exception(type(error), error, error.__traceback__)),
        }
    if isinstance(error, dict):
        return redact_payload(error)
    return {"type": error.__class__.__name__, "message": str(error)}


def log_external_call_started(
    *,
    service: str,
    operation: str,
    method: str,
    url: str,
    path: str = "",
    request: dict[str, Any] | None = None,
    attempt: int | None = None,
) -> ExternalCallLogContext:
    call = ExternalCallLogContext(
        call_id=str(uuid.uuid4()),
        service=service,
        operation=operation,
        method=method,
        url=url,
        path=path,
        started_at=time.monotonic(),
    )
    payload = _base_payload(call, attempt=attempt)
    if request is not None:
        payload["request"] = redact_payload(request)
    log_json(logger, logging.INFO, "external_call_started", **payload)
    return call


def log_external_call_retry(
    call: ExternalCallLogContext,
    *,
    reason: str,
    error: Any = None,
    attempt: int | None = None,
    request: dict[str, Any] | None = None,
) -> None:
    payload = _base_payload(call, attempt=attempt)
    payload["reason"] = reason
    if request is not None:
        payload["request"] = redact_payload(request)
    if error is not None:
        payload["error"] = _error_payload(error)
    log_json(logger, logging.INFO, "external_call_retry", **payload)


def log_external_call_finished(
    call: ExternalCallLogContext,
    *,
    response: dict[str, Any] | None = None,
    attempt: int | None = None,
) -> None:
    payload = _base_payload(call, attempt=attempt)
    if response is not None:
        payload["response"] = redact_payload(response)
    log_json(logger, logging.INFO, "external_call_finished", **payload)


def log_external_call_failed(
    call: ExternalCallLogContext,
    *,
    error: Any,
    response: dict[str, Any] | None = None,
    attempt: int | None = None,
) -> None:
    payload = _base_payload(call, attempt=attempt)
    payload["error"] = _error_payload(error)
    if response is not None:
        payload["response"] = redact_payload(response)
    log_json(logger, logging.ERROR, "external_call_failed", **payload)
