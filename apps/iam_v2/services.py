from dataclasses import dataclass

from apps.access.exceptions import StandardForbidden, StandardUnauthorized
from apps.access.models import DirectoryStatus, UserStatus
from apps.iam_v2.models import FixedRole, V2AccountProfile, V2AccountRoleAssignment


ROLE_OPERATION_PERMISSIONS = {
    FixedRole.PLATFORM_SUPER_ADMIN: {"view", "monitor", "dispatch_task", "review_task", "edit_config", "unbind"},
    FixedRole.DEPARTMENT_ADMIN: {"view", "monitor", "dispatch_task", "review_task", "edit_config", "bind", "unbind"},
    FixedRole.TASK_MONITOR_DISPATCHER: {"view", "monitor", "dispatch_task"},
    FixedRole.PILOT: {"view", "monitor", "review_task"},
    FixedRole.WORK_ORDER_HANDLER: {"view", "review_task"},
}

PERMISSION_ORDER = ["view", "monitor", "dispatch_task", "review_task", "edit_config", "bind", "unbind"]


@dataclass(frozen=True)
class V2RequestContext:
    user: object
    profile: V2AccountProfile
    department: object
    role_codes: list[str]


def _active_authenticated_user(request):
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        raise StandardUnauthorized()
    if not getattr(user, "is_active", False) or getattr(user, "status", None) != UserStatus.ACTIVE:
        raise StandardUnauthorized()
    return user


def resolve_v2_context(request) -> V2RequestContext:
    user = _active_authenticated_user(request)
    profile = (
        V2AccountProfile.objects.select_related("department", "department__tenant")
        .filter(user=user, status=DirectoryStatus.ACTIVE, department__status=DirectoryStatus.ACTIVE)
        .first()
    )
    if profile is None:
        raise StandardForbidden()

    role_codes = list(
        V2AccountRoleAssignment.objects.filter(account_profile=profile)
        .order_by("id")
        .values_list("role_code", flat=True)
    )
    return V2RequestContext(user=user, profile=profile, department=profile.department, role_codes=role_codes)


def is_platform_super_admin(context: V2RequestContext) -> bool:
    return FixedRole.PLATFORM_SUPER_ADMIN in context.role_codes


def is_department_admin(context: V2RequestContext) -> bool:
    return FixedRole.DEPARTMENT_ADMIN in context.role_codes


def require_platform_super_admin(request) -> V2RequestContext:
    context = resolve_v2_context(request)
    if not is_platform_super_admin(context):
        raise StandardForbidden()
    return context


def require_department_admin(request) -> V2RequestContext:
    context = resolve_v2_context(request)
    if not is_department_admin(context):
        raise StandardForbidden()
    return context


def role_permissions(role_codes: list[str]) -> list[str]:
    permissions: set[str] = set()
    for role_code in role_codes:
        permissions.update(ROLE_OPERATION_PERMISSIONS.get(role_code, set()))
    return [permission for permission in PERMISSION_ORDER if permission in permissions]


def has_v2_operation_permission(context: V2RequestContext, permission: str) -> bool:
    return permission in role_permissions(context.role_codes)


def require_v2_operation_permission(context: V2RequestContext, permission: str) -> None:
    if not has_v2_operation_permission(context, permission):
        raise StandardForbidden()
