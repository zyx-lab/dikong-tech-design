from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from apps.access.api_v1.base import IamAPIView, ensure_empty_body
from apps.access.api_v1.serializers.session import (
    LoginRequestSerializer,
    LoginResponseSerializer,
    RefreshRequestSerializer,
    RegisterByPhoneRequestSerializer,
    RegisterRequestSerializer,
    RegisterResponseSerializer,
    SessionTokenSerializer,
)
from apps.access.api_v1.services.auth import authenticate_formal_user, create_auth_session, refresh_auth_session, register_by_phone, register_by_username, revoke_current_session
from apps.access.api_v1.serializers.common import StaffProfileSerializer
from apps.api_v1.schema import BUSINESS_INTERNAL_ERROR_RESPONSE, empty_envelope_serializer, object_envelope_serializer


def _session_user_payload(user):
    staff_profile = getattr(user, "staff_profile", None)
    staff_payload = StaffProfileSerializer(staff_profile).data if staff_profile else None
    return {
        "userId": user.id,
        "username": user.username,
        "status": "ACTIVE",
        "hasPlatformAccess": bool(user.is_platform_admin),
        "staffProfile": staff_payload,
    }


def _registered_user_payload(user):
    staff_profile = getattr(user, "staff_profile", None)
    staff_payload = StaffProfileSerializer(staff_profile).data if staff_profile else None
    return {
        "userId": user.id,
        "username": user.username,
        "status": "ACTIVE",
        "createdAt": user.created_at,
        "updatedAt": user.updated_at,
        "staffProfile": staff_payload,
    }


class SessionLoginView(IamAPIView):
    permission_classes = [AllowAny]

    @extend_schema(
        auth=[],
        request=LoginRequestSerializer,
        responses={
            200: object_envelope_serializer("IamSessionLoginEnvelope", LoginResponseSerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="正式 IAM 登录",
    )
    def post(self, request):
        serializer = LoginRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate_formal_user(**serializer.validated_data)
        token_payload = create_auth_session(user=user, request=request)
        token_payload["user"] = _session_user_payload(user)
        return Response(token_payload, status=status.HTTP_200_OK)


class SessionRefreshView(IamAPIView):
    permission_classes = [AllowAny]

    @extend_schema(
        auth=[],
        request=RefreshRequestSerializer,
        responses={
            200: object_envelope_serializer("IamSessionRefreshEnvelope", SessionTokenSerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="刷新正式 IAM 会话",
    )
    def post(self, request):
        serializer = RefreshRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = refresh_auth_session(refresh_token=serializer.validated_data["refreshToken"], request=request)
        return Response(payload, status=status.HTTP_200_OK)


class SessionLogoutView(IamAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=None,
        responses={
            200: empty_envelope_serializer("IamSessionLogoutEnvelope"),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="撤销当前正式 IAM 会话",
    )
    def post(self, request):
        ensure_empty_body(request)
        revoke_current_session(request.auth)
        return Response(None, status=status.HTTP_200_OK)


class SessionRegisterView(IamAPIView):
    permission_classes = [AllowAny]

    @extend_schema(
        auth=[],
        request=RegisterRequestSerializer,
        responses={
            201: object_envelope_serializer("IamSessionRegisterEnvelope", RegisterResponseSerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="用户名方式注册正式 IAM 业务账号",
    )
    def post(self, request):
        serializer = RegisterRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = register_by_username(**serializer.validated_data)
        return Response(_registered_user_payload(user), status=status.HTTP_201_CREATED)


class SessionRegisterByPhoneView(IamAPIView):
    permission_classes = [AllowAny]

    @extend_schema(
        auth=[],
        request=RegisterByPhoneRequestSerializer,
        responses={
            201: object_envelope_serializer("IamSessionRegisterByPhoneEnvelope", RegisterResponseSerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="手机号方式注册正式 IAM 业务账号",
    )
    def post(self, request):
        serializer = RegisterByPhoneRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = register_by_phone(
            phone=serializer.validated_data["phone"],
            sms_code=serializer.validated_data["smsCode"],
            password=serializer.validated_data["password"],
        )
        return Response(_registered_user_payload(user), status=status.HTTP_201_CREATED)
