from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.access.exceptions import StandardForbidden
from apps.access.authentication import BearerAuthSessionAuthentication
from apps.api_v2.openapi import V2MeContextSerializer, list_data_serializer
from apps.access.models import DirectoryStatus, UserStatus
from apps.common.api_response import BusinessApiResponseMixin, StandardCode, standard_error_payload
from apps.iam_v2.models import (
    Department,
    PLATFORM_ROLE_CODES,
    V2AccountQualification,
    V2AccountProfile,
    V2AccountRoleProfile,
    V2AccountRoleAssignment,
    V2Permission,
    V2ProfileType,
    V2Role,
    V2RoleCustomDepartment,
)
from apps.iam_v2.serializers import (
    AccountCreateSerializer,
    AccountQualificationReadSerializer,
    AccountQualificationWriteSerializer,
    AccountReadSerializer,
    AccountRoleProfileReadSerializer,
    AccountRoleProfileUpdateSerializer,
    AccountRoleProfileWriteSerializer,
    AccountRolesReplaceSerializer,
    AccountUpdateSerializer,
    DepartmentCreateSerializer,
    DepartmentReadSerializer,
    DepartmentUpdateSerializer,
    FIXED_ROLE_ORDER,
    V2PermissionReadSerializer,
    V2ProfileTypeReadSerializer,
    V2ProfileTypeWriteSerializer,
    V2RoleDataScopeSerializer,
    V2RoleMenusReplaceSerializer,
    V2RolePermissionsReplaceSerializer,
    V2RoleReadSerializer,
    V2RoleWriteSerializer,
    ordered_v2_role_codes,
)
from apps.iam_v2.bootstrap import (
    replace_role_direct_permissions,
    replace_role_menus,
    sync_registered_permissions,
)
from apps.iam_v2.account_profile_services import (
    account_has_effective_qualification,
    active_profile_type_or_error,
    active_qualifications_queryset,
    disable_profiles_for_removed_roles,
    require_account_role_for_profile_type,
    require_active_account_role_profile,
)
from apps.iam_v2.services import (
    apply_data_scope,
    is_department_admin,
    is_platform_super_admin,
    require_platform_super_admin,
    require_v2_permission,
    resolve_v2_context,
)
from apps.resource_v2.audit import log_v2_action

User = get_user_model()


class EmptySchemaSerializer(serializers.Serializer):
    pass


class ResetPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(max_length=128)


def _duplicate_response(exc):
    return Response(
        standard_error_payload(StandardCode.DUPLICATE, "资源已存在", {"detail": str(exc)}),
        status=status.HTTP_409_CONFLICT,
    )


def _duplicate_payload_response(data):
    return Response(
        standard_error_payload(StandardCode.DUPLICATE, "资源已存在", data),
        status=status.HTTP_409_CONFLICT,
    )


def _not_found_response():
    return Response(standard_error_payload(StandardCode.NOT_FOUND, "资源不存在", None), status=status.HTTP_404_NOT_FOUND)


def _account_queryset():
    return V2AccountProfile.objects.select_related("user", "department").prefetch_related("role_assignments")


def _account_or_404(id):
    account = _account_queryset().filter(pk=id).first()
    if account is None:
        return None
    return account


def _ensure_account_visible(context, account: V2AccountProfile) -> None:
    if not apply_data_scope(context, V2AccountProfile.objects.filter(pk=account.pk), department_field="department", self_user_field="user").exists():
        raise StandardForbidden()


def _profile_type_or_404(code: str):
    return V2ProfileType.objects.filter(code=code).first()


def _account_role_profile_or_404(account: V2AccountProfile, profile_type: str):
    return V2AccountRoleProfile.objects.select_related("account_profile", "account_profile__user").filter(
        account_profile=account,
        profile_type=profile_type,
        deleted_at__isnull=True,
    ).first()


def _account_qualification_or_404(account: V2AccountProfile, qualification_id: int):
    return V2AccountQualification.objects.filter(
        account_profile=account,
        pk=qualification_id,
        deleted_at__isnull=True,
    ).first()


def _parse_bool_query(value, *, field_name: str) -> bool | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise serializers.ValidationError({field_name: [f"{field_name} 必须是 true 或 false"]})


def _phone_exists(*, phone: str, exclude_account_id: int | None = None) -> bool:
    queryset = V2AccountProfile.objects.filter(phone=phone)
    if exclude_account_id is not None:
        queryset = queryset.exclude(pk=exclude_account_id)
    return queryset.exists()


def _ordered_role_codes(role_codes):
    return ordered_v2_role_codes(role_codes)


def _replace_account_roles(*, account: V2AccountProfile, role_codes: list[str], actor):
    previous_role_codes = set(account.role_assignments.values_list("role_code", flat=True))
    next_role_codes = set(_ordered_role_codes(role_codes))
    V2AccountRoleAssignment.objects.filter(account_profile=account).delete()
    for role_code in _ordered_role_codes(role_codes):
        V2AccountRoleAssignment.objects.create(
            account_profile=account,
            role_code=role_code,
            assigned_by_user=actor,
        )
    disable_profiles_for_removed_roles(account=account, previous_role_codes=previous_role_codes, next_role_codes=next_role_codes)
    return _account_queryset().get(pk=account.pk)


def _log_account_action(*, request, context, action: str, account: V2AccountProfile, before_data=None, after_data=None):
    return log_v2_action(
        request=request,
        context=context,
        action=action,
        target_type="v2_account",
        target_id=account.id,
        resource_owner_department=account.department,
        before_data=before_data,
        after_data=after_data,
    )


def _log_department_action(*, request, context, action: str, department: Department, before_data=None, after_data=None):
    return log_v2_action(
        request=request,
        context=context,
        action=action,
        target_type="v2_department",
        target_id=department.id,
        resource_owner_department=department,
        before_data=before_data,
        after_data=after_data,
    )


def _validate_active_department(department_id):
    department = Department.objects.filter(pk=department_id, status=DirectoryStatus.ACTIVE).first()
    if department is None:
        raise serializers.ValidationError({"departmentId": ["部门不存在或未启用"]})
    return department


def _require_account_manager(context):
    require_v2_permission(context, "iam:account:read")


def _require_department_roles_only(role_codes):
    roles = list(V2Role.objects.filter(code__in=role_codes, status=DirectoryStatus.ACTIVE))
    role_by_code = {role.code: role for role in roles}
    invalid = []
    for role_code in role_codes:
        role = role_by_code.get(role_code)
        if role is None:
            invalid.append(role_code)
            continue
        if role.is_super_admin:
            invalid.append(role_code)
    if invalid:
        raise serializers.ValidationError({"roleCodes": [f"不能通过账号接口分配超管或不存在角色: {', '.join(invalid)}"]})


