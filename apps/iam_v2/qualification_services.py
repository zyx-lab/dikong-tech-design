from django.db.models import QuerySet

from apps.iam_v2.models import DEPARTMENT_ROLE_CODES, V2AccountProfile, V2AccountQualification


def qualification_role_codes(role_codes: list[str]) -> list[str]:
    return [role_code for role_code in role_codes if role_code in DEPARTMENT_ROLE_CODES]


def account_qualifications_for_roles(account_profile: V2AccountProfile, role_codes: list[str]) -> QuerySet:
    return V2AccountQualification.objects.filter(
        account_profile=account_profile,
        role_code__in=qualification_role_codes(role_codes),
    ).order_by("-expires_at", "-id")


def account_has_effective_qualification(account_profile: V2AccountProfile, role_code: str) -> bool:
    return any(
        qualification.is_effective()
        for qualification in V2AccountQualification.objects.filter(
            account_profile=account_profile,
            role_code=role_code,
        )
    )
