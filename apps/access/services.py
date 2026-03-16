from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Optional

from django.db.models import Q, QuerySet
from django.forms.models import model_to_dict
from django.utils import timezone

from apps.access.models import (
    AuditLog,
    DirectoryStatus,
    EmploymentStatus,
    Permission,
    QualificationRecordStatus,
    RolePermissionGrant,
    ScopeType,
    StaffProfile,
    TenantMember,
    TenantMemberRoleStatus,
    TenantMemberStatus,
    TenantStatus,
    User,
    UserStatus,
)


@dataclass
class IdentityCheckResult:
    ok: bool
    reason_code: str
    staff: Optional[StaffProfile] = None


@dataclass
class TenantAccessContext:
    ok: bool
    reason_code: str
    tenant: Any = None
    member: Optional[TenantMember] = None
    role_codes: list[str] = field(default_factory=list)


class IdentityReason:
    OK = "OK"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    USER_STATUS_INVALID = "USER_STATUS_INVALID"
    STAFF_NOT_BOUND = "STAFF_NOT_BOUND"
    STAFF_INACTIVE = "STAFF_INACTIVE"
    TENANT_CONTEXT_REQUIRED = "TENANT_CONTEXT_REQUIRED"
    TENANT_INACTIVE = "TENANT_INACTIVE"
    TENANT_MEMBERSHIP_REQUIRED = "TENANT_MEMBERSHIP_REQUIRED"


@dataclass
class AuthorizationDecision:
    allowed: bool
    reason_code: str
    scope: Optional[str] = None
    tenant_member_id: Optional[int] = None


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

PLATFORM_ADMIN_ROLE_CODE = "platform_admin"


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in values:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


class IdentityService:
    """账号与业务主体状态校验。"""

    @staticmethod
    def get_staff(user: User) -> Optional[StaffProfile]:
        if not user or not user.is_authenticated:
            return None
        return StaffProfile.objects.filter(user=user).first()

    @staticmethod
    def check_account(user: User) -> IdentityCheckResult:
        if not user or not user.is_authenticated:
            return IdentityCheckResult(False, IdentityReason.UNAUTHENTICATED)

        if not user.is_active:
            return IdentityCheckResult(False, IdentityReason.ACCOUNT_DISABLED)

        if user.status != UserStatus.ACTIVE:
            return IdentityCheckResult(False, IdentityReason.USER_STATUS_INVALID)

        return IdentityCheckResult(True, IdentityReason.OK)

    @staticmethod
    def check_staff_actor(user: User) -> IdentityCheckResult:
        account_result = IdentityService.check_account(user)
        if not account_result.ok:
            return account_result

        if user.is_superuser:
            return IdentityCheckResult(True, IdentityReason.OK)

        staff = IdentityService.get_staff(user)
        if not staff:
            return IdentityCheckResult(False, IdentityReason.STAFF_NOT_BOUND)

        if staff.employment_status != EmploymentStatus.ACTIVE:
            return IdentityCheckResult(False, IdentityReason.STAFF_INACTIVE)

        return IdentityCheckResult(True, IdentityReason.OK, staff=staff)

    @staticmethod
    def get_active_tenant_member(user: User, tenant) -> Optional[TenantMember]:
        if not user or not tenant:
            return None
        return (
            TenantMember.objects.filter(
                tenant=tenant,
                user=user,
                status=TenantMemberStatus.ACTIVE,
            )
            .select_related("tenant", "user")
            .first()
        )


def snapshot(instance) -> Optional[dict[str, Any]]:
    """模型快照：用于记录审计日志的 before/after。"""

    if instance is None:
        return None
    field_names = [field.name for field in instance._meta.fields]
    return model_to_dict(instance, fields=field_names)


def _resolve_client_ip(request) -> Optional[str]:
    if request is None:
        return None

    meta = getattr(request, "META", {}) or {}

    forwarded_for = meta.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    return meta.get("REMOTE_ADDR")


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
    if tenant is None and request is not None:
        tenant = getattr(request, "tenant_context", None)

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


def default_invitation_expiry(at=None):
    base_time = at or timezone.now()
    return base_time + timedelta(days=7)


def expire_stale_tenant_member_invitations(now=None) -> int:
    current_time = now or timezone.now()
    return TenantMember.objects.filter(
        status=TenantMemberStatus.INVITED,
        expires_at__lt=current_time,
    ).update(
        status=TenantMemberStatus.EXPIRED,
        invitation_token=None,
        updated_at=current_time,
    )