def _require_department_operator_roles_only(role_codes):
    roles = V2Role.objects.filter(code__in=role_codes, status=DirectoryStatus.ACTIVE)
    invalid = [
        role.code
        for role in roles
        if role.is_super_admin
        or not role.assignable_by_department_admin
        or role.data_scope == V2Role.DataScope.ALL
    ]
    missing = sorted(set(role_codes) - set(roles.values_list("code", flat=True)))
    invalid.extend(missing)
    if invalid:
        raise serializers.ValidationError({"roleCodes": [f"部门管理员只能分配可分配且非 ALL 范围角色: {', '.join(invalid)}"]})


def _existing_role_codes(account, allowed_role_codes):
    return [
        role_code
        for role_code in account.role_assignments.values_list("role_code", flat=True)
        if role_code in allowed_role_codes
    ]


def _super_role_codes() -> list[str]:
    codes = list(V2Role.objects.filter(is_super_admin=True, status=DirectoryStatus.ACTIVE).values_list("code", flat=True))
    return codes or ["platform_super_admin"]


def _is_super_admin_account(account: V2AccountProfile) -> bool:
    return account.role_assignments.filter(role_code__in=_super_role_codes()).exists()


def _ensure_not_last_active_super_account(account: V2AccountProfile) -> None:
    if not _is_super_admin_account(account):
        return
    active_super_count = V2AccountProfile.objects.filter(
        status=DirectoryStatus.ACTIVE,
        user__is_active=True,
        user__status=UserStatus.ACTIVE,
        role_assignments__role_code__in=_super_role_codes(),
    ).distinct().count()
    if active_super_count <= 1:
        raise serializers.ValidationError({"id": ["不能停用、删除或移除最后一个有效超管账号"]})


class V2IamAPIView(BusinessApiResponseMixin, GenericAPIView):
    authentication_classes = [BearerAuthSessionAuthentication]
    serializer_class = EmptySchemaSerializer


DEPARTMENT_LIST_RESPONSE = list_data_serializer("V2DepartmentListData", DepartmentReadSerializer)
ACCOUNT_LIST_RESPONSE = list_data_serializer("V2AccountListData", AccountReadSerializer)
PROFILE_TYPE_LIST_RESPONSE = list_data_serializer("V2ProfileTypeListData", V2ProfileTypeReadSerializer)
ACCOUNT_ROLE_PROFILE_LIST_RESPONSE = list_data_serializer("V2AccountRoleProfileListData", AccountRoleProfileReadSerializer)
ACCOUNT_QUALIFICATION_LIST_RESPONSE = list_data_serializer("V2AccountQualificationListData", AccountQualificationReadSerializer)
ROLE_LIST_RESPONSE = list_data_serializer("V2RoleListData", V2RoleReadSerializer)
PERMISSION_LIST_RESPONSE = list_data_serializer("V2PermissionListData", V2PermissionReadSerializer)


