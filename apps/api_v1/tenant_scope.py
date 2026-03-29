from apps.access.exceptions import StandardForbidden
from apps.access.services import is_active_platform_admin


def _ensure_business_api_actor(user):
    if is_active_platform_admin(user):
        raise StandardForbidden(msg="平台管理员不可访问租户业务接口")


def get_request_tenant(context):
    request = context.get("request")
    if request is None:
        return None
    _ensure_business_api_actor(getattr(request, "user", None))
    return getattr(request, "tenant_context", None)


def require_request_tenant(context):
    tenant = get_request_tenant(context)
    if tenant is None:
        raise StandardForbidden(msg="tenant context required")
    return tenant


class TenantScopedBusinessMixin:
    """统一要求业务 API 在租户上下文内运行。"""

    tenant_lookup = "tenant"

    def get_current_tenant(self):
        _ensure_business_api_actor(getattr(self.request, "user", None))
        tenant = getattr(self.request, "tenant_context", None)
        if tenant is None:
            raise StandardForbidden(msg="tenant context required")
        return tenant

    def scope_queryset_to_tenant(self, queryset):
        tenant = self.get_current_tenant()
        return queryset.filter(**{self.tenant_lookup: tenant})
