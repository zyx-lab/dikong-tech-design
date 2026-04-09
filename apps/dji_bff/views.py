from __future__ import annotations

import json

from django.conf import settings
from django.http import Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from apps.dji_bff.gateway import DjiGatewayError
from apps.dji_bff.services import (
    handle_media_group_upload_callback,
    handle_media_upload_callback,
)
from apps.dji_bff.tasks import sync_device_indexes, sync_media_indexes


def _success(data=None, *, status: int = 200):
    return JsonResponse(
        {
            "code": "00000",
            "msg": "success",
            "data": {} if data is None else data,
        },
        status=status,
    )


def _error(msg: str, *, status: int = 403, code: str = "A0403", data=None):
    return JsonResponse(
        {
            "code": code,
            "msg": msg,
            "data": {} if data is None else data,
        },
        status=status,
    )


def _require_internal_token(request):
    expected = getattr(settings, "DJI_INTERNAL_API_TOKEN", "")
    actual = request.headers.get("X-DJI-Internal-Token", "")
    if not expected:
        return _error("系统内部接口未启用", status=403)
    if actual != expected:
        return _error("系统身份校验失败", status=403)
    return None


def _require_internal_post(request):
    auth_error = _require_internal_token(request)
    if auth_error is not None:
        return auth_error
    if request.method != "POST":
        raise Http404
    return None


def _gateway_sync_error(exc: DjiGatewayError):
    return _error(
        "DJI 同步失败",
        status=exc.status_code if exc.status_code >= 400 else 502,
        code="E0001",
        data={"detail": str(exc), "upstream": exc.data},
    )


def _load_json(request) -> dict:
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _run_internal_post(request, handler, *, parse_json: bool = False, catch_gateway_error: bool = False):
    precondition_error = _require_internal_post(request)
    if precondition_error is not None:
        return precondition_error
    try:
        if parse_json:
            payload = _load_json(request)
            result = handler(payload, request=request)
        else:
            result = handler()
    except DjiGatewayError as exc:
        if catch_gateway_error:
            return _gateway_sync_error(exc)
        raise
    return _success(result)


@csrf_exempt
def sync_devices(request):
    return _run_internal_post(request, sync_device_indexes, catch_gateway_error=True)


@csrf_exempt
def sync_media(request):
    return _run_internal_post(request, sync_media_indexes, catch_gateway_error=True)


@csrf_exempt
def media_upload_callback(request):
    return _run_internal_post(request, handle_media_upload_callback, parse_json=True)


@csrf_exempt
def media_group_upload_callback(request):
    return _run_internal_post(request, handle_media_group_upload_callback, parse_json=True)