class MeContextView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_me_context",
        summary="获取当前 v2 登录上下文",
        description="返回当前 Bearer Token 对应用户、部门和真实 v2 角色；平台身份可能包含 platform_super_admin。",
        responses={200: OpenApiResponse(response=V2MeContextSerializer, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        return Response(
            {
                "user": {
                    "id": context.user.id,
                    "username": context.user.username,
                },
                "department": DepartmentReadSerializer(context.department).data,
                "roles": context.role_codes,
                "permissions": sorted(context.permissions),
                "dataScopes": list(context.data_scopes),
            },
            status=status.HTTP_200_OK,
        )


class DepartmentListCreateView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_departments_list",
        summary="查询 v2 部门列表",
        description="平台超管可查看全部部门；部门管理员和业务角色仅查看本部门及下级部门。",
        responses={200: OpenApiResponse(response=DEPARTMENT_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:department:read")
        if is_platform_super_admin(context):
            queryset = Department.objects.select_related("parent").order_by("path", "id")
        else:
            queryset = Department.objects.select_related("parent").filter(
                path__startswith=context.department.path,
            ).order_by("path", "id")
        serializer = DepartmentReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_iam_departments_create",
        summary="创建 v2 部门",
        description="仅平台超管可在已有部门下创建子部门；根部门由系统初始化维护。",
        request=DepartmentCreateSerializer,
        responses={201: OpenApiResponse(response=DepartmentReadSerializer, description="创建成功。")},
    )
    def post(self, request):
        context = require_platform_super_admin(request)
        require_v2_permission(context, "iam:department:create")
        serializer = DepartmentCreateSerializer(data=request.data, context={"request": request, "v2_context": context})
        serializer.is_valid(raise_exception=True)
        try:
            department = serializer.save()
        except IntegrityError as exc:
            return _duplicate_response(exc)
        data = DepartmentReadSerializer(department).data
        _log_department_action(
            request=request,
            context=context,
            action="create_department",
            department=department,
            after_data=data,
        )
        return Response(data, status=status.HTTP_201_CREATED)


class DepartmentDetailView(V2IamAPIView):
    def _department(self, id):
        department = Department.objects.filter(pk=id).first()
        if department is None:
            raise serializers.ValidationError({"id": ["部门不存在"]})
        return department

    @extend_schema(
        operation_id="v2_iam_departments_update",
        summary="更新 v2 部门",
        description="仅平台超管可更新部门名称；当前实现不支持移动部门节点。",
        request=DepartmentUpdateSerializer,
        responses={200: OpenApiResponse(response=DepartmentReadSerializer, description="更新成功。")},
    )
    def put(self, request, id: int):
        context = require_platform_super_admin(request)
        require_v2_permission(context, "iam:department:update")
        department = self._department(id)
        serializer = DepartmentUpdateSerializer(
            data=request.data,
            context={"department": department},
        )
        serializer.is_valid(raise_exception=True)
        before_data = DepartmentReadSerializer(department).data
        try:
            department = serializer.update(department, serializer.validated_data)
        except IntegrityError as exc:
            return _duplicate_response(exc)
        after_data = DepartmentReadSerializer(department).data
        _log_department_action(
            request=request,
            context=context,
            action="update_department",
            department=department,
            before_data=before_data,
            after_data=after_data,
        )
        return Response(after_data, status=status.HTTP_200_OK)


class DepartmentEnableView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_departments_enable",
        summary="启用 v2 部门",
        description="仅平台超管可启用部门；请求体固定为空对象或无请求体。",
        request=None,
        responses={200: OpenApiResponse(response=DepartmentReadSerializer, description="启用成功。")},
    )
    def post(self, request, id: int):
        context = require_platform_super_admin(request)
        require_v2_permission(context, "iam:department:enable")
        department = Department.objects.filter(pk=id).first()
        if department is None:
            return Response(standard_error_payload(StandardCode.NOT_FOUND, "资源不存在", None), status=status.HTTP_404_NOT_FOUND)
        before_data = DepartmentReadSerializer(department).data
        department.status = DirectoryStatus.ACTIVE
        department.save(update_fields=["status", "updated_at"])
        after_data = DepartmentReadSerializer(department).data
        _log_department_action(
            request=request,
            context=context,
            action="enable_department",
            department=department,
            before_data=before_data,
            after_data=after_data,
        )
        return Response(after_data, status=status.HTTP_200_OK)


class DepartmentDisableView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_departments_disable",
        summary="禁用 v2 部门",
        description="仅平台超管可禁用部门；请求体固定为空对象或无请求体。",
        request=None,
        responses={200: OpenApiResponse(response=DepartmentReadSerializer, description="禁用成功。")},
    )
    def post(self, request, id: int):
        context = require_platform_super_admin(request)
        require_v2_permission(context, "iam:department:disable")
        department = Department.objects.filter(pk=id).first()
        if department is None:
            return Response(standard_error_payload(StandardCode.NOT_FOUND, "资源不存在", None), status=status.HTTP_404_NOT_FOUND)
        before_data = DepartmentReadSerializer(department).data
        department.status = DirectoryStatus.DISABLED
        department.save(update_fields=["status", "updated_at"])
        after_data = DepartmentReadSerializer(department).data
        _log_department_action(
            request=request,
            context=context,
            action="disable_department",
            department=department,
            before_data=before_data,
            after_data=after_data,
        )
        return Response(after_data, status=status.HTTP_200_OK)


class ProfileTypeListCreateView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_profile_types_list",
        summary="查询账号档案类型",
        responses={200: OpenApiResponse(response=PROFILE_TYPE_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:profile_type:read")
        queryset = V2ProfileType.objects.order_by("sort", "id")
        serializer = V2ProfileTypeReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_iam_profile_types_create",
        summary="创建账号档案类型",
        request=V2ProfileTypeWriteSerializer,
        responses={201: OpenApiResponse(response=V2ProfileTypeReadSerializer, description="创建成功。")},
    )
    @transaction.atomic
    def post(self, request):
        context = require_platform_super_admin(request)
        require_v2_permission(context, "iam:profile_type:create")
        serializer = V2ProfileTypeWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data["code"]
        if V2ProfileType.objects.filter(code=code).exists():
            return _duplicate_payload_response({"code": ["档案类型编码已存在"]})
        profile_type = V2ProfileType.objects.create(
            code=code,
            role_code=code,
            name=serializer.validated_data["name"],
            status=serializer.validated_data.get("status", DirectoryStatus.ACTIVE),
            is_system=False,
            sort=serializer.validated_data.get("sort", 100),
            remark=serializer.validated_data.get("remark", ""),
        )
        data = V2ProfileTypeReadSerializer(profile_type).data
        log_v2_action(
            request=request,
            context=context,
            action="create_profile_type",
            target_type="v2_profile_type",
            target_id=profile_type.code,
            after_data=data,
        )
        return Response(data, status=status.HTTP_201_CREATED)


class ProfileTypeDetailView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_profile_types_retrieve",
        summary="读取账号档案类型",
        responses={200: OpenApiResponse(response=V2ProfileTypeReadSerializer, description="读取成功。")},
    )
    def get(self, request, code: str):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:profile_type:read")
        profile_type = _profile_type_or_404(code)
        if profile_type is None:
            return _not_found_response()
        return Response(V2ProfileTypeReadSerializer(profile_type).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_iam_profile_types_update",
        summary="更新账号档案类型",
        request=V2ProfileTypeWriteSerializer,
        responses={200: OpenApiResponse(response=V2ProfileTypeReadSerializer, description="更新成功。")},
    )
    @transaction.atomic
    def put(self, request, code: str):
        context = require_platform_super_admin(request)
        require_v2_permission(context, "iam:profile_type:update")
        profile_type = _profile_type_or_404(code)
        if profile_type is None:
            return _not_found_response()
        if profile_type.is_system:
            raise serializers.ValidationError({"code": ["内置档案类型不可修改"]})
        serializer = V2ProfileTypeWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data["code"] != profile_type.code:
            raise serializers.ValidationError({"code": ["档案类型编码不可通过详情接口修改"]})
        before_data = V2ProfileTypeReadSerializer(profile_type).data
        profile_type.name = serializer.validated_data["name"]
        profile_type.status = serializer.validated_data.get("status", DirectoryStatus.ACTIVE)
        profile_type.sort = serializer.validated_data.get("sort", 100)
        profile_type.remark = serializer.validated_data.get("remark", "")
        profile_type.save(update_fields=["name", "status", "sort", "remark", "updated_at"])
        data = V2ProfileTypeReadSerializer(profile_type).data
        log_v2_action(
            request=request,
            context=context,
            action="update_profile_type",
            target_type="v2_profile_type",
            target_id=profile_type.code,
            before_data=before_data,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_iam_profile_types_delete",
        summary="删除账号档案类型",
        responses={200: OpenApiResponse(response=V2ProfileTypeReadSerializer, description="删除成功。")},
    )
    @transaction.atomic
    def delete(self, request, code: str):
        context = require_platform_super_admin(request)
        require_v2_permission(context, "iam:profile_type:delete")
        profile_type = _profile_type_or_404(code)
        if profile_type is None:
            return _not_found_response()
        if profile_type.is_system:
            raise serializers.ValidationError({"code": ["内置档案类型不可删除"]})
        if (
            V2AccountRoleProfile.objects.filter(profile_type=profile_type.code).exists()
            or V2AccountQualification.objects.filter(profile_type=profile_type.code).exists()
        ):
            return Response(
                standard_error_payload(StandardCode.CONSTRAINT_CONFLICT, "资源已被引用，不能删除", {"code": [profile_type.code]}),
                status=status.HTTP_409_CONFLICT,
            )
        data = V2ProfileTypeReadSerializer(profile_type).data
        profile_type.delete()
        log_v2_action(
            request=request,
            context=context,
            action="delete_profile_type",
            target_type="v2_profile_type",
            target_id=code,
            before_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)


class RoleListView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_roles_list",
        summary="查询 v2 角色目录",
        description="返回 v2 全局角色目录；部门管理员只能将 assignableByDepartmentAdmin=true 且非 ALL 范围角色分配给本部门及下级账号。",
        responses={200: OpenApiResponse(response=ROLE_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:role:read")
        queryset = V2Role.objects.order_by("sort", "id")
        if is_department_admin(context) and not is_platform_super_admin(context):
            queryset = queryset.filter(assignable_by_department_admin=True).exclude(data_scope=V2Role.DataScope.ALL)
        serializer = V2RoleReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_iam_roles_create",
        summary="创建 v2 角色",
        request=V2RoleWriteSerializer,
        responses={201: OpenApiResponse(response=V2RoleReadSerializer, description="创建成功。")},
    )
    @transaction.atomic
    def post(self, request):
        context = require_platform_super_admin(request)
        require_v2_permission(context, "iam:role:create")
        serializer = V2RoleWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if V2Role.objects.filter(code=serializer.validated_data["code"]).exists():
            return _duplicate_payload_response({"code": ["角色编码已存在"]})
        role = V2Role.objects.create(
            code=serializer.validated_data["code"],
            name=serializer.validated_data["name"],
            status=serializer.validated_data.get("status", DirectoryStatus.ACTIVE),
            assignable_by_department_admin=serializer.validated_data.get("assignableByDepartmentAdmin", False),
            data_scope=serializer.validated_data.get("dataScope", V2Role.DataScope.SELF),
            sort=serializer.validated_data.get("sort", 100),
            remark=serializer.validated_data.get("remark", ""),
        )
        data = V2RoleReadSerializer(role).data
        log_v2_action(
            request=request,
            context=context,
            action="create_role",
            target_type="v2_role",
            target_id=role.id,
            before_data=None,
            after_data=data,
        )
        return Response(data, status=status.HTTP_201_CREATED)


class RoleDetailView(V2IamAPIView):
    def _role(self, id: int):
        role = V2Role.objects.filter(pk=id).first()
        if role is None:
            return None
        return role

    @extend_schema(
        operation_id="v2_iam_roles_retrieve",
        summary="读取 v2 角色详情",
        responses={200: OpenApiResponse(response=V2RoleReadSerializer, description="读取成功。")},
    )
    def get(self, request, id: int):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:role:read")
        role = self._role(id)
        if role is None:
            return _not_found_response()
        return Response(V2RoleReadSerializer(role).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_iam_roles_update",
        summary="更新 v2 角色",
        request=V2RoleWriteSerializer,
        responses={200: OpenApiResponse(response=V2RoleReadSerializer, description="更新成功。")},
    )
    @transaction.atomic
    def put(self, request, id: int):
        context = require_platform_super_admin(request)
        require_v2_permission(context, "iam:role:update")
        role = self._role(id)
        if role is None:
            return _not_found_response()
        if role.is_system:
            raise serializers.ValidationError({"id": ["内置系统角色不可通过接口更新"]})
        serializer = V2RoleWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if V2Role.objects.exclude(pk=role.pk).filter(code=serializer.validated_data["code"]).exists():
            return _duplicate_payload_response({"code": ["角色编码已存在"]})
        before_data = V2RoleReadSerializer(role).data
        role.code = serializer.validated_data["code"]
        role.name = serializer.validated_data["name"]
        role.status = serializer.validated_data.get("status", DirectoryStatus.ACTIVE)
        role.assignable_by_department_admin = serializer.validated_data.get("assignableByDepartmentAdmin", False)
        role.data_scope = serializer.validated_data.get("dataScope", V2Role.DataScope.SELF)
        role.sort = serializer.validated_data.get("sort", 100)
        role.remark = serializer.validated_data.get("remark", "")
        role.save()
        data = V2RoleReadSerializer(role).data
        log_v2_action(
            request=request,
            context=context,
            action="update_role",
            target_type="v2_role",
            target_id=role.id,
            before_data=before_data,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)


class RoleMenusView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_roles_menus_replace",
        summary="替换角色菜单授权",
        description="保存菜单/按钮授权后，后端自动授予这些菜单和按钮绑定的权限点。",
        request=V2RoleMenusReplaceSerializer,
        responses={200: OpenApiResponse(response=V2RoleReadSerializer, description="保存成功。")},
    )
    @transaction.atomic
    def put(self, request, id: int):
        context = require_platform_super_admin(request)
        require_v2_permission(context, "iam:role:assign_menu")
        role = V2Role.objects.filter(pk=id).first()
        if role is None:
            return _not_found_response()
        serializer = V2RoleMenusReplaceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before_data = V2RoleReadSerializer(role).data
        replace_role_menus(role=role, menu_ids=serializer.validated_data["menuIds"])
        role.refresh_from_db()
        data = V2RoleReadSerializer(role).data
        log_v2_action(
            request=request,
            context=context,
            action="assign_role_menus",
            target_type="v2_role",
            target_id=role.id,
            before_data=before_data,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)


class RolePermissionsView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_roles_permissions_replace",
        summary="替换角色高级权限授权",
        description="仅替换高级区直接授权；菜单/按钮自动带出的权限会保留。",
        request=V2RolePermissionsReplaceSerializer,
        responses={200: OpenApiResponse(response=V2RoleReadSerializer, description="保存成功。")},
    )
    @transaction.atomic
    def put(self, request, id: int):
        context = require_platform_super_admin(request)
        require_v2_permission(context, "iam:role:assign_permission")
        role = V2Role.objects.filter(pk=id).first()
        if role is None:
            return _not_found_response()
        serializer = V2RolePermissionsReplaceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before_data = V2RoleReadSerializer(role).data
        replace_role_direct_permissions(role=role, permission_ids=serializer.validated_data["permissionIds"])
        role.refresh_from_db()
        data = V2RoleReadSerializer(role).data
        log_v2_action(
            request=request,
            context=context,
            action="assign_role_permissions",
            target_type="v2_role",
            target_id=role.id,
            before_data=before_data,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)


