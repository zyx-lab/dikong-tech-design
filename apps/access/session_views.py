from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiResponse, extend_schema

from apps.access.api_base import IamAPIView, ensure_empty_body
from apps.access.session_serializers import RefreshRequestSerializer, SessionTokenSerializer
from apps.access.session_services import refresh_auth_session, revoke_current_session
from apps.api_contracts.openapi import standard_empty_response


class EmptyResponseSerializer(serializers.Serializer):
    pass


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
        summary="刷新 v2 IAM 会话",
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
        summary="撤销当前 v2 IAM 会话",
        description="请求头必须携带 `Authorization: Bearer <accessToken>`；请求体固定为空对象或无请求体。",
    )
    def post(self, request):
        ensure_empty_body(request)
        revoke_current_session(request.auth)
        return Response(None, status=status.HTTP_200_OK)
