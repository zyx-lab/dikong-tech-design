from dataclasses import dataclass

from apps.access.models import DirectoryStatus, EmploymentStatus, TenantMemberRoleStatus, TenantMemberStatus


@dataclass(frozen=True)
class TenantMemberValidationMessages:
    tenant_mismatch: str
    inactive_member: str
    missing_staff_profile: str
    inactive_employment: str
    missing_role: str


def _raise_field_error(*, error_cls, field_name: str, message: str):
    raise error_cls({field_name: message})


def validate_relation_belongs_to_tenant(*, related_obj, tenant_id, field_name: str, mismatch_message: str, error_cls):
    if tenant_id and related_obj is not None and related_obj.tenant_id != tenant_id:
        _raise_field_error(error_cls=error_cls, field_name=field_name, message=mismatch_message)


def validate_tenant_member_as_pilot(*, tenant_member, tenant_id, field_name: str, messages: TenantMemberValidationMessages, error_cls):
    if tenant_member is None:
        return

    validate_relation_belongs_to_tenant(
        related_obj=tenant_member,
        tenant_id=tenant_id,
        field_name=field_name,
        mismatch_message=messages.tenant_mismatch,
        error_cls=error_cls,
    )
    if tenant_member.status != TenantMemberStatus.ACTIVE:
        _raise_field_error(error_cls=error_cls, field_name=field_name, message=messages.inactive_member)

    staff = getattr(tenant_member.user, "staff_profile", None)
    if staff is None:
        _raise_field_error(error_cls=error_cls, field_name=field_name, message=messages.missing_staff_profile)
    if staff.employment_status != EmploymentStatus.ACTIVE:
        _raise_field_error(error_cls=error_cls, field_name=field_name, message=messages.inactive_employment)
    if not tenant_member.role_bindings.filter(
        system_role__code="pilot_operator",
        system_role__status=DirectoryStatus.ACTIVE,
        status=TenantMemberRoleStatus.GRANTED,
    ).exists():
        _raise_field_error(error_cls=error_cls, field_name=field_name, message=messages.missing_role)