class RoleDataScopeView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_roles_data_scope_update",
        summary="更新角色数据范围",
        request=V2RoleDataScopeSerializer,
        responses={200: OpenApiResponse(response=V2RoleReadSerializer, description="保存成功。")},
    )
    @transaction.atomic
    def put(self, request, id: int):
        context = require_platform_super_admin(request)
        require_v2_permission(context, "iam:role:update_data_scope")
        role = V2Role.objects.filter(pk=id).first()
        if role is None:
            return _not_found_response()
        if role.is_super_admin:
            raise serializers.ValidationError({"dataScope": ["超管角色数据范围固定为 ALL"]})
        serializer = V2RoleDataScopeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if role.assignable_by_department_admin and serializer.validated_data["dataScope"] == V2Role.DataScope.ALL:
            raise serializers.ValidationError({"dataScope": ["部门管理员可分配角色不能使用 ALL 数据范围"]})
        before_data = V2RoleReadSerializer(role).data
        role.data_scope = serializer.validated_data["dataScope"]
        role.save(update_fields=["data_scope", "updated_at"])
        V2RoleCustomDepartment.objects.filter(role=role).delete()
        if role.data_scope == V2Role.DataScope.CUSTOM_DEPARTMENTS:
            department_ids = serializer.validated_data.get("customDepartmentIds", [])
            departments = list(Department.objects.filter(id__in=department_ids, status=DirectoryStatus.ACTIVE))
            found_ids = {department.id for department in departments}
            missing = set(department_ids) - found_ids
            if missing:
                raise serializers.ValidationError({"customDepartmentIds": [f"部门不存在或已停用: {', '.join(str(item) for item in sorted(missing))}"]})
            for department in departments:
                V2RoleCustomDepartment.objects.create(role=role, department=department)
        role.refresh_from_db()
        data = V2RoleReadSerializer(role).data
        log_v2_action(
            request=request,
            context=context,
            action="update_role_data_scope",
            target_type="v2_role",
            target_id=role.id,
            before_data=before_data,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)


