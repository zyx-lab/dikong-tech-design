from types import SimpleNamespace
from uuid import uuid4

from django.contrib.auth.models import Group, Permission
from django.utils import timezone

from apps.access.models import (
    GroupPermissionScope,
    ScopeStatus,
    ScopeType,
    StaffProfile,
    SystemRole,
    SystemRoleGroup,
    Tenant,
    TenantMemberAttributeStatus,
    TenantMemberPosition,
    TenantMemberQualification,
    TenantMember,
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
        status=TenantStatus.ENABLED,
    )
    member, _ = TenantMember.objects.update_or_create(
        tenant=tenant,
        user=user,
        defaults={
            "display_name": display_name or getattr(user, "username", "成员"),
            "status": TenantMemberStatus.ACTIVE,
            "joined_at": timezone.now(),
        },
    )
    role, _ = SystemRole.objects.update_or_create(
        code=role_code,
        defaults={"name": role_name, "status": 1},
    )
    TenantMemberRole.objects.update_or_create(
        tenant_member=member,
        system_role=role,
        defaults={"status": TenantMemberRoleStatus.ACTIVE},
    )
    return tenant, member, role


def ensure_tenant_member_position(
    member: TenantMember,
    *,
    code: str,
    name: str,
    description: str = "",
    status: int = TenantMemberAttributeStatus.ACTIVE,
):
    position, _ = TenantMemberPosition.objects.update_or_create(
        tenant_member=member,
        code=code,
        defaults={
            "name": name,
            "description": description,
            "status": status,
        },
    )
    return position


def ensure_tenant_member_qualification(
    member: TenantMember,
    *,
    code: str,
    name: str,
    description: str = "",
    status: int = TenantMemberAttributeStatus.ACTIVE,
    valid_until=None,
    payload: dict | None = None,
):
    qualification, _ = TenantMemberQualification.objects.update_or_create(
        tenant_member=member,
        code=code,
        defaults={
            "name": name,
            "description": description,
            "status": status,
            "valid_until": valid_until,
            "payload": payload or {},
        },
    )
    return qualification


def grant_role_permissions(
    role: SystemRole,
    permission_scopes: dict[str, str],
    *,
    group_name: str | None = None,
):
    group = Group.objects.create(name=group_name or f"{role.code}-{uuid4().hex[:8]}")
    permissions = []
    for permission_code in permission_scopes.keys():
        app_label, codename = permission_code.split(".", 1)
        permission = Permission.objects.get(content_type__app_label=app_label, codename=codename)
        permissions.append(permission)
    group.permissions.add(*permissions)
    SystemRoleGroup.objects.update_or_create(
        system_role=role,
        group=group,
        defaults={"status": ScopeStatus.ACTIVE},
    )
    for permission_code, scope_type in permission_scopes.items():
        app_label, codename = permission_code.split(".", 1)
        permission = Permission.objects.get(content_type__app_label=app_label, codename=codename)
        GroupPermissionScope.objects.update_or_create(
            group=group,
            permission=permission,
            defaults={"scope_type": scope_type, "status": ScopeStatus.ACTIVE},
        )
    return group


def build_request(user, tenant=None):
    return SimpleNamespace(user=user, tenant_context=tenant)
