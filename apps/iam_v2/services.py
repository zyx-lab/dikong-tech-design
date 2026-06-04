from dataclasses import dataclass

from django.db.models import Q, QuerySet

from apps.access.exceptions import StandardForbidden, StandardUnauthorized
from apps.access.models import DirectoryStatus, UserStatus
from apps.iam_v2.models import (
    FixedRole,
    V2AccountProfile,
    V2AccountRoleAssignment,
    V2Permission,
    V2Role,
    V2RoleCustomDepartment,
    V2RolePermissionGrant,
)
from apps.iam_v2.permissions import PERMISSION_CODES


ROLE_OPERATION_PERMISSIONS = {
    FixedRole.PLATFORM_SUPER_ADMIN.value: {"view", "monitor", "dispatch_task", "use", "review_task", "edit_config", "bind", "unbind"},
    FixedRole.DEPARTMENT_ADMIN.value: {"view", "monitor", "dispatch_task", "use", "review_task", "edit_config", "bind", "unbind"},
    FixedRole.TASK_MONITOR_DISPATCHER.value: {"view", "monitor", "dispatch_task", "use"},
    FixedRole.PILOT.value: {"view", "monitor", "review_task"},
    FixedRole.WORK_ORDER_HANDLER.value: {"view", "review_task"},
}

PERMISSION_ORDER = ["view", "monitor", "dispatch_task", "use", "review_task", "edit_config", "bind", "unbind"]
OPERATION_PERMISSION_CODES = {
    "view": {
        "resource:drone:read",
        "resource:dock:read",
        "resource:gateway:read",
        "resource:payload:read",
        "inspection:route:read",
        "inspection:mission:read",
    },
    "monitor": {
        "resource:mqtt_message:read",
        "inspection:telemetry:read",
        "inspection:flight:read",
    },
    "dispatch_task": {
        "inspection:mission:create",
        "inspection:mission:start",
    },
    "use": {
        "inspection:live:start",
        "inspection:live:update",
        "inspection:live:switch",
    },
    "review_task": {
        "inspection:mission:complete",
        "inspection:mission:fail",
        "inspection:flight_record:read",
    },
    "edit_config": {
        "resource:dji_connection:update",
        "resource:dji_connection:discover",
        "resource:share_group:update",
    },
    "bind": {"resource:binding:create"},
    "unbind": {"resource:binding:delete"},
}


@dataclass(frozen=True)
class V2RequestContext:
    user: object
    profile: V2AccountProfile
    department: object
    role_codes: list[str]
    permissions: frozenset[str]
    data_scopes: tuple[str, ...]
    is_super_admin: bool = False


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
        V2AccountProfile.objects.select_related("department")
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
    roles = active_roles_for_codes(role_codes)
    return V2RequestContext(
        user=user,
        profile=profile,
        department=profile.department,
        role_codes=role_codes,
        permissions=frozenset(permission_codes_for_roles(roles)),
        data_scopes=data_scopes_for_roles(roles),
        is_super_admin=any(role.is_super_admin for role in roles),
    )


def active_roles_for_codes(role_codes: list[str]) -> list[V2Role]:
    if not role_codes:
        return []
    roles = list(V2Role.objects.filter(code__in=role_codes, status=DirectoryStatus.ACTIVE).order_by("sort", "id"))
    role_by_code = {role.code: role for role in roles}
    return [role_by_code[role_code] for role_code in role_codes if role_code in role_by_code]


def permission_codes_for_roles(roles: list[V2Role]) -> set[str]:
    if any(role.is_super_admin for role in roles):
        return set(
            V2Permission.objects.filter(status=DirectoryStatus.ACTIVE).values_list("code", flat=True)
        ) or set(PERMISSION_CODES)
    role_ids = [role.id for role in roles]
    if not role_ids:
        return set()
    return set(
        V2RolePermissionGrant.objects.filter(
            role_id__in=role_ids,
            permission__status=DirectoryStatus.ACTIVE,
        ).values_list("permission__code", flat=True)
    )


