from django.contrib.auth import authenticate
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.access.api_base import IamAPIView
from apps.access.exceptions import StandardUnauthorized
from apps.access.models import DirectoryStatus, UserStatus
from apps.access.session_serializers import LoginRequestSerializer
from apps.access.session_services import create_auth_session
from apps.iam_v2.models import V2AccountProfile, V2AccountRoleAssignment
from apps.iam_v2.profile_payloads import v2_account_summary_payload
from apps.iam_v2.profile_serializers import V2SessionLoginResponseSerializer
from apps.iam_v2.services import V2RequestContext, active_roles_for_codes, data_scopes_for_roles, permission_codes_for_roles


def _authenticate_v2_user(*, username: str, password: str) -> V2RequestContext:
    user = authenticate(username=username, password=password)
    if not user or user.is_superuser or not user.is_active or user.status != UserStatus.ACTIVE:
        raise StandardUnauthorized()
    profile = (
        V2AccountProfile.objects.select_related("department")
        .filter(user=user, status=DirectoryStatus.ACTIVE, department__status=DirectoryStatus.ACTIVE)
        .first()
    )
    if profile is None:
        raise StandardUnauthorized()
    role_codes = list(
        V2AccountRoleAssignment.objects.filter(account_profile=profile)
        .order_by("id")
        .values_list("role_code", flat=True)
    )
    roles = active_roles_for_codes(role_codes)
    return V2RequestContext(
        user=user,
        profile=profile,
        department=profile.department,
        role_codes=role_codes,
        permissions=frozenset(permission_codes_for_roles(roles)),
        data_scopes=data_scopes_for_roles(roles),
        is_super_admin=any(role.is_super_admin for role in roles),
    )


class SessionLoginView(IamAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        auth=[],
        request=LoginRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=V2SessionLoginResponseSerializer,
                description="登录成功。返回 v2 账户摘要和当前认证会话的 accessToken / refreshToken。",
            ),
        },
        summary="v2 IAM 登录",
        description="仅允许已有 active v2 账号登录；手机号是账户联系方式，不作为登录标识。",
    )
    def post(self, request):
        serializer = LoginRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        context = _authenticate_v2_user(**serializer.validated_data)
        token_payload = create_auth_session(user=context.user, request=request)
        token_payload["user"] = v2_account_summary_payload(context)
        return Response(token_payload, status=status.HTTP_200_OK)