def expire_stale_tenant_member_invitation(member: Optional[TenantMember], now=None) -> Optional[TenantMember]:
    if member is None:
        return None
    current_time = now or timezone.now()
    if member.status == TenantMemberStatus.INVITED and member.expires_at and member.expires_at < current_time:
        member.status = TenantMemberStatus.EXPIRED
        member.invitation_token = None
        member.save(update_fields=["status", "invitation_token", "updated_at"])
    return member


def _is_active_superuser(user) -> bool:
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_superuser", False)
        and getattr(user, "is_active", False)
        and getattr(user, "status", None) == UserStatus.ACTIVE
    )


def is_active_platform_admin(user) -> bool:
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_platform_admin", False)
        and getattr(user, "is_active", False)
        and getattr(user, "status", None) == UserStatus.ACTIVE
    )


def get_permission_obj(permission_code: str) -> Optional[Permission]:
    return Permission.objects.filter(code=permission_code).first()


def resolve_tenant_access_context(request) -> TenantAccessContext:
    user = getattr(request, "user", None)
    account_result = IdentityService.check_account(user)
    if not account_result.ok:
        return TenantAccessContext(False, account_result.reason_code)

    tenant = getattr(request, "tenant_context", None)
    if tenant is None:
        return TenantAccessContext(False, IdentityReason.TENANT_CONTEXT_REQUIRED)
    if tenant.status != TenantStatus.ACTIVE:
        return TenantAccessContext(False, IdentityReason.TENANT_INACTIVE)

    member = (
        TenantMember.objects.filter(
            tenant=tenant,
            user=user,
            status=TenantMemberStatus.ACTIVE,
        )
        .select_related("tenant", "user")
        .prefetch_related("role_bindings__system_role")
        .first()
    )
    if member is None:
        return TenantAccessContext(False, IdentityReason.TENANT_MEMBERSHIP_REQUIRED)

    role_codes = list(
        member.role_bindings.filter(status=TenantMemberRoleStatus.GRANTED)
        .order_by("id")
        .values_list("system_role__code", flat=True)
    )
    return TenantAccessContext(True, IdentityReason.OK, tenant=tenant, member=member, role_codes=role_codes)


def resolve_staff_tenant_member(staff: Optional[StaffProfile], tenant) -> Optional[TenantMember]:
    if staff is None or tenant is None:
        return None
    return IdentityService.get_active_tenant_member(staff.user, tenant)


def staff_has_role_in_tenant(staff: Optional[StaffProfile], tenant, role_code: str) -> bool:
    tenant_member = resolve_staff_tenant_member(staff, tenant)
    if tenant_member is None or not role_code:
        return False
    return tenant_member.role_bindings.filter(
        system_role__code=role_code,
        system_role__status=DirectoryStatus.ACTIVE,
        status=TenantMemberRoleStatus.GRANTED,
    ).exists()


def staff_has_position_in_tenant(staff: Optional[StaffProfile], tenant, position_code: str) -> bool:
    return staff_has_role_in_tenant(staff, tenant, position_code)


def staff_has_qualification_in_tenant(
    staff: Optional[StaffProfile],
    tenant,
    qualification_code: str,
    *,
    on_date=None,
) -> bool:
    tenant_member = resolve_staff_tenant_member(staff, tenant)
    if tenant_member is None or not qualification_code:
        return False

    current_date = on_date or timezone.localdate()
    return tenant_member.qualifications.filter(
        qualification_type__code=qualification_code,
        qualification_type__status=DirectoryStatus.ACTIVE,
        status=QualificationRecordStatus.ACTIVE,
    ).filter(
        Q(qualification_type__requires_validity=False)
        | (
            Q(valid_from__isnull=False)
            & Q(valid_until__isnull=False)
            & Q(valid_from__lte=current_date)
            & Q(valid_until__gte=current_date)
        )
    ).exists()


def _role_grants_queryset(*, role_codes: list[str], permission_code: Optional[str] = None):
    normalized_role_codes = _dedupe_keep_order([str(item).strip() for item in role_codes if str(item).strip()])
    if not normalized_role_codes:
        return RolePermissionGrant.objects.none()

    queryset = RolePermissionGrant.objects.filter(
        role__code__in=normalized_role_codes,
        role__status=DirectoryStatus.ACTIVE,
        permission__status=DirectoryStatus.ACTIVE,
    )
    if permission_code:
        queryset = queryset.filter(permission__code=permission_code)
    return queryset


def _pick_max_scope(scopes: list[str]) -> Optional[str]:
    if not scopes:
        return None
    sorted_scopes = sorted(scopes, key=lambda value: _SCOPE_RANK.get(value, 0), reverse=True)
    return sorted_scopes[0]


