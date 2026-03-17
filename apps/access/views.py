from datetime import datetime

from django.contrib.auth import authenticate, login, logout
from django.utils import timezone
from rest_framework import generics, serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view, inline_serializer

from apps.access.drf_permissions import PermissionMapMixin, RequireInternalPermission
from apps.access.exceptions import BusinessIdempotentDuplicate, BusinessPermissionDenied, BusinessResourceNotFound, BusinessStateConflict
from apps.access.models import (
    AuditLog,
    Permission,
    Role,
    Tenant,
    TenantMember,
    TenantMemberRoleStatus,
    TenantMemberStatus,
    TenantStatus,
    User,
)
from apps.access.serializers import (
    AuditLogSerializer,
    CurrentUserInvitationSerializer,
    CurrentUserTenantSerializer,
    MePermissionSerializer,
    PermissionCodeSerializer,
    RoleSerializer,
    TenantInitializeAdminSerializer,
    TenantMemberConfirmInvitationSerializer,
    TenantMemberCreateSerializer,
    TenantMemberInviteSerializer,
    TenantMemberRejectInvitationSerializer,
    TenantMemberRoleAssignSerializer,
    TenantMemberSerializer,
    TenantSetPlanSerializer,
    TenantSerializer,
    UserManageSerializer,
    UserPhoneRegisterSerializer,
    UserSelfRegisterSerializer,
)
from apps.access.services import (
    AuthzService,
    expire_stale_tenant_member_invitations,
    is_active_platform_admin,
    log_action,
    snapshot,
)


def _validation_error_response(exc: serializers.ValidationError) -> Response:
    detail = exc.detail
    payload = dict(detail) if isinstance(detail, dict) else {"error": detail}
    payload.setdefault("business_code", "INVALID_PARAMS")
    payload.setdefault("business_detail_code", "VALIDATION_ERROR")
    return Response(payload, status=status.HTTP_400_BAD_REQUEST)


def _user_payload(user: User) -> dict:
    payload = {
        "id": user.id,
        "username": user.username,
        "is_active": user.is_active,
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
        "is_platform_admin": user.is_platform_admin,
        "status": user.status,
    }
    staff = getattr(user, "staff_profile", None)
    if staff:
        payload["staff"] = {
            "id": staff.id,
            "name": staff.name,
            "phone": staff.phone,
            "email": staff.email,
            "employment_status": staff.employment_status,
            "org_id": staff.org_id,
        }
    else:
        payload["staff"] = None
    return payload


def _parse_iso_datetime(value: str):
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _get_current_tenant_member_context(request):
    tenant = getattr(request, "tenant_context", None)
    if tenant is None:
        raise BusinessPermissionDenied("tenant context required", business_detail_code="TENANT_CONTEXT_REQUIRED")
    if tenant.status != TenantStatus.ACTIVE:
        raise BusinessPermissionDenied("tenant inactive", business_detail_code="TENANT_INACTIVE")

    member = (
        TenantMember.objects.filter(
            tenant=tenant,
            user=request.user,
            status=TenantMemberStatus.ACTIVE,
        )
        .select_related("tenant", "user")
        .prefetch_related("role_bindings__system_role")
        .first()
    )
    if member is None:
        raise BusinessPermissionDenied("active tenant membership required", business_detail_code="TENANT_MEMBERSHIP_REQUIRED")

    role_codes = list(
        member.role_bindings.filter(status=TenantMemberRoleStatus.GRANTED)
        .order_by("id")
        .values_list("system_role__code", flat=True)
    )
    return tenant, member, role_codes


def _scope_internal_queryset_to_current_tenant(request, queryset, *, lookup: str):
    if request.user.is_superuser or is_active_platform_admin(request.user):
        return queryset
    tenant, _member, _role_codes = _get_current_tenant_member_context(request)
    return queryset.filter(**{lookup: tenant})


def _scope_tenant_queryset_to_current_tenant(request, queryset):
    if request.user.is_superuser or is_active_platform_admin(request.user):
        return queryset
    tenant, _member, _role_codes = _get_current_tenant_member_context(request)
    return queryset.filter(id=tenant.id)


def _scope_audit_log_queryset(request, queryset):
    if request.user.is_superuser:
        return queryset
    if is_active_platform_admin(request.user):
        return queryset.filter(tenant__isnull=True)
    tenant, _member, _role_codes = _get_current_tenant_member_context(request)
    return queryset.filter(tenant=tenant)


def _internal_success_serializer(name: str, extra_fields: dict | None = None):
    fields = {
        "business_code": serializers.CharField(help_text="内部接口业务码。成功固定为 SUCCESS。"),
        "business_detail_code": serializers.CharField(help_text="内部接口细分业务码。成功固定为 OK。"),
    }
    if extra_fields:
        fields.update(extra_fields)
    return inline_serializer(name=name, fields=fields)


LOGIN_REQUEST_SERIALIZER = inline_serializer(
    name="InternalLoginRequest",
    fields={
        "username": serializers.CharField(help_text="用户名。"),
        "password": serializers.CharField(help_text="密码。", trim_whitespace=False),
    },
)


TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER = OpenApiParameter(
    name="X-TENANT-CODE",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=False,
    description=(
        "当前租户编码。租户成员访问租户侧 internal IAM 接口时通常必填；"
        "superuser 与 platform_admin 访问平台目录或平台租户接口时可不传。"
    ),
)


TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER = OpenApiParameter(
    name="X-TENANT-CODE",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description="当前租户编码。该接口只允许在明确租户上下文中调用。",
)


PERMISSION_MODULE_PARAMETER = OpenApiParameter(
    name="module",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    required=False,
    description="按权限模块过滤，例如 access、mission、drone。",
)


TENANT_MEMBER_TENANT_ID_PARAMETER = OpenApiParameter(
    name="tenant_id",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.QUERY,
    required=False,
    description="按租户 ID 过滤成员列表。租户管理员通常只会查询当前租户。",
)


TENANT_MEMBER_STATUS_PARAMETER = OpenApiParameter(
    name="status",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    required=False,
    description="按成员状态过滤，例如 ACTIVE、INVITED、DISABLED。",
)


AUDIT_LOG_FILTER_PARAMETERS = [
    OpenApiParameter(name="action", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="按审计动作过滤。"),
    OpenApiParameter(name="target_type", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="按目标资源类型过滤。"),
    OpenApiParameter(name="actor_user_id", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False, description="按操作者用户 ID 过滤。"),
    OpenApiParameter(name="request_id", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="按请求链路 ID 过滤。"),
    OpenApiParameter(
        name="date_from",
        type=OpenApiTypes.DATETIME,
        location=OpenApiParameter.QUERY,
        required=False,
        description="起始时间，ISO 8601 格式，例如 2026-03-17T09:00:00+08:00。",
    ),
    OpenApiParameter(
        name="date_to",
        type=OpenApiTypes.DATETIME,
        location=OpenApiParameter.QUERY,
        required=False,
        description="结束时间，ISO 8601 格式，例如 2026-03-17T18:00:00+08:00。",
    ),
]


INTERNAL_AUTH_REQUIRED_RESPONSE = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="未登录、会话已失效，或认证信息无效。",
    examples=[
        OpenApiExample(
            "未登录",
            value={
                "business_code": "PERMISSION_DENIED",
                "business_detail_code": "NOT_AUTHENTICATED",
                "detail": "Authentication credentials were not provided.",
            },
            response_only=True,
            status_codes=["401"],
        )
    ],
)


INTERNAL_PERMISSION_DENIED_RESPONSE = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="已登录但无权执行该操作，或缺少合法租户上下文。",
    examples=[
        OpenApiExample(
            "租户上下文缺失",
            value={
                "business_code": "PERMISSION_DENIED",
                "business_detail_code": "TENANT_CONTEXT_REQUIRED",
                "detail": "tenant context required",
            },
            response_only=True,
            status_codes=["403"],
        )
    ],
)


INTERNAL_VALIDATION_ERROR_RESPONSE = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="请求参数校验失败。不同接口可能返回字段级错误键值或简单错误描述。",
    examples=[
        OpenApiExample(
            "参数校验失败",
            value={
                "business_code": "INVALID_PARAMS",
                "business_detail_code": "VALIDATION_ERROR",
                "phone": ["该字段不能为空"],
            },
            response_only=True,
            status_codes=["400"],
        )
    ],
)


INTERNAL_INTERNAL_ERROR_RESPONSE = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="服务端内部异常。",
    examples=[
        OpenApiExample(
            "内部错误",
            value={
                "business_code": "INTERNAL_ERROR",
                "business_detail_code": "INTERNAL_ERROR",
                "detail": "internal server error",
            },
            response_only=True,
            status_codes=["500"],
        )
    ],
)


