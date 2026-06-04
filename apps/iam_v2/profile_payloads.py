from apps.iam_v2.serializers import DepartmentReadSerializer
from apps.iam_v2.serializers import AccountQualificationReadSerializer, AccountRoleProfileReadSerializer
from apps.iam_v2.services import V2RequestContext
from apps.iam_v2.account_profile_services import active_qualifications_queryset


def v2_account_summary_payload(context: V2RequestContext) -> dict:
    profile = context.profile
    return {
        "userId": context.user.id,
        "username": context.user.username,
        "accountProfileId": profile.id,
        "name": profile.name,
        "phone": profile.phone,
        "email": profile.email,
        "status": profile.status,
        "department": DepartmentReadSerializer(context.department).data,
        "roleCodes": context.role_codes,
    }


def v2_me_profile_payload(context: V2RequestContext) -> dict:
    profile = context.profile
    role_profiles = profile.role_profiles.select_related("account_profile", "account_profile__user").filter(deleted_at__isnull=True)
    qualifications = active_qualifications_queryset(profile)
    payload = v2_account_summary_payload(context)
    payload.update(
        {
            "profiles": AccountRoleProfileReadSerializer(role_profiles, many=True).data,
            "qualifications": AccountQualificationReadSerializer(qualifications, many=True).data,
            "createdAt": profile.created_at,
            "updatedAt": profile.updated_at,
        }
    )
    return payload