class PermissionListView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_permissions_list",
        summary="查询 v2 权限点",
        responses={200: OpenApiResponse(response=PERMISSION_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:permission:read")
        queryset = V2Permission.objects.order_by("domain", "resource", "action", "id")
        serializer = V2PermissionReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)


class PermissionSyncView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_permissions_sync",
        summary="同步后端注册的 v2 权限点",
        request=None,
        responses={200: OpenApiResponse(response=PERMISSION_LIST_RESPONSE, description="同步成功。")},
    )
    def post(self, request):
        context = require_platform_super_admin(request)
        require_v2_permission(context, "iam:permission:sync")
        before_data = {"total": V2Permission.objects.count()}
        sync_registered_permissions(disable_stale=True)
        queryset = V2Permission.objects.order_by("domain", "resource", "action", "id")
        data = {"list": V2PermissionReadSerializer(queryset, many=True).data, "total": queryset.count()}
        log_v2_action(
            request=request,
            context=context,
            action="sync_permissions",
            target_type="v2_permission",
            before_data=before_data,
            after_data={"total": queryset.count()},
        )
        return Response(data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_iam_roles_delete",
        summary="删除 v2 角色",
        responses={200: OpenApiResponse(response=V2RoleReadSerializer, description="删除成功。")},
    )
    @transaction.atomic
    def delete(self, request, id: int):
        context = require_platform_super_admin(request)
        require_v2_permission(context, "iam:role:delete")
        role = self._role(id)
        if role is None:
            return _not_found_response()
        if role.is_system or role.is_super_admin:
            raise serializers.ValidationError({"id": ["内置角色和超管角色不可删除"]})
        data = V2RoleReadSerializer(role).data
        role.delete()
        log_v2_action(
            request=request,
            context=context,
            action="delete_role",
            target_type="v2_role",
            target_id=id,
            before_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)


class AccountProfilesListCreateView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_account_profiles_list",
        summary="查询账号角色档案",
        responses={200: OpenApiResponse(response=ACCOUNT_ROLE_PROFILE_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request, id: int):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account_profile:read")
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        _ensure_account_visible(context, account)
        queryset = V2AccountRoleProfile.objects.select_related("account_profile", "account_profile__user").filter(
            account_profile=account,
            deleted_at__isnull=True,
        )
        serializer = AccountRoleProfileReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_iam_account_profiles_create",
        summary="创建账号角色档案",
        request=AccountRoleProfileWriteSerializer,
        responses={201: OpenApiResponse(response=AccountRoleProfileReadSerializer, description="创建成功。")},
    )
    @transaction.atomic
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account_profile:create")
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        _ensure_account_visible(context, account)
        serializer = AccountRoleProfileWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile_type = active_profile_type_or_error(serializer.validated_data["profileType"])
        require_account_role_for_profile_type(account, profile_type.code)
        if V2AccountRoleProfile.objects.filter(account_profile=account, profile_type=profile_type.code, deleted_at__isnull=True).exists():
            return _duplicate_payload_response({"profileType": ["账号已存在该类型档案"]})
        profile = V2AccountRoleProfile.objects.create(
            account_profile=account,
            profile_type=profile_type.code,
            display_name=serializer.validated_data["displayName"],
            level=serializer.validated_data.get("level", ""),
            status=serializer.validated_data.get("status", DirectoryStatus.ACTIVE),
            remark=serializer.validated_data.get("remark", ""),
        )
        data = AccountRoleProfileReadSerializer(profile).data
        log_v2_action(
            request=request,
            context=context,
            action="create_account_profile",
            target_type="v2_account_role_profile",
            target_id=profile.id,
            resource_owner_department=account.department,
            after_data=data,
        )
        return Response(data, status=status.HTTP_201_CREATED)


class AccountProfileDetailView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_account_profiles_retrieve",
        summary="读取账号角色档案",
        responses={200: OpenApiResponse(response=AccountRoleProfileReadSerializer, description="读取成功。")},
    )
    def get(self, request, id: int, profile_type: str):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account_profile:read")
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        _ensure_account_visible(context, account)
        profile = _account_role_profile_or_404(account, profile_type)
        if profile is None:
            return _not_found_response()
        return Response(AccountRoleProfileReadSerializer(profile).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_iam_account_profiles_update",
        summary="更新账号角色档案",
        request=AccountRoleProfileUpdateSerializer,
        responses={200: OpenApiResponse(response=AccountRoleProfileReadSerializer, description="更新成功。")},
    )
    @transaction.atomic
    def put(self, request, id: int, profile_type: str):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account_profile:update")
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        _ensure_account_visible(context, account)
        active_profile_type_or_error(profile_type)
        require_account_role_for_profile_type(account, profile_type)
        profile = _account_role_profile_or_404(account, profile_type)
        if profile is None:
            return _not_found_response()
        serializer = AccountRoleProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before_data = AccountRoleProfileReadSerializer(profile).data
        profile.display_name = serializer.validated_data["displayName"]
        profile.level = serializer.validated_data.get("level", "")
        profile.status = serializer.validated_data.get("status", DirectoryStatus.ACTIVE)
        profile.remark = serializer.validated_data.get("remark", "")
        profile.save(update_fields=["display_name", "level", "status", "remark", "updated_at"])
        data = AccountRoleProfileReadSerializer(profile).data
        log_v2_action(
            request=request,
            context=context,
            action="update_account_profile",
            target_type="v2_account_role_profile",
            target_id=profile.id,
            resource_owner_department=account.department,
            before_data=before_data,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_iam_account_profiles_delete",
        summary="删除账号角色档案",
        responses={200: OpenApiResponse(response=AccountRoleProfileReadSerializer, description="删除成功。")},
    )
    @transaction.atomic
    def delete(self, request, id: int, profile_type: str):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account_profile:delete")
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        _ensure_account_visible(context, account)
        profile = _account_role_profile_or_404(account, profile_type)
        if profile is None:
            return _not_found_response()
        before_data = AccountRoleProfileReadSerializer(profile).data
        profile.soft_delete()
        data = AccountRoleProfileReadSerializer(profile).data
        log_v2_action(
            request=request,
            context=context,
            action="delete_account_profile",
            target_type="v2_account_role_profile",
            target_id=profile.id,
            resource_owner_department=account.department,
            before_data=before_data,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)


