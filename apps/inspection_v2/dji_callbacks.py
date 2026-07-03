from __future__ import annotations

import logging

from django.conf import settings
from django.http import Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from apps.common.request import load_json_body
from apps.inspection_v2.services import handle_v2_media_upload_callback
from apps.resource_v2.gateway import DjiGatewayError

logger = logging.getLogger(__name__)


def _success(data=None, *, status: int = 200):
    return JsonResponse({"code": "00000", "msg": "success", "data": {} if data is None else data}, status=status)


def _error(msg: str, *, status: int = 403, code: str = "A0403", data=None):
    return JsonResponse({"code": code, "msg": msg, "data": {} if data is None else data}, status=status)


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


def _run_internal_post(request, handler):
    precondition_error = _require_internal_post(request)
    if precondition_error is not None:
        return precondition_error
    try:
        return _success(handler(load_json_body(request)))
    except DjiGatewayError as exc:
        return _error(
            "DJI 同步失败",
            status=exc.status_code if exc.status_code >= 400 else 502,
            code="E0001",
            data={"detail": str(exc), "upstream": exc.data},
        )


@csrf_exempt
def media_upload_callback(request):
    return _run_internal_post(request, handle_v2_media_upload_callback)


@csrf_exempt
def media_group_upload_callback(request):
    def _handle(payload):
        logger.info("dji_media_group_upload_callback_ignored", extra={"payload_keys": sorted(payload.keys())})
        return {"resolved_count": 0, "ignored_count": 1}

    return _run_internal_post(request, _handle)
