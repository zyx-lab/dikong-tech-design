from apps.iam_v2.profile_serializers import AccountQualificationReadSerializer
from apps.iam_v2.qualification_services import account_qualifications_for_roles
from apps.iam_v2.serializers import DepartmentReadSerializer
from apps.iam_v2.services import V2RequestContext
from apps.workforce_v2.models import PilotProfile


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


def _pilot_role_profile_payload(profile) -> dict | None:
    pilot = PilotProfile.objects.filter(account_profile=profile).first()
    if pilot is None:
        return None
    return {
        "id": pilot.id,
        "accountProfileId": pilot.account_profile_id,
        "displayName": pilot.display_name,
        "level": pilot.level,
        "status": pilot.status,
        "remark": pilot.remark,
        "createdAt": pilot.created_at,
        "updatedAt": pilot.updated_at,
    }


def role_profiles_payload(context: V2RequestContext) -> dict:
    return {
        "departmentAdmin": None,
        "taskMonitorDispatcher": None,
        "pilot": _pilot_role_profile_payload(context.profile),
        "workOrderHandler": None,
    }


def v2_me_profile_payload(context: V2RequestContext) -> dict:
    profile = context.profile
    payload = v2_account_summary_payload(context)
    payload.update(
        {
            "roleProfiles": role_profiles_payload(context),
            "qualifications": AccountQualificationReadSerializer(
                account_qualifications_for_roles(profile, context.role_codes),
                many=True,
            ).data,
            "createdAt": profile.created_at,
            "updatedAt": profile.updated_at,
        }
    )
    return payload
