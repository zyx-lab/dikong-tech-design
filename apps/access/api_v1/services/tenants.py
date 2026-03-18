from django.db import transaction

from apps.access.exceptions import StandardConstraintConflict, StandardDuplicate
from apps.access.models import Tenant, TenantStatus
from apps.access.services import log_action


def serialize_tenant_payload(tenant: Tenant) -> dict:
    return {
        "tenantId": tenant.id,
        "tenantCode": tenant.code,
        "name": tenant.name,
        "status": "ACTIVE" if tenant.status == TenantStatus.ACTIVE else "DISABLED",
        "plan": None,
        "remark": tenant.remark,
    }


@transaction.atomic
def create_tenant(*, actor, tenant_code: str, name: str, remark: str | None) -> Tenant:
    if Tenant.objects.filter(code=tenant_code).exists():
        raise StandardDuplicate(msg="tenantCode 已存在")
    tenant = Tenant.objects.create(
        code=tenant_code,
        name=name,
        status=TenantStatus.ACTIVE,
        plan="",
        remark=(remark or "").strip(),
    )
    log_action(
        action="IAM_PLATFORM_TENANT_CREATED",
        target_type="tenant",
        target_id=tenant.id,
        actor_user=actor,
        after_data=serialize_tenant_payload(tenant),
    )
    return tenant


@transaction.atomic
def enable_tenant(*, tenant: Tenant, actor) -> Tenant:
    if tenant.status == TenantStatus.ACTIVE:
        raise StandardConstraintConflict(msg="租户已是启用状态")
    before = serialize_tenant_payload(tenant)
    tenant.status = TenantStatus.ACTIVE
    tenant.save(update_fields=["status", "updated_at"])
    log_action(
        action="IAM_PLATFORM_TENANT_ENABLED",
        target_type="tenant",
        target_id=tenant.id,
        actor_user=actor,
        before_data=before,
        after_data=serialize_tenant_payload(tenant),
    )
    return tenant


@transaction.atomic
def disable_tenant(*, tenant: Tenant, actor) -> Tenant:
    if tenant.status == TenantStatus.DISABLED:
        raise StandardConstraintConflict(msg="租户已是停用状态")
    before = serialize_tenant_payload(tenant)
    tenant.status = TenantStatus.DISABLED
    tenant.save(update_fields=["status", "updated_at"])
    log_action(
        action="IAM_PLATFORM_TENANT_DISABLED",
        target_type="tenant",
        target_id=tenant.id,
        actor_user=actor,
        before_data=before,
        after_data=serialize_tenant_payload(tenant),
    )
    return tenant
