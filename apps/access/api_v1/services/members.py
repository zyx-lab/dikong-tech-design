from django.db import transaction
from django.utils import timezone

from apps.access.api_v1.context import active_tenant_admin_member_count, is_assignable_tenant_role, normalize_display_name
from apps.access.exceptions import (
    StandardConstraintConflict,
    StandardForbidden,
    StandardMembershipDuplicate,
    StandardNotFound,
)
from apps.access.models import (
    DirectoryStatus,
    Role,
    Tenant,
    TenantMember,
    TenantMemberRole,
    TenantMemberRoleStatus,
    TenantMemberStatus,
    User,
)
from apps.access.services import log_action


def list_member_role_codes(member: TenantMember) -> list[str]:
    return list(
        member.role_bindings.filter(
            status=TenantMemberRoleStatus.GRANTED,
            system_role__status=DirectoryStatus.ACTIVE,
        )
        .exclude(system_role__code="platform_admin")
        .order_by("id")
        .values_list("system_role__code", flat=True)
    )


def require_tenant_admin(context) -> None:
    if "tenant_admin" not in context.role_codes:
        raise StandardForbidden()


def assignable_roles_queryset():
    return Role.objects.filter(status=DirectoryStatus.ACTIVE).exclude(code="platform_admin").order_by("id")


def get_assignable_roles_by_codes(role_codes: list[str]) -> list[Role]:
    normalized_codes = []
    seen = set()
    for role_code in role_codes:
        value = str(role_code).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized_codes.append(value)

    roles = list(assignable_roles_queryset().filter(code__in=normalized_codes))
    role_map = {role.code: role for role in roles}
    invalid_codes = [code for code in normalized_codes if code not in role_map]
    if invalid_codes:
        raise StandardConstraintConflict(msg="角色集合包含不可分配角色", data={"roleCodes": invalid_codes})
    return [role_map[code] for code in normalized_codes]


def get_tenant_member_or_404(*, tenant: Tenant, member_id: int) -> TenantMember:
    member = (
        TenantMember.objects.filter(tenant=tenant, id=member_id)
        .select_related("tenant", "user")
        .prefetch_related("role_bindings__system_role")
        .first()
    )
    if member is None:
        raise StandardNotFound()
    return member


def serialize_member_payload(member: TenantMember) -> dict:
    return {
        "memberId": member.id,
        "userId": member.user_id,
        "username": member.user.username,
        "displayName": normalize_display_name(member.display_name),
        "status": "ACTIVE" if member.status == TenantMemberStatus.ACTIVE else "DISABLED",
        "roleCodes": list_member_role_codes(member),
    }


def _ensure_last_tenant_admin_not_removed(*, tenant: Tenant, member: TenantMember, next_role_codes: list[str], next_status: int) -> None:
    current_role_codes = set(list_member_role_codes(member))
    current_is_active_admin = member.status == TenantMemberStatus.ACTIVE and "tenant_admin" in current_role_codes
    next_is_active_admin = next_status == TenantMemberStatus.ACTIVE and "tenant_admin" in set(next_role_codes)
    if not current_is_active_admin or next_is_active_admin:
        return
    if active_tenant_admin_member_count(tenant, excluding_member_id=member.id) > 0:
        return
    raise StandardConstraintConflict(msg="当前租户至少需要保留一个有效租户管理员")


@transaction.atomic
def create_tenant_member(*, tenant: Tenant, actor, user: User, display_name: str | None, role_codes: list[str] | None) -> TenantMember:
    existing = TenantMember.objects.filter(tenant=tenant, user=user).first()
    if existing is not None:
        raise StandardMembershipDuplicate()

    roles = get_assignable_roles_by_codes(role_codes or [])
    now = timezone.now()
    member = TenantMember.objects.create(
        tenant=tenant,
        user=user,
        display_name=normalize_display_name(display_name) or "",
        status=TenantMemberStatus.ACTIVE,
        responded_at=now,
        joined_at=now,
        invitation_token=None,
        invited_by_user=None,
        invited_at=None,
        expires_at=None,
    )
    for role in roles:
        TenantMemberRole.objects.create(
            tenant_member=member,
            system_role=role,
            status=TenantMemberRoleStatus.GRANTED,
            assigned_by_user=actor,
            assigned_at=now,
        )
    log_action(
        action="IAM_TENANT_MEMBER_CREATED",
        target_type="tenant_member",
        target_id=member.id,
        tenant=tenant,
        actor_user=actor,
        after_data=serialize_member_payload(member),
    )
    return member


@transaction.atomic
def update_member_display_name(*, member: TenantMember, actor, display_name: str | None) -> TenantMember:
    before = serialize_member_payload(member)
    member.display_name = normalize_display_name(display_name) or ""
    member.save(update_fields=["display_name", "updated_at"])
    log_action(
        action="IAM_TENANT_MEMBER_UPDATED",
        target_type="tenant_member",
        target_id=member.id,
        tenant=member.tenant,
        actor_user=actor,
        before_data=before,
        after_data=serialize_member_payload(member),
    )
    return member


