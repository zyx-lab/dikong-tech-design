from __future__ import annotations

from typing import Any

from rest_framework.response import Response


class BusinessCode:
    SUCCESS = "SUCCESS"
    INVALID_PARAMS = "INVALID_PARAMS"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    STATE_CONFLICT = "STATE_CONFLICT"
    IDEMPOTENT_DUPLICATE = "IDEMPOTENT_DUPLICATE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_KNOWN_CODES = {
    BusinessCode.SUCCESS,
    BusinessCode.INVALID_PARAMS,
    BusinessCode.PERMISSION_DENIED,
    BusinessCode.RESOURCE_NOT_FOUND,
    BusinessCode.STATE_CONFLICT,
    BusinessCode.IDEMPOTENT_DUPLICATE,
    BusinessCode.INTERNAL_ERROR,
}

_DUPLICATE_HINTS = (
    "idempotent_duplicate",
    "already exists",
    "已存在",
    "unique",
    "重复",
)

_STATE_CONFLICT_HINTS = (
    "state_conflict",
    "状态冲突",
    "不可逆",
    "invalid state",
)


def _normalized_text(payload: Any) -> str:
    return str(payload).strip().lower()


def _normalize_detail_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper().replace("-", "_")
    return normalized or None


def _extract_payload_detail_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None

    declared = payload.get("business_detail_code")
    normalized_declared = _normalize_detail_code(declared)
    if normalized_declared:
        return normalized_declared

    detail = payload.get("detail")
    detail_code = getattr(detail, "code", None)
    normalized_detail_code = _normalize_detail_code(detail_code)
    if normalized_detail_code:
        return normalized_detail_code
    return None


def _extract_declared_business_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None

    value = payload.get("business_code")
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    if normalized in _KNOWN_CODES:
        return normalized
    return None


def infer_business_code(status_code: int, payload: Any) -> str:
    declared = _extract_declared_business_code(payload)
    if declared:
        return declared

    detail_code = _extract_payload_detail_code(payload)
    if detail_code in {"DUPLICATE_REQUEST", "UNIQUE", "UNIQUE_VIOLATION"}:
        return BusinessCode.IDEMPOTENT_DUPLICATE
    if detail_code in {"STATE_CONFLICT"}:
        return BusinessCode.STATE_CONFLICT
    if detail_code in {"INTERNAL_ERROR", "INTERNAL_SERVER_ERROR"}:
        return BusinessCode.INTERNAL_ERROR

    text = _normalized_text(payload)
    if any(hint in text for hint in _DUPLICATE_HINTS) and status_code in (400, 409):
        return BusinessCode.IDEMPOTENT_DUPLICATE
    if any(hint in text for hint in _STATE_CONFLICT_HINTS):
        return BusinessCode.STATE_CONFLICT

    if 200 <= status_code < 400:
        return BusinessCode.SUCCESS
    if status_code in (401, 403):
        return BusinessCode.PERMISSION_DENIED
    if status_code == 404:
        return BusinessCode.RESOURCE_NOT_FOUND
    if status_code == 409:
        return BusinessCode.STATE_CONFLICT
    if status_code == 400:
        return BusinessCode.INVALID_PARAMS
    if 400 <= status_code < 500:
        return BusinessCode.INVALID_PARAMS
    return BusinessCode.INTERNAL_ERROR


def infer_business_detail_code(status_code: int, payload: Any, business_code: str) -> str:
    declared = _extract_payload_detail_code(payload)
    if declared:
        if business_code == BusinessCode.PERMISSION_DENIED and declared == "PERMISSION_DENIED":
            return "FORBIDDEN"
        if business_code == BusinessCode.RESOURCE_NOT_FOUND and declared == "RESOURCE_NOT_FOUND":
            return "NOT_FOUND"
        if business_code == BusinessCode.INTERNAL_ERROR and declared in {"ERROR", "INTERNAL_SERVER_ERROR"}:
            return "INTERNAL_ERROR"
        return declared

    text = _normalized_text(payload)
    if business_code == BusinessCode.SUCCESS:
        return "OK"
    if business_code == BusinessCode.PERMISSION_DENIED:
        if "scope_not_configured" in text:
            return "SCOPE_NOT_CONFIGURED"
        if "permission_not_configured" in text:
            return "PERMISSION_NOT_CONFIGURED"
        if status_code == 401 or "not_authenticated" in text or "未提供" in text:
            return "NOT_AUTHENTICATED"
        return "FORBIDDEN"
    if business_code == BusinessCode.RESOURCE_NOT_FOUND:
        return "NOT_FOUND"
    if business_code == BusinessCode.IDEMPOTENT_DUPLICATE:
        return "DUPLICATE_REQUEST"
    if business_code == BusinessCode.STATE_CONFLICT:
        return "STATE_CONFLICT"
    if business_code == BusinessCode.INTERNAL_ERROR:
        return "INTERNAL_ERROR"
    if status_code == 405:
        return "METHOD_NOT_ALLOWED"
    return "VALIDATION_ERROR"


def attach_business_code(payload: Any, status_code: int) -> Any:
    business_code = infer_business_code(status_code, payload)
    detail_code = infer_business_detail_code(status_code, payload, business_code)

    if isinstance(payload, dict):
        normalized = dict(payload)
        normalized.setdefault("business_code", business_code)
        normalized.setdefault("business_detail_code", detail_code)
        return normalized

    if payload is None:
        return {"business_code": business_code, "business_detail_code": detail_code}

    if isinstance(payload, (list, tuple)):
        return {"business_code": business_code, "business_detail_code": detail_code, "data": payload}

    return {"business_code": business_code, "business_detail_code": detail_code, "detail": payload}


class BusinessApiResponseMixin:
    """为业务 API 输出统一补充 business_code 字段。"""

    def finalize_response(self, request, response, *args, **kwargs):
        finalized = super().finalize_response(request, response, *args, **kwargs)
        if isinstance(finalized, Response):
            finalized.data = attach_business_code(getattr(finalized, "data", None), int(finalized.status_code))
        return finalized
