from datetime import datetime

from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema

from apps.access.api_v1.base import IamGenericAPIView, IamAPIView, ensure_empty_body, ensure_no_extra_query_params
from apps.access.api_v1.context import eligible_user_queryset, require_platform_operator
from apps.access.api_v1.openapi import (
    IAM_CONSTRAINT_CONFLICT_RESPONSE,
    IAM_FORBIDDEN_RESPONSE,
    IAM_INVALID_PARAMS_RESPONSE,
    IAM_NOT_FOUND_RESPONSE,
    IAM_PAGE_NUM_PARAMETER,
    IAM_PAGE_SIZE_PARAMETER,
    IAM_UNAUTHORIZED_RESPONSE,
    iam_path_int_parameter,
)
from apps.access.api_v1.serializers.common import AuditLogSerializer, PermissionSerializer, RoleDetailSerializer, TenantDirectorySerializer, TenantSummarySerializer
from apps.access.api_v1.serializers.platform import (
    PlatformInitializeAdminResponseSerializer,
    PlatformInitializeAdminSerializer,
    PlatformTenantCreateSerializer,
)
from apps.access.api_v1.services.members import initialize_tenant_admin, serialize_member_payload
from apps.access.api_v1.services.tenants import create_tenant, disable_tenant, enable_tenant
from apps.access.models import AuditLog, DirectoryStatus, Permission, Role, Tenant
from apps.api_v1.schema import (
    BUSINESS_INTERNAL_ERROR_RESPONSE,
    array_envelope_serializer,
    business_error_example,
    business_error_response,
    object_envelope_serializer,
    paginated_envelope_serializer,
)


def _parse_datetime(value: str | None, *, field_name: str):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise serializers.ValidationError({field_name: ["时间格式必须为 ISO 8601"]}) from exc
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


PLATFORM_PERMISSIONS_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": [
        {
            "permissionId": 1,
            "code": "iam.platform.permission.read",
            "name": "查看平台权限目录",
            "module": "iam",
            "resourceCode": "platform_permission",
            "status": "ACTIVE",
        }
    ],
}

PLATFORM_ROLES_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": [
        {
            "roleId": 1,
            "code": "platform_admin",
            "name": "平台管理员",
            "description": "平台工作态角色模板",
            "createdAt": "2026-03-19T10:00:00+08:00",
            "updatedAt": "2026-03-19T10:00:00+08:00",
            "permissionGrants": [
                {"permission": "iam.platform.tenant.read", "scopeType": "ALL"}
            ],
        }
    ],
}

PLATFORM_ROLE_DETAIL_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": {
        "roleId": 1,
        "code": "platform_admin",
        "name": "平台管理员",
        "description": "平台工作态角色模板",
        "createdAt": "2026-03-19T10:00:00+08:00",
        "updatedAt": "2026-03-19T10:00:00+08:00",
        "permissionGrants": [
            {"permission": "iam.platform.tenant.read", "scopeType": "ALL"}
        ],
    },
}

PLATFORM_AUDIT_LOGS_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": {
        "list": [
            {
                "auditLogId": 6001,
                "tenantId": 2001,
                "tenantCode": "demo_tenant",
                "action": "IAM_PLATFORM_TENANT_CREATED",
                "targetType": "tenant",
                "targetId": "2001",
                "operatorUserId": 1001,
                "operatorDisplayName": "平台管理员",
                "createdAt": "2026-03-19T10:30:00+08:00",
                "summary": "IAM_PLATFORM_TENANT_CREATED",
            }
        ],
        "total": 1,
    },
}

PLATFORM_TENANTS_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": {
        "list": [
            {
                "tenantId": 2001,
                "tenantCode": "demo_tenant",
                "name": "演示租户",
                "status": "ACTIVE",
                "plan": None,
                "createdAt": "2026-03-19T10:00:00+08:00",
                "updatedAt": "2026-03-19T10:00:00+08:00",
            }
        ],
        "total": 1,
    },
}

PLATFORM_TENANT_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": {
        "tenantId": 2001,
        "tenantCode": "demo_tenant",
        "name": "演示租户",
        "status": "ACTIVE",
        "plan": None,
        "remark": "正式租户",
        "createdAt": "2026-03-19T10:00:00+08:00",
        "updatedAt": "2026-03-19T10:00:00+08:00",
    },
}

