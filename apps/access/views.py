from datetime import datetime

from django.contrib.auth import authenticate, login
from django.contrib.auth.models import Group, Permission
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema

from apps.access.drf_permissions import PermissionMapMixin, RequireInternalPermission
from apps.access.models import (
    AuditLog,
    GroupPermissionScope,
    ScopeStatus,
    StaffType,
    StaffTypeGroup,
    SystemRole,
    Tenant,
    TenantMember,
    TenantMemberRole,
    User,
)
from apps.access.serializers import (
    AuditLogSerializer,
    GroupPermissionAssignSerializer,
    GroupScopeAssignSerializer,
    GroupSerializer,
    MePermissionSerializer,
    PermissionCodeSerializer,
    StaffTypeGroupAssignSerializer,
    StaffTypeSerializer,
    SystemRoleSerializer,
    TenantMemberCreateSerializer,
    TenantMemberRoleAssignSerializer,
    TenantMemberRoleSerializer,
    TenantMemberSerializer,
    TenantSerializer,
    UserManageSerializer,
)
from apps.access.services import AuthzService, IdentityService, log_action, snapshot


def _permission_codes_for_group(group: Group) -> list[str]:
    perms = group.permissions.select_related("content_type").all()
    return sorted([f"{perm.content_type.app_label}.{perm.codename}" for perm in perms])


def _scope_payload_for_group(group: Group) -> list[dict]:
    scopes = (
        GroupPermissionScope.objects.filter(group=group)
        .select_related("permission__content_type")
        .order_by("permission__content_type__app_label", "permission__codename")
    )
    return [
        {
            "permission": f"{scope.permission.content_type.app_label}.{scope.permission.codename}",
            "scope_type": scope.scope_type,
            "status": scope.status,
        }
        for scope in scopes
    ]


def _group_payload_for_staff_type(staff_type: StaffType) -> list[dict]:
    links = (
        StaffTypeGroup.objects.filter(staff_type=staff_type, status=ScopeStatus.ACTIVE)
        .select_related("group")
        .order_by("group_id")
    )
    return [{"group_id": link.group_id, "group_name": link.group.name} for link in links]


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
            "staff_type_id": staff.staff_type_id,
            "staff_type_name": staff.staff_type.name,
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


class MePermissionsView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = MePermissionSerializer

    @extend_schema(responses=MePermissionSerializer(many=True))
    def get(self, request):
        if not request.user.is_superuser:
            identity_result = IdentityService.check_business_user(request.user)
            if not identity_result.ok:
                return Response({"detail": identity_result.reason_code}, status=status.HTTP_403_FORBIDDEN)

        permissions = AuthzService.get_permission_scope_map(request.user)
        serializer = self.get_serializer(permissions, many=True)
        return Response({"items": serializer.data})


class ApiRootView(APIView):
    """IAM 内部 API 根入口。"""

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
                    "me_permissions": reverse("me-permissions", request=request),
                    "permission_catalog": reverse("permission-catalog", request=request),
                    "groups": reverse("group-list-create", request=request),
                    "staff_types": reverse("staff-type-list-create", request=request),
                    "scope_matrix": reverse("scope-matrix", request=request),
                    "audit_logs": reverse("audit-log-list", request=request),
                },
            }
        )


class SessionStatusView(APIView):
    """供文档页展示当前会话登录态。"""

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
    """用户登录接口"""

    permission_classes = [AllowAny]

    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses=OpenApiTypes.OBJECT,
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

        if user is not None:
            login(request, user)
            return Response({
                "business_code": "SUCCESS",
                "business_detail_code": "OK",
                "username": user.username,
                "is_superuser": user.is_superuser,
            })
        else:
            return Response(
                {"business_code": "PERMISSION_DENIED", "business_detail_code": "INVALID_CREDENTIALS", "error": "invalid username or password"},
                status=status.HTTP_401_UNAUTHORIZED,
            )


class LogoutView(APIView):
    """用户登出接口"""

    permission_classes = [AllowAny]

    def post(self, request):
        from django.contrib.auth import logout
        logout(request)
        return Response({
            "business_code": "SUCCESS",
            "business_detail_code": "OK",
        })


class UserListCreateView(PermissionMapMixin, generics.ListCreateAPIView):
    serializer_class = UserManageSerializer
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "GET": "access.view_user",
        "POST": "access.manage_user_accounts",
    }
    queryset = User.objects.select_related("staff_profile__staff_type").all().order_by("id")

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
    queryset = User.objects.select_related("staff_profile__staff_type").all()

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
    required_permission = "access.manage_auth_groups"
    queryset = Permission.objects.select_related("content_type").all().order_by("content_type__app_label", "codename")

    def get_queryset(self):
        qs = super().get_queryset()
        app_label = self.request.query_params.get("app_label")
        if app_label:
            qs = qs.filter(content_type__app_label=app_label)
        return qs


