from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rest_framework import serializers, status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.access.exceptions import StandardForbidden
from apps.access.authentication import BearerAuthSessionAuthentication
from apps.access.models import DirectoryStatus, UserStatus
from apps.common.api_response import BusinessApiResponseMixin, StandardCode, standard_error_payload
from apps.iam_v2.models import Department, V2AccountProfile, V2AccountRoleAssignment
from apps.iam_v2.serializers import (
    AccountCreateSerializer,
    AccountReadSerializer,
    AccountRolesReplaceSerializer,
    AccountUpdateSerializer,
    BUSINESS_ROLE_CODES,
    DepartmentCreateSerializer,
    DepartmentReadSerializer,
    DepartmentUpdateSerializer,
    FIXED_ROLE_ORDER,
    SYSTEM_ROLE_CODES,
    fixed_role_payloads,
)
from apps.iam_v2.services import is_department_admin, is_platform_super_admin, require_platform_super_admin, resolve_v2_context
from apps.resource_v2.audit import log_v2_action

User = get_user_model()


class EmptySchemaSerializer(serializers.Serializer):
    pass


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
    return V2AccountProfile.objects.select_related("user", "department", "department__tenant").prefetch_related("role_assignments")


def _account_or_404(id):
    account = _account_queryset().filter(pk=id).first()
    if account is None:
        return None
    return account


def _ordered_role_codes(role_codes):
    role_set = set(role_codes)
    return [role_code for role_code in FIXED_ROLE_ORDER if role_code in role_set]


def _replace_account_roles(*, account: V2AccountProfile, role_codes: list[str], actor):
    V2AccountRoleAssignment.objects.filter(account_profile=account).delete()
    for role_code in _ordered_role_codes(role_codes):
        V2AccountRoleAssignment.objects.create(
            account_profile=account,
            role_code=role_code,
            assigned_by_user=actor,
        )
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
    if not (is_platform_super_admin(context) or is_department_admin(context)):
        raise StandardForbidden()


def _require_business_roles_only(role_codes):
    invalid = [role_code for role_code in role_codes if role_code not in BUSINESS_ROLE_CODES]
    if invalid:
        raise serializers.ValidationError({"roleCodes": [f"部门管理员只能分配业务角色: {', '.join(invalid)}"]})


class V2IamAPIView(BusinessApiResponseMixin, GenericAPIView):
    authentication_classes = [BearerAuthSessionAuthentication]
    serializer_class = EmptySchemaSerializer


class MeContextView(V2IamAPIView):
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
            },
            status=status.HTTP_200_OK,
        )


class DepartmentListCreateView(V2IamAPIView):
    def get(self, request):
        context = resolve_v2_context(request)
        if "platform_super_admin" in context.role_codes:
            queryset = Department.objects.select_related("tenant", "parent").order_by("path", "id")
        else:
            queryset = Department.objects.select_related("tenant", "parent").filter(
                tenant=context.department.tenant,
                path__startswith=context.department.path,
            ).order_by("path", "id")
        serializer = DepartmentReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)

    def post(self, request):
        context = require_platform_super_admin(request)
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

    def put(self, request, id: int):
        context = require_platform_super_admin(request)
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
    def post(self, request, id: int):
        context = require_platform_super_admin(request)
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
    def post(self, request, id: int):
        context = require_platform_super_admin(request)
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


class RoleListView(V2IamAPIView):
    def get(self, request):
        resolve_v2_context(request)
        items = fixed_role_payloads()
        return Response({"list": items, "total": len(items)}, status=status.HTTP_200_OK)


class AccountListCreateView(V2IamAPIView):
    def get(self, request):
        context = resolve_v2_context(request)
        _require_account_manager(context)
        queryset = _account_queryset().order_by("id")

        if is_department_admin(context) and not is_platform_super_admin(context):
            queryset = queryset.filter(department=context.department)

        department_id = request.query_params.get("departmentId")
        if department_id not in (None, ""):
            try:
                normalized_department_id = int(department_id)
            except (TypeError, ValueError) as exc:
                raise serializers.ValidationError({"departmentId": ["departmentId 必须是整数"]}) from exc
            if is_department_admin(context) and not is_platform_super_admin(context) and normalized_department_id != context.department.id:
                raise StandardForbidden()
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

        serializer = AccountReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request):
        context = resolve_v2_context(request)
        _require_account_manager(context)
        serializer = AccountCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        department = _validate_active_department(serializer.validated_data["departmentId"])
        if is_department_admin(context) and not is_platform_super_admin(context) and department.id != context.department.id:
            raise StandardForbidden()

        role_codes = serializer.validated_data["roleCodes"]
        if is_department_admin(context) and not is_platform_super_admin(context):
            _require_business_roles_only(role_codes)

        username = serializer.validated_data["username"]
        if User.objects.filter(username=username).exists():
            return _duplicate_payload_response({"username": ["用户名已存在"]})

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
        account = V2AccountProfile.objects.create(user=user, department=department, status=account_status)
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
    @transaction.atomic
    def put(self, request, id: int):
        context = require_platform_super_admin(request)
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        serializer = AccountUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        department = _validate_active_department(serializer.validated_data["departmentId"])
        username = serializer.validated_data["username"]
        if User.objects.exclude(pk=account.user_id).filter(username=username).exists():
            return _duplicate_payload_response({"username": ["用户名已存在"]})

        before_data = AccountReadSerializer(account).data
        old_department_id = account.department_id
        account_status = int(serializer.validated_data["status"])
        user = account.user
        user.username = username
        user.status = account_status
        user.is_active = account_status == UserStatus.ACTIVE
        update_fields = ["username", "status", "is_active", "updated_at"]
        password = serializer.validated_data.get("password")
        if password:
            user.set_password(password)
            update_fields.append("password")
        user.save(update_fields=update_fields)

        account.department = department
        account.status = account_status
        account.save(update_fields=["department", "status", "updated_at"])
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


class AccountRolesView(V2IamAPIView):
    @transaction.atomic
    def put(self, request, id: int):
        context = resolve_v2_context(request)
        _require_account_manager(context)
        account = _account_or_404(id)
        if account is None:
            return _not_found_response()
        serializer = AccountRolesReplaceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        requested_role_codes = serializer.validated_data["roleCodes"]

        if is_platform_super_admin(context):
            next_role_codes = requested_role_codes
        else:
            if account.department_id != context.department.id:
                raise StandardForbidden()
            _require_business_roles_only(requested_role_codes)
            existing_system_role_codes = [
                role_code
                for role_code in account.role_assignments.values_list("role_code", flat=True)
                if role_code in SYSTEM_ROLE_CODES
            ]
            next_role_codes = _ordered_role_codes(existing_system_role_codes + requested_role_codes)

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
