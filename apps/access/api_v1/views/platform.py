from datetime import datetime

from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiParameter, extend_schema

from apps.access.api_v1.base import IamGenericAPIView, IamAPIView, ensure_empty_body, ensure_no_extra_query_params
from apps.access.api_v1.context import eligible_user_queryset, require_platform_operator
from apps.access.api_v1.serializers.common import AuditLogSerializer, PermissionSerializer, RoleDetailSerializer, TenantDirectorySerializer, TenantSummarySerializer
from apps.access.api_v1.serializers.platform import (
    PlatformInitializeAdminResponseSerializer,
    PlatformInitializeAdminSerializer,
    PlatformTenantCreateSerializer,
)
from apps.access.api_v1.services.members import initialize_tenant_admin, serialize_member_payload
from apps.access.api_v1.services.tenants import create_tenant, disable_tenant, enable_tenant
from apps.access.models import AuditLog, DirectoryStatus, Permission, Role, Tenant
from apps.api_v1.schema import BUSINESS_INTERNAL_ERROR_RESPONSE, array_envelope_serializer, object_envelope_serializer, paginated_envelope_serializer


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


class PlatformPermissionsView(IamAPIView):
    @extend_schema(
        responses={
            200: array_envelope_serializer("IamPlatformPermissionsEnvelope", PermissionSerializer),
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
            200: array_envelope_serializer("IamPlatformRolesEnvelope", RoleDetailSerializer),
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
        responses={
            200: object_envelope_serializer("IamPlatformRoleDetailEnvelope", RoleDetailSerializer),
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
            OpenApiParameter(name="tenantId", type=int, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="action", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="operatorUserId", type=int, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="startAt", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="endAt", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="sortBy", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="sortOrder", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="pageNum", type=int, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="pageSize", type=int, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={
            200: paginated_envelope_serializer("IamPlatformAuditLogsEnvelope", AuditLogSerializer),
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
            OpenApiParameter(name="pageNum", type=int, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="pageSize", type=int, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="keywords", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="status", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="sortBy", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="sortOrder", type=str, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={
            200: paginated_envelope_serializer("IamPlatformTenantsEnvelope", TenantDirectorySerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="查询或创建租户实体",
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
        responses={
            201: object_envelope_serializer("IamPlatformTenantCreatedEnvelope", TenantSummarySerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="创建租户实体",
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
        responses={
            200: object_envelope_serializer("IamPlatformTenantDetailEnvelope", TenantSummarySerializer),
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
        request=None,
        responses={
            200: object_envelope_serializer("IamPlatformTenantEnableEnvelope", TenantSummarySerializer),
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
        request=None,
        responses={
            200: object_envelope_serializer("IamPlatformTenantDisableEnvelope", TenantSummarySerializer),
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
        request=PlatformInitializeAdminSerializer,
        responses={
            200: object_envelope_serializer("IamPlatformInitializeAdminEnvelope", PlatformInitializeAdminResponseSerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="为目标租户初始化租户管理员",
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
