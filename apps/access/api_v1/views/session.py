from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema

from apps.access.api_v1.base import IamAPIView, ensure_empty_body
from apps.access.api_v1.openapi import IAM_BEARER_AUTH
from apps.access.api_v1.serializers.common import StaffProfileSerializer
from apps.access.api_v1.serializers.session import (
    LoginRequestSerializer,
    LoginResponseSerializer,
    RefreshRequestSerializer,
    RegisterByPhoneRequestSerializer,
    RegisterRequestSerializer,
    RegisterResponseSerializer,
    SessionTokenSerializer,
)
from apps.access.api_v1.services.auth import (
    authenticate_formal_user,
    create_auth_session,
    refresh_auth_session,
    register_by_phone,
    register_by_username,
    revoke_current_session,
)
from apps.api_v1.schema import (
    BUSINESS_INTERNAL_ERROR_RESPONSE,
    business_error_example,
    business_error_response,
    empty_envelope_serializer,
    object_envelope_serializer,
)


SESSION_LOGIN_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": {
        "accessToken": "access_token_example",
        "refreshToken": "refresh_token_example",
        "tokenType": "Bearer",
        "expiresIn": 7200,
        "refreshExpiresIn": 604800,
        "user": {
            "userId": 1001,
            "username": "demo_user",
            "status": "ACTIVE",
            "hasPlatformAccess": False,
            "staffProfile": {
                "name": "张三",
                "phone": "13800138000",
                "email": "demo@example.com",
                "employmentStatus": "ACTIVE",
                "orgId": 10,
            },
        },
    },
}

SESSION_REFRESH_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": {
        "accessToken": "rotated_access_token_example",
        "refreshToken": "rotated_refresh_token_example",
        "tokenType": "Bearer",
        "expiresIn": 7200,
        "refreshExpiresIn": 604800,
    },
}

SESSION_LOGOUT_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": None,
}

SESSION_REGISTER_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": {
        "userId": 1002,
        "username": "new_user",
        "status": "ACTIVE",
        "createdAt": "2026-03-19T10:00:00+08:00",
        "updatedAt": "2026-03-19T10:00:00+08:00",
        "staffProfile": {
            "name": "李四",
            "phone": "13900139000",
            "email": "",
            "employmentStatus": "ACTIVE",
            "orgId": None,
        },
    },
}

SESSION_REGISTER_BY_PHONE_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": {
        "userId": 1003,
        "username": "13900139000",
        "status": "ACTIVE",
        "createdAt": "2026-03-19T10:05:00+08:00",
        "updatedAt": "2026-03-19T10:05:00+08:00",
        "staffProfile": {
            "name": "13900139000",
            "phone": "13900139000",
            "email": "",
            "employmentStatus": "ACTIVE",
            "orgId": None,
        },
    },
}

SESSION_LOGIN_INVALID_RESPONSE = business_error_response(
    description="登录请求体缺少必填字段或字段格式不合法时返回 400 + B0001。",
    examples=[
        business_error_example(
            "缺少用户名",
            code="B0001",
            msg="参数校验失败",
            status_codes=["400"],
            data={"username": ["该字段是必填项。"]},
        )
    ],
)

SESSION_LOGIN_UNAUTHORIZED_RESPONSE = business_error_response(
    description=(
        "用户名或密码错误、账号不属于正式 IAM 账号集合，或账号状态为 DISABLED 时，"
        "统一返回 401 + A0401。"
    ),
    examples=[
        business_error_example(
            "账号不可登录",
            code="A0401",
            msg="登录状态已失效",
            status_codes=["401"],
        )
    ],
)

SESSION_REFRESH_INVALID_RESPONSE = business_error_response(
    description="refreshToken 缺失或格式非法时返回 400 + B0001。",
    examples=[
        business_error_example(
            "缺少 refreshToken",
            code="B0001",
            msg="参数校验失败",
            status_codes=["400"],
            data={"refreshToken": ["该字段是必填项。"]},
        )
    ],
)

SESSION_REFRESH_UNAUTHORIZED_RESPONSE = business_error_response(
    description="refreshToken 无效、过期、已轮换淘汰、已被撤销，或所属账号状态已变为 DISABLED 时返回 401 + A0401。",
    examples=[
        business_error_example(
            "refreshToken 无效或过期",
            code="A0401",
            msg="登录状态已失效",
            status_codes=["401"],
        )
    ],
)

SESSION_LOGOUT_INVALID_RESPONSE = business_error_response(
    description="logout 请求体必须为空对象或不带请求体；提交业务字段时返回 400 + B0001。",
    examples=[
        business_error_example(
            "logout 带业务字段",
            code="B0001",
            msg="参数校验失败",
            status_codes=["400"],
            data={"body": ["该接口不接受业务字段"]},
        )
    ],
)

SESSION_REGISTER_INVALID_RESPONSE = business_error_response(
    description="注册请求体缺少必填字段或字段格式不合法时返回 400 + B0001。",
    examples=[
        business_error_example(
            "缺少手机号",
            code="B0001",
            msg="参数校验失败",
            status_codes=["400"],
            data={"phone": ["该字段是必填项。"]},
        )
    ],
)

SESSION_REGISTER_DUPLICATE_RESPONSE = business_error_response(
    description="用户名已存在时返回 409 + C0101。",
    examples=[
        business_error_example(
            "用户名已存在",
            code="C0101",
            msg="用户名已存在",
            status_codes=["409"],
        )
    ],
)

