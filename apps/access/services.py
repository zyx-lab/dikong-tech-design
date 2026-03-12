from dataclasses import dataclass
from typing import Any, Callable, Optional

from django.contrib.auth.models import Permission
from django.db.models import QuerySet
from django.forms.models import model_to_dict

from apps.access.models import (
    AuditLog,
    EmploymentStatus,
    GroupPermissionScope,
    ScopeStatus,
    ScopeType,
    StaffProfile,
    StaffTypeGroup,
    StaffTypeStatus,
    User,
    UserStatus,
)


@dataclass
class IdentityCheckResult:
    ok: bool
    reason_code: str
    staff: Optional[StaffProfile] = None


class IdentityReason:
    OK = "OK"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    USER_STATUS_INVALID = "USER_STATUS_INVALID"
    STAFF_NOT_BOUND = "STAFF_NOT_BOUND"
    STAFF_INACTIVE = "STAFF_INACTIVE"
    STAFF_TYPE_NOT_ASSIGNED = "STAFF_TYPE_NOT_ASSIGNED"
    STAFF_TYPE_DISABLED = "STAFF_TYPE_DISABLED"


@dataclass
class AuthorizationDecision:
    allowed: bool
    reason_code: str
    scope: Optional[str] = None
    staff_id: Optional[int] = None


class AuthorizationReason:
    OK = "OK"
    PERMISSION_NOT_FOUND = "PERMISSION_NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    SCOPE_NOT_CONFIGURED = "SCOPE_NOT_CONFIGURED"
    SCOPE_DENIED = "SCOPE_DENIED"
    BIZ_RULE_DENIED = "BIZ_RULE_DENIED"


_SCOPE_RANK = {
    ScopeType.OWN: 1,
    ScopeType.ASSIGNED: 2,
    ScopeType.ALL: 3,
}


class IdentityService:
    """身份状态校验服务。"""

    @staticmethod
    def get_staff(user: User) -> Optional[StaffProfile]:
        if not user or not user.is_authenticated:
            return None
        return StaffProfile.objects.select_related("staff_type").filter(user=user).first()

    @staticmethod
    def check_business_user(user: User) -> IdentityCheckResult:
        return IdentityService._check(
            user,
            require_staff=not bool(getattr(user, "is_superuser", False)),
        )

    @staticmethod
    def check_system_operator(user: User) -> IdentityCheckResult:
        return IdentityService._check(
            user,
            require_staff=not bool(getattr(user, "is_superuser", False)),
        )

    @staticmethod
    def _check(user: User, *, require_staff: bool) -> IdentityCheckResult:
        if not user or not user.is_authenticated:
            return IdentityCheckResult(False, IdentityReason.UNAUTHENTICATED)

        if not user.is_active:
            return IdentityCheckResult(False, IdentityReason.ACCOUNT_DISABLED)

        if user.status != UserStatus.ACTIVE:
            return IdentityCheckResult(False, IdentityReason.USER_STATUS_INVALID)

        if user.is_superuser:
            return IdentityCheckResult(True, IdentityReason.OK)

        if not require_staff:
            return IdentityCheckResult(True, IdentityReason.OK)

        staff = IdentityService.get_staff(user)
        if not staff:
            return IdentityCheckResult(False, IdentityReason.STAFF_NOT_BOUND)

        if staff.employment_status != EmploymentStatus.ACTIVE:
            return IdentityCheckResult(False, IdentityReason.STAFF_INACTIVE)

        if not staff.staff_type_id:
            return IdentityCheckResult(False, IdentityReason.STAFF_TYPE_NOT_ASSIGNED)

        if staff.staff_type.status != StaffTypeStatus.ACTIVE:
            return IdentityCheckResult(False, IdentityReason.STAFF_TYPE_DISABLED)

        # 这里只做“授权链基础身份”校验，不做业务资质判定。
        # 例如飞手体检、专项证照等规则应在业务接口的 biz_checker 中执行。
        return IdentityCheckResult(True, IdentityReason.OK, staff=staff)


def snapshot(instance) -> Optional[dict[str, Any]]:
    """模型快照：用于记录审计日志的 before/after。"""

    if instance is None:
        return None
    field_names = [field.name for field in instance._meta.fields]
    return model_to_dict(instance, fields=field_names)


def _resolve_client_ip(request) -> Optional[str]:
    if request is None:
        return None

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    return request.META.get("REMOTE_ADDR")