class GroupListCreateView(PermissionMapMixin, generics.ListCreateAPIView):
    serializer_class = GroupSerializer
    permission_classes = [RequireInternalPermission]
    required_permission = "access.manage_auth_groups"
    queryset = Group.objects.prefetch_related("permissions__content_type").all().order_by("id")

    def perform_create(self, serializer):
        group = serializer.save()
        log_action(
            request=self.request,
            action="GROUP_CREATE",
            target_type="group",
            target_id=group.id,
            after_data=snapshot(group),
        )


class GroupDetailView(PermissionMapMixin, generics.RetrieveUpdateAPIView):
    serializer_class = GroupSerializer
    permission_classes = [RequireInternalPermission]
    required_permission = "access.manage_auth_groups"
    queryset = Group.objects.prefetch_related("permissions__content_type").all()

    def perform_update(self, serializer):
        before = snapshot(self.get_object())
        group = serializer.save()
        log_action(
            request=self.request,
            action="GROUP_UPDATE",
            target_type="group",
            target_id=group.id,
            before_data=before,
            after_data=snapshot(group),
        )


class GroupPermissionAssignView(PermissionMapMixin, generics.GenericAPIView):
    permission_classes = [RequireInternalPermission]
    required_permission = "access.manage_auth_groups"
    serializer_class = GroupPermissionAssignSerializer

    @extend_schema(request=GroupPermissionAssignSerializer, responses=OpenApiTypes.OBJECT)
    def post(self, request, group_id: int):
        group = get_object_or_404(Group, id=group_id)
        serializer = self.get_serializer(data=request.data, context={"group": group})
        serializer.is_valid(raise_exception=True)

        before_data = {"permissions": _permission_codes_for_group(group)}
        serializer.save()
        after_data = {"permissions": _permission_codes_for_group(group)}

        log_action(
            request=request,
            action="GROUP_PERMISSION_ASSIGN",
            target_type="group",
            target_id=group.id,
            before_data=before_data,
            after_data=after_data,
        )
        return Response(after_data)


class GroupScopeAssignView(PermissionMapMixin, generics.GenericAPIView):
    permission_classes = [RequireInternalPermission]
    required_permission = "access.manage_auth_scopes"
    serializer_class = GroupScopeAssignSerializer

    @extend_schema(request=GroupScopeAssignSerializer, responses=OpenApiTypes.OBJECT)
    def post(self, request, group_id: int):
        group = get_object_or_404(Group, id=group_id)
        serializer = self.get_serializer(data=request.data, context={"group": group})
        serializer.is_valid(raise_exception=True)

        before_data = {"scopes": _scope_payload_for_group(group)}
        serializer.save()
        after_data = {"scopes": _scope_payload_for_group(group)}

        log_action(
            request=request,
            action="GROUP_SCOPE_ASSIGN",
            target_type="group",
            target_id=group.id,
            before_data=before_data,
            after_data=after_data,
        )
        return Response(after_data)


class StaffTypeListCreateView(PermissionMapMixin, generics.ListCreateAPIView):
    serializer_class = StaffTypeSerializer
    permission_classes = [RequireInternalPermission]
    required_permission = "access.manage_staff_type_groups"
    queryset = StaffType.objects.all().order_by("id")

    def perform_create(self, serializer):
        staff_type = serializer.save()
        log_action(
            request=self.request,
            action="STAFF_TYPE_CREATE",
            target_type="staff_type",
            target_id=staff_type.id,
            after_data=snapshot(staff_type),
        )


class StaffTypeDetailView(PermissionMapMixin, generics.RetrieveUpdateAPIView):
    serializer_class = StaffTypeSerializer
    permission_classes = [RequireInternalPermission]
    required_permission = "access.manage_staff_type_groups"
    queryset = StaffType.objects.all()

    def perform_update(self, serializer):
        before = snapshot(self.get_object())
        staff_type = serializer.save()
        log_action(
            request=self.request,
            action="STAFF_TYPE_UPDATE",
            target_type="staff_type",
            target_id=staff_type.id,
            before_data=before,
            after_data=snapshot(staff_type),
        )


