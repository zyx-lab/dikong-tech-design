from django.db.models import QuerySet

from apps.access.exceptions import StandardForbidden, StandardNotFound
from apps.access.models import DirectoryStatus
from apps.iam_v2.models import FixedRole, V2AccountProfile
from apps.iam_v2.qualification_services import account_has_effective_qualification
from apps.iam_v2.services import is_platform_super_admin
from apps.workforce_v2.models import PilotProfile


def is_task_dispatcher(context) -> bool:
    return FixedRole.TASK_MONITOR_DISPATCHER in context.role_codes or is_platform_super_admin(context)


def is_pilot(context) -> bool:
    return FixedRole.PILOT in context.role_codes


def is_department_admin_role(context) -> bool:
    return FixedRole.DEPARTMENT_ADMIN in context.role_codes or is_platform_super_admin(context)


def can_view_pilots(context) -> bool:
    return is_department_admin_role(context) or is_task_dispatcher(context) or is_pilot(context)


def require_pilot_viewer(context) -> None:
    if not can_view_pilots(context):
        raise StandardForbidden()


def department_scope_filter(context, field_prefix: str = "account_profile__department"):
    return {f"{field_prefix}__path__startswith": context.department.path}


def visible_pilots_queryset(context) -> QuerySet:
    require_pilot_viewer(context)
    queryset = PilotProfile.objects.select_related(
        "account_profile",
        "account_profile__user",
        "account_profile__department",
    )
    if is_platform_super_admin(context):
        return queryset.order_by("id")
    if is_pilot(context) and not (is_department_admin_role(context) or is_task_dispatcher(context)):
        return queryset.filter(account_profile__user=context.user).order_by("id")
    return queryset.filter(**department_scope_filter(context)).order_by("id")


def manageable_pilots_queryset(context) -> QuerySet:
    queryset = visible_pilots_queryset(context)
    if is_platform_super_admin(context):
        return queryset
    return queryset.filter(account_profile__department=context.department)


def get_visible_pilot_or_404(context, pilot_id: int) -> PilotProfile:
    pilot = visible_pilots_queryset(context).filter(pk=pilot_id).first()
    if pilot is None:
        raise StandardNotFound()
    return pilot


def get_manageable_pilot_or_404(context, pilot_id: int) -> PilotProfile:
    pilot = manageable_pilots_queryset(context).filter(pk=pilot_id).first()
    if pilot is None:
        raise StandardNotFound()
    return pilot


def require_pilot_manager(context) -> None:
    if not is_department_admin_role(context):
        raise StandardForbidden()


def account_profile_for_pilot(context, account_profile_id: int) -> V2AccountProfile:
    queryset = V2AccountProfile.objects.select_related("department", "user").filter(
        pk=account_profile_id,
        status=DirectoryStatus.ACTIVE,
        department__status=DirectoryStatus.ACTIVE,
    )
    if not is_platform_super_admin(context):
        queryset = queryset.filter(department=context.department)
    profile = queryset.first()
    if profile is None:
        raise StandardNotFound()
    if not profile.role_assignments.filter(role_code=FixedRole.PILOT).exists():
        raise StandardForbidden(msg="账号未分配 pilot 角色")
    return profile


def pilot_has_effective_qualification(pilot: PilotProfile) -> bool:
    return account_has_effective_qualification(pilot.account_profile, FixedRole.PILOT)