def log_action(
    *,
    action: str,
    target_type: str,
    target_id: Any = "",
    before_data: Optional[dict[str, Any]] = None,
    after_data: Optional[dict[str, Any]] = None,
    tenant=None,
    request=None,
    actor_user=None,
) -> AuditLog:
    """统一审计日志入口。"""

    if actor_user is None and request is not None:
        actor_user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None

    return AuditLog.objects.create(
        tenant=tenant,
        actor_user=actor_user,
        action=action,
        target_type=target_type,
        target_id=str(target_id or ""),
        before_data=before_data,
        after_data=after_data,
        ip=_resolve_client_ip(request),
        request_id=getattr(request, "request_id", "") if request is not None else "",
    )


def _parse_permission_code(permission_code: str) -> tuple[Optional[str], Optional[str]]:
    if "." not in permission_code:
        return None, None
    app_label, codename = permission_code.split(".", 1)
    if not app_label or not codename:
        return None, None
    return app_label, codename


def _is_active_superuser(user) -> bool:
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_superuser", False)
        and getattr(user, "is_active", False)
        and getattr(user, "status", None) == UserStatus.ACTIVE
    )


def get_permission_obj(permission_code: str) -> Optional[Permission]:
    app_label, codename = _parse_permission_code(permission_code)
    if not app_label:
        return None
    try:
        return Permission.objects.select_related("content_type").get(
            content_type__app_label=app_label,
            codename=codename,
        )
    except Permission.DoesNotExist:
        return None


def _active_group_ids_for_staff_type(staff_type_id: int) -> list[int]:
    return list(
        StaffTypeGroup.objects.filter(
            staff_type_id=staff_type_id,
            status=ScopeStatus.ACTIVE,
        ).values_list("group_id", flat=True)
    )


def _pick_max_scope(scopes: list[str]) -> Optional[str]:
    if not scopes:
        return None
    sorted_scopes = sorted(scopes, key=lambda value: _SCOPE_RANK.get(value, 0), reverse=True)
    return sorted_scopes[0]


def resolve_effective_scope(user, permission_code: str) -> Optional[str]:
    if _is_active_superuser(user):
        permission = get_permission_obj(permission_code)
        return ScopeType.ALL if permission else None

    identity_result = IdentityService.check_business_user(user)
    if not identity_result.ok:
        return None

    permission = get_permission_obj(permission_code)
    if not permission:
        return None

    group_ids = _active_group_ids_for_staff_type(identity_result.staff.staff_type_id)
    if not group_ids:
        return None

    # 同一权限可能来自多个 group，这里按优先级合并为“最终有效 scope”。
    scopes = list(
        GroupPermissionScope.objects.filter(
            group_id__in=group_ids,
            permission=permission,
            status=ScopeStatus.ACTIVE,
        ).values_list("scope_type", flat=True)
    )
    return _pick_max_scope(scopes)


def get_staff_type_permission_codes(user) -> list[str]:
    if _is_active_superuser(user):
        permissions = Permission.objects.select_related("content_type").all().order_by("content_type__app_label", "codename")
        return [f"{perm.content_type.app_label}.{perm.codename}" for perm in permissions]

    identity_result = IdentityService.check_business_user(user)
    if not identity_result.ok:
        return []

    group_ids = _active_group_ids_for_staff_type(identity_result.staff.staff_type_id)
    if not group_ids:
        return []

    permissions = (
        Permission.objects.filter(group__id__in=group_ids)
        .distinct()
        .select_related("content_type")
        .order_by("content_type__app_label", "codename")
    )
    return [f"{perm.content_type.app_label}.{perm.codename}" for perm in permissions]


def has_staff_type_permission(user, permission_code: str) -> bool:
    permission = get_permission_obj(permission_code)
    if not permission or not user or not user.is_authenticated:
        return False

    if _is_active_superuser(user):
        return True

    # 这里复用业务身份校验：只有有效业务身份（staff + staff_type）才可拿到权限。
    identity_result = IdentityService.check_business_user(user)
    if not identity_result.ok:
        return False

    group_ids = _active_group_ids_for_staff_type(identity_result.staff.staff_type_id)
    if not group_ids:
        return False

    return Permission.objects.filter(id=permission.id, group__id__in=group_ids).exists()


def _is_owner(obj, staff_id: int) -> bool:
    # 统一约定 OWN 只认 created_by_staff_id，避免多套隐式命名带来的理解成本。
    return getattr(obj, "created_by_staff_id", None) == staff_id


