import time
import uuid

from django.core.exceptions import PermissionDenied

from apps.access.models import Tenant, TenantStatus
from apps.access.request_logging import build_request_context, current_request_id


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


class TenantContextMiddleware:
    """解析租户 header，并为请求附加租户上下文。"""

    header_name = "HTTP_X_TENANT_CODE"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        raw_tenant_code = request.META.get(self.header_name)
        tenant_code = raw_tenant_code.strip() if isinstance(raw_tenant_code, str) else raw_tenant_code

        request.tenant_context_code = tenant_code or None
        request.tenant_context = None

        if not tenant_code:
            return self.get_response(request)

        tenant = Tenant.objects.filter(code=tenant_code).first()
        request.tenant_context = tenant

        if request.path.startswith("/api/v1/iam/"):
            return self.get_response(request)

        if tenant is None or tenant.status != TenantStatus.ACTIVE:
            raise PermissionDenied("Invalid or disabled tenant")

        if hasattr(request, "log_context"):
            request.log_context = build_request_context(request)
        return self.get_response(request)
