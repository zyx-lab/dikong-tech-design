"""Custom exception handlers for DRF."""
import logging
import time

from rest_framework.exceptions import APIException, AuthenticationFailed, NotAuthenticated
from rest_framework.response import Response
from rest_framework.views import exception_handler

from apps.api_v1.business_response import build_standard_response, standard_error_payload
from apps.access.request_logging import (
    build_exception_log_payload,
    build_request_context,
    build_request_log_payload,
    build_response_log_payload,
    log_json,
)

logger = logging.getLogger(__name__)


def _log_request_exception(request, exc, response):
    if request is None:
        return

    trace_id = getattr(request, "request_id", None) or getattr(request, "trace_id", None)
    started_at = getattr(request, "log_started_at", None)
    duration_ms = None
    if started_at is not None:
        duration_ms = max(0, int((time.monotonic() - started_at) * 1000))

    response_payload = build_response_log_payload(response) if response is not None else {
        "status_code": 500,
        "headers": {},
        "body": None,
    }
    log_json(
        logger,
        logging.ERROR,
        "request_exception",
        request=build_request_log_payload(request),
        response=response_payload,
        exception=build_exception_log_payload(exc),
        status_code=getattr(response, "status_code", 500),
        context=build_request_context(request),
        request_id=trace_id,
        duration_ms=duration_ms,
    )


class StandardizedApiException(APIException):
    """Business API exception with explicit code/msg/data contract."""

    status_code = 400
    standard_code = "B0001"
    standard_msg = "参数校验失败"
    standard_data = None

    def __init__(self, *, msg=None, data=None, standard_code=None, status_code=None):
        resolved_msg = msg or self.standard_msg
        super().__init__(detail=resolved_msg)
        self.standard_code = standard_code or self.standard_code
        self.standard_msg = resolved_msg
        self.standard_data = data if data is not None else self.standard_data
        if status_code is not None:
            self.status_code = status_code


class StandardUnauthorized(StandardizedApiException):
    status_code = 401
    standard_code = "A0401"
    standard_msg = "登录状态已失效"


class StandardForbidden(StandardizedApiException):
    status_code = 403
    standard_code = "A0403"
    standard_msg = "无操作权限"


class StandardNotFound(StandardizedApiException):
    status_code = 404
    standard_code = "C0404"
    standard_msg = "资源不存在"


class StandardDuplicate(StandardizedApiException):
    status_code = 409
    standard_code = "C0101"
    standard_msg = "资源已存在"


class StandardPhoneDuplicate(StandardizedApiException):
    status_code = 409
    standard_code = "C0102"
    standard_msg = "手机号已存在"


class StandardMembershipDuplicate(StandardizedApiException):
    status_code = 409
    standard_code = "C0103"
    standard_msg = "用户已在当前租户中"


class StandardConstraintConflict(StandardizedApiException):
    status_code = 409
    standard_code = "C0203"
    standard_msg = "当前资源状态或业务约束不允许执行该操作"


def custom_exception_handler(exc, context):
    """DRF exception handler with a dedicated /api/v1 standard envelope."""
    response = exception_handler(exc, context)
    request = context.get("request")
    is_business_api = request is not None and (
        request.path.startswith("/api/v1/") or request.path.startswith("/api/v2/")
    )

    if response is not None:
        if is_business_api:
            if isinstance(exc, StandardizedApiException):
                response.data = standard_error_payload(
                    exc.standard_code,
                    exc.standard_msg,
                    exc.standard_data,
                    trace_id=getattr(request, "trace_id", None),
                )
            elif isinstance(exc, (NotAuthenticated, AuthenticationFailed)) or response.status_code == 401:
                payload = {"detail": str(response.data.get("detail", "Authentication required"))}
                response.data = build_standard_response(payload, response.status_code, trace_id=getattr(request, "trace_id", None))
            elif response.status_code == 403:
                if hasattr(exc, "message") and exc.message:
                    detail = exc.message
                elif isinstance(response.data, dict) and "detail" in response.data:
                    detail = str(response.data["detail"])
                else:
                    detail = "Permission denied"
                payload = {"detail": detail}
                response.data = build_standard_response(payload, response.status_code, trace_id=getattr(request, "trace_id", None))
            else:
                payload = response.data
                response.data = build_standard_response(payload, response.status_code, trace_id=getattr(request, "trace_id", None))
        else:
            # Some authentication failures are rendered as 403 by DRF,
            # so check the exception type before falling back to generic forbidden.
            if isinstance(exc, (NotAuthenticated, AuthenticationFailed)) or response.status_code == 401:
                response.data = {
                    "business_code": "PERMISSION_DENIED",
                    "business_detail_code": "NOT_AUTHENTICATED",
                    "detail": str(response.data.get('detail', 'Authentication required')),
                }

            # Check if it's a permission denied error (403)
            elif response.status_code == 403:
                # Extract the permission message if available
                if hasattr(exc, 'message') and exc.message:
                    detail = exc.message
                elif isinstance(response.data, dict) and 'detail' in response.data:
                    detail = str(response.data['detail'])
                else:
                    detail = "Permission denied"

                response.data = {
                    "business_code": "PERMISSION_DENIED",
                    "business_detail_code": "FORBIDDEN",
                    "detail": detail,
                }

        _log_request_exception(request, exc, response)
        return response

    _log_request_exception(request, exc, response)

    if is_business_api:
        return Response(
            build_standard_response({"detail": "internal server error"}, 500, trace_id=getattr(request, "trace_id", None)),
            status=500,
        )

    return Response(
        {
            "business_code": "INTERNAL_ERROR",
            "business_detail_code": "INTERNAL_ERROR",
            "detail": "internal server error",
        },
        status=500,
    )