TENANT_NOT_FOUND_RESPONSE = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="租户不存在。",
    examples=[
        OpenApiExample(
            "租户不存在",
            value={
                "business_code": "RESOURCE_NOT_FOUND",
                "business_detail_code": "TENANT_NOT_FOUND",
                "detail": "tenant not found",
            },
            response_only=True,
            status_codes=["404"],
        )
    ],
)


TENANT_MEMBER_NOT_FOUND_RESPONSE = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="租户成员不存在。",
    examples=[
        OpenApiExample(
            "成员不存在",
            value={
                "business_code": "RESOURCE_NOT_FOUND",
                "business_detail_code": "TENANT_MEMBER_NOT_FOUND",
                "detail": "tenant member not found",
            },
            response_only=True,
            status_codes=["404"],
        )
    ],
)


INVITATION_NOT_FOUND_RESPONSE = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="邀请 token 不存在、已过期后被清理，或不属于当前用户。",
    examples=[
        OpenApiExample(
            "邀请不存在",
            value={
                "business_code": "RESOURCE_NOT_FOUND",
                "business_detail_code": "INVITATION_NOT_FOUND",
                "detail": "invitation not found",
            },
            response_only=True,
            status_codes=["404"],
        )
    ],
)


STATE_CONFLICT_RESPONSE = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="资源当前状态不允许执行该动作。",
    examples=[
        OpenApiExample(
            "状态冲突",
            value={
                "business_code": "STATE_CONFLICT",
                "business_detail_code": "STATE_CONFLICT",
                "detail": "state conflict",
            },
            response_only=True,
            status_codes=["409"],
        )
    ],
)


IDEMPOTENT_DUPLICATE_RESPONSE = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="重复提交或请求目标已经处于期望状态。",
    examples=[
        OpenApiExample(
            "重复请求",
            value={
                "business_code": "IDEMPOTENT_DUPLICATE",
                "business_detail_code": "DUPLICATE_REQUEST",
                "detail": "duplicate request",
            },
            response_only=True,
            status_codes=["409"],
        )
    ],
)


LOGIN_RESPONSE_SERIALIZER = _internal_success_serializer(
    "InternalLoginResponse",
    {
        "username": serializers.CharField(),
        "is_superuser": serializers.BooleanField(),
        "is_platform_admin": serializers.BooleanField(),
    },
)


REGISTER_RESPONSE_SERIALIZER = _internal_success_serializer(
    "InternalRegisterResponse",
    {
        "user_id": serializers.IntegerField(),
        "username": serializers.CharField(),
    },
)


ME_PERMISSIONS_RESPONSE_SERIALIZER = _internal_success_serializer(
    "InternalMePermissionsResponse",
    {
        "tenant_code": serializers.CharField(allow_null=True),
        "roles": serializers.ListField(child=serializers.CharField()),
        "items": MePermissionSerializer(many=True),
    },
)


ME_TENANTS_RESPONSE_SERIALIZER = _internal_success_serializer(
    "InternalMeTenantListResponse",
    {
        "username": serializers.CharField(),
        "tenants": CurrentUserTenantSerializer(many=True),
        "default_tenant": serializers.IntegerField(allow_null=True),
    },
)


ME_INVITATIONS_RESPONSE_SERIALIZER = _internal_success_serializer(
    "InternalMeInvitationListResponse",
    {
        "items": CurrentUserInvitationSerializer(many=True),
    },
)


TENANT_AUDIT_LOG_LIST_RESPONSE_SERIALIZER = _internal_success_serializer(
    "InternalTenantAuditLogListResponse",
    {
        "count": serializers.IntegerField(),
        "data": AuditLogSerializer(many=True),
    },
)


TENANT_LIST_RESPONSE_SERIALIZER = _internal_success_serializer(
    "InternalTenantListResponse",
    {
        "data": TenantSerializer(many=True),
        "count": serializers.IntegerField(),
    },
)


TENANT_DETAIL_RESPONSE_SERIALIZER = _internal_success_serializer(
    "InternalTenantDetailResponse",
    {
        "data": TenantSerializer(),
    },
)


TENANT_CREATE_RESPONSE_SERIALIZER = _internal_success_serializer(
    "InternalTenantCreateResponse",
    {
        "id": serializers.IntegerField(),
        "code": serializers.CharField(),
        "name": serializers.CharField(),
        "status": serializers.CharField(),
    },
)


TENANT_STATE_RESPONSE_SERIALIZER = _internal_success_serializer(
    "InternalTenantStateResponse",
    {
        "id": serializers.IntegerField(),
        "code": serializers.CharField(),
        "name": serializers.CharField(),
        "status": serializers.CharField(),
    },
)


TENANT_INITIALIZE_ADMIN_RESPONSE_SERIALIZER = _internal_success_serializer(
    "InternalTenantInitializeAdminResponse",
    {
        "member_id": serializers.IntegerField(),
        "tenant_id": serializers.IntegerField(),
        "user_id": serializers.IntegerField(),
        "roles": serializers.ListField(child=serializers.CharField()),
        "status": serializers.CharField(),
        "message": serializers.CharField(),
    },
)


TENANT_SET_PLAN_RESPONSE_SERIALIZER = _internal_success_serializer(
    "InternalTenantSetPlanResponse",
    {
        "id": serializers.IntegerField(),
        "code": serializers.CharField(),
        "name": serializers.CharField(),
        "status": serializers.CharField(),
        "plan": serializers.CharField(),
    },
)


TENANT_MEMBER_INVITE_RESPONSE_SERIALIZER = _internal_success_serializer(
    "InternalTenantMemberInviteResponse",
    {
        "member_id": serializers.IntegerField(),
        "invitation_token": serializers.CharField(),
        "message": serializers.CharField(),
    },
)


TENANT_MEMBER_CONFIRM_RESPONSE_SERIALIZER = _internal_success_serializer(
    "InternalTenantMemberConfirmInvitationResponse",
    {
        "member_id": serializers.IntegerField(),
        "roles": serializers.ListField(child=serializers.CharField()),
        "message": serializers.CharField(),
    },
)


TENANT_MEMBER_MESSAGE_RESPONSE_SERIALIZER = _internal_success_serializer(
    "InternalTenantMemberMessageResponse",
    {
        "message": serializers.CharField(),
    },
)


TENANT_MEMBER_STATUS_RESPONSE_SERIALIZER = _internal_success_serializer(
    "InternalTenantMemberStatusResponse",
    {
        "member_id": serializers.IntegerField(),
        "tenant_id": serializers.IntegerField(),
        "user_id": serializers.IntegerField(),
        "status": serializers.CharField(),
    },
)


@extend_schema_view(
    get=extend_schema(
        tags=["当前用户"],
        summary="获取当前会话权限矩阵",
        description=(
            "返回当前登录账号在 internal IAM 侧的有效权限列表。"
            "租户成员会基于 `X-TENANT-CODE` 对应的租户角色计算权限；"
            "platform_admin 与 superuser 可在无租户上下文下查询自身平台权限。"
        ),
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=ME_PERMISSIONS_RESPONSE_SERIALIZER,
                description="当前会话的权限矩阵。",
                examples=[
                    OpenApiExample(
                        "租户成员权限矩阵",
                        value={
                            "business_code": "SUCCESS",
                            "business_detail_code": "OK",
                            "tenant_code": "tenant_demo",
                            "roles": ["tenant_admin"],
                            "items": [
                                {"permission": "access.view_tenant_member", "scope": "ALL", "enabled": True},
                                {"permission": "drone.view_drone", "scope": "ALL", "enabled": True},
                            ],
                        },
                        response_only=True,
                        status_codes=["200"],
                    )
                ],
            ),
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
)
class MePermissionsView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = MePermissionSerializer

    def get(self, request):
        tenant = getattr(request, "tenant_context", None)
        if tenant is None and not (request.user.is_superuser or is_active_platform_admin(request.user)):
            raise BusinessPermissionDenied("tenant context required", business_detail_code="TENANT_CONTEXT_REQUIRED")

        if request.user.is_superuser:
            permissions = AuthzService.get_permission_scope_map(request)
            role_codes: list[str] = []
        elif is_active_platform_admin(request.user):
            permissions = AuthzService.get_permission_scope_map(request)
            role_codes = ["platform_admin"]
        else:
            tenant, _member, role_codes = _get_current_tenant_member_context(request)
            permissions = AuthzService.get_tenant_role_permission_scope_map(role_codes)

        serializer = self.get_serializer(permissions, many=True)
        return Response(
            {
                "business_code": "SUCCESS",
                "business_detail_code": "OK",
                "tenant_code": tenant.code if tenant is not None else None,
                "roles": role_codes,
                "items": serializer.data,
            }
        )