class AccountQualificationsListCreateView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_account_qualifications_list",
        summary="查询账号资质",
        parameters=[OpenApiParameter("profileType", str, OpenApiParameter.QUERY, required=False, description="按账号档案类型过滤。")],
        responses={200: OpenApiResponse(response=ACCOUNT_QUALIFICATION_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request, id: int):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account_qualification:read")
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        _ensure_account_visible(context, account)
        profile_type = str(request.query_params.get("profileType") or "").strip()
        if profile_type:
            active_profile_type_or_error(profile_type)
        queryset = active_qualifications_queryset(account, profile_type or None)
        serializer = AccountQualificationReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_iam_account_qualifications_create",
        summary="创建账号资质",
        request=AccountQualificationWriteSerializer,
        responses={201: OpenApiResponse(response=AccountQualificationReadSerializer, description="创建成功。")},
    )
    @transaction.atomic
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account_qualification:create")
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        _ensure_account_visible(context, account)
        serializer = AccountQualificationWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile_type = active_profile_type_or_error(serializer.validated_data["profileType"])
        require_active_account_role_profile(account, profile_type.code)
        try:
            qualification = V2AccountQualification.objects.create(
                account_profile=account,
                profile_type=profile_type.code,
                qualification_type=serializer.validated_data["qualificationType"],
                certificate_no=serializer.validated_data["certificateNo"],
                issued_at=serializer.validated_data["issuedAt"],
                expires_at=serializer.validated_data["expiresAt"],
                status=serializer.validated_data["status"],
                remark=serializer.validated_data.get("remark", ""),
            )
        except IntegrityError as exc:
            return _duplicate_response(exc)
        data = AccountQualificationReadSerializer(qualification).data
        log_v2_action(
            request=request,
            context=context,
            action="create_account_qualification",
            target_type="v2_account_qualification",
            target_id=qualification.id,
            resource_owner_department=account.department,
            after_data=data,
        )
        return Response(data, status=status.HTTP_201_CREATED)


class AccountQualificationDetailView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_account_qualifications_retrieve",
        summary="读取账号资质",
        responses={200: OpenApiResponse(response=AccountQualificationReadSerializer, description="读取成功。")},
    )
    def get(self, request, id: int, qualification_id: int):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account_qualification:read")
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        _ensure_account_visible(context, account)
        qualification = _account_qualification_or_404(account, qualification_id)
        if qualification is None:
            return _not_found_response()
        return Response(AccountQualificationReadSerializer(qualification).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_iam_account_qualifications_update",
        summary="更新账号资质",
        request=AccountQualificationWriteSerializer,
        responses={200: OpenApiResponse(response=AccountQualificationReadSerializer, description="更新成功。")},
    )
    @transaction.atomic
    def put(self, request, id: int, qualification_id: int):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account_qualification:update")
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        _ensure_account_visible(context, account)
        qualification = _account_qualification_or_404(account, qualification_id)
        if qualification is None:
            return _not_found_response()
        serializer = AccountQualificationWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile_type = active_profile_type_or_error(serializer.validated_data["profileType"])
        require_active_account_role_profile(account, profile_type.code)
        before_data = AccountQualificationReadSerializer(qualification).data
        qualification.profile_type = profile_type.code
        qualification.qualification_type = serializer.validated_data["qualificationType"]
        qualification.certificate_no = serializer.validated_data["certificateNo"]
        qualification.issued_at = serializer.validated_data["issuedAt"]
        qualification.expires_at = serializer.validated_data["expiresAt"]
        qualification.status = serializer.validated_data["status"]
        qualification.remark = serializer.validated_data.get("remark", "")
        try:
            qualification.save(
                update_fields=[
                    "profile_type",
                    "qualification_type",
                    "certificate_no",
                    "issued_at",
                    "expires_at",
                    "status",
                    "remark",
                    "updated_at",
                ]
            )
        except IntegrityError as exc:
            return _duplicate_response(exc)
        data = AccountQualificationReadSerializer(qualification).data
        log_v2_action(
            request=request,
            context=context,
            action="update_account_qualification",
            target_type="v2_account_qualification",
            target_id=qualification.id,
            resource_owner_department=account.department,
            before_data=before_data,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_iam_account_qualifications_delete",
        summary="删除账号资质",
        responses={200: OpenApiResponse(response=AccountQualificationReadSerializer, description="删除成功。")},
    )
    @transaction.atomic
    def delete(self, request, id: int, qualification_id: int):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account_qualification:delete")
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        _ensure_account_visible(context, account)
        qualification = _account_qualification_or_404(account, qualification_id)
        if qualification is None:
            return _not_found_response()
        before_data = AccountQualificationReadSerializer(qualification).data
        qualification.soft_delete()
        data = AccountQualificationReadSerializer(qualification).data
        log_v2_action(
            request=request,
            context=context,
            action="delete_account_qualification",
            target_type="v2_account_qualification",
            target_id=qualification.id,
            resource_owner_department=account.department,
            before_data=before_data,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)