PLATFORM_TENANT_DISABLED_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": {
        "tenantId": 2001,
        "tenantCode": "demo_tenant",
        "name": "演示租户",
        "status": "DISABLED",
        "plan": None,
        "remark": "正式租户",
        "createdAt": "2026-03-19T10:00:00+08:00",
        "updatedAt": "2026-03-19T10:20:00+08:00",
    },
}

PLATFORM_INITIALIZE_ADMIN_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": {
        "tenantId": 2001,
        "memberId": 3001,
        "userId": 1002,
        "username": "member_user",
        "displayName": None,
        "status": "ACTIVE",
        "roleCodes": ["tenant_admin"],
    },
}

PLATFORM_TENANT_DUPLICATE_RESPONSE = business_error_response(
    description="tenantCode 已存在时返回 409 + C0101。",
    examples=[
        business_error_example(
            "tenantCode 已存在",
            code="C0101",
            msg="租户编码已存在",
            status_codes=["409"],
        )
    ],
)

PLATFORM_INITIALIZE_ADMIN_CONFLICT_RESPONSE = business_error_response(
    description="目标租户状态不允许初始化或当前已存在 ACTIVE tenant_admin 时返回 409 + C0203。",
    examples=[
        business_error_example(
            "已存在租户管理员",
            code="C0203",
            msg="当前租户已存在启用的 tenant_admin",
            status_codes=["409"],
        )
    ],
)