def _merge_permission_scope(permission_scopes: dict[str, str], permission_code: str, scope: Optional[str]) -> None:
    if not permission_code or not scope:
        return
    current = permission_scopes.get(permission_code)
    if current is None or _SCOPE_RANK.get(scope, 0) > _SCOPE_RANK.get(current, 0):
        permission_scopes[permission_code] = scope


def _role_has_permission(role_codes: list[str], permission_code: str) -> bool:
    return _role_grants_queryset(role_codes=role_codes, permission_code=permission_code).exists()


def resolve_effective_scope(request, permission_code: str) -> Optional[str]:
    user = getattr(request, "user", None)
    permission = get_permission_obj(permission_code)
    if not permission or permission.status != DirectoryStatus.ACTIVE:
        return None

    if _is_active_superuser(user):
        return ScopeType.ALL

    if is_active_platform_admin(user):
        scopes = list(
            _role_grants_queryset(
                role_codes=[PLATFORM_ADMIN_ROLE_CODE],
                permission_code=permission_code,
            ).values_list("scope_type", flat=True)
        )
        return _pick_max_scope(scopes)

    tenant_access = resolve_tenant_access_context(request)
    if not tenant_access.ok:
        return None

    scopes = list(_role_grants_queryset(role_codes=tenant_access.role_codes, permission_code=permission_code).values_list("scope_type", flat=True))
    return _pick_max_scope(scopes)


def get_request_permission_codes(request) -> list[str]:
    user = getattr(request, "user", None)
    if _is_active_superuser(user):
        return list(Permission.objects.filter(status=DirectoryStatus.ACTIVE).order_by("code").values_list("code", flat=True))

    if is_active_platform_admin(user):
        permissions = (
            Permission.objects.filter(
                status=DirectoryStatus.ACTIVE,
                role_grants__role__code=PLATFORM_ADMIN_ROLE_CODE,
                role_grants__role__status=DirectoryStatus.ACTIVE,
            )
            .distinct()
            .order_by("code")
        )
        return list(permissions.values_list("code", flat=True))

    tenant_access = resolve_tenant_access_context(request)
    if not tenant_access.ok:
        return []

    permissions = (
        Permission.objects.filter(
            status=DirectoryStatus.ACTIVE,
            role_grants__role__code__in=tenant_access.role_codes,
            role_grants__role__status=DirectoryStatus.ACTIVE,
        )
        .distinct()
        .order_by("code")
    )
    return list(permissions.values_list("code", flat=True))


def has_request_permission(request, permission_code: str) -> bool:
    permission = get_permission_obj(permission_code)
    user = getattr(request, "user", None)
    if not permission or not user or not user.is_authenticated:
        return False
    if permission.status != DirectoryStatus.ACTIVE:
        return False

    if _is_active_superuser(user):
        return True

    if is_active_platform_admin(user):
        return _role_has_permission([PLATFORM_ADMIN_ROLE_CODE], permission_code)

    tenant_access = resolve_tenant_access_context(request)
    if not tenant_access.ok:
        return False

    return _role_has_permission(tenant_access.role_codes, permission_code)


def _is_owner(obj, tenant_member_id: int) -> bool:
    return getattr(obj, "created_by_tenant_member_id", None) == tenant_member_id


def _is_assigned(obj, tenant_member_id: int) -> bool:
    if getattr(obj, "assigned_tenant_member_id", None) == tenant_member_id:
        return True

    assignments = getattr(obj, "assignments", None)
    if assignments is None or not hasattr(assignments, "filter"):
        return False

    qs = assignments.filter(tenant_member_id=tenant_member_id)
    model_fields = {field.name for field in assignments.model._meta.fields}
    if "status" in model_fields:
        qs = qs.filter(status="ACTIVE")
    return qs.exists()


def is_obj_in_scope(obj, scope: str, tenant_member_id: int) -> bool:
    if scope == ScopeType.ALL:
        return True
    if scope == ScopeType.OWN:
        return _is_owner(obj, tenant_member_id)
    if scope == ScopeType.ASSIGNED:
        return _is_assigned(obj, tenant_member_id)
    return False