class AccountListCreateView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_accounts_list",
        summary="查询 v2 账号列表",
        description="平台超管可查询全部账号；部门管理员仅查询本部门账号。",
        parameters=[
            OpenApiParameter("departmentId", int, OpenApiParameter.QUERY, required=False, description="按部门 ID 精确过滤。"),
            OpenApiParameter("status", int, OpenApiParameter.QUERY, required=False, description="按状态过滤：0 disabled，1 active。"),
            OpenApiParameter("keywords", str, OpenApiParameter.QUERY, required=False, description="按用户名模糊过滤。"),
            OpenApiParameter("roleCode", str, OpenApiParameter.QUERY, required=False, description="按账号角色编码过滤。"),
            OpenApiParameter("profileType", str, OpenApiParameter.QUERY, required=False, description="按有效账号档案类型过滤。"),
            OpenApiParameter("qualified", bool, OpenApiParameter.QUERY, required=False, description="为 true 时，要求 profileType 对应至少一条有效资质。"),
        ],
        responses={200: OpenApiResponse(response=ACCOUNT_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account:read")
        queryset = _account_queryset().order_by("id")
        queryset = apply_data_scope(context, queryset, department_field="department", self_user_field="user")

        department_id = request.query_params.get("departmentId")
        if department_id not in (None, ""):
            try:
                normalized_department_id = int(department_id)
            except (TypeError, ValueError) as exc:
                raise serializers.ValidationError({"departmentId": ["departmentId 必须是整数"]}) from exc
            queryset = queryset.filter(department_id=normalized_department_id)

        status_value = request.query_params.get("status")
        if status_value not in (None, ""):
            try:
                normalized_status = int(status_value)
            except (TypeError, ValueError) as exc:
                raise serializers.ValidationError({"status": ["status 必须是 0 或 1"]}) from exc
            if normalized_status not in {DirectoryStatus.ACTIVE, DirectoryStatus.DISABLED}:
                raise serializers.ValidationError({"status": ["status 必须是 0 或 1"]})
            queryset = queryset.filter(status=normalized_status)

        keywords = str(request.query_params.get("keywords") or "").strip()
        if keywords:
            queryset = queryset.filter(user__username__icontains=keywords)

        role_code = str(request.query_params.get("roleCode") or "").strip()
        if role_code:
            if not V2Role.objects.filter(code=role_code, status=DirectoryStatus.ACTIVE).exists():
                raise serializers.ValidationError({"roleCode": ["角色不存在或已停用"]})
            queryset = queryset.filter(role_assignments__role_code=role_code)

        profile_type = str(request.query_params.get("profileType") or "").strip()
        if profile_type:
            active_profile_type_or_error(profile_type)
            queryset = queryset.filter(
                role_profiles__profile_type=profile_type,
                role_profiles__status=DirectoryStatus.ACTIVE,
                role_profiles__deleted_at__isnull=True,
            )

        qualified = _parse_bool_query(request.query_params.get("qualified"), field_name="qualified")
        if qualified is True:
            if not profile_type:
                raise serializers.ValidationError({"profileType": ["qualified=true 时必须指定 profileType"]})
            today = timezone.now().date()
            queryset = queryset.filter(
                qualifications__profile_type=profile_type,
                qualifications__status=DirectoryStatus.ACTIVE,
                qualifications__deleted_at__isnull=True,
                qualifications__issued_at__lte=today,
                qualifications__expires_at__gte=today,
            )
        elif qualified is False:
            pass

        queryset = queryset.distinct()
        serializer = AccountReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_iam_accounts_create",
        summary="创建 v2 账号",
        description=(
            "平台超管可创建任意部门账号并分配部门角色；部门管理员只能创建本部门操作型角色账号。"
            "platform_super_admin 是平台身份，不能通过此接口分配。"
        ),
        request=AccountCreateSerializer,
        responses={201: OpenApiResponse(response=AccountReadSerializer, description="创建成功。")},
    )
    @transaction.atomic
    def post(self, request):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account:create")
        serializer = AccountCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        department = _validate_active_department(serializer.validated_data["departmentId"])
        if not is_platform_super_admin(context) and not department.path.startswith(context.department.path):
            raise StandardForbidden()

        role_codes = serializer.validated_data["roleCodes"]
        _require_department_roles_only(role_codes)
        if not is_platform_super_admin(context):
            _require_department_operator_roles_only(role_codes)

        username = serializer.validated_data["username"]
        if User.objects.filter(username=username).exists():
            return _duplicate_payload_response({"username": ["用户名已存在"]})
        phone = serializer.validated_data["phone"]
        if _phone_exists(phone=phone):
            return _duplicate_payload_response({"phone": ["手机号已存在"]})

        account_status = int(serializer.validated_data.get("status", DirectoryStatus.ACTIVE))
        user = User.objects.create_user(
            username=username,
            password=serializer.validated_data["password"],
            status=account_status,
            is_active=account_status == DirectoryStatus.ACTIVE,
            is_staff=False,
            is_superuser=False,
            is_platform_admin=False,
        )
        account = V2AccountProfile.objects.create(
            user=user,
            department=department,
            name=serializer.validated_data["name"],
            phone=phone,
            email=serializer.validated_data.get("email", ""),
            status=account_status,
        )
        account = _replace_account_roles(account=account, role_codes=role_codes, actor=request.user)
        data = AccountReadSerializer(account).data
        _log_account_action(
            request=request,
            context=context,
            action="create_account",
            account=account,
            after_data=data,
        )
        return Response(data, status=status.HTTP_201_CREATED)


class AccountDetailView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_accounts_update",
        summary="更新 v2 账号",
        description="仅平台超管可更新账号用户名、密码、部门和状态；password 可不传。",
        request=AccountUpdateSerializer,
        responses={200: OpenApiResponse(response=AccountReadSerializer, description="更新成功。")},
    )
    @transaction.atomic
    def put(self, request, id: int):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account:update")
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        if not apply_data_scope(context, V2AccountProfile.objects.filter(pk=account.pk), department_field="department", self_user_field="user").exists():
            raise StandardForbidden()
        serializer = AccountUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        department = _validate_active_department(serializer.validated_data["departmentId"])
        if not is_platform_super_admin(context) and not department.path.startswith(context.department.path):
            raise StandardForbidden()
        username = serializer.validated_data["username"]
        if User.objects.exclude(pk=account.user_id).filter(username=username).exists():
            return _duplicate_payload_response({"username": ["用户名已存在"]})
        phone = serializer.validated_data["phone"]
        if _phone_exists(phone=phone, exclude_account_id=account.id):
            return _duplicate_payload_response({"phone": ["手机号已存在"]})

        before_data = AccountReadSerializer(account).data
        old_department_id = account.department_id
        account_status = int(serializer.validated_data["status"])
        if account_status != DirectoryStatus.ACTIVE:
            _ensure_not_last_active_super_account(account)
        user = account.user
        user.username = username
        user.status = account_status
        user.is_active = account_status == UserStatus.ACTIVE
        update_fields = ["username", "status", "is_active", "updated_at"]
        password = serializer.validated_data.get("password")
        if password:
            require_v2_permission(context, "iam:account:reset_password")
            user.set_password(password)
            update_fields.append("password")
        user.save(update_fields=update_fields)

        account.department = department
        account.name = serializer.validated_data["name"]
        account.phone = phone
        account.email = serializer.validated_data.get("email", "")
        account.status = account_status
        account.save(update_fields=["department", "name", "phone", "email", "status", "updated_at"])
        account = _account_queryset().get(pk=account.pk)
        after_data = AccountReadSerializer(account).data
        _log_account_action(
            request=request,
            context=context,
            action="update_account",
            account=account,
            before_data=before_data,
            after_data=after_data,
        )
        if old_department_id != account.department_id:
            _log_account_action(
                request=request,
                context=context,
                action="change_account_department",
                account=account,
                before_data=before_data,
                after_data=after_data,
            )
        return Response(after_data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_iam_accounts_delete",
        summary="删除 v2 账号",
        description="删除账号及 v2 账号档案；不能删除最后一个有效超管账号。",
        responses={200: OpenApiResponse(response=AccountReadSerializer, description="删除成功。")},
    )
    @transaction.atomic
    def delete(self, request, id: int):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account:delete")
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        if not apply_data_scope(context, V2AccountProfile.objects.filter(pk=account.pk), department_field="department", self_user_field="user").exists():
            raise StandardForbidden()
        _ensure_not_last_active_super_account(account)
        before_data = AccountReadSerializer(account).data
        owner_department = account.department
        user = account.user
        account.delete()
        user.delete()
        log_v2_action(
            request=request,
            context=context,
            action="delete_account",
            target_type="v2_account",
            target_id=id,
            resource_owner_department=owner_department,
            before_data=before_data,
        )
        return Response(before_data, status=status.HTTP_200_OK)


