import logging
import time
import uuid

from apps.access.request_logging import (
    build_exception_log_payload,
    build_request_context,
    build_request_log_payload,
    build_response_log_payload,
    current_request_id,
    log_json,
)

logger = logging.getLogger("apps.access.lifecycle")


def _should_log_api_lifecycle(request) -> bool:
    path = getattr(request, "path", "")
    return isinstance(path, str) and path.startswith(("/api/v2/", "/api/internal/dji/"))


class RequestContextMiddleware:
    """为每个请求附加 request_id，便于审计日志关联链路。"""

    header_name = "HTTP_X_REQUEST_ID"
    response_header = "X-Request-ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.META.get(self.header_name) or str(uuid.uuid4())
        request.request_id = request_id
        request.trace_id = request_id
        request.log_started_at = time.monotonic()
        request.log_context = build_request_context(request)
        token = current_request_id.set(request_id)
        try:
            response = self.get_response(request)
            response[self.response_header] = request_id
            return response
        finally:
            current_request_id.reset(token)


class RequestLifecycleLoggingMiddleware:
    """输出正式业务 API 请求的结构化生命周期日志。"""

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _duration_ms(request) -> int | None:
        started_at = getattr(request, "log_started_at", None)
        if started_at is None:
            return None
        return max(0, int((time.monotonic() - started_at) * 1000))

    @staticmethod
    def _trace_id(request):
        return getattr(request, "request_id", None) or getattr(request, "trace_id", None)

    def __call__(self, request):
        should_log_lifecycle = _should_log_api_lifecycle(request)
        trace_id = self._trace_id(request)
        if should_log_lifecycle:
            log_json(
                logger,
                logging.INFO,
                "request_started",
                request=build_request_log_payload(request),
                context=build_request_context(request),
                request_id=trace_id,
            )

        try:
            response = self.get_response(request)
        except Exception as exc:
            if should_log_lifecycle:
                status_code = getattr(exc, "status_code", 500)
                log_json(
                    logger,
                    logging.ERROR,
                    "request_exception",
                    request=build_request_log_payload(request),
                    response={"status_code": status_code, "headers": {}, "body": None},
                    exception=build_exception_log_payload(exc),
                    status_code=status_code,
                    context=build_request_context(request),
                    request_id=trace_id,
                    duration_ms=self._duration_ms(request),
                )
            raise

        if should_log_lifecycle:
            status_code = getattr(response, "status_code", None)
            log_json(
                logger,
                logging.INFO,
                "request_finished",
                request=build_request_log_payload(request),
                response=build_response_log_payload(response),
                context=build_request_context(request),
                request_id=trace_id,
                status_code=int(status_code) if status_code is not None else None,
                duration_ms=self._duration_ms(request),
            )
        return response

