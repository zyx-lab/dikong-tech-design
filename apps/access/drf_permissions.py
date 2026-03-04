from rest_framework.permissions import BasePermission

from apps.access.services import AuthzService, IdentityService, apply_scope_to_queryset, has_staff_type_permission


class PermissionMapMixin:
    """统一从 View 中读取“当前请求需要的权限码”。"""

    permission_map = {}
    method_permission_map = {}

    def get_required_permission(self):
        action = getattr(self, "action", None)
        if action and action in self.permission_map:
            return self.permission_map[action]

        request = getattr(self, "request", None)
        if request is not None:
            method = request.method.upper()
            if method in self.method_permission_map:
                return self.method_permission_map[method]

        return getattr(self, "required_permission", None)


class RequireInternalPermission(BasePermission):
    """内部 IAM 接口鉴权。

    规则：
    - superuser：允许（root，全量权限）。
    - 普通账号：必须具备有效 staff/staff_type，且命中声明的权限码。
    - 不做 scope 对象级限制（IAM 管理动作默认使用 ALL 管理语义）。
    """

    message = "permission denied"

    def has_permission(self, request, view):
        identity_result = IdentityService.check_system_operator(request.user)
        if not identity_result.ok:
            self.message = identity_result.reason_code
            return False

        if request.user.is_superuser:
            return True

        perm_code = view.get_required_permission() if hasattr(view, "get_required_permission") else None
        if not perm_code:
            self.message = "PERMISSION_NOT_CONFIGURED"
            return False

        if not has_staff_type_permission(request.user, perm_code):
            self.message = "PERMISSION_DENIED"
            return False

        return True


class ScopedActionPermission(BasePermission):
    """检查身份 + 权限 + scope，适用于动作级鉴权。"""

    message = "permission denied"

    def has_permission(self, request, view):
        perm_code = view.get_required_permission() if hasattr(view, "get_required_permission") else None
        if not perm_code:
            self.message = "PERMISSION_NOT_CONFIGURED"
            return False

        decision = AuthzService.authorize(request.user, perm_code)
        if not decision.allowed:
            self.message = decision.reason_code
            return False

        request._authz_decision = decision
        return True

    def has_object_permission(self, request, view, obj):
        perm_code = view.get_required_permission() if hasattr(view, "get_required_permission") else None
        if not perm_code:
            self.message = "PERMISSION_NOT_CONFIGURED"
            return False

        decision = AuthzService.authorize(request.user, perm_code, obj=obj)
        if not decision.allowed:
            self.message = decision.reason_code
            return False

        request._authz_decision = decision
        return True


class ScopedQuerysetMixin:
    """在 queryset 层应用 scope，避免列表页数据越权。"""

    assigned_scope_filter_builder = None

    def apply_scope(self, queryset):
        perm_code = self.get_required_permission()
        if not perm_code:
            return queryset.none()

        decision = getattr(self.request, "_authz_decision", None)
        if decision is None:
            decision = AuthzService.authorize(self.request.user, perm_code)

        if not decision.allowed:
            return queryset.none()

        return apply_scope_to_queryset(
            queryset=queryset,
            scope=decision.scope,
            staff_id=decision.staff_id,
            assigned_filter_builder=self.assigned_scope_filter_builder,
        )