class AccountEnableView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_accounts_enable",
        summary="启用 v2 账号",
        request=None,
        responses={200: OpenApiResponse(response=AccountReadSerializer, description="启用成功。")},
    )
    @transaction.atomic
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account:enable")
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        if not apply_data_scope(context, V2AccountProfile.objects.filter(pk=account.pk), department_field="department", self_user_field="user").exists():
            raise StandardForbidden()
        before_data = AccountReadSerializer(account).data
        account.status = DirectoryStatus.ACTIVE
        account.save(update_fields=["status", "updated_at"])
        user = account.user
        user.status = UserStatus.ACTIVE
        user.is_active = True
        user.save(update_fields=["status", "is_active", "updated_at"])
        account = _account_queryset().get(pk=account.pk)
        after_data = AccountReadSerializer(account).data
        _log_account_action(
            request=request,
            context=context,
            action="enable_account",
            account=account,
            before_data=before_data,
            after_data=after_data,
        )
        return Response(after_data, status=status.HTTP_200_OK)


class AccountDisableView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_accounts_disable",
        summary="禁用 v2 账号",
        request=None,
        responses={200: OpenApiResponse(response=AccountReadSerializer, description="禁用成功。")},
    )
    @transaction.atomic
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account:disable")
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        if not apply_data_scope(context, V2AccountProfile.objects.filter(pk=account.pk), department_field="department", self_user_field="user").exists():
            raise StandardForbidden()
        _ensure_not_last_active_super_account(account)
        before_data = AccountReadSerializer(account).data
        account.status = DirectoryStatus.DISABLED
        account.save(update_fields=["status", "updated_at"])
        user = account.user
        user.status = UserStatus.DISABLED
        user.is_active = False
        user.save(update_fields=["status", "is_active", "updated_at"])
        account = _account_queryset().get(pk=account.pk)
        after_data = AccountReadSerializer(account).data
        _log_account_action(
            request=request,
            context=context,
            action="disable_account",
            account=account,
            before_data=before_data,
            after_data=after_data,
        )
        return Response(after_data, status=status.HTTP_200_OK)


class AccountResetPasswordView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_accounts_reset_password",
        summary="重置 v2 账号密码",
        request=ResetPasswordSerializer,
        responses={200: OpenApiResponse(response=AccountReadSerializer, description="重置成功。")},
    )
    @transaction.atomic
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account:reset_password")
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        if not apply_data_scope(context, V2AccountProfile.objects.filter(pk=account.pk), department_field="department", self_user_field="user").exists():
            raise StandardForbidden()
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before_data = AccountReadSerializer(account).data
        account.user.set_password(serializer.validated_data["password"])
        account.user.save(update_fields=["password", "updated_at"])
        after_data = AccountReadSerializer(account).data
        _log_account_action(
            request=request,
            context=context,
            action="reset_account_password",
            account=account,
            before_data=before_data,
            after_data=after_data,
        )
        return Response(after_data, status=status.HTTP_200_OK)


class AccountRolesView(V2IamAPIView):
    @extend_schema(
        operation_id="v2_iam_accounts_roles_replace",
        summary="替换 v2 账号角色",
        description=(
            "平台超管可替换部门角色；部门管理员只能维护本部门账号的操作型角色，"
            "platform_super_admin 和 department_admin 等受保护角色会保留。"
        ),
        request=AccountRolesReplaceSerializer,
        responses={200: OpenApiResponse(response=AccountReadSerializer, description="替换成功。")},
    )
    @transaction.atomic
    def put(self, request, id: int):
        context = resolve_v2_context(request)
        require_v2_permission(context, "iam:account:assign_role")
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        if not apply_data_scope(context, V2AccountProfile.objects.filter(pk=account.pk), department_field="department", self_user_field="user").exists():
            raise StandardForbidden()
        serializer = AccountRolesReplaceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        requested_role_codes = serializer.validated_data["roleCodes"]
        _require_department_roles_only(requested_role_codes)

        if is_platform_super_admin(context):
            existing_platform_role_codes = _existing_role_codes(account, PLATFORM_ROLE_CODES)
            next_role_codes = _ordered_role_codes(existing_platform_role_codes + requested_role_codes)
        else:
            _require_department_operator_roles_only(requested_role_codes)
            protected_role_codes = [
                role_code
                for role_code in account.role_assignments.values_list("role_code", flat=True)
                if not V2Role.objects.filter(
                    code=role_code,
                    assignable_by_department_admin=True,
                    is_super_admin=False,
                )
                .exclude(data_scope=V2Role.DataScope.ALL)
                .exists()
            ]
            next_role_codes = _ordered_role_codes(protected_role_codes + requested_role_codes)

        before_data = AccountReadSerializer(account).data
        account = _replace_account_roles(account=account, role_codes=next_role_codes, actor=request.user)
        after_data = AccountReadSerializer(account).data
        _log_account_action(
            request=request,
            context=context,
            action="change_account_roles",
            account=account,
            before_data=before_data,
            after_data=after_data,
        )
        return Response(after_data, status=status.HTTP_200_OK)
