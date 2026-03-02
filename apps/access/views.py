from datetime import datetime

from django.contrib.auth.models import Group, Permission
from django.db import transaction
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
    EmploymentStatus,
    GroupPermissionScope,
    RegistrationApplication,
    RegistrationApplicationStatus,
    ScopeStatus,
    StaffProfile,
    StaffType,
    StaffTypeGroup,
    User,
    UserStatus,
)
from apps.access.serializers import (
    AuditLogSerializer,
    GroupPermissionAssignSerializer,
    GroupScopeAssignSerializer,
    GroupSerializer,
    MePermissionSerializer,
    PermissionCodeSerializer,
    RegistrationApplicationAdminSerializer,
    RegistrationApplicationApproveSerializer,
    RegistrationApplicationRejectSerializer,
    StaffTypeGroupAssignSerializer,
    StaffTypeSerializer,
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


def _registration_payload(application: RegistrationApplication) -> dict:
    return {
        "id": application.id,
        "application_no": application.application_no,
        "name": application.name,
        "phone": application.phone,
        "email": application.email,
        "requested_staff_type_code": application.requested_staff_type_code,
        "requested_org_id": application.requested_org_id,
        "status": application.status,
        "review_comment": application.review_comment,
        "reviewer_user_id": application.reviewer_user_id,
        "reviewed_at": application.reviewed_at.isoformat() if application.reviewed_at else None,
        "created_user_id": application.created_user_id,
        "created_staff_id": application.created_staff_id,
    }


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
        # 该接口返回“业务授权链”的有效权限，所以 superuser（非业务角色）会被拒绝。
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
                    "registration_applications": reverse("registration-application-list", request=request),
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


class ScopeMatrixView(PermissionMapMixin, APIView):
    permission_classes = [RequireInternalPermission]
    required_permission = "access.manage_auth_scopes"

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        groups = Group.objects.all().order_by("id")
        rows = []
        scopes = GroupPermissionScope.objects.select_related("group", "permission__content_type").filter(status=ScopeStatus.ACTIVE)

        matrix = {}
        for scope in scopes:
            key = f"{scope.permission.content_type.app_label}.{scope.permission.codename}"
            matrix.setdefault(key, {})[scope.group_id] = scope.scope_type

        for permission_key, group_scopes in sorted(matrix.items()):
            rows.append(
                {
                    "permission": permission_key,
                    "scopes": [{"group_id": group.id, "scope": group_scopes.get(group.id)} for group in groups],
                }
            )

        return Response({"rows": rows})


class RegistrationApplicationListView(PermissionMapMixin, generics.ListAPIView):
    """IAM 侧：注册申请列表。"""

    serializer_class = RegistrationApplicationAdminSerializer
    permission_classes = [RequireInternalPermission]
    required_permission = "access.view_registration_application"
    queryset = RegistrationApplication.objects.select_related("reviewer_user", "created_user", "created_staff").all()

    def get_queryset(self):
        qs = super().get_queryset()
        status_value = self.request.query_params.get("status")
        phone = self.request.query_params.get("phone")
        email = self.request.query_params.get("email")
        application_no = self.request.query_params.get("application_no")
        requested_staff_type_code = self.request.query_params.get("requested_staff_type_code")
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")

        if status_value:
            qs = qs.filter(status=status_value)
        if phone:
            qs = qs.filter(phone__icontains=phone)
        if email:
            qs = qs.filter(email__icontains=email)
        if application_no:
            qs = qs.filter(application_no__icontains=application_no)
        if requested_staff_type_code:
            qs = qs.filter(requested_staff_type_code=requested_staff_type_code)

        if date_from:
            start = _parse_iso_datetime(date_from)
            if start is not None:
                qs = qs.filter(created_at__gte=start)

        if date_to:
            end = _parse_iso_datetime(date_to)
            if end is not None:
                qs = qs.filter(created_at__lte=end)

        return qs.order_by("-created_at", "-id")


class RegistrationApplicationDetailView(PermissionMapMixin, generics.RetrieveAPIView):
    """IAM 侧：注册申请详情。"""

    serializer_class = RegistrationApplicationAdminSerializer
    permission_classes = [RequireInternalPermission]
    required_permission = "access.view_registration_application"
    queryset = RegistrationApplication.objects.select_related("reviewer_user", "created_user", "created_staff").all()


class RegistrationApplicationApproveView(PermissionMapMixin, generics.GenericAPIView):
    """IAM 侧：审核通过并创建正式业务账号。"""

    serializer_class = RegistrationApplicationApproveSerializer
    permission_classes = [RequireInternalPermission]
    required_permission = "access.manage_registration_application"

    @extend_schema(request=RegistrationApplicationApproveSerializer, responses=RegistrationApplicationAdminSerializer)
    def post(self, request, pk: int):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            application = get_object_or_404(RegistrationApplication.objects.select_for_update(), id=pk)

            if application.status != RegistrationApplicationStatus.PENDING_REVIEW:
                return Response(
                    {
                        "detail": "APPLICATION_NOT_PENDING_REVIEW",
                        "status": application.status,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            staff_no = serializer.validated_data["staff_no"]
            if StaffProfile.objects.filter(staff_no=staff_no).exists():
                return Response({"staff_no": ["staff_no 已存在"]}, status=status.HTTP_400_BAD_REQUEST)

            # 防止重复开户造成身份事实冲突。
            if StaffProfile.objects.filter(phone=application.phone).exists():
                return Response({"phone": ["该手机号已存在正式人员档案"]}, status=status.HTTP_400_BAD_REQUEST)
            if StaffProfile.objects.filter(email=application.email).exists():
                return Response({"email": ["该邮箱已存在正式人员档案"]}, status=status.HTTP_400_BAD_REQUEST)

            final_staff_type = serializer.resolve_final_staff_type(application.requested_staff_type_code)
            before_data = _registration_payload(application)

            user = User.objects.create_user(
                username=serializer.validated_data["username"],
                password=serializer.validated_data["password"],
                is_active=True,
                is_staff=False,
                is_superuser=False,
                status=UserStatus.ACTIVE,
            )
            staff = StaffProfile.objects.create(
                user=user,
                staff_no=staff_no,
                name=application.name,
                phone=application.phone,
                email=application.email,
                employment_status=EmploymentStatus.ACTIVE,
                staff_type=final_staff_type,
                org_id=serializer.validated_data.get("final_org_id", application.requested_org_id),
            )

            application.status = RegistrationApplicationStatus.APPROVED_ACCOUNT_CREATED
            application.reviewer_user = request.user
            application.reviewed_at = timezone.now()
            application.review_comment = serializer.validated_data.get("review_comment", "")
            application.created_user = user
            application.created_staff = staff
            application.save(
                update_fields=[
                    "status",
                    "reviewer_user",
                    "reviewed_at",
                    "review_comment",
                    "created_user",
                    "created_staff",
                    "updated_at",
                ]
            )

            log_action(
                request=request,
                action="REGISTRATION_APPLICATION_APPROVE",
                target_type="registration_application",
                target_id=application.id,
                before_data=before_data,
                after_data=_registration_payload(application),
            )
            log_action(
                request=request,
                action="REGISTRATION_ACCOUNT_CREATE",
                target_type="user",
                target_id=user.id,
                after_data=_user_payload(user),
            )

        return Response(RegistrationApplicationAdminSerializer(application).data, status=status.HTTP_200_OK)


class RegistrationApplicationRejectView(PermissionMapMixin, generics.GenericAPIView):
    """IAM 侧：驳回注册申请。"""

    serializer_class = RegistrationApplicationRejectSerializer
    permission_classes = [RequireInternalPermission]
    required_permission = "access.manage_registration_application"

    @extend_schema(request=RegistrationApplicationRejectSerializer, responses=RegistrationApplicationAdminSerializer)
    def post(self, request, pk: int):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            application = get_object_or_404(RegistrationApplication.objects.select_for_update(), id=pk)

            if application.status != RegistrationApplicationStatus.PENDING_REVIEW:
                return Response(
                    {
                        "detail": "APPLICATION_NOT_PENDING_REVIEW",
                        "status": application.status,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            before_data = _registration_payload(application)
            application.status = RegistrationApplicationStatus.REJECTED
            application.reviewer_user = request.user
            application.reviewed_at = timezone.now()
            application.review_comment = serializer.validated_data["review_comment"]
            application.save(
                update_fields=[
                    "status",
                    "reviewer_user",
                    "reviewed_at",
                    "review_comment",
                    "updated_at",
                ]
            )

            log_action(
                request=request,
                action="REGISTRATION_APPLICATION_REJECT",
                target_type="registration_application",
                target_id=application.id,
                before_data=before_data,
                after_data=_registration_payload(application),
            )

        return Response(RegistrationApplicationAdminSerializer(application).data, status=status.HTTP_200_OK)


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