def _is_assigned(obj, staff_id: int) -> bool:
    # 统一约定 ASSIGNED 优先认 assigned_staff_id；
    # 若资源使用 assignments 关系（如无人机），按 ACTIVE 分配判定。
    if getattr(obj, "assigned_staff_id", None) == staff_id:
        return True

    assignments = getattr(obj, "assignments", None)
    if assignments is None or not hasattr(assignments, "filter"):
        return False

    qs = assignments.filter(staff_id=staff_id)
    model_fields = {field.name for field in assignments.model._meta.fields}
    if "status" in model_fields:
        qs = qs.filter(status="ACTIVE")
    return qs.exists()


def is_obj_in_scope(obj, scope: str, staff_id: int) -> bool:
    if scope == ScopeType.ALL:
        return True
    if scope == ScopeType.OWN:
        return _is_owner(obj, staff_id)
    if scope == ScopeType.ASSIGNED:
        return _is_assigned(obj, staff_id)
    return False


def apply_scope_to_queryset(
    queryset: QuerySet,
    scope: str,
    staff_id: int,
    assigned_filter_builder: Optional[Callable[[int], dict]] = None,
) -> QuerySet:
    """在列表层提前收敛数据范围，避免先查全量再逐条判定。"""

    if scope == ScopeType.ALL:
        return queryset

    field_names = {field.name for field in queryset.model._meta.get_fields()}

    if scope == ScopeType.OWN:
        if "created_by_staff_id" in field_names or "created_by_staff" in field_names:
            return queryset.filter(created_by_staff_id=staff_id)
        return queryset.none()

    if scope == ScopeType.ASSIGNED:
        if assigned_filter_builder:
            return queryset.filter(**assigned_filter_builder(staff_id)).distinct()

        if "assigned_staff_id" in field_names:
            return queryset.filter(assigned_staff_id=staff_id)
        if "assignments" in field_names:
            return queryset.filter(assignments__staff_id=staff_id, assignments__status="ACTIVE").distinct()

        return queryset.none()

    return queryset.none()


class AuthzService:
    """授权服务：组合身份、权限、范围、业务规则做最终判定。"""

    @staticmethod
    def authorize(user, permission_code: str, obj=None, biz_checker: Optional[Callable[[object, object], bool]] = None):
        permission = get_permission_obj(permission_code)
        if not permission:
            return AuthorizationDecision(False, AuthorizationReason.PERMISSION_NOT_FOUND)

        # superuser 视为 root：拥有所有当前/未来权限，不参与 staff_type 矩阵与 scope 收敛。
        if user and getattr(user, "is_authenticated", False) and getattr(user, "is_superuser", False):
            if not user.is_active:
                return AuthorizationDecision(False, IdentityReason.ACCOUNT_DISABLED)
            if user.status != UserStatus.ACTIVE:
                return AuthorizationDecision(False, IdentityReason.USER_STATUS_INVALID)
            if biz_checker is not None and not biz_checker(user, obj):
                return AuthorizationDecision(False, AuthorizationReason.BIZ_RULE_DENIED, scope=ScopeType.ALL)
            return AuthorizationDecision(True, AuthorizationReason.OK, scope=ScopeType.ALL)

        # 统一鉴权链：身份 -> 权限 -> scope -> 业务状态机。
        identity_result = IdentityService.check_business_user(user)
        if not identity_result.ok:
            return AuthorizationDecision(False, identity_result.reason_code)

        if not has_staff_type_permission(user, permission_code):
            return AuthorizationDecision(False, AuthorizationReason.PERMISSION_DENIED, staff_id=identity_result.staff.id)

        scope = resolve_effective_scope(user, permission_code)
        if not scope:
            return AuthorizationDecision(False, AuthorizationReason.SCOPE_NOT_CONFIGURED, staff_id=identity_result.staff.id)

        if obj is not None and not is_obj_in_scope(obj, scope, identity_result.staff.id):
            return AuthorizationDecision(False, AuthorizationReason.SCOPE_DENIED, scope=scope, staff_id=identity_result.staff.id)

        if biz_checker is not None and not biz_checker(user, obj):
            return AuthorizationDecision(False, AuthorizationReason.BIZ_RULE_DENIED, scope=scope, staff_id=identity_result.staff.id)

        return AuthorizationDecision(True, AuthorizationReason.OK, scope=scope, staff_id=identity_result.staff.id)

    @staticmethod
    def get_permission_scope_map(user) -> list[dict]:
        permission_codes = get_staff_type_permission_codes(user)
        result = []
        for code in permission_codes:
            scope = resolve_effective_scope(user, code)
            result.append(
                {
                    "permission": code,
                    "scope": scope,
                    "enabled": scope is not None,
                }
            )
        return result