class PlatformPermissionsView(IamAPIView):
    @extend_schema(
        responses={
            200: OpenApiResponse(
                response=array_envelope_serializer("IamPlatformPermissionsEnvelope", PermissionSerializer),
                description="权限标识：`iam.platform.permission.read`。固定目录型非分页接口；status 正式取值固定为 ACTIVE、DISABLED。",
                examples=[
                    OpenApiExample(
                        "平台权限目录示例",
                        response_only=True,
                        status_codes=["200"],
                        value=PLATFORM_PERMISSIONS_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: IAM_FORBIDDEN_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="读取平台权限目录",
    )
    def get(self, request):
        require_platform_operator(request.user)
        queryset = Permission.objects.filter(status__in=[DirectoryStatus.ACTIVE, DirectoryStatus.DISABLED]).order_by("module", "code", "id")
        payload = [
            {
                "permissionId": item.id,
                "code": item.code,
                "name": item.name,
                "module": item.module,
                "resourceCode": item.resource_code,
                "status": "ACTIVE" if item.status == DirectoryStatus.ACTIVE else "DISABLED",
            }
            for item in queryset
        ]
        return Response(payload, status=status.HTTP_200_OK)


class PlatformRolesView(IamAPIView):
    @extend_schema(
        operation_id="iam_platform_role_list",
        responses={
            200: OpenApiResponse(
                response=array_envelope_serializer("IamPlatformRolesEnvelope", RoleDetailSerializer),
                description="权限标识：`iam.platform.role.read`。固定目录型非分页接口；返回角色模板目录，不定义 status 字段。",
                examples=[
                    OpenApiExample(
                        "平台角色目录示例",
                        response_only=True,
                        status_codes=["200"],
                        value=PLATFORM_ROLES_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: IAM_FORBIDDEN_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="读取平台角色模板目录",
    )
    def get(self, request):
        require_platform_operator(request.user)
        roles = list(Role.objects.filter(status=DirectoryStatus.ACTIVE).prefetch_related("permission_grants__permission").order_by("id"))
        payload = []
        for role in roles:
            payload.append(
                {
                    "roleId": role.id,
                    "code": role.code,
                    "name": role.name,
                    "description": role.description,
                    "createdAt": role.created_at,
                    "updatedAt": role.updated_at,
                    "permissionGrants": [
                        {
                            "permission": grant.permission.code,
                            "scopeType": grant.scope_type,
                        }
                        for grant in role.permission_grants.select_related("permission").order_by("permission__code", "id")
                    ],
                }
            )
        return Response(payload, status=status.HTTP_200_OK)


class PlatformRoleDetailView(IamAPIView):
    @extend_schema(
        operation_id="iam_platform_role_detail",
        parameters=[iam_path_int_parameter("roleId", "角色模板 ID。")],
        responses={
            200: OpenApiResponse(
                response=object_envelope_serializer("IamPlatformRoleDetailEnvelope", RoleDetailSerializer),
                description="权限标识：`iam.platform.role.read`。返回角色模板详情，不定义 status 字段；permissionGrants[].permission 固定表示权限 code。",
                examples=[
                    OpenApiExample(
                        "平台角色详情示例",
                        response_only=True,
                        status_codes=["200"],
                        value=PLATFORM_ROLE_DETAIL_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: IAM_FORBIDDEN_RESPONSE,
            404: IAM_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="读取平台角色模板详情",
    )
    def get(self, request, roleId: int):
        require_platform_operator(request.user)
        role = Role.objects.filter(id=roleId, status=DirectoryStatus.ACTIVE).prefetch_related("permission_grants__permission").first()
        if role is None:
            from apps.access.exceptions import StandardNotFound

            raise StandardNotFound()
        payload = {
            "roleId": role.id,
            "code": role.code,
            "name": role.name,
            "description": role.description,
            "createdAt": role.created_at,
            "updatedAt": role.updated_at,
            "permissionGrants": [
                {
                    "permission": grant.permission.code,
                    "scopeType": grant.scope_type,
                }
                for grant in role.permission_grants.select_related("permission").order_by("permission__code", "id")
            ],
        }
        return Response(payload, status=status.HTTP_200_OK)


class PlatformAuditLogsView(IamGenericAPIView):
    @extend_schema(
        operation_id="iam_platform_audit_log_list",
        parameters=[
            OpenApiParameter(name="tenantId", type=int, location=OpenApiParameter.QUERY, required=False, description="按租户实体 ID 精确筛选。"),
            OpenApiParameter(name="action", type=str, location=OpenApiParameter.QUERY, required=False, description="按 action 精确筛选。"),
            OpenApiParameter(name="operatorUserId", type=int, location=OpenApiParameter.QUERY, required=False, description="按操作人 userId 精确筛选。"),
            OpenApiParameter(name="startAt", type=str, location=OpenApiParameter.QUERY, required=False, description="起始时间，ISO 8601。"),
            OpenApiParameter(name="endAt", type=str, location=OpenApiParameter.QUERY, required=False, description="结束时间，ISO 8601。"),
            OpenApiParameter(name="sortBy", type=str, location=OpenApiParameter.QUERY, required=False, description="仅允许 createdAt，默认 createdAt。"),
            OpenApiParameter(name="sortOrder", type=str, location=OpenApiParameter.QUERY, required=False, description="仅允许 asc、desc；默认 desc。"),
            IAM_PAGE_NUM_PARAMETER,
            IAM_PAGE_SIZE_PARAMETER,
        ],
        responses={
            200: OpenApiResponse(
                response=paginated_envelope_serializer("IamPlatformAuditLogsEnvelope", AuditLogSerializer),
                description="权限标识：`iam.platform.auditLog.read`。平台维度审计日志分页列表；默认按 createdAt desc 排序。",
                examples=[
                    OpenApiExample(
                        "平台审计日志成功示例",
                        response_only=True,
                        status_codes=["200"],
                        value=PLATFORM_AUDIT_LOGS_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: IAM_INVALID_PARAMS_RESPONSE,
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: IAM_FORBIDDEN_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="查询平台审计日志",
    )
    def get(self, request):
        require_platform_operator(request.user)
        ensure_no_extra_query_params(request, {"tenantId", "action", "operatorUserId", "startAt", "endAt", "sortBy", "sortOrder", "pageNum", "pageSize"})
        queryset = AuditLog.objects.select_related("tenant", "actor_user", "actor_user__staff_profile")
        tenant_id = request.query_params.get("tenantId")
        if tenant_id:
            try:
                queryset = queryset.filter(tenant_id=int(tenant_id))
            except ValueError as exc:
                raise serializers.ValidationError({"tenantId": ["必须为整数"]}) from exc
        action = (request.query_params.get("action") or "").strip()
        if action:
            queryset = queryset.filter(action=action)
        operator_user_id = request.query_params.get("operatorUserId")
        if operator_user_id:
            try:
                queryset = queryset.filter(actor_user_id=int(operator_user_id))
            except ValueError as exc:
                raise serializers.ValidationError({"operatorUserId": ["必须为整数"]}) from exc
        start_at = _parse_datetime(request.query_params.get("startAt"), field_name="startAt")
        if start_at:
            queryset = queryset.filter(created_at__gte=start_at)
        end_at = _parse_datetime(request.query_params.get("endAt"), field_name="endAt")
        if end_at:
            queryset = queryset.filter(created_at__lte=end_at)
        sort_by = request.query_params.get("sortBy") or "createdAt"
        sort_order = (request.query_params.get("sortOrder") or "desc").lower()
        if sort_by != "createdAt":
            raise serializers.ValidationError({"sortBy": ["仅支持 createdAt"]})
        if sort_order not in {"asc", "desc"}:
            raise serializers.ValidationError({"sortOrder": ["只允许 asc 或 desc"]})
        queryset = queryset.order_by("-created_at", "-id") if sort_order == "desc" else queryset.order_by("created_at", "id")
        page = self.paginate_queryset(queryset)
        items = page if page is not None else queryset
        payload = AuditLogSerializer(items, many=True).data
        return self.get_paginated_response(payload)


class PlatformTenantsView(IamGenericAPIView):
    @extend_schema(
        operation_id="iam_platform_tenant_list",
        parameters=[
            IAM_PAGE_NUM_PARAMETER,
            IAM_PAGE_SIZE_PARAMETER,
            OpenApiParameter(name="keywords", type=str, location=OpenApiParameter.QUERY, required=False, description="按 tenantCode、name 模糊检索。"),
            OpenApiParameter(name="status", type=str, location=OpenApiParameter.QUERY, required=False, description="仅允许 ACTIVE、DISABLED。"),
            OpenApiParameter(name="sortBy", type=str, location=OpenApiParameter.QUERY, required=False, description="仅允许 tenantId、tenantCode、name、createdAt、updatedAt；默认 tenantId。"),
            OpenApiParameter(name="sortOrder", type=str, location=OpenApiParameter.QUERY, required=False, description="仅允许 asc、desc；默认 desc。"),
        ],
        responses={
            200: OpenApiResponse(
                response=paginated_envelope_serializer("IamPlatformTenantsEnvelope", TenantDirectorySerializer),
                description="权限标识：`iam.platform.tenant.read`。平台租户实体分页列表；plan 当前固定返回 null，不支持作为正式筛选条件。",
                examples=[
                    OpenApiExample(
                        "平台租户列表示例",
                        response_only=True,
                        status_codes=["200"],
                        value=PLATFORM_TENANTS_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: IAM_INVALID_PARAMS_RESPONSE,
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: IAM_FORBIDDEN_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="查询或创建租户实体",
        description="平台侧租户实体列表入口，默认按 tenantId desc 排序。",
    )
    def get(self, request):
        require_platform_operator(request.user)
        ensure_no_extra_query_params(request, {"pageNum", "pageSize", "keywords", "status", "sortBy", "sortOrder"})
        queryset = Tenant.objects.all()
        keywords = (request.query_params.get("keywords") or "").strip()
        if keywords:
            queryset = queryset.filter(Q(code__icontains=keywords) | Q(name__icontains=keywords))
        status_value = request.query_params.get("status")
        if status_value:
            mapping = {"ACTIVE": 1, "DISABLED": 0}
            normalized = status_value.strip().upper()
            if normalized not in mapping:
                raise serializers.ValidationError({"status": ["只允许 ACTIVE 或 DISABLED"]})
            queryset = queryset.filter(status=mapping[normalized])
        sort_by = request.query_params.get("sortBy") or "tenantId"
        sort_order = (request.query_params.get("sortOrder") or "desc").lower()
        field_mapping = {
            "tenantId": "id",
            "tenantCode": "code",
            "name": "name",
            "createdAt": "created_at",
            "updatedAt": "updated_at",
        }
        if sort_by not in field_mapping:
            raise serializers.ValidationError({"sortBy": ["不支持的排序字段"]})
        if sort_order not in {"asc", "desc"}:
            raise serializers.ValidationError({"sortOrder": ["只允许 asc 或 desc"]})
        order_field = field_mapping[sort_by]
        queryset = queryset.order_by(f"-{order_field}" if sort_order == "desc" else order_field, "id")
        page = self.paginate_queryset(queryset)
        items = page if page is not None else queryset
        payload = [
            {
                "tenantId": tenant.id,
                "tenantCode": tenant.code,
                "name": tenant.name,
                "status": "ACTIVE" if tenant.status == 1 else "DISABLED",
                "plan": None,
                "createdAt": tenant.created_at,
                "updatedAt": tenant.updated_at,
            }
            for tenant in items
        ]
        return self.get_paginated_response(payload)

    @extend_schema(
        operation_id="iam_platform_tenant_create",
        request=PlatformTenantCreateSerializer,
        examples=[
            OpenApiExample(
                "创建租户请求示例",
                request_only=True,
                value={"tenantCode": "demo_tenant", "name": "演示租户", "remark": "正式租户"},
            )
        ],
        responses={
            201: OpenApiResponse(
                response=object_envelope_serializer("IamPlatformTenantCreatedEnvelope", TenantSummarySerializer),
                description="权限标识：`iam.platform.tenant.create`。创建成功后 status 固定为 ACTIVE，plan 固定返回 null。",
                examples=[
                    OpenApiExample(
                        "创建租户成功示例",
                        response_only=True,
                        status_codes=["201"],
                        value=PLATFORM_TENANT_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: IAM_INVALID_PARAMS_RESPONSE,
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: IAM_FORBIDDEN_RESPONSE,
            409: PLATFORM_TENANT_DUPLICATE_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="创建租户实体",
        description="请求体只允许 tenantCode、name、remark；不接受 status、plan、tenantId、code 等字段。",
    )
    def post(self, request):
        identity = require_platform_operator(request.user)
        serializer = PlatformTenantCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenant = create_tenant(
            actor=identity.user,
            tenant_code=serializer.validated_data["tenantCode"],
            name=serializer.validated_data["name"],
            remark=serializer.validated_data.get("remark"),
        )
        return Response(
            {
                "tenantId": tenant.id,
                "tenantCode": tenant.code,
                "name": tenant.name,
                "status": "ACTIVE",
                "plan": None,
                "remark": tenant.remark,
                "createdAt": tenant.created_at,
                "updatedAt": tenant.updated_at,
            },
            status=status.HTTP_201_CREATED,
        )


class PlatformTenantDetailView(IamAPIView):
    @extend_schema(
        operation_id="iam_platform_tenant_detail",
        parameters=[iam_path_int_parameter("tenantId", "租户实体 ID。")],
        responses={
            200: OpenApiResponse(
                response=object_envelope_serializer("IamPlatformTenantDetailEnvelope", TenantSummarySerializer),
                description="权限标识：`iam.platform.tenant.read`。返回单个租户实体详情；plan 为只读预留字段，当前固定返回 null。",
                examples=[
                    OpenApiExample(
                        "读取租户详情示例",
                        response_only=True,
                        status_codes=["200"],
                        value=PLATFORM_TENANT_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: IAM_FORBIDDEN_RESPONSE,
            404: IAM_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="读取单个租户实体",
    )
    def get(self, request, tenantId: int):
        require_platform_operator(request.user)
        tenant = Tenant.objects.filter(id=tenantId).first()
        if tenant is None:
            from apps.access.exceptions import StandardNotFound

            raise StandardNotFound()
        return Response(
            {
                "tenantId": tenant.id,
                "tenantCode": tenant.code,
                "name": tenant.name,
                "status": "ACTIVE" if tenant.status == 1 else "DISABLED",
                "plan": None,
                "remark": tenant.remark,
                "createdAt": tenant.created_at,
                "updatedAt": tenant.updated_at,
            },
            status=status.HTTP_200_OK,
        )


class PlatformTenantEnableView(IamAPIView):
    @extend_schema(
        parameters=[iam_path_int_parameter("tenantId", "租户实体 ID。")],
        request=None,
        responses={
            200: OpenApiResponse(
                response=object_envelope_serializer("IamPlatformTenantEnableEnvelope", TenantSummarySerializer),
                description="权限标识：`iam.platform.tenant.enable`。请求体固定为空；成功后返回更新后的租户对象，status 固定为 ACTIVE。",
                examples=[
                    OpenApiExample(
                        "启用租户成功示例",
                        response_only=True,
                        status_codes=["200"],
                        value=PLATFORM_TENANT_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: IAM_INVALID_PARAMS_RESPONSE,
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: IAM_FORBIDDEN_RESPONSE,
            404: IAM_NOT_FOUND_RESPONSE,
            409: IAM_CONSTRAINT_CONFLICT_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="启用租户实体",
    )
    def post(self, request, tenantId: int):
        ensure_empty_body(request)
        identity = require_platform_operator(request.user)
        tenant = Tenant.objects.filter(id=tenantId).first()
        if tenant is None:
            from apps.access.exceptions import StandardNotFound

            raise StandardNotFound()
        tenant = enable_tenant(tenant=tenant, actor=identity.user)
        return Response(
            {
                "tenantId": tenant.id,
                "tenantCode": tenant.code,
                "name": tenant.name,
                "status": "ACTIVE",
                "plan": None,
                "remark": tenant.remark,
                "createdAt": tenant.created_at,
                "updatedAt": tenant.updated_at,
            },
            status=status.HTTP_200_OK,
        )


class PlatformTenantDisableView(IamAPIView):
    @extend_schema(
        parameters=[iam_path_int_parameter("tenantId", "租户实体 ID。")],
        request=None,
        responses={
            200: OpenApiResponse(
                response=object_envelope_serializer("IamPlatformTenantDisableEnvelope", TenantSummarySerializer),
                description="权限标识：`iam.platform.tenant.disable`。请求体固定为空；成功后返回更新后的租户对象，status 固定为 DISABLED。",
                examples=[
                    OpenApiExample(
                        "停用租户成功示例",
                        response_only=True,
                        status_codes=["200"],
                        value=PLATFORM_TENANT_DISABLED_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: IAM_INVALID_PARAMS_RESPONSE,
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: IAM_FORBIDDEN_RESPONSE,
            404: IAM_NOT_FOUND_RESPONSE,
            409: IAM_CONSTRAINT_CONFLICT_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="停用租户实体",
    )
    def post(self, request, tenantId: int):
        ensure_empty_body(request)
        identity = require_platform_operator(request.user)
        tenant = Tenant.objects.filter(id=tenantId).first()
        if tenant is None:
            from apps.access.exceptions import StandardNotFound

            raise StandardNotFound()
        tenant = disable_tenant(tenant=tenant, actor=identity.user)
        return Response(
            {
                "tenantId": tenant.id,
                "tenantCode": tenant.code,
                "name": tenant.name,
                "status": "DISABLED",
                "plan": None,
                "remark": tenant.remark,
                "createdAt": tenant.created_at,
                "updatedAt": tenant.updated_at,
            },
            status=status.HTTP_200_OK,
        )


class PlatformTenantInitializeAdminView(IamAPIView):
    @extend_schema(
        parameters=[iam_path_int_parameter("tenantId", "租户实体 ID。")],
        request=PlatformInitializeAdminSerializer,
        examples=[
            OpenApiExample(
                "初始化租户管理员请求示例",
                request_only=True,
                value={"userId": 1002, "displayName": "李四"},
            )
        ],
        responses={
            200: OpenApiResponse(
                response=object_envelope_serializer("IamPlatformInitializeAdminEnvelope", PlatformInitializeAdminResponseSerializer),
                description=(
                    "权限标识：`iam.platform.tenant.initializeAdmin`。平台侧唯一允许写入租户成员关系的正式例外；"
                    "成功后 status 固定为 ACTIVE，roleCodes 中必须包含 tenant_admin。"
                ),
                examples=[
                    OpenApiExample(
                        "初始化租户管理员成功示例",
                        response_only=True,
                        status_codes=["200"],
                        value=PLATFORM_INITIALIZE_ADMIN_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: IAM_INVALID_PARAMS_RESPONSE,
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: IAM_FORBIDDEN_RESPONSE,
            404: IAM_NOT_FOUND_RESPONSE,
            409: PLATFORM_INITIALIZE_ADMIN_CONFLICT_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="为目标租户初始化租户管理员",
        description="请求体只允许 userId、displayName；不接受 roleCodes、roles、tenantId、status、qualifications 等字段。",
    )
    def post(self, request, tenantId: int):
        identity = require_platform_operator(request.user)
        serializer = PlatformInitializeAdminSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenant = Tenant.objects.filter(id=tenantId).first()
        if tenant is None:
            from apps.access.exceptions import StandardNotFound

            raise StandardNotFound()
        user = eligible_user_queryset().filter(id=serializer.validated_data["userId"]).select_related("staff_profile").first()
        if user is None:
            from apps.access.exceptions import StandardNotFound

            raise StandardNotFound()
        member = initialize_tenant_admin(
            tenant=tenant,
            actor=identity.user,
            user=user,
            display_name=serializer.validated_data.get("displayName"),
        )
        payload = serialize_member_payload(member)
        payload["tenantId"] = tenant.id
        return Response(payload, status=status.HTTP_200_OK)