SESSION_REGISTER_PHONE_DUPLICATE_RESPONSE = business_error_response(
    description="手机号已存在时返回 409 + C0102。",
    examples=[
        business_error_example(
            "手机号已存在",
            code="C0102",
            msg="手机号已存在",
            status_codes=["409"],
        )
    ],
)

SESSION_REGISTER_BY_PHONE_INVALID_RESPONSE = business_error_response(
    description="手机号注册请求体缺少字段、字段格式不合法，或短信验证码错误/失效时返回 400 + B0001。",
    examples=[
        business_error_example(
            "短信验证码错误",
            code="B0001",
            msg="参数校验失败",
            status_codes=["400"],
            data={"smsCode": ["短信验证码错误或失效"]},
        )
    ],
)



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
        examples=[
            OpenApiExample(
                "登录请求示例",
                request_only=True,
                value={"username": "demo_user", "password": "pass1234"},
            )
        ],
        responses={
            200: OpenApiResponse(
                response=object_envelope_serializer("IamSessionLoginEnvelope", LoginResponseSerializer),
                description=(
                    "登录成功。返回当前认证会话的 accessToken / refreshToken；"
                    "`user.status` 的状态域固定为 ACTIVE、DISABLED，但本接口成功响应实际固定返回 ACTIVE。"
                ),
                examples=[
                    OpenApiExample(
                        "登录成功示例",
                        response_only=True,
                        status_codes=["200"],
                        value=SESSION_LOGIN_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: SESSION_LOGIN_INVALID_RESPONSE,
            401: SESSION_LOGIN_UNAUTHORIZED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="正式 IAM 登录",
        description=(
            "正式 IAM 会话登录接口。认证成功后签发可撤销认证会话对应的 Bearer Token。"
            "`hasPlatformAccess` 仅表示当前登录账号是否具备调用 `platform/*` 的能力，"
            "不表示正式角色模板或运行态字段。"
        ),
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
        examples=[
            OpenApiExample(
                "刷新请求示例",
                request_only=True,
                value={"refreshToken": "refresh_token_example"},
            )
        ],
        responses={
            200: OpenApiResponse(
                response=object_envelope_serializer("IamSessionRefreshEnvelope", SessionTokenSerializer),
                description="刷新成功。服务端固定执行 refresh token rotation，旧 refreshToken 立即失效。",
                examples=[
                    OpenApiExample(
                        "刷新成功示例",
                        response_only=True,
                        status_codes=["200"],
                        value=SESSION_REFRESH_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: SESSION_REFRESH_INVALID_RESPONSE,
            401: SESSION_REFRESH_UNAUTHORIZED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="刷新正式 IAM 会话",
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
        auth=IAM_BEARER_AUTH,
        request=None,
        responses={
            200: OpenApiResponse(
                response=empty_envelope_serializer("IamSessionLogoutEnvelope"),
                description="登出成功。服务端撤销当前认证会话，当前会话下尚未过期的 accessToken 与 refreshToken 一并失效。",
                examples=[
                    OpenApiExample(
                        "登出成功示例",
                        response_only=True,
                        status_codes=["200"],
                        value=SESSION_LOGOUT_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: SESSION_LOGOUT_INVALID_RESPONSE,
            401: SESSION_REFRESH_UNAUTHORIZED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="撤销当前正式 IAM 会话",
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
        examples=[
            OpenApiExample(
                "用户名注册请求示例",
                request_only=True,
                value={
                    "username": "new_user",
                    "password": "pass1234",
                    "name": "李四",
                    "phone": "13900139000",
                },
            )
        ],
        responses={
            201: OpenApiResponse(
                response=object_envelope_serializer("IamSessionRegisterEnvelope", RegisterResponseSerializer),
                description="注册成功。创建正式 IAM 业务账号，但不自动登录，不返回 token；初始运行态固定为 unassigned。",
                examples=[
                    OpenApiExample(
                        "用户名注册成功示例",
                        response_only=True,
                        status_codes=["201"],
                        value=SESSION_REGISTER_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: SESSION_REGISTER_INVALID_RESPONSE,
            409: OpenApiResponse(
                response=None,
                description="用户名或手机号发生唯一键冲突时返回 409；用户名冲突为 C0101，手机号冲突为 C0102。",
                examples=[
                    business_error_example(
                        "用户名已存在",
                        code="C0101",
                        msg="用户名已存在",
                        status_codes=["409"],
                    ),
                    business_error_example(
                        "手机号已存在",
                        code="C0102",
                        msg="手机号已存在",
                        status_codes=["409"],
                    ),
                ],
            ),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="用户名方式注册正式 IAM 业务账号",
        description="注册成功后账号状态固定写入 ACTIVE；用户后续需显式调用 `POST /api/v1/iam/session/login` 获取 Bearer Token。",
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
        examples=[
            OpenApiExample(
                "手机号注册请求示例",
                request_only=True,
                value={"phone": "13900139000", "smsCode": "123456", "password": "pass1234"},
            )
        ],
        responses={
            201: OpenApiResponse(
                response=object_envelope_serializer("IamSessionRegisterByPhoneEnvelope", RegisterResponseSerializer),
                description="注册成功。服务端以手机号作为登录用户名，不自动登录，不返回 token；初始运行态固定为 unassigned。",
                examples=[
                    OpenApiExample(
                        "手机号注册成功示例",
                        response_only=True,
                        status_codes=["201"],
                        value=SESSION_REGISTER_BY_PHONE_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: SESSION_REGISTER_BY_PHONE_INVALID_RESPONSE,
            409: SESSION_REGISTER_PHONE_DUPLICATE_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="手机号方式注册正式 IAM 业务账号",
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