@transaction.atomic
def replace_member_roles(*, tenant: Tenant, member: TenantMember, actor, role_codes: list[str]) -> TenantMember:
    normalized_codes = [role.code for role in get_assignable_roles_by_codes(role_codes)]
    _ensure_last_tenant_admin_not_removed(
        tenant=tenant,
        member=member,
        next_role_codes=normalized_codes,
        next_status=member.status,
    )
    before = serialize_member_payload(member)
    member.role_bindings.all().delete()
    now = timezone.now()
    for role in get_assignable_roles_by_codes(role_codes):
        TenantMemberRole.objects.create(
            tenant_member=member,
            system_role=role,
            status=TenantMemberRoleStatus.GRANTED,
            assigned_by_user=actor,
            assigned_at=now,
        )
    member.refresh_from_db()
    log_action(
        action="IAM_TENANT_MEMBER_ROLES_REPLACED",
        target_type="tenant_member",
        target_id=member.id,
        tenant=tenant,
        actor_user=actor,
        before_data=before,
        after_data=serialize_member_payload(member),
    )
    return member


@transaction.atomic
def enable_member(*, member: TenantMember, actor) -> TenantMember:
    if member.status == TenantMemberStatus.ACTIVE:
        raise StandardConstraintConflict(msg="成员已是启用状态")
    before = serialize_member_payload(member)
    now = timezone.now()
    member.status = TenantMemberStatus.ACTIVE
    if member.responded_at is None:
        member.responded_at = now
    if member.joined_at is None:
        member.joined_at = now
    member.invitation_token = None
    member.invited_by_user = None
    member.invited_at = None
    member.expires_at = None
    member.save()
    log_action(
        action="IAM_TENANT_MEMBER_ENABLED",
        target_type="tenant_member",
        target_id=member.id,
        tenant=member.tenant,
        actor_user=actor,
        before_data=before,
        after_data=serialize_member_payload(member),
    )
    return member


@transaction.atomic
def disable_member(*, member: TenantMember, actor) -> TenantMember:
    if member.status == TenantMemberStatus.DISABLED:
        raise StandardConstraintConflict(msg="成员已是停用状态")
    _ensure_last_tenant_admin_not_removed(
        tenant=member.tenant,
        member=member,
        next_role_codes=list_member_role_codes(member),
        next_status=TenantMemberStatus.DISABLED,
    )
    before = serialize_member_payload(member)
    member.status = TenantMemberStatus.DISABLED
    member.save(update_fields=["status", "updated_at"])
    log_action(
        action="IAM_TENANT_MEMBER_DISABLED",
        target_type="tenant_member",
        target_id=member.id,
        tenant=member.tenant,
        actor_user=actor,
        before_data=before,
        after_data=serialize_member_payload(member),
    )
    return member


@transaction.atomic
def initialize_tenant_admin(*, tenant: Tenant, actor, user: User, display_name: str | None) -> TenantMember:
    if tenant.status != 1:
        raise StandardConstraintConflict(msg="目标租户当前状态不允许初始化管理员")
    if active_tenant_admin_member_count(tenant) > 0:
        raise StandardConstraintConflict(msg="目标租户已存在有效租户管理员")

    member = TenantMember.objects.filter(tenant=tenant, user=user).select_related("user", "tenant").first()
    now = timezone.now()
    if member is None:
        member = TenantMember.objects.create(
            tenant=tenant,
            user=user,
            display_name=normalize_display_name(display_name) or "",
            status=TenantMemberStatus.ACTIVE,
            responded_at=now,
            joined_at=now,
        )
    else:
        member.status = TenantMemberStatus.ACTIVE
        if member.responded_at is None:
            member.responded_at = now
        if member.joined_at is None:
            member.joined_at = now
        member.invitation_token = None
        member.invited_by_user = None
        member.invited_at = None
        member.expires_at = None
        member.save()

    tenant_admin_role = assignable_roles_queryset().filter(code="tenant_admin").first()
    if tenant_admin_role is None:
        raise StandardConstraintConflict(msg="tenant_admin 角色目录不存在")

    TenantMemberRole.objects.update_or_create(
        tenant_member=member,
        system_role=tenant_admin_role,
        defaults={
            "status": TenantMemberRoleStatus.GRANTED,
            "assigned_by_user": actor,
            "assigned_at": now,
        },
    )
    log_action(
        action="IAM_TENANT_ADMIN_INITIALIZED",
        target_type="tenant_member",
        target_id=member.id,
        tenant=tenant,
        actor_user=actor,
        after_data=serialize_member_payload(member),
    )
    return member
