from __future__ import annotations

from typing import Any

from rest_framework import status
from rest_framework.response import Response

from apps.common.pagination import StandardPageNumberPagination


class StandardCode:
    SUCCESS = "00000"
    NOT_AUTHENTICATED = "A0401"
    FORBIDDEN = "A0403"
    INVALID_PARAMS = "B0001"
    DUPLICATE = "C0101"
    PHONE_DUPLICATE = "C0102"
    MEMBERSHIP_DUPLICATE = "C0103"
    STATE_CONFLICT = "C0201"
    CONSTRAINT_CONFLICT = "C0203"
    RESOURCE_IN_USE = "C0202"
    NOT_FOUND = "C0404"
    INTERNAL_ERROR = "E0001"


_DUPLICATE_HINTS = (
    "duplicate",
    "already exists",
    "已存在",
    "unique",
    "重复",
)
_STATE_CONFLICT_HINTS = (
    "state conflict",
    "状态冲突",
    "不可逆",
    "invalid state",
)
_RESOURCE_IN_USE_HINTS = (
    "已被引用",
    "无法删除",
    "不能删除",
    "被任务引用",
)
_NOT_FOUND_HINTS = (
    "no ",
    "not found",
    "不存在",
)
_TENANT_CONTEXT_HINTS = (
    "tenant context required",
    "tenant_context_required",
)
_PLATFORM_ADMIN_HINTS = (
    "platform admin cannot access tenant business api",
)
_AUTH_REQUIRED_HINTS = (
    "authentication credentials were not provided",
    "authentication required",
    "not authenticated",
    "身份认证信息未提供",
    "未提供身份认证",
)


def _normalized_text(payload: Any) -> str:
    return str(payload).strip().lower()


def _contains_cjk(text: str | None) -> bool:
    if not text:
        return False
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _normalize_list_payload(data: Any) -> Any:
    if not isinstance(data, dict):
        return data

    if "list" in data and "total" in data:
        return dict(data)

    return data


def standard_success_payload(data: Any, *, trace_id: str | None = None) -> dict[str, Any]:
    payload = {
        "code": StandardCode.SUCCESS,
        "msg": "success",
        "data": _normalize_list_payload(data),
    }
    if trace_id:
        payload["traceId"] = trace_id
    return payload


def standard_error_payload(code: str, msg: str, data: Any = None, *, trace_id: str | None = None) -> dict[str, Any]:
    payload = {
        "code": code,
        "msg": msg,
        "data": data,
    }
    if trace_id:
        payload["traceId"] = trace_id
    return payload


def validation_error_payload(errors: Any, msg: str = "参数校验失败", *, trace_id: str | None = None) -> dict[str, Any]:
    return standard_error_payload(StandardCode.INVALID_PARAMS, msg, errors, trace_id=trace_id)


def _extract_detail(payload: Any) -> str | None:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if detail is None:
            detail = payload.get("error")
        if detail is None:
            return None
        return str(detail)
    if isinstance(payload, str):
        return payload
    return None


