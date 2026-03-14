import uuid

from django.core.exceptions import PermissionDenied

from apps.access.models import Tenant, TenantStatus


class RequestContextMiddleware:
    """为每个请求附加 request_id，便于审计日志关联链路。"""

    header_name = "HTTP_X_REQUEST_ID"
    response_header = "X-Request-ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.META.get(self.header_name) or str(uuid.uuid4())
        request.request_id = request_id
        response = self.get_response(request)
        response[self.response_header] = request_id
        return response


class TenantContextMiddleware:
    """租户上下文中间件。

    从 HTTP Header 中读取 X-Tenant-Code，解析当前租户并附加到 request 对象。
    """

    header_name = "HTTP_X_TENANT_CODE"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant_code = request.META.get(self.header_name)

        if tenant_code:
            tenant = Tenant.objects.filter(code=tenant_code, status=TenantStatus.ACTIVE).first()
            if not tenant:
                raise PermissionDenied("Invalid or disabled tenant")
            request.tenant_context = tenant
        else:
            request.tenant_context = None  # 平台接口

        return self.get_response(request)
