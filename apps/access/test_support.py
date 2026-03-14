from types import SimpleNamespace
from uuid import uuid4

from django.utils import timezone

from apps.access.models import (
    DirectoryStatus,
    Permission,
    QualificationRecordStatus,
    QualificationType,
    Role,
    RolePermissionGrant,
    ScopeType,
    StaffProfile,
    Tenant,
    TenantMember,
    TenantMemberQualification,
    TenantMemberRole,
    TenantMemberRoleStatus,
    TenantMemberStatus,
    TenantStatus,
)


def ensure_staff_profile(
    user,
    *,
    staff_no: str,
    name: str,
    employment_status: int = 1,
):
    staff, _ = StaffProfile.objects.update_or_create(
        user=user,
        defaults={
            "staff_no": staff_no,
            "name": name,
            "employment_status": employment_status,
        },
    )
    return staff


def ensure_tenant_role_binding(
    user,
    *,
    tenant: Tenant | None = None,
    tenant_code: str | None = None,
    role_code: str,
    role_name: str,
    display_name: str | None = None,
):
    tenant = tenant or Tenant.objects.create(
        code=tenant_code or f"tenant_{uuid4().hex[:8]}",
        name=f"租户{uuid4().hex[:4]}",
        status=TenantStatus.ACTIVE,
    )
    member, _ = TenantMember.objects.update_or_create(
        tenant=tenant,
        user=user,
        defaults={
            "display_name": display_name or getattr(user, "username", "成员"),
            "status": TenantMemberStatus.ACTIVE,
            "responded_at": timezone.now(),
            "joined_at": timezone.now(),
        },
    )
    role, _ = Role.objects.update_or_create(
        code=role_code,
        defaults={"name": role_name, "status": DirectoryStatus.ACTIVE},
    )
    TenantMemberRole.objects.update_or_create(
        tenant_member=member,
        system_role=role,
        defaults={
            "status": TenantMemberRoleStatus.GRANTED,
            "assigned_at": timezone.now(),
        },
    )
    return tenant, member, role


def ensure_tenant_member_position(
    member: TenantMember,
    *,
    code: str,
    name: str,
    description: str = "",
    status: int = DirectoryStatus.ACTIVE,
):
    role, _ = Role.objects.update_or_create(
        code=code,
        defaults={
            "name": name,
            "description": description,
            "status": status,
        },
    )
    binding, _ = TenantMemberRole.objects.update_or_create(
        tenant_member=member,
        system_role=role,
        defaults={
            "status": TenantMemberRoleStatus.GRANTED,
            "assigned_at": timezone.now(),
        },
    )
    return binding


def ensure_tenant_member_qualification(
    member: TenantMember,
    *,
    code: str,
    name: str,
    status: int = QualificationRecordStatus.ACTIVE,
    valid_from=None,
    valid_until=None,
    payload: dict | None = None,
):
    qualification_type, _ = QualificationType.objects.update_or_create(
        code=code,
        defaults={
            "name": name,
            "status": DirectoryStatus.ACTIVE,
            "requires_validity": bool(valid_from or valid_until),
        },
    )
    qualification = TenantMemberQualification.objects.create(
        tenant_member=member,
        qualification_type=qualification_type,
        status=status,
        valid_from=valid_from,
        valid_until=valid_until,
        payload_json=payload or {},
    )
    return qualification


def grant_role_permissions(
    role: Role,
    permission_scopes: dict[str, str],
    *,
    group_name: str | None = None,
):
    del group_name
    grants = []
    for permission_code, scope_type in permission_scopes.items():
        module = permission_code.split(".", 1)[0] if "." in permission_code else "access"
        permission, _ = Permission.objects.update_or_create(
            code=permission_code,
            defaults={
                "name": permission_code,
                "module": module,
                "resource_code": module if scope_type in {ScopeType.OWN, ScopeType.ASSIGNED} else "",
                "status": DirectoryStatus.ACTIVE,
            },
        )
        grant, _ = RolePermissionGrant.objects.update_or_create(
            role=role,
            permission=permission,
            defaults={"scope_type": scope_type},
        )
        grants.append(grant)
    return grants


def build_request(user, tenant=None):
    return SimpleNamespace(user=user, tenant_context=tenant)