def _extract_errors(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return None

    if "errors" in payload:
        return payload["errors"]

    special_keys = {"detail", "error", "code", "msg", "data", "traceId"}
    if not any(key in payload for key in special_keys):
        return payload

    return None


def _infer_standard_code(status_code: int, payload: Any) -> str:
    text = _normalized_text(payload)

    if 200 <= status_code < 400:
        return StandardCode.SUCCESS

    if status_code == 401 or any(hint in text for hint in _AUTH_REQUIRED_HINTS):
        return StandardCode.NOT_AUTHENTICATED
    if status_code == 403:
        return StandardCode.FORBIDDEN
    if status_code == 404:
        return StandardCode.NOT_FOUND

    if any(hint in text for hint in _DUPLICATE_HINTS):
        return StandardCode.DUPLICATE

    if status_code == 409:
        if any(hint in text for hint in _RESOURCE_IN_USE_HINTS):
            return StandardCode.RESOURCE_IN_USE
        return StandardCode.STATE_CONFLICT

    if status_code == 400:
        if any(hint in text for hint in _STATE_CONFLICT_HINTS):
            return StandardCode.STATE_CONFLICT
        return StandardCode.INVALID_PARAMS

    if any(hint in text for hint in _NOT_FOUND_HINTS):
        return StandardCode.NOT_FOUND

    if status_code >= 500:
        return StandardCode.INTERNAL_ERROR

    return StandardCode.INVALID_PARAMS


def _preferred_error_message(payload: Any, standard_code: str) -> str:
    detail = _extract_detail(payload)
    text = _normalized_text(payload)

    if any(hint in text for hint in _TENANT_CONTEXT_HINTS):
        return "缺少租户上下文"
    if any(hint in text for hint in _PLATFORM_ADMIN_HINTS):
        return "平台管理员不可访问租户业务接口"

    fallback = {
        StandardCode.NOT_AUTHENTICATED: "登录状态已失效",
        StandardCode.FORBIDDEN: "无操作权限",
        StandardCode.INVALID_PARAMS: "参数校验失败",
        StandardCode.DUPLICATE: "资源已存在",
        StandardCode.STATE_CONFLICT: "当前状态不允许操作",
        StandardCode.RESOURCE_IN_USE: "当前数据已被引用，无法删除",
        StandardCode.NOT_FOUND: "资源不存在",
        StandardCode.INTERNAL_ERROR: "系统异常",
    }.get(standard_code, "系统异常")

    if standard_code == StandardCode.NOT_AUTHENTICATED:
        return fallback

    if _contains_cjk(detail):
        return detail
    return fallback


def _build_error_data(payload: Any, standard_code: str, msg: str) -> Any:
    detail = _extract_detail(payload)
    errors = _extract_errors(payload)

    if standard_code in {
        StandardCode.NOT_AUTHENTICATED,
        StandardCode.FORBIDDEN,
        StandardCode.NOT_FOUND,
        StandardCode.INTERNAL_ERROR,
    }:
        return None

    if errors is not None:
        return errors

    if detail and detail != msg and _contains_cjk(detail):
        return {"detail": detail}

    return None


def build_standard_response(payload: Any, status_code: int, *, trace_id: str | None = None) -> dict[str, Any]:
    if isinstance(payload, dict) and {"code", "msg", "data"}.issubset(payload.keys()):
        return payload

    if 200 <= status_code < 400:
        return standard_success_payload(payload, trace_id=trace_id)

    standard_code = _infer_standard_code(status_code, payload)
    msg = _preferred_error_message(payload, standard_code)
    data = _build_error_data(payload, standard_code, msg)
    return standard_error_payload(standard_code, msg, data, trace_id=trace_id)


def attach_standard_envelope(payload: Any, status_code: int) -> dict[str, Any]:
    return build_standard_response(payload, status_code)


class BusinessApiResponseMixin:
    """为业务 API 输出统一 code/msg/data 响应结构。"""

    pagination_class = StandardPageNumberPagination

    def finalize_response(self, request, response, *args, **kwargs):
        finalized = super().finalize_response(request, response, *args, **kwargs)
        if isinstance(finalized, Response):
            trace_id = getattr(request, "request_id", None) or getattr(request, "trace_id", None)
            finalized.data = build_standard_response(
                getattr(finalized, "data", None),
                int(finalized.status_code),
                trace_id=trace_id,
            )
        return finalized


def build_instance_payload(serializer_class, instance, request) -> dict[str, Any]:
    return dict(serializer_class(instance, context={"request": request}).data)


def build_instance_response(
    serializer_class,
    instance,
    request,
    *,
    http_status: int,
    include_headers: bool = False,
    headers_builder=None,
):
    payload = build_instance_payload(serializer_class, instance, request)
    if include_headers and headers_builder is not None:
        return Response(payload, status=http_status, headers=headers_builder(payload))
    return Response(payload, status=http_status)


def reject_request_body_if_present(request, *, message: str, use_content_length: bool = False):
    if use_content_length:
        request_meta = getattr(request, "META", {})
        content_length = request_meta.get("CONTENT_LENGTH") if isinstance(request_meta, dict) else None
        has_body = content_length not in (None, "", "0")
    else:
        has_body = bool(getattr(request, "data", None))

    if not has_body:
        return None

    return Response(
        standard_error_payload(
            StandardCode.INVALID_PARAMS,
            message,
            {"body": "不支持请求体，请移除 body 后重试"},
        ),
        status=status.HTTP_400_BAD_REQUEST,
    )