def data_scopes_for_roles(roles: list[V2Role]) -> tuple[str, ...]:
    order = [
        V2Role.DataScope.ALL,
        V2Role.DataScope.DEPT_AND_CHILDREN,
        V2Role.DataScope.DEPT_ONLY,
        V2Role.DataScope.SELF,
        V2Role.DataScope.CUSTOM_DEPARTMENTS,
    ]
    scopes = {role.data_scope for role in roles if role.status == DirectoryStatus.ACTIVE}
    return tuple(scope for scope in order if scope in scopes)


def is_platform_super_admin(context: V2RequestContext) -> bool:
    return context.is_super_admin


def is_department_admin(context: V2RequestContext) -> bool:
    return FixedRole.DEPARTMENT_ADMIN.value in context.role_codes


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
    roles = active_roles_for_codes(list(role_codes))
    backend_permissions = permission_codes_for_roles(roles)
    if any(role.is_super_admin for role in roles):
        return list(PERMISSION_ORDER)

    permissions: set[str] = set()
    for operation, required_codes in OPERATION_PERMISSION_CODES.items():
        if backend_permissions.intersection(required_codes):
            permissions.add(operation)

    if not permissions:
        for role_code in role_codes:
            permissions.update(ROLE_OPERATION_PERMISSIONS.get(str(role_code), set()))
    return [permission for permission in PERMISSION_ORDER if permission in permissions]


def has_v2_operation_permission(context: V2RequestContext, permission: str) -> bool:
    if is_platform_super_admin(context):
        return True
    for permission_code in OPERATION_PERMISSION_CODES.get(permission, set()):
        if has_v2_permission(context, permission_code):
            return True
    return permission in role_permissions(context.role_codes)


def require_v2_operation_permission(context: V2RequestContext, permission: str) -> None:
    if not has_v2_operation_permission(context, permission):
        raise StandardForbidden()


def has_v2_permission(context: V2RequestContext, permission_code: str) -> bool:
    if is_platform_super_admin(context):
        return permission_code in PERMISSION_CODES or V2Permission.objects.filter(code=permission_code).exists()
    return permission_code in context.permissions


def require_v2_permission(context: V2RequestContext, permission_code: str) -> None:
    if not has_v2_permission(context, permission_code):
        raise StandardForbidden()


def require_request_v2_permission(request, permission_code: str) -> V2RequestContext:
    context = resolve_v2_context(request)
    require_v2_permission(context, permission_code)
    return context


def _field_id_lookup(field_path: str) -> str:
    return f"{field_path}__id" if "__" in field_path else f"{field_path}_id"


def apply_data_scope(
    context: V2RequestContext,
    queryset: QuerySet,
    *,
    department_field: str,
    self_user_field: str | None = None,
) -> QuerySet:
    scopes = set(context.data_scopes)
    if V2Role.DataScope.ALL in scopes or is_platform_super_admin(context):
        return queryset

    filters = Q()
    if V2Role.DataScope.DEPT_AND_CHILDREN in scopes:
        filters |= Q(**{f"{department_field}__path__startswith": context.department.path})
    if V2Role.DataScope.DEPT_ONLY in scopes:
        filters |= Q(**{_field_id_lookup(department_field): context.department.id})
    if V2Role.DataScope.SELF in scopes and self_user_field:
        filters |= Q(**{_field_id_lookup(self_user_field): context.user.id})
    if V2Role.DataScope.CUSTOM_DEPARTMENTS in scopes:
        role_ids = V2Role.objects.filter(
            code__in=context.role_codes,
            data_scope=V2Role.DataScope.CUSTOM_DEPARTMENTS,
            status=DirectoryStatus.ACTIVE,
        ).values_list("id", flat=True)
        department_ids = V2RoleCustomDepartment.objects.filter(role_id__in=role_ids).values_list("department_id", flat=True)
        filters |= Q(**{f"{_field_id_lookup(department_field)}__in": department_ids})

    if not filters:
        return queryset.none()
    return queryset.filter(filters).distinct()