def apply_scope_to_queryset(
    queryset: QuerySet,
    scope: str,
    tenant_member_id: int,
    assigned_filter_builder: Optional[Callable[[int], dict]] = None,
) -> QuerySet:
    """在列表层提前收敛数据范围，避免先查全量再逐条判定。"""

    if scope == ScopeType.ALL:
        return queryset

    field_names = {field.name for field in queryset.model._meta.get_fields()}

    if scope == ScopeType.OWN:
        if "created_by_tenant_member_id" in field_names:
            return queryset.filter(created_by_tenant_member_id=tenant_member_id)
        return queryset.none()

    if scope == ScopeType.ASSIGNED:
        if assigned_filter_builder:
            return queryset.filter(**assigned_filter_builder(tenant_member_id)).distinct()

        if "assigned_tenant_member_id" in field_names:
            return queryset.filter(assigned_tenant_member_id=tenant_member_id)
        if "assignments" in field_names:
            return queryset.filter(assignments__tenant_member_id=tenant_member_id, assignments__status="ACTIVE").distinct()

        return queryset.none()

    return queryset.none()


class AuthzService:
    """授权服务：组合账号、租户成员、角色、scope 与业务规则做最终判定。"""

    @staticmethod
    def authorize(request, permission_code: str, obj=None, biz_checker: Optional[Callable[[object, object], bool]] = None):
        user = getattr(request, "user", None)
        if _is_active_superuser(user):
            if biz_checker is not None and not biz_checker(user, obj):
                return AuthorizationDecision(False, AuthorizationReason.BIZ_RULE_DENIED, scope=ScopeType.ALL)
            return AuthorizationDecision(True, AuthorizationReason.OK, scope=ScopeType.ALL)

        if is_active_platform_admin(user):
            if not _role_has_permission([PLATFORM_ADMIN_ROLE_CODE], permission_code):
                return AuthorizationDecision(False, AuthorizationReason.PERMISSION_DENIED)

            scope = resolve_effective_scope(request, permission_code)
            if not scope:
                return AuthorizationDecision(False, AuthorizationReason.SCOPE_NOT_CONFIGURED)
            if scope != ScopeType.ALL:
                return AuthorizationDecision(False, AuthorizationReason.SCOPE_DENIED, scope=scope)

            if biz_checker is not None and not biz_checker(user, obj):
                return AuthorizationDecision(False, AuthorizationReason.BIZ_RULE_DENIED, scope=scope)

            return AuthorizationDecision(True, AuthorizationReason.OK, scope=scope)

        permission = get_permission_obj(permission_code)
        if not permission:
            return AuthorizationDecision(False, AuthorizationReason.PERMISSION_DENIED)
        if permission.status != DirectoryStatus.ACTIVE:
            return AuthorizationDecision(False, AuthorizationReason.PERMISSION_DENIED)

        tenant_access = resolve_tenant_access_context(request)
        if not tenant_access.ok:
            return AuthorizationDecision(False, tenant_access.reason_code)

        if not _role_has_permission(tenant_access.role_codes, permission_code):
            return AuthorizationDecision(False, AuthorizationReason.PERMISSION_DENIED)

        scope = resolve_effective_scope(request, permission_code)
        if not scope:
            return AuthorizationDecision(False, AuthorizationReason.SCOPE_NOT_CONFIGURED)

        tenant_member_id: Optional[int] = tenant_access.member.id if scope != ScopeType.ALL else None

        if obj is not None and scope != ScopeType.ALL and not is_obj_in_scope(obj, scope, tenant_member_id):
            return AuthorizationDecision(False, AuthorizationReason.SCOPE_DENIED, scope=scope, tenant_member_id=tenant_member_id)

        if biz_checker is not None and not biz_checker(user, obj):
            return AuthorizationDecision(False, AuthorizationReason.BIZ_RULE_DENIED, scope=scope, tenant_member_id=tenant_member_id)

        return AuthorizationDecision(True, AuthorizationReason.OK, scope=scope, tenant_member_id=tenant_member_id)

    @staticmethod
    def get_permission_scope_map(request) -> list[dict]:
        permission_codes = get_request_permission_codes(request)
        result = []
        for code in permission_codes:
            scope = resolve_effective_scope(request, code)
            result.append(
                {
                    "permission": code,
                    "scope": scope,
                    "enabled": scope is not None,
                }
            )
        return result

    @staticmethod
    def get_tenant_role_permission_scope_map(role_codes: list[str]) -> list[dict]:
        normalized_role_codes = _dedupe_keep_order([str(item).strip() for item in role_codes if str(item).strip()])
        if not normalized_role_codes:
            return []

        permission_scopes: dict[str, str] = {}
        grants = (
            _role_grants_queryset(role_codes=normalized_role_codes)
            .select_related("permission")
            .order_by("permission__code")
        )
        for item in grants:
            _merge_permission_scope(permission_scopes, item.permission.code, item.scope_type)

        return [
            {
                "permission": permission_code,
                "scope": scope,
                "enabled": True,
            }
            for permission_code, scope in sorted(permission_scopes.items())
        ]
