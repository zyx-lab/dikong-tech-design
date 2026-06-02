from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiResponse, extend_schema

from apps.access.api_base import IamAPIView, ensure_empty_body
from apps.api_v2.openapi import standard_empty_response
from apps.access.session_serializers import (
    LoginRequestSerializer,
    LoginResponseSerializer,
    RefreshRequestSerializer,
    RegisterByPhoneRequestSerializer,
    RegisterRequestSerializer,
    RegisterResponseSerializer,
    SessionTokenSerializer,
    StaffProfileSerializer,
)
from apps.access.session_services import (
    authenticate_formal_user,
    create_auth_session,
    refresh_auth_session,
    register_by_phone,
    register_by_username,
    revoke_current_session,
)


class EmptyResponseSerializer(serializers.Serializer):
    pass


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
            200: OpenApiResponse(
                response=LoginResponseSerializer,
                description="登录成功。返回当前认证会话的 accessToken / refreshToken。",
            ),
        },
        summary="IAM 登录",
        description="当前 API 版本的会话登录接口。认证成功后签发可撤销 Bearer Token。",
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
            200: OpenApiResponse(
                response=SessionTokenSerializer,
                description="刷新成功。服务端执行 refresh token rotation，旧 refreshToken 立即失效。",
            ),
        },
        summary="刷新 IAM 会话",
        description="使用当前认证会话最新一次签发且尚未失效的 refreshToken 轮换令牌。",
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
            200: standard_empty_response("登出成功。服务端撤销当前认证会话。"),
        },
        summary="撤销当前 IAM 会话",
        description="请求头必须携带 `Authorization: Bearer <accessToken>`；请求体固定为空对象或无请求体。",
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
            201: OpenApiResponse(
                response=RegisterResponseSerializer,
                description="注册成功。创建 IAM 业务账号，但不自动登录，不返回 token。",
            ),
        },
        summary="用户名方式注册 IAM 业务账号",
        description="注册成功后账号状态固定写入 ACTIVE；用户后续需显式调用当前版本的 session/login 获取 Bearer Token。",
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
            201: OpenApiResponse(
                response=RegisterResponseSerializer,
                description="注册成功。服务端以手机号作为登录用户名，不自动登录，不返回 token。",
            ),
        },
        summary="手机号方式注册 IAM 业务账号",
        description="请求体字段固定为 `phone`、`smsCode`、`password`；不接受 snake_case 别名。",
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
