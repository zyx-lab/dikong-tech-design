from datetime import datetime

from django.contrib.auth import authenticate, login, logout
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema

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
    log_action,
    snapshot,
)


def _user_payload(user: User) -> dict:
    payload = {
        "id": user.id,
        "username": user.username,
        "is_active": user.is_active,
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
        "status": user.status,
    }
    staff = getattr(user, "staff_profile", None)
    if staff:
        payload["staff"] = {
            "id": staff.id,
            "staff_no": staff.staff_no,
            "name": staff.name,
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


class MePermissionsView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = MePermissionSerializer

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        tenant = getattr(request, "tenant_context", None)
        if tenant is None:
            raise BusinessPermissionDenied("tenant context required", business_detail_code="TENANT_CONTEXT_REQUIRED")

        if request.user.is_superuser:
            permissions = AuthzService.get_permission_scope_map(request)
            role_codes: list[str] = []
        else:
            tenant, _member, role_codes = _get_current_tenant_member_context(request)
            permissions = AuthzService.get_tenant_role_permission_scope_map(role_codes)

        serializer = self.get_serializer(permissions, many=True)
        return Response(
            {
                "business_code": "SUCCESS",
                "business_detail_code": "OK",
                "tenant_code": tenant.code,
                "roles": role_codes,
                "items": serializer.data,
            }
        )


class MeTenantListView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CurrentUserTenantSerializer

    @extend_schema(responses=OpenApiTypes.OBJECT)
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


class MeInvitationListView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CurrentUserInvitationSerializer

    @extend_schema(responses=OpenApiTypes.OBJECT)
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
                }
            )
        return Response({"is_authenticated": False})


class LoginView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
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
            }
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        logout(request)
        return Response({"business_code": "SUCCESS", "business_detail_code": "OK"})


class UserSelfRegisterView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = UserSelfRegisterSerializer

    @extend_schema(request=UserSelfRegisterSerializer, responses=OpenApiTypes.OBJECT)
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


class UserPhoneRegisterView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = UserPhoneRegisterSerializer

    @extend_schema(request=UserPhoneRegisterSerializer, responses=OpenApiTypes.OBJECT)
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


class UserListCreateView(PermissionMapMixin, generics.ListCreateAPIView):
    serializer_class = UserManageSerializer
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "GET": "access.view_user",
        "POST": "access.manage_user_accounts",
    }
    queryset = User.objects.select_related("staff_profile").all().order_by("id")

    def perform_create(self, serializer):
        user = serializer.save()
        log_action(
            request=self.request,
            action="USER_CREATE",
            target_type="user",
            target_id=user.id,
            after_data=_user_payload(user),
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


class PermissionCatalogView(PermissionMapMixin, generics.ListAPIView):
    serializer_class = PermissionCodeSerializer
    permission_classes = [RequireInternalPermission]
    required_permission = "access.view_permission_catalog"
    queryset = Permission.objects.all().order_by("code")

    def get_queryset(self):
        qs = super().get_queryset()
        module = self.request.query_params.get("module")
        if module:
            qs = qs.filter(module=module)
        return qs


class RoleListView(PermissionMapMixin, generics.ListAPIView):
    serializer_class = RoleSerializer
    permission_classes = [RequireInternalPermission]
    required_permission = "access.view_role"
    queryset = Role.objects.prefetch_related("permission_grants__permission").all().order_by("id")


class RoleDetailView(PermissionMapMixin, generics.RetrieveAPIView):
    serializer_class = RoleSerializer
    permission_classes = [RequireInternalPermission]
    required_permission = "access.view_role"
    queryset = Role.objects.prefetch_related("permission_grants__permission").all()


class AuditLogListView(PermissionMapMixin, generics.ListAPIView):
    serializer_class = AuditLogSerializer
    permission_classes = [RequireInternalPermission]
    required_permission = "access.view_auth_audit_logs"
    queryset = AuditLog.objects.select_related("actor_user").all()

    def get_queryset(self):
        qs = super().get_queryset()
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


class TenantAuditLogListView(generics.ListAPIView):
    serializer_class = AuditLogSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None
    queryset = AuditLog.objects.select_related("actor_user", "tenant").all()

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

    @extend_schema(responses=OpenApiTypes.OBJECT)
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


class TenantViewSet(PermissionMapMixin, generics.ListCreateAPIView, generics.RetrieveAPIView):
    serializer_class = TenantSerializer
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "GET": "access.view_tenant",
        "POST": "access.manage_tenant",
    }
    queryset = Tenant.objects.all()

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


class TenantDetailView(PermissionMapMixin, generics.RetrieveAPIView):
    serializer_class = TenantSerializer
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "GET": "access.view_tenant",
    }
    queryset = Tenant.objects.all()

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response({"business_code": "SUCCESS", "business_detail_code": "OK", "data": serializer.data})


class TenantDisableView(PermissionMapMixin, generics.GenericAPIView):
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "POST": "access.manage_tenant",
    }
    queryset = Tenant.objects.all()

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
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

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
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

    @extend_schema(request=TenantInitializeAdminSerializer, responses=OpenApiTypes.OBJECT)
    def post(self, request, pk: int):
        tenant = self.get_queryset().filter(id=pk).first()
        if tenant is None:
            raise BusinessResourceNotFound("tenant not found", business_detail_code="TENANT_NOT_FOUND")

        serializer = self.get_serializer(data=request.data, context={"tenant": tenant, "request": request})
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

    @extend_schema(request=TenantSetPlanSerializer, responses=OpenApiTypes.OBJECT)
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


class TenantMemberListCreateView(PermissionMapMixin, generics.ListCreateAPIView):
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "GET": "access.view_tenant_member",
        "POST": "access.manage_tenant_member",
    }

    def get_queryset(self):
        qs = (
            TenantMember.objects.select_related("tenant", "user")
            .prefetch_related("role_bindings__system_role", "qualifications__qualification_type")
            .all()
        )
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
    serializer_class = TenantMemberInviteSerializer
    method_permission_map = {
        "POST": "access.manage_tenant_member",
    }

    @extend_schema(request=TenantMemberInviteSerializer, responses=OpenApiTypes.OBJECT)
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

    @extend_schema(request=TenantMemberConfirmInvitationSerializer, responses=OpenApiTypes.OBJECT)
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

    @extend_schema(request=TenantMemberRejectInvitationSerializer, responses=OpenApiTypes.OBJECT)
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
    method_permission_map = {
        "POST": "access.manage_tenant_member",
    }
    queryset = TenantMember.objects.select_related("tenant", "user").prefetch_related("role_bindings__system_role", "qualifications__qualification_type")

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
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
    method_permission_map = {
        "POST": "access.manage_tenant_member",
    }
    queryset = TenantMember.objects.select_related("tenant", "user").prefetch_related("role_bindings__system_role", "qualifications__qualification_type")

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
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


class TenantMemberDetailView(PermissionMapMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TenantMemberSerializer
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "GET": "access.view_tenant_member",
        "PUT": "access.manage_tenant_member",
        "PATCH": "access.manage_tenant_member",
        "DELETE": "access.manage_tenant_member",
    }
    queryset = TenantMember.objects.select_related("tenant", "user").prefetch_related("role_bindings__system_role", "qualifications__qualification_type")

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
    required_permission = "access.assign_tenant_member_role"
    serializer_class = TenantMemberRoleAssignSerializer
    queryset = TenantMember.objects.all()

    @extend_schema(request=TenantMemberRoleAssignSerializer, responses=TenantMemberSerializer)
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