class StaffTypeGroupAssignView(PermissionMapMixin, generics.GenericAPIView):
    permission_classes = [RequireInternalPermission]
    required_permission = "access.manage_staff_type_groups"
    serializer_class = StaffTypeGroupAssignSerializer

    @extend_schema(request=StaffTypeGroupAssignSerializer, responses=OpenApiTypes.OBJECT)
    def post(self, request, staff_type_id: int):
        staff_type = get_object_or_404(StaffType, id=staff_type_id)
        serializer = self.get_serializer(data=request.data, context={"staff_type": staff_type})
        serializer.is_valid(raise_exception=True)

        before_data = {"groups": _group_payload_for_staff_type(staff_type)}
        serializer.save()
        after_data = {"groups": _group_payload_for_staff_type(staff_type)}

        log_action(
            request=request,
            action="STAFF_TYPE_GROUP_ASSIGN",
            target_type="staff_type",
            target_id=staff_type.id,
            before_data=before_data,
            after_data=after_data,
        )
        return Response(after_data)


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


class TenantViewSet(PermissionMapMixin, generics.CreateAPIView):
    """创建租户 API"""

    serializer_class = TenantSerializer
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "POST": "access.manage_tenant",
    }
    queryset = Tenant.objects.all()

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


# ========== 平台固定角色 API ==========

class SystemRoleListCreateView(PermissionMapMixin, generics.ListCreateAPIView):
    """平台固定角色列表/创建"""

    serializer_class = SystemRoleSerializer
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "GET": "access.view_system_role",
        "POST": "access.manage_system_role",
    }
    queryset = SystemRole.objects.all().order_by("id")


class SystemRoleDetailView(PermissionMapMixin, generics.RetrieveUpdateAPIView):
    """平台固定角色详情/更新"""

    serializer_class = SystemRoleSerializer
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "GET": "access.view_system_role",
        "PUT": "access.manage_system_role",
        "PATCH": "access.manage_system_role",
    }
    queryset = SystemRole.objects.all()


# ========== 租户成员管理 API ==========

class TenantMemberListCreateView(PermissionMapMixin, generics.ListCreateAPIView):
    """租户成员列表/创建"""

    serializer_class = TenantMemberSerializer
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "GET": "access.view_tenant_member",
        "POST": "access.manage_tenant_member",
    }

    def get_queryset(self):
        qs = TenantMember.objects.select_related("tenant", "user").all()
        tenant_id = self.request.query_params.get("tenant_id")
        status = self.request.query_params.get("status")
        if tenant_id:
            qs = qs.filter(tenant_id=tenant_id)
        if status:
            qs = qs.filter(status=status)
        return qs.order_by("-id")

    def get_serializer_class(self):
        if self.request.method == "POST":
            return TenantMemberCreateSerializer
        return TenantMemberSerializer

    def get_serializer(self, *args, **kwargs):
        if hasattr(self, "get_object"):
            return super().get_serializer(*args, **kwargs)
        # 对于创建，需要传递 tenant
        if self.request.method == "POST":
            tenant_id = self.request.data.get("tenant_id")
            if not tenant_id:
                # 如果没有指定 tenant_id，获取第一个启用的租户
                tenant = Tenant.objects.filter(status=1).first()
            else:
                tenant = Tenant.objects.filter(id=tenant_id).first()
            kwargs["context"] = {"tenant": tenant}
        return super().get_serializer(*args, **kwargs)

    def perform_create(self, serializer):
        member = serializer.save()
        log_action(
            request=self.request,
            action="TENANT_MEMBER_CREATE",
            target_type="tenant_member",
            target_id=member.id,
            after_data={"id": member.id, "tenant_id": member.tenant_id, "user_id": member.user_id},
        )


class TenantMemberDetailView(PermissionMapMixin, generics.RetrieveUpdateDestroyAPIView):
    """租户成员详情/更新/删除"""

    serializer_class = TenantMemberSerializer
    permission_classes = [RequireInternalPermission]
    method_permission_map = {
        "GET": "access.view_tenant_member",
        "PUT": "access.manage_tenant_member",
        "PATCH": "access.manage_tenant_member",
        "DELETE": "access.manage_tenant_member",
    }
    queryset = TenantMember.objects.select_related("tenant", "user")

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
    """租户成员角色分配"""

    permission_classes = [RequireInternalPermission]
    required_permission = "access.assign_tenant_member_role"
    serializer_class = TenantMemberRoleAssignSerializer
    queryset = TenantMember.objects.all()

    @extend_schema(request=TenantMemberRoleAssignSerializer, responses=TenantMemberSerializer)
    def post(self, request, pk):
        member = self.get_object()
        serializer = self.get_serializer(data=request.data, context={"tenant_member": member})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        # 重新获取成员信息
        member = TenantMember.objects.select_related("tenant", "user").get(id=pk)
        return Response(TenantMemberSerializer(member).data)
