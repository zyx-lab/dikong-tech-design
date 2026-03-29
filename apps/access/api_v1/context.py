from dataclasses import dataclass
import re


from apps.access.exceptions import StandardForbidden, StandardUnauthorized
from apps.access.models import (
    DirectoryStatus,
    RolePermissionGrant,
    StaffProfile,
    Tenant,
    TenantMember,
    TenantMemberRoleStatus,
    TenantMemberStatus,
    TenantStatus,
    User,
    UserStatus,
)

TENANT_CODE_HEADER = "HTTP_X_TENANT_CODE"
TENANT_CODE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

RUNTIME_UNASSIGNED = "unassigned"
RUNTIME_TENANT_MEMBER = "tenant_member"
RUNTIME_PLATFORM_OPERATOR = "platform_operator"


@dataclass(frozen=True)
class FormalIdentity:
    user: User
    runtime: str
    staff_profile: StaffProfile | None


@dataclass(frozen=True)
class TenantRequestContext:
    identity: FormalIdentity
    tenant: Tenant
    member: TenantMember
    role_codes: list[str]


def normalize_display_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def require_authenticated_formal_identity(user) -> FormalIdentity:
    if not user or not getattr(user, "is_authenticated", False):
        raise StandardUnauthorized()
    if not user.is_active or user.status != UserStatus.ACTIVE:
        raise StandardUnauthorized()
    if user.is_superuser:
        raise StandardForbidden()

    if user.is_platform_admin:
        return FormalIdentity(user=user, runtime=RUNTIME_PLATFORM_OPERATOR, staff_profile=None)

    staff_profile = StaffProfile.objects.filter(user=user).first()
    if staff_profile is None:
        raise StandardForbidden()

    has_membership = TenantMember.objects.filter(user=user).exists()
    runtime = RUNTIME_TENANT_MEMBER if has_membership else RUNTIME_UNASSIGNED
    return FormalIdentity(user=user, runtime=runtime, staff_profile=staff_profile)


def require_business_identity(user) -> FormalIdentity:
    identity = require_authenticated_formal_identity(user)
    if identity.runtime == RUNTIME_PLATFORM_OPERATOR:
        raise StandardForbidden()
    return identity


def require_platform_operator(user) -> FormalIdentity:
    identity = require_authenticated_formal_identity(user)
    if identity.runtime != RUNTIME_PLATFORM_OPERATOR:
        raise StandardForbidden()
    return identity


def parse_tenant_code_header(request) -> str:
    tenant_code = getattr(request, "tenant_context_code", None)
    if tenant_code is None:
        tenant_code = request.META.get(TENANT_CODE_HEADER)
    if not tenant_code:
        raise ValueError("X-TENANT-CODE is required")
    tenant_code = tenant_code.strip()
    if not TENANT_CODE_PATTERN.match(tenant_code):
        raise ValueError("X-TENANT-CODE is invalid")
    return tenant_code


def resolve_tenant_request_context(request) -> TenantRequestContext:
    identity = require_business_identity(request.user)
    if identity.runtime != RUNTIME_TENANT_MEMBER:
        raise StandardForbidden()

    parse_tenant_code_header(request)

    tenant = getattr(request, "tenant_context", None)
    if tenant is None or tenant.status != TenantStatus.ACTIVE:
        raise StandardForbidden()

    member = (
        TenantMember.objects.filter(
            tenant=tenant,
            user=identity.user,
            status=TenantMemberStatus.ACTIVE,
        )
        .select_related("tenant", "user")
        .prefetch_related("role_bindings__system_role")
        .first()
    )
    if member is None:
        raise StandardForbidden()

    role_codes = list(
        member.role_bindings.filter(
            status=TenantMemberRoleStatus.GRANTED,
            system_role__status=DirectoryStatus.ACTIVE,
        )
        .exclude(system_role__code="platform_admin")
        .order_by("id")
        .values_list("system_role__code", flat=True)
    )
    return TenantRequestContext(identity=identity, tenant=tenant, member=member, role_codes=role_codes)


def is_assignable_tenant_role(role) -> bool:
    return role.status == DirectoryStatus.ACTIVE and role.code != "platform_admin"


def tenant_context_has_permission(context: TenantRequestContext, permission_code: str) -> bool:
    return RolePermissionGrant.objects.filter(
        role__code__in=context.role_codes,
        role__status=DirectoryStatus.ACTIVE,
        permission__code=permission_code,
        permission__status=DirectoryStatus.ACTIVE,
    ).exists()


def active_tenant_admin_member_count(tenant: Tenant, *, excluding_member_id: int | None = None) -> int:
    queryset = TenantMember.objects.filter(
        tenant=tenant,
        status=TenantMemberStatus.ACTIVE,
        role_bindings__status=TenantMemberRoleStatus.GRANTED,
        role_bindings__system_role__code="tenant_admin",
        role_bindings__system_role__status=DirectoryStatus.ACTIVE,
    )
    if excluding_member_id is not None:
        queryset = queryset.exclude(id=excluding_member_id)
    return queryset.distinct().count()


def is_formal_business_account(user: User) -> bool:
    if user.is_superuser or user.is_platform_admin:
        return False
    if not user.is_active or user.status != UserStatus.ACTIVE:
        return False
    return StaffProfile.objects.filter(user=user).exists()



def eligible_user_queryset():
    return User.objects.filter(
        is_superuser=False,
        is_platform_admin=False,
        is_active=True,
        status=UserStatus.ACTIVE,
        staff_profile__isnull=False,
    ).distinct()
