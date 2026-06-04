from django.db.models import QuerySet
from rest_framework import serializers

from apps.access.models import DirectoryStatus
from apps.iam_v2.models import V2AccountProfile, V2AccountQualification, V2AccountRoleProfile, V2ProfileType


def active_profile_type_or_error(profile_type: str) -> V2ProfileType:
    profile_type_code = str(profile_type or "").strip()
    profile_type_obj = V2ProfileType.objects.filter(code=profile_type_code, status=DirectoryStatus.ACTIVE).first()
    if profile_type_obj is None:
        raise serializers.ValidationError({"profileType": ["档案类型不存在或已停用"]})
    return profile_type_obj


def require_account_role_for_profile_type(account: V2AccountProfile, profile_type: str) -> None:
    if not account.role_assignments.filter(role_code=profile_type).exists():
        raise serializers.ValidationError({"profileType": ["账号未分配该档案类型绑定的角色"]})


def active_account_role_profile(account: V2AccountProfile, profile_type: str) -> V2AccountRoleProfile | None:
    return V2AccountRoleProfile.objects.filter(
        account_profile=account,
        profile_type=profile_type,
        status=DirectoryStatus.ACTIVE,
        deleted_at__isnull=True,
    ).first()


def require_active_account_role_profile(account: V2AccountProfile, profile_type: str) -> V2AccountRoleProfile:
    profile = active_account_role_profile(account, profile_type)
    if profile is None:
        raise serializers.ValidationError({"profileType": ["账号缺少该类型有效档案"]})
    return profile


def active_qualifications_queryset(account: V2AccountProfile, profile_type: str | None = None) -> QuerySet:
    queryset = V2AccountQualification.objects.filter(account_profile=account, deleted_at__isnull=True)
    if profile_type:
        queryset = queryset.filter(profile_type=profile_type)
    return queryset.order_by("profile_type", "-expires_at", "-id")


def account_has_effective_qualification(account: V2AccountProfile, profile_type: str) -> bool:
    return any(qualification.is_effective() for qualification in active_qualifications_queryset(account, profile_type))


def disable_profiles_for_removed_roles(*, account: V2AccountProfile, previous_role_codes: set[str], next_role_codes: set[str]) -> list[V2AccountRoleProfile]:
    removed_role_codes = previous_role_codes - next_role_codes
    if not removed_role_codes:
        return []
    profiles = list(
        V2AccountRoleProfile.objects.filter(
            account_profile=account,
            profile_type__in=removed_role_codes,
            deleted_at__isnull=True,
        )
    )
    for profile in profiles:
        if profile.status == DirectoryStatus.DISABLED:
            continue
        profile.status = DirectoryStatus.DISABLED
        profile.save(update_fields=["status", "updated_at"])
    return profiles