@extend_schema_view(
    get=extend_schema(
        tags=["当前用户"],
        summary="获取当前用户可切换租户列表",
        description="返回当前账号所有 ACTIVE 状态的租户成员关系及其已授予角色编码，用于登录后租户选择。",
        responses={
            200: OpenApiResponse(
                response=ME_TENANTS_RESPONSE_SERIALIZER,
                description="当前用户可访问的租户列表。",
                examples=[
                    OpenApiExample(
                        "租户列表",
                        value={
                            "business_code": "SUCCESS",
                            "business_detail_code": "OK",
                            "username": "zhangsan",
                            "tenants": [
                                {
                                    "tenant_id": 1,
                                    "tenant_code": "tenant_a",
                                    "tenant_name": "租户A",
                                    "roles": ["tenant_admin"],
                                },
                                {
                                    "tenant_id": 2,
                                    "tenant_code": "tenant_b",
                                    "tenant_name": "租户B",
                                    "roles": ["pilot_operator"],
                                },
                            ],
                            "default_tenant": 1,
                        },
                        response_only=True,
                        status_codes=["200"],
                    )
                ],
            ),
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
)
class MeTenantListView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CurrentUserTenantSerializer

    def get(self, request):
        memberships = (
            TenantMember.objects.filter(
                user=request.user,
                status=TenantMemberStatus.ACTIVE,
                tenant__status=TenantStatus.ACTIVE,
            )
            .select_related("tenant")
            .prefetch_related("role_bindings__system_role")
            .order_by("tenant_id", "id")
        )
        tenants = self.get_serializer(memberships, many=True).data
        default_tenant = tenants[0]["tenant_id"] if tenants else None
        return Response(
            {
                "business_code": "SUCCESS",
                "business_detail_code": "OK",
                "username": request.user.username,
                "tenants": tenants,
                "default_tenant": default_tenant,
            }
        )


@extend_schema_view(
    get=extend_schema(
        tags=["当前用户"],
        summary="获取当前用户待处理邀请",
        description="返回当前账号尚未确认的租户邀请。查询前会先清理已经超时的邀请。",
        responses={
            200: OpenApiResponse(
                response=ME_INVITATIONS_RESPONSE_SERIALIZER,
                description="当前用户的邀请列表。",
                examples=[
                    OpenApiExample(
                        "邀请列表",
                        value={
                            "business_code": "SUCCESS",
                            "business_detail_code": "OK",
                            "items": [
                                {
                                    "member_id": 12,
                                    "tenant_id": 3,
                                    "tenant_code": "tenant_invite",
                                    "tenant_name": "邀请租户",
                                    "display_name": "张三",
                                    "roles": ["pilot_operator"],
                                    "qualifications": [],
                                    "invitation_token": "f8c2fdb7d1c346f0a9bc9ab8b7fa6c64",
                                    "invited_at": "2026-03-17T10:00:00+08:00",
                                    "expires_at": "2026-03-24T10:00:00+08:00",
                                }
                            ],
                        },
                        response_only=True,
                        status_codes=["200"],
                    )
                ],
            ),
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
)
class MeInvitationListView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CurrentUserInvitationSerializer

    def get(self, request):
        expire_stale_tenant_member_invitations()
        invitations = (
            TenantMember.objects.filter(
                user=request.user,
                status=TenantMemberStatus.INVITED,
                invitation_token__isnull=False,
            )
            .exclude(invitation_token="")
            .select_related("tenant")
            .prefetch_related("role_bindings__system_role", "qualifications__qualification_type")
            .order_by("tenant_id", "id")
        )
        items = self.get_serializer(invitations, many=True).data
        return Response(
            {
                "business_code": "SUCCESS",
                "business_detail_code": "OK",
                "items": items,
            }
        )


class ApiRootView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(exclude=True)
    def get(self, request):
        return Response(
            {
                "name": "Internal IAM API",
                "endpoints": {
                    "all_docs": reverse("docs", request=request),
                    "all_docs_schema": reverse("docs-schema", request=request),
                    "swagger": reverse("internal-docs", request=request),
                    "business_api_v1": reverse("api-v1-root", request=request),
                    "business_docs_v1": reverse("business-docs", request=request),
                    "session_status": reverse("session-status", request=request),
                    "users": reverse("user-list-create", request=request),
                    "me_invitations": reverse("me-invitation-list", request=request),
                    "me_invitation_reject": reverse("me-invitation-reject", request=request),
                    "me_tenants": reverse("me-tenant-list", request=request),
                    "me_permissions": reverse("me-permissions", request=request),
                    "permission_catalog": reverse("permission-catalog", request=request),
                    "roles": reverse("role-list", request=request),
                    "audit_logs": reverse("audit-log-list", request=request),
                    "tenant_audit_logs": reverse("tenant-audit-log-list", request=request),
                },
            }
        )


class SessionStatusView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(exclude=True)
    def get(self, request):
        user = request.user
        if user and user.is_authenticated:
            return Response(
                {
                    "is_authenticated": True,
                    "username": user.username,
                    "is_superuser": bool(user.is_superuser),
                    "is_platform_admin": bool(user.is_platform_admin),
                }
            )
        return Response({"is_authenticated": False})


class LoginView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["认证与会话"],
        summary="用户名密码登录",
        description="创建 internal IAM 的 Django Session。成功后可继续访问 `/internal/auth/*` 系列接口。",
        request=LOGIN_REQUEST_SERIALIZER,
        responses={
            200: OpenApiResponse(
                response=LOGIN_RESPONSE_SERIALIZER,
                description="登录成功。",
                examples=[
                    OpenApiExample(
                        "登录成功",
                        value={
                            "business_code": "SUCCESS",
                            "business_detail_code": "OK",
                            "username": "admin",
                            "is_superuser": False,
                            "is_platform_admin": True,
                        },
                        response_only=True,
                        status_codes=["200"],
                    )
                ],
            ),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="用户名或密码缺失。",
                examples=[
                    OpenApiExample(
                        "缺少必填参数",
                        value={
                            "business_code": "INVALID_PARAMS",
                            "business_detail_code": "VALIDATION_ERROR",
                            "error": "username and password required",
                        },
                        response_only=True,
                        status_codes=["400"],
                    )
                ],
            ),
            401: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="用户名或密码错误。",
                examples=[
                    OpenApiExample(
                        "凭证错误",
                        value={
                            "business_code": "PERMISSION_DENIED",
                            "business_detail_code": "INVALID_CREDENTIALS",
                            "error": "invalid username or password",
                        },
                        response_only=True,
                        status_codes=["401"],
                    )
                ],
            ),
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
    def post(self, request):
        username = request.data.get("username")
        password = request.data.get("password")

        if not username or not password:
            return Response(
                {"business_code": "INVALID_PARAMS", "business_detail_code": "VALIDATION_ERROR", "error": "username and password required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = authenticate(request, username=username, password=password)
        if user is None:
            return Response(
                {"business_code": "PERMISSION_DENIED", "business_detail_code": "INVALID_CREDENTIALS", "error": "invalid username or password"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        login(request, user)
        return Response(
            {
                "business_code": "SUCCESS",
                "business_detail_code": "OK",
                "username": user.username,
                "is_superuser": user.is_superuser,
                "is_platform_admin": user.is_platform_admin,
            }
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["认证与会话"],
        summary="退出登录",
        description="清理当前 Session，会话态退出 internal IAM。",
        request=None,
        responses={
            200: OpenApiResponse(
                response=_internal_success_serializer("InternalLogoutResponse"),
                description="退出成功。",
            ),
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
    def post(self, request):
        logout(request)
        return Response({"business_code": "SUCCESS", "business_detail_code": "OK"})


@extend_schema_view(
    post=extend_schema(
        tags=["认证与会话"],
        summary="用户名注册账号",
        description="创建基础用户账号与全局 `StaffProfile` 档案，适用于未登录用户自助注册。",
        request=UserSelfRegisterSerializer,
        responses={
            201: OpenApiResponse(
                response=REGISTER_RESPONSE_SERIALIZER,
                description="注册成功。",
                examples=[
                    OpenApiExample(
                        "注册成功",
                        value={
                            "business_code": "SUCCESS",
                            "business_detail_code": "OK",
                            "user_id": 101,
                            "username": "register_user",
                        },
                        response_only=True,
                        status_codes=["201"],
                    )
                ],
            ),
            400: INTERNAL_VALIDATION_ERROR_RESPONSE,
            409: IDEMPOTENT_DUPLICATE_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
)
class UserSelfRegisterView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = UserSelfRegisterSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except serializers.ValidationError:
            return Response(
                {"business_code": "INVALID_PARAMS", "business_detail_code": "VALIDATION_ERROR"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = serializer.save()
        log_action(
            request=request,
            action="USER_REGISTER",
            target_type="user",
            target_id=user.id,
            after_data=_user_payload(user),
            actor_user=user,
        )
        return Response(
            {
                "business_code": "SUCCESS",
                "business_detail_code": "OK",
                "user_id": user.id,
                "username": user.username,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    post=extend_schema(
        tags=["认证与会话"],
        summary="手机号注册账号",
        description="使用 mock 短信验证码注册账号。当前阶段 `sms_code` 固定为 `123456`。",
        request=UserPhoneRegisterSerializer,
        responses={
            201: OpenApiResponse(
                response=REGISTER_RESPONSE_SERIALIZER,
                description="注册成功。",
                examples=[
                    OpenApiExample(
                        "手机号注册成功",
                        value={
                            "business_code": "SUCCESS",
                            "business_detail_code": "OK",
                            "user_id": 102,
                            "username": "13800138002",
                        },
                        response_only=True,
                        status_codes=["201"],
                    )
                ],
            ),
            400: INTERNAL_VALIDATION_ERROR_RESPONSE,
            409: IDEMPOTENT_DUPLICATE_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
)
class UserPhoneRegisterView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = UserPhoneRegisterSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except serializers.ValidationError:
            return Response(
                {"business_code": "INVALID_PARAMS", "business_detail_code": "VALIDATION_ERROR"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = serializer.save()
        log_action(
            request=request,
            action="USER_REGISTER_BY_PHONE",
            target_type="user",
            target_id=user.id,
            after_data=_user_payload(user),
            actor_user=user,
        )
        return Response(
            {
                "business_code": "SUCCESS",
                "business_detail_code": "OK",
                "user_id": user.id,
                "username": user.username,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    get=extend_schema(
        tags=["用户管理"],
        summary="查询用户账号列表",
        description=(
            "返回 internal IAM 用户列表。superuser 可查看全部；platform_admin 可查看平台维度账号；"
            "租户管理员只会看到当前租户内已有成员关系的账号。"
        ),
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    ),
    post=extend_schema(
        tags=["用户管理"],
        summary="创建用户账号",
        description="创建用户账号，并可同时写入全局 `StaffProfile`。该接口返回原始用户对象，不带 `business_code` 包装。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        request=UserManageSerializer,
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    ),
)
class UserListCreateView(PermissionMapMixin, generics.ListCreateAPIView):
    serializer_class = UserManageSerializer
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "GET": "access.view_user",
        "POST": "access.manage_user_accounts",
    }
    queryset = User.objects.select_related("staff_profile").all().order_by("id")

    @extend_schema(
        tags=["用户管理"],
        summary="查询用户账号列表",
        description=(
            "返回 internal IAM 用户列表。superuser 可查看全部；platform_admin 可查看平台维度账号；"
            "租户管理员只会看到当前租户内已有成员关系的账号。"
        ),
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        tags=["用户管理"],
        summary="创建用户账号",
        description="创建用户账号，并可同时写入全局 `StaffProfile`。该接口返回原始用户对象，不带 `business_code` 包装。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        request=UserManageSerializer,
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = _scope_internal_queryset_to_current_tenant(
            self.request,
            queryset,
            lookup="tenant_members__tenant",
        )
        return queryset.distinct()

    def perform_create(self, serializer):
        user = serializer.save()
        log_action(
            request=self.request,
            action="USER_CREATE",
            target_type="user",
            target_id=user.id,
            after_data=_user_payload(user),
        )


@extend_schema_view(
    get=extend_schema(
        tags=["用户管理"],
        summary="查询单个用户账号",
        description="按 ID 查询单个用户账号及其全局 staff 档案。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    ),
    put=extend_schema(
        tags=["用户管理"],
        summary="全量更新用户账号",
        description="更新用户账号与 staff 档案。该接口返回原始用户对象，不带 `business_code` 包装。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        request=UserManageSerializer,
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    ),
    patch=extend_schema(
        tags=["用户管理"],
        summary="局部更新用户账号",
        description="局部更新用户账号与 staff 档案。该接口返回原始用户对象，不带 `business_code` 包装。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        request=UserManageSerializer,
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    ),
)
class UserDetailView(PermissionMapMixin, generics.RetrieveUpdateAPIView):
    serializer_class = UserManageSerializer
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "GET": "access.view_user",
        "PUT": "access.manage_user_accounts",
        "PATCH": "access.manage_user_accounts",
    }
    queryset = User.objects.select_related("staff_profile").all()

    @extend_schema(
        tags=["用户管理"],
        summary="查询单个用户账号",
        description="按 ID 查询单个用户账号及其全局 staff 档案。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        tags=["用户管理"],
        summary="全量更新用户账号",
        description="更新用户账号与 staff 档案。该接口返回原始用户对象，不带 `business_code` 包装。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        request=UserManageSerializer,
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
    def put(self, request, *args, **kwargs):
        return super().put(request, *args, **kwargs)

    @extend_schema(
        tags=["用户管理"],
        summary="局部更新用户账号",
        description="局部更新用户账号与 staff 档案。该接口返回原始用户对象，不带 `business_code` 包装。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        request=UserManageSerializer,
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
    def patch(self, request, *args, **kwargs):
        return super().patch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = _scope_internal_queryset_to_current_tenant(
            self.request,
            queryset,
            lookup="tenant_members__tenant",
        )
        return queryset.distinct()

    def perform_update(self, serializer):
        before = _user_payload(self.get_object())
        user = serializer.save()
        log_action(
            request=self.request,
            action="USER_UPDATE",
            target_type="user",
            target_id=user.id,
            before_data=before,
            after_data=_user_payload(user),
        )


@extend_schema_view(
    get=extend_schema(
        tags=["平台目录"],
        summary="查询权限目录",
        description="返回平台固定权限目录，可按模块过滤。该接口为只读目录查询。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER, PERMISSION_MODULE_PARAMETER],
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
)
class PermissionCatalogView(PermissionMapMixin, generics.ListAPIView):
    serializer_class = PermissionCodeSerializer
    permission_classes = [RequireInternalPermission]
    required_permission = "access.view_permission_catalog"
    queryset = Permission.objects.all().order_by("code")

    @extend_schema(
        tags=["平台目录"],
        summary="查询权限目录",
        description="返回平台固定权限目录，可按模块过滤。该接口为只读目录查询。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER, PERMISSION_MODULE_PARAMETER],
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        qs = super().get_queryset()
        module = self.request.query_params.get("module")
        if module:
            qs = qs.filter(module=module)
        return qs


@extend_schema_view(
    get=extend_schema(
        tags=["平台目录"],
        summary="查询角色目录",
        description="返回平台固定角色目录，以及每个角色默认绑定的权限模板。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
)
class RoleListView(PermissionMapMixin, generics.ListAPIView):
    serializer_class = RoleSerializer
    permission_classes = [RequireInternalPermission]
    required_permission = "access.view_role"
    queryset = Role.objects.prefetch_related("permission_grants__permission").all().order_by("id")

    @extend_schema(
        tags=["平台目录"],
        summary="查询角色目录",
        description="返回平台固定角色目录，以及每个角色默认绑定的权限模板。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


@extend_schema_view(
    get=extend_schema(
        tags=["平台目录"],
        summary="查询角色详情",
        description="按角色 ID 查询平台目录角色及其权限模板。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
)
class RoleDetailView(PermissionMapMixin, generics.RetrieveAPIView):
    serializer_class = RoleSerializer
    permission_classes = [RequireInternalPermission]
    required_permission = "access.view_role"
    queryset = Role.objects.prefetch_related("permission_grants__permission").all()

    @extend_schema(
        tags=["平台目录"],
        summary="查询角色详情",
        description="按角色 ID 查询平台目录角色及其权限模板。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


@extend_schema_view(
    get=extend_schema(
        tags=["审计日志"],
        summary="查询审计日志",
        description=(
            "查询 internal IAM 审计日志。superuser 可查看全部；platform_admin 仅查看平台级日志；"
            "租户成员在带 `X-TENANT-CODE` 时只查看当前租户日志。"
        ),
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER, *AUDIT_LOG_FILTER_PARAMETERS],
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
)
class AuditLogListView(PermissionMapMixin, generics.ListAPIView):
    serializer_class = AuditLogSerializer
    permission_classes = [RequireInternalPermission]
    required_permission = "access.view_auth_audit_logs"
    queryset = AuditLog.objects.select_related("actor_user").all()

    @extend_schema(
        tags=["审计日志"],
        summary="查询审计日志",
        description=(
            "查询 internal IAM 审计日志。superuser 可查看全部；platform_admin 仅查看平台级日志；"
            "租户成员在带 `X-TENANT-CODE` 时只查看当前租户日志。"
        ),
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER, *AUDIT_LOG_FILTER_PARAMETERS],
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        qs = _scope_audit_log_queryset(self.request, super().get_queryset())
        action = self.request.query_params.get("action")
        target_type = self.request.query_params.get("target_type")
        actor_user_id = self.request.query_params.get("actor_user_id")
        request_id = self.request.query_params.get("request_id")
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")

        if action:
            qs = qs.filter(action=action)
        if target_type:
            qs = qs.filter(target_type=target_type)
        if actor_user_id:
            qs = qs.filter(actor_user_id=actor_user_id)
        if request_id:
            qs = qs.filter(request_id=request_id)
        if date_from:
            start = _parse_iso_datetime(date_from)
            if start is not None:
                qs = qs.filter(created_at__gte=start)
        if date_to:
            end = _parse_iso_datetime(date_to)
            if end is not None:
                qs = qs.filter(created_at__lte=end)

        return qs.order_by("-created_at", "-id")


@extend_schema_view(
    get=extend_schema(
        tags=["审计日志"],
        summary="查询当前租户审计日志",
        description="仅查询当前租户上下文内的审计日志，适用于租户管理员在租户侧排查成员、角色、邀请等变更。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER, *AUDIT_LOG_FILTER_PARAMETERS],
        responses={
            200: OpenApiResponse(
                response=TENANT_AUDIT_LOG_LIST_RESPONSE_SERIALIZER,
                description="租户审计日志列表。",
            ),
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
)
class TenantAuditLogListView(generics.ListAPIView):
    serializer_class = AuditLogSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None
    queryset = AuditLog.objects.select_related("actor_user", "tenant").all()

    @extend_schema(
        tags=["审计日志"],
        summary="查询当前租户审计日志",
        description="仅查询当前租户上下文内的审计日志，适用于租户管理员在租户侧排查成员、角色、邀请等变更。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER, *AUDIT_LOG_FILTER_PARAMETERS],
        responses={
            200: OpenApiResponse(response=TENANT_AUDIT_LOG_LIST_RESPONSE_SERIALIZER, description="租户审计日志列表。"),
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def _get_current_tenant(self):
        decision = AuthzService.authorize(self.request, "access.view_auth_audit_logs")
        if not decision.allowed:
            raise BusinessPermissionDenied("tenant audit permission denied", business_detail_code=decision.reason_code)
        tenant, _member, _role_codes = _get_current_tenant_member_context(self.request)
        return tenant

    def get_queryset(self):
        tenant = self._get_current_tenant()
        qs = super().get_queryset().filter(tenant=tenant)
        action = self.request.query_params.get("action")
        target_type = self.request.query_params.get("target_type")
        actor_user_id = self.request.query_params.get("actor_user_id")
        request_id = self.request.query_params.get("request_id")
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")

        if action:
            qs = qs.filter(action=action)
        if target_type:
            qs = qs.filter(target_type=target_type)
        if actor_user_id:
            qs = qs.filter(actor_user_id=actor_user_id)
        if request_id:
            qs = qs.filter(request_id=request_id)
        if date_from:
            start = _parse_iso_datetime(date_from)
            if start is not None:
                qs = qs.filter(created_at__gte=start)
        if date_to:
            end = _parse_iso_datetime(date_to)
            if end is not None:
                qs = qs.filter(created_at__lte=end)

        return qs.order_by("-created_at", "-id")

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(
            {
                "business_code": "SUCCESS",
                "business_detail_code": "OK",
                "count": len(serializer.data),
                "data": serializer.data,
            }
        )


@extend_schema_view(
    get=extend_schema(
        tags=["租户管理"],
        summary="查询租户列表",
        description="查询平台租户列表。superuser 与 platform_admin 可查看平台范围租户；普通租户账号只会看到当前租户。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=TENANT_LIST_RESPONSE_SERIALIZER, description="租户列表。"),
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    ),
    post=extend_schema(
        tags=["租户管理"],
        summary="创建租户",
        description="创建新的租户记录。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        request=TenantSerializer,
        responses={
            201: OpenApiResponse(response=TENANT_CREATE_RESPONSE_SERIALIZER, description="租户创建成功。"),
            400: INTERNAL_VALIDATION_ERROR_RESPONSE,
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    ),
)
class TenantViewSet(PermissionMapMixin, generics.ListCreateAPIView, generics.RetrieveAPIView):
    serializer_class = TenantSerializer
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "GET": "access.view_tenant",
        "POST": "access.manage_tenant",
    }
    queryset = Tenant.objects.all()

    @extend_schema(
        tags=["租户管理"],
        summary="查询租户列表",
        description="查询平台租户列表。superuser 与 platform_admin 可查看平台范围租户；普通租户账号只会看到当前租户。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=TENANT_LIST_RESPONSE_SERIALIZER, description="租户列表。"),
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        tags=["租户管理"],
        summary="创建租户",
        description="创建新的租户记录。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        request=TenantSerializer,
        responses={
            201: OpenApiResponse(response=TENANT_CREATE_RESPONSE_SERIALIZER, description="租户创建成功。"),
            400: INTERNAL_VALIDATION_ERROR_RESPONSE,
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def get_queryset(self):
        return _scope_tenant_queryset_to_current_tenant(self.request, super().get_queryset())

    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response({"business_code": "SUCCESS", "business_detail_code": "OK", "data": serializer.data, "count": len(serializer.data)})

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response({"business_code": "SUCCESS", "business_detail_code": "OK", "data": serializer.data})

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except Exception:
            return Response(
                {"business_code": "INVALID_PARAMS", "business_detail_code": "VALIDATION_ERROR"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        self.perform_create(serializer)
        return Response(
            {
                "business_code": "SUCCESS",
                "business_detail_code": "OK",
                "id": serializer.instance.id,
                "code": serializer.instance.code,
                "name": serializer.instance.name,
                "status": serializer.instance.status,
            },
            status=status.HTTP_201_CREATED,
        )

    def perform_create(self, serializer):
        tenant = serializer.save()
        log_action(
            request=self.request,
            action="TENANT_CREATE",
            target_type="tenant",
            target_id=tenant.id,
            after_data={"id": tenant.id, "code": tenant.code, "name": tenant.name},
        )


@extend_schema_view(
    get=extend_schema(
        tags=["租户管理"],
        summary="查询单个租户详情",
        description="按租户 ID 查询详情。与 `/tenants/{id}` 的 retrieve 结果一致，保留单独文档入口。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=TENANT_DETAIL_RESPONSE_SERIALIZER, description="租户详情。"),
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            404: TENANT_NOT_FOUND_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
)
class TenantDetailView(PermissionMapMixin, generics.RetrieveAPIView):
    serializer_class = TenantSerializer
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "GET": "access.view_tenant",
    }
    queryset = Tenant.objects.all()

    @extend_schema(
        tags=["租户管理"],
        summary="查询单个租户详情",
        description="按租户 ID 查询详情。与 `/tenants/{id}` 的 retrieve 结果一致，保留单独文档入口。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(response=TENANT_DETAIL_RESPONSE_SERIALIZER, description="租户详情。"),
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            404: TENANT_NOT_FOUND_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return _scope_tenant_queryset_to_current_tenant(self.request, super().get_queryset())

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_queryset().filter(id=kwargs["pk"]).first()
        if instance is None:
            raise BusinessResourceNotFound("tenant not found", business_detail_code="TENANT_NOT_FOUND")
        serializer = self.get_serializer(instance)
        return Response({"business_code": "SUCCESS", "business_detail_code": "OK", "data": serializer.data})


class TenantDisableView(PermissionMapMixin, generics.GenericAPIView):
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "POST": "access.manage_tenant",
    }
    queryset = Tenant.objects.all()

    def get_queryset(self):
        return _scope_tenant_queryset_to_current_tenant(self.request, super().get_queryset())

    @extend_schema(
        tags=["租户管理"],
        summary="停用租户",
        description="将租户状态切换为 DISABLED。重复停用会返回幂等冲突。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=TENANT_STATE_RESPONSE_SERIALIZER, description="租户已停用。"),
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            404: TENANT_NOT_FOUND_RESPONSE,
            409: IDEMPOTENT_DUPLICATE_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
    def post(self, request, pk: int):
        tenant = self.get_queryset().filter(id=pk).first()
        if tenant is None:
            raise BusinessResourceNotFound("tenant not found", business_detail_code="TENANT_NOT_FOUND")

        before_data = snapshot(tenant)
        if tenant.status == TenantStatus.DISABLED:
            raise BusinessIdempotentDuplicate("tenant already disabled", business_detail_code="TENANT_ALREADY_DISABLED")

        tenant.status = TenantStatus.DISABLED
        tenant.save(update_fields=["status", "updated_at"])
        after_data = snapshot(tenant)
        log_action(
            request=request,
            action="TENANT_DISABLE",
            target_type="tenant",
            target_id=tenant.id,
            before_data=before_data,
            after_data=after_data,
        )
        return Response({"business_code": "SUCCESS", "business_detail_code": "OK", "id": tenant.id, "code": tenant.code, "name": tenant.name, "status": tenant.status})


class TenantEnableView(PermissionMapMixin, generics.GenericAPIView):
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "POST": "access.manage_tenant",
    }
    queryset = Tenant.objects.all()

    def get_queryset(self):
        return _scope_tenant_queryset_to_current_tenant(self.request, super().get_queryset())

    @extend_schema(
        tags=["租户管理"],
        summary="启用租户",
        description="将租户状态切换为 ACTIVE。重复启用会返回幂等冲突。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=TENANT_STATE_RESPONSE_SERIALIZER, description="租户已启用。"),
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            404: TENANT_NOT_FOUND_RESPONSE,
            409: IDEMPOTENT_DUPLICATE_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
    def post(self, request, pk: int):
        tenant = self.get_queryset().filter(id=pk).first()
        if tenant is None:
            raise BusinessResourceNotFound("tenant not found", business_detail_code="TENANT_NOT_FOUND")

        before_data = snapshot(tenant)
        if tenant.status == TenantStatus.ACTIVE:
            raise BusinessIdempotentDuplicate("tenant already enabled", business_detail_code="TENANT_ALREADY_ENABLED")

        tenant.status = TenantStatus.ACTIVE
        tenant.save(update_fields=["status", "updated_at"])
        after_data = snapshot(tenant)
        log_action(
            request=request,
            action="TENANT_ENABLE",
            target_type="tenant",
            target_id=tenant.id,
            before_data=before_data,
            after_data=after_data,
        )
        return Response({"business_code": "SUCCESS", "business_detail_code": "OK", "id": tenant.id, "code": tenant.code, "name": tenant.name, "status": tenant.status})


class TenantInitializeAdminView(PermissionMapMixin, generics.GenericAPIView):
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "POST": "access.manage_tenant",
    }
    serializer_class = TenantInitializeAdminSerializer
    queryset = Tenant.objects.all()

    def get_queryset(self):
        return _scope_tenant_queryset_to_current_tenant(self.request, super().get_queryset())

    @extend_schema(
        tags=["租户管理"],
        summary="初始化租户管理员",
        description="为指定租户创建或激活首个 `tenant_admin` 成员，并写入初始角色绑定与资质。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        request=TenantInitializeAdminSerializer,
        responses={
            200: OpenApiResponse(response=TENANT_INITIALIZE_ADMIN_RESPONSE_SERIALIZER, description="租户管理员初始化成功。"),
            400: INTERNAL_VALIDATION_ERROR_RESPONSE,
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            404: TENANT_NOT_FOUND_RESPONSE,
            409: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="租户管理员已初始化，或租户状态不允许初始化。",
                examples=[
                    OpenApiExample(
                        "已存在租户管理员",
                        value={
                            "business_code": "IDEMPOTENT_DUPLICATE",
                            "business_detail_code": "TENANT_ADMIN_ALREADY_INITIALIZED",
                            "detail": "tenant admin already initialized",
                        },
                        response_only=True,
                        status_codes=["409"],
                    )
                ],
            ),
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
    def post(self, request, pk: int):
        tenant = self.get_queryset().filter(id=pk).first()
        if tenant is None:
            raise BusinessResourceNotFound("tenant not found", business_detail_code="TENANT_NOT_FOUND")

        serializer = self.get_serializer(data=request.data, context={"tenant": tenant, "request": request})
        try:
            serializer.is_valid(raise_exception=True)
        except serializers.ValidationError as exc:
            return _validation_error_response(exc)

        try:
            member = serializer.save()
        except serializers.ValidationError as exc:
            return _validation_error_response(exc)
        role_codes = list(
            member.role_bindings.filter(status=TenantMemberRoleStatus.GRANTED)
            .order_by("id")
            .values_list("system_role__code", flat=True)
        )
        log_action(
            request=request,
            action="TENANT_ADMIN_INITIALIZE",
            target_type="tenant_member",
            target_id=member.id,
            after_data={"id": member.id, "tenant_id": member.tenant_id, "user_id": member.user_id, "status": member.status, "roles": role_codes},
        )
        return Response(
            {
                "business_code": "SUCCESS",
                "business_detail_code": "OK",
                "member_id": member.id,
                "tenant_id": member.tenant_id,
                "user_id": member.user_id,
                "roles": role_codes,
                "status": member.status,
                "message": "租户管理员初始化成功",
            }
        )


class TenantSetPlanView(PermissionMapMixin, generics.GenericAPIView):
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "POST": "access.manage_tenant",
    }
    serializer_class = TenantSetPlanSerializer
    queryset = Tenant.objects.all()

    def get_queryset(self):
        return _scope_tenant_queryset_to_current_tenant(self.request, super().get_queryset())

    @extend_schema(
        tags=["租户管理"],
        summary="设置租户套餐",
        description="更新租户 plan 字段。若新 plan 与当前值相同，则返回幂等冲突。",
        parameters=[TENANT_CONTEXT_OPTIONAL_HEADER_PARAMETER],
        request=TenantSetPlanSerializer,
        responses={
            200: OpenApiResponse(response=TENANT_SET_PLAN_RESPONSE_SERIALIZER, description="套餐更新成功。"),
            400: INTERNAL_VALIDATION_ERROR_RESPONSE,
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            404: TENANT_NOT_FOUND_RESPONSE,
            409: IDEMPOTENT_DUPLICATE_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
    def post(self, request, pk: int):
        tenant = self.get_queryset().filter(id=pk).first()
        if tenant is None:
            raise BusinessResourceNotFound("tenant not found", business_detail_code="TENANT_NOT_FOUND")

        before_data = snapshot(tenant)
        serializer = self.get_serializer(data=request.data, context={"tenant": tenant})
        try:
            serializer.is_valid(raise_exception=True)
        except serializers.ValidationError:
            return Response(
                {"business_code": "INVALID_PARAMS", "business_detail_code": "VALIDATION_ERROR"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant = serializer.save()
        after_data = snapshot(tenant)
        log_action(
            request=request,
            action="TENANT_PLAN_CHANGE",
            target_type="tenant",
            target_id=tenant.id,
            before_data=before_data,
            after_data=after_data,
        )
        return Response({"business_code": "SUCCESS", "business_detail_code": "OK", "id": tenant.id, "code": tenant.code, "name": tenant.name, "status": tenant.status, "plan": tenant.plan})


@extend_schema_view(
    get=extend_schema(
        tags=["租户成员管理"],
        summary="查询租户成员列表",
        description="查询当前租户成员。该接口禁止 platform_admin 使用，必须在租户上下文中调用。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER, TENANT_MEMBER_TENANT_ID_PARAMETER, TENANT_MEMBER_STATUS_PARAMETER],
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    ),
    post=extend_schema(
        tags=["租户成员管理"],
        summary="直接创建租户成员",
        description="为当前租户直接创建 ACTIVE 成员，可同时写入预设角色与资质。成功响应返回原始成员对象。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER],
        request=TenantMemberCreateSerializer,
        responses={400: INTERNAL_VALIDATION_ERROR_RESPONSE, 401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    ),
)
class TenantMemberListCreateView(PermissionMapMixin, generics.ListCreateAPIView):
    permission_classes = [RequireInternalPermission]
    platform_admin_forbidden = True
    method_permission_map = {
        "GET": "access.view_tenant_member",
        "POST": "access.manage_tenant_member",
    }

    @extend_schema(
        tags=["租户成员管理"],
        summary="查询租户成员列表",
        description="查询当前租户成员。该接口禁止 platform_admin 使用，必须在租户上下文中调用。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER, TENANT_MEMBER_TENANT_ID_PARAMETER, TENANT_MEMBER_STATUS_PARAMETER],
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        tags=["租户成员管理"],
        summary="直接创建租户成员",
        description="为当前租户直接创建 ACTIVE 成员，可同时写入预设角色与资质。成功响应返回原始成员对象。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER],
        request=TenantMemberCreateSerializer,
        responses={400: INTERNAL_VALIDATION_ERROR_RESPONSE, 401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def get_queryset(self):
        qs = (
            TenantMember.objects.select_related("tenant", "user")
            .prefetch_related("role_bindings__system_role", "qualifications__qualification_type")
            .all()
        )
        qs = _scope_internal_queryset_to_current_tenant(self.request, qs, lookup="tenant")
        tenant_id = self.request.query_params.get("tenant_id")
        status_value = self.request.query_params.get("status")
        if tenant_id:
            qs = qs.filter(tenant_id=tenant_id)
        if status_value:
            qs = qs.filter(status=status_value)
        return qs.order_by("-id")

    def get_serializer_class(self):
        if self.request.method == "POST":
            return TenantMemberCreateSerializer
        return TenantMemberSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["request"] = self.request
        return context

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)
        except serializers.ValidationError as exc:
            return _validation_error_response(exc)

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer):
        member = serializer.save()
        log_action(
            request=self.request,
            action="TENANT_MEMBER_CREATE",
            target_type="tenant_member",
            target_id=member.id,
            tenant=member.tenant,
            after_data={"id": member.id, "tenant_id": member.tenant_id, "user_id": member.user_id, "status": member.status},
        )


class TenantMemberInviteView(PermissionMapMixin, generics.GenericAPIView):
    permission_classes = [RequireInternalPermission]
    platform_admin_forbidden = True
    serializer_class = TenantMemberInviteSerializer
    method_permission_map = {
        "POST": "access.manage_tenant_member",
    }

    @extend_schema(
        tags=["租户成员管理"],
        summary="发送租户成员邀请",
        description="为当前租户创建 INVITED 状态成员，并预绑定角色与资质，返回邀请 token。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER],
        request=TenantMemberInviteSerializer,
        responses={
            201: OpenApiResponse(response=TENANT_MEMBER_INVITE_RESPONSE_SERIALIZER, description="邀请创建成功。"),
            400: INTERNAL_VALIDATION_ERROR_RESPONSE,
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            409: STATE_CONFLICT_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        try:
            serializer.is_valid(raise_exception=True)
        except serializers.ValidationError as exc:
            return _validation_error_response(exc)

        try:
            member = serializer.save()
        except serializers.ValidationError as exc:
            return _validation_error_response(exc)
        log_action(
            request=request,
            action="TENANT_MEMBER_INVITE",
            target_type="tenant_member",
            target_id=member.id,
            tenant=member.tenant,
            after_data={"id": member.id, "tenant_id": member.tenant_id, "user_id": member.user_id, "status": member.status},
        )
        return Response(
            {
                "business_code": "SUCCESS",
                "business_detail_code": "OK",
                "member_id": member.id,
                "invitation_token": member.invitation_token,
                "message": "邀请发送成功",
            },
            status=status.HTTP_201_CREATED,
        )


class TenantMemberConfirmInvitationView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = TenantMemberConfirmInvitationSerializer

    @extend_schema(
        tags=["租户成员管理"],
        summary="确认加入租户",
        description="当前登录用户使用邀请 token 接受租户邀请。成功后成员状态由 INVITED 变为 ACTIVE。",
        request=TenantMemberConfirmInvitationSerializer,
        responses={
            200: OpenApiResponse(response=TENANT_MEMBER_CONFIRM_RESPONSE_SERIALIZER, description="加入租户成功。"),
            400: INTERNAL_VALIDATION_ERROR_RESPONSE,
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            404: INVITATION_NOT_FOUND_RESPONSE,
            409: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="邀请状态无效，例如已经被接受、拒绝或撤销。",
                examples=[
                    OpenApiExample(
                        "邀请状态无效",
                        value={
                            "business_code": "STATE_CONFLICT",
                            "business_detail_code": "INVITATION_STATUS_INVALID",
                            "detail": "invitation is not invited",
                        },
                        response_only=True,
                        status_codes=["409"],
                    )
                ],
            ),
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        try:
            serializer.is_valid(raise_exception=True)
        except serializers.ValidationError:
            return Response(
                {"business_code": "INVALID_PARAMS", "business_detail_code": "VALIDATION_ERROR"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        member = serializer.save()
        role_codes = list(
            member.role_bindings.filter(status=TenantMemberRoleStatus.GRANTED)
            .order_by("id")
            .values_list("system_role__code", flat=True)
        )
        log_action(
            request=request,
            action="TENANT_MEMBER_CONFIRM_INVITATION",
            target_type="tenant_member",
            target_id=member.id,
            tenant=member.tenant,
            after_data={"id": member.id, "tenant_id": member.tenant_id, "user_id": member.user_id, "status": member.status},
        )
        return Response({"business_code": "SUCCESS", "business_detail_code": "OK", "member_id": member.id, "roles": role_codes, "message": "您已成功加入租户"})


class MeInvitationRejectView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = TenantMemberRejectInvitationSerializer

    @extend_schema(
        tags=["当前用户"],
        summary="拒绝租户邀请",
        description="当前登录用户使用邀请 token 拒绝一条待处理租户邀请。",
        request=TenantMemberRejectInvitationSerializer,
        responses={
            200: OpenApiResponse(response=TENANT_MEMBER_MESSAGE_RESPONSE_SERIALIZER, description="邀请已拒绝。"),
            400: INTERNAL_VALIDATION_ERROR_RESPONSE,
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            404: INVITATION_NOT_FOUND_RESPONSE,
            409: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="邀请已经不是待处理状态。",
                examples=[
                    OpenApiExample(
                        "邀请状态无效",
                        value={
                            "business_code": "STATE_CONFLICT",
                            "business_detail_code": "INVITATION_STATUS_INVALID",
                            "detail": "invitation is not invited",
                        },
                        response_only=True,
                        status_codes=["409"],
                    )
                ],
            ),
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        try:
            serializer.is_valid(raise_exception=True)
        except serializers.ValidationError:
            return Response(
                {"business_code": "INVALID_PARAMS", "business_detail_code": "VALIDATION_ERROR"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        member = serializer.save()
        log_action(
            request=request,
            action="TENANT_MEMBER_REJECT_INVITATION",
            target_type="tenant_member",
            target_id=member.id,
            tenant=member.tenant,
            before_data={"id": member.id, "tenant_id": member.tenant_id, "user_id": member.user_id, "status": TenantMemberStatus.INVITED},
            after_data={"id": member.id, "tenant_id": member.tenant_id, "user_id": member.user_id, "status": member.status},
        )
        return Response({"business_code": "SUCCESS", "business_detail_code": "OK", "message": "您已拒绝该租户邀请"})


class TenantMemberDisableView(PermissionMapMixin, generics.GenericAPIView):
    permission_classes = [RequireInternalPermission]
    platform_admin_forbidden = True
    method_permission_map = {
        "POST": "access.manage_tenant_member",
    }
    queryset = TenantMember.objects.select_related("tenant", "user").prefetch_related("role_bindings__system_role", "qualifications__qualification_type")

    def get_queryset(self):
        return _scope_internal_queryset_to_current_tenant(self.request, super().get_queryset(), lookup="tenant")

    @extend_schema(
        tags=["租户成员管理"],
        summary="停用或撤销租户成员",
        description="ACTIVE 成员会转为 DISABLED；INVITED 成员会转为 REVOKED。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=TENANT_MEMBER_STATUS_RESPONSE_SERIALIZER, description="成员状态已更新。"),
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            404: TENANT_MEMBER_NOT_FOUND_RESPONSE,
            409: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="成员已经停用，或当前状态不允许停用。",
                examples=[
                    OpenApiExample(
                        "成员已停用",
                        value={
                            "business_code": "IDEMPOTENT_DUPLICATE",
                            "business_detail_code": "TENANT_MEMBER_ALREADY_DISABLED",
                            "detail": "tenant member already disabled",
                        },
                        response_only=True,
                        status_codes=["409"],
                    )
                ],
            ),
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
    def post(self, request, pk: int):
        member = self.get_queryset().filter(id=pk).first()
        if member is None:
            raise BusinessResourceNotFound("tenant member not found", business_detail_code="TENANT_MEMBER_NOT_FOUND")

        before_data = {"id": member.id, "tenant_id": member.tenant_id, "user_id": member.user_id, "status": member.status}
        if member.status in {TenantMemberStatus.DISABLED, TenantMemberStatus.REVOKED}:
            raise BusinessIdempotentDuplicate("tenant member already disabled", business_detail_code="TENANT_MEMBER_ALREADY_DISABLED")

        if member.status == TenantMemberStatus.ACTIVE:
            member.status = TenantMemberStatus.DISABLED
            member.invitation_token = None
            member.invited_by_user = None
            member.invited_at = None
            member.expires_at = None
        elif member.status == TenantMemberStatus.INVITED:
            member.status = TenantMemberStatus.REVOKED
            member.invitation_token = None
        else:
            raise BusinessStateConflict("tenant member status invalid", business_detail_code="TENANT_MEMBER_STATUS_INVALID")

        member.save()
        after_data = {"id": member.id, "tenant_id": member.tenant_id, "user_id": member.user_id, "status": member.status}
        log_action(
            request=request,
            action="TENANT_MEMBER_DISABLE",
            target_type="tenant_member",
            target_id=member.id,
            tenant=member.tenant,
            before_data=before_data,
            after_data=after_data,
        )
        return Response({"business_code": "SUCCESS", "business_detail_code": "OK", "member_id": member.id, "tenant_id": member.tenant_id, "user_id": member.user_id, "status": member.status})


class TenantMemberEnableView(PermissionMapMixin, generics.GenericAPIView):
    permission_classes = [RequireInternalPermission]
    platform_admin_forbidden = True
    method_permission_map = {
        "POST": "access.manage_tenant_member",
    }
    queryset = TenantMember.objects.select_related("tenant", "user").prefetch_related("role_bindings__system_role", "qualifications__qualification_type")

    def get_queryset(self):
        return _scope_internal_queryset_to_current_tenant(self.request, super().get_queryset(), lookup="tenant")

    @extend_schema(
        tags=["租户成员管理"],
        summary="重新启用租户成员",
        description="仅允许将 DISABLED 成员重新启用为 ACTIVE。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=TENANT_MEMBER_STATUS_RESPONSE_SERIALIZER, description="成员已重新启用。"),
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            404: TENANT_MEMBER_NOT_FOUND_RESPONSE,
            409: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="成员已经启用，或当前状态不允许启用。",
                examples=[
                    OpenApiExample(
                        "成员已启用",
                        value={
                            "business_code": "IDEMPOTENT_DUPLICATE",
                            "business_detail_code": "TENANT_MEMBER_ALREADY_ENABLED",
                            "detail": "tenant member already enabled",
                        },
                        response_only=True,
                        status_codes=["409"],
                    )
                ],
            ),
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
    def post(self, request, pk: int):
        member = self.get_queryset().filter(id=pk).first()
        if member is None:
            raise BusinessResourceNotFound("tenant member not found", business_detail_code="TENANT_MEMBER_NOT_FOUND")

        before_data = {"id": member.id, "tenant_id": member.tenant_id, "user_id": member.user_id, "status": member.status}
        if member.status == TenantMemberStatus.ACTIVE:
            raise BusinessIdempotentDuplicate("tenant member already enabled", business_detail_code="TENANT_MEMBER_ALREADY_ENABLED")
        if member.status != TenantMemberStatus.DISABLED:
            raise BusinessStateConflict("tenant member status invalid", business_detail_code="TENANT_MEMBER_STATUS_INVALID")

        member.status = TenantMemberStatus.ACTIVE
        member.joined_at = member.joined_at or timezone.now()
        member.responded_at = member.responded_at or member.joined_at
        member.save(update_fields=["status", "joined_at", "responded_at", "updated_at"])
        after_data = {"id": member.id, "tenant_id": member.tenant_id, "user_id": member.user_id, "status": member.status}
        log_action(
            request=request,
            action="TENANT_MEMBER_ENABLE",
            target_type="tenant_member",
            target_id=member.id,
            tenant=member.tenant,
            before_data=before_data,
            after_data=after_data,
        )
        return Response({"business_code": "SUCCESS", "business_detail_code": "OK", "member_id": member.id, "tenant_id": member.tenant_id, "user_id": member.user_id, "status": member.status})


@extend_schema_view(
    get=extend_schema(
        tags=["租户成员管理"],
        summary="查询租户成员详情",
        description="按成员 ID 查询当前租户成员详情、角色编码与资质。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER],
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 404: TENANT_MEMBER_NOT_FOUND_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    ),
    put=extend_schema(
        tags=["租户成员管理"],
        summary="全量更新租户成员",
        description="更新成员展示名、member_no 与资质。该接口返回原始成员对象。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER],
        request=TenantMemberSerializer,
        responses={400: INTERNAL_VALIDATION_ERROR_RESPONSE, 401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 404: TENANT_MEMBER_NOT_FOUND_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    ),
    patch=extend_schema(
        tags=["租户成员管理"],
        summary="局部更新租户成员",
        description="局部更新成员展示名、member_no 与资质。该接口返回原始成员对象。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER],
        request=TenantMemberSerializer,
        responses={400: INTERNAL_VALIDATION_ERROR_RESPONSE, 401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 404: TENANT_MEMBER_NOT_FOUND_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    ),
    delete=extend_schema(
        tags=["租户成员管理"],
        summary="删除租户成员",
        description="硬删除当前租户成员记录。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER],
        responses={204: OpenApiResponse(description="删除成功。"), 401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 404: TENANT_MEMBER_NOT_FOUND_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    ),
)
class TenantMemberDetailView(PermissionMapMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TenantMemberSerializer
    permission_classes = [RequireInternalPermission]
    platform_admin_forbidden = True
    method_permission_map = {
        "GET": "access.view_tenant_member",
        "PUT": "access.manage_tenant_member",
        "PATCH": "access.manage_tenant_member",
        "DELETE": "access.manage_tenant_member",
    }
    queryset = TenantMember.objects.select_related("tenant", "user").prefetch_related("role_bindings__system_role", "qualifications__qualification_type")

    @extend_schema(
        tags=["租户成员管理"],
        summary="查询租户成员详情",
        description="按成员 ID 查询当前租户成员详情、角色编码与资质。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER],
        responses={401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 404: TENANT_MEMBER_NOT_FOUND_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        tags=["租户成员管理"],
        summary="全量更新租户成员",
        description="更新成员展示名、member_no 与资质。该接口返回原始成员对象。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER],
        request=TenantMemberSerializer,
        responses={400: INTERNAL_VALIDATION_ERROR_RESPONSE, 401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 404: TENANT_MEMBER_NOT_FOUND_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
    def put(self, request, *args, **kwargs):
        return super().put(request, *args, **kwargs)

    @extend_schema(
        tags=["租户成员管理"],
        summary="局部更新租户成员",
        description="局部更新成员展示名、member_no 与资质。该接口返回原始成员对象。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER],
        request=TenantMemberSerializer,
        responses={400: INTERNAL_VALIDATION_ERROR_RESPONSE, 401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 404: TENANT_MEMBER_NOT_FOUND_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
    def patch(self, request, *args, **kwargs):
        return super().patch(request, *args, **kwargs)

    @extend_schema(
        tags=["租户成员管理"],
        summary="删除租户成员",
        description="硬删除当前租户成员记录。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER],
        responses={204: OpenApiResponse(description="删除成功。"), 401: INTERNAL_AUTH_REQUIRED_RESPONSE, 403: INTERNAL_PERMISSION_DENIED_RESPONSE, 404: TENANT_MEMBER_NOT_FOUND_RESPONSE, 500: INTERNAL_INTERNAL_ERROR_RESPONSE},
    )
    def delete(self, request, *args, **kwargs):
        return super().delete(request, *args, **kwargs)

    def get_queryset(self):
        return _scope_internal_queryset_to_current_tenant(self.request, super().get_queryset(), lookup="tenant")

    def perform_destroy(self, instance):
        log_action(
            request=self.request,
            action="TENANT_MEMBER_DELETE",
            target_type="tenant_member",
            target_id=instance.id,
            before_data={"id": instance.id, "tenant_id": instance.tenant_id, "user_id": instance.user_id},
        )
        instance.delete()


class TenantMemberRoleAssignView(PermissionMapMixin, generics.GenericAPIView):
    permission_classes = [RequireInternalPermission]
    platform_admin_forbidden = True
    required_permission = "access.assign_tenant_member_role"
    serializer_class = TenantMemberRoleAssignSerializer
    queryset = TenantMember.objects.all()

    def get_queryset(self):
        return _scope_internal_queryset_to_current_tenant(self.request, super().get_queryset(), lookup="tenant")

    @extend_schema(
        tags=["租户成员管理"],
        summary="重设成员角色列表",
        description="用新的 `role_codes` 集合覆盖当前成员已授予角色。未包含的角色会被标记为 REVOKED。",
        parameters=[TENANT_CONTEXT_REQUIRED_HEADER_PARAMETER],
        request=TenantMemberRoleAssignSerializer,
        responses={
            200: OpenApiResponse(response=TenantMemberSerializer, description="更新后的成员对象。"),
            400: INTERNAL_VALIDATION_ERROR_RESPONSE,
            401: INTERNAL_AUTH_REQUIRED_RESPONSE,
            403: INTERNAL_PERMISSION_DENIED_RESPONSE,
            404: TENANT_MEMBER_NOT_FOUND_RESPONSE,
            500: INTERNAL_INTERNAL_ERROR_RESPONSE,
        },
    )
    def post(self, request, pk):
        member = self.get_object()
        serializer = self.get_serializer(data=request.data, context={"tenant_member": member, "request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        member = (
            TenantMember.objects.select_related("tenant", "user")
            .prefetch_related("role_bindings__system_role", "qualifications__qualification_type")
            .get(id=pk)
        )
        return Response(TenantMemberSerializer(member).data)
