from rest_framework import status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema

from apps.access.api_v1.base import IamGenericAPIView, IamAPIView, ensure_no_extra_query_params
from apps.access.api_v1.context import require_business_identity
from apps.access.api_v1.openapi import (
    IAM_FORBIDDEN_RESPONSE,
    IAM_PAGE_NUM_PARAMETER,
    IAM_PAGE_SIZE_PARAMETER,
    IAM_UNAUTHORIZED_RESPONSE,
)
from apps.access.api_v1.serializers.me import MeProfileResponseSerializer, MeTenantsItemSerializer
from apps.access.api_v1.services.members import list_member_role_codes
from apps.access.models import TenantMember, TenantMemberStatus, TenantStatus
from apps.api_v1.schema import BUSINESS_INTERNAL_ERROR_RESPONSE, object_envelope_serializer, paginated_envelope_serializer


ME_PROFILE_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": {
        "userId": 1001,
        "username": "demo_user",
        "status": "ACTIVE",
        "createdAt": "2026-03-19T10:00:00+08:00",
        "updatedAt": "2026-03-19T10:00:00+08:00",
        "staffProfile": {
            "name": "张三",
            "phone": "13800138000",
            "email": "demo@example.com",
            "employmentStatus": "ACTIVE",
            "orgId": 10,
        },
    },
}

ME_TENANTS_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": {
        "list": [
            {
                "tenantId": 2001,
                "tenantCode": "demo_tenant",
                "name": "演示租户",
                "memberId": 3001,
                "roleCodes": ["tenant_admin", "dispatcher"],
            }
        ],
        "total": 1,
    },
}

ME_TENANTS_EMPTY_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": {"list": [], "total": 0},
}


def _me_profile_payload(user):
    staff = getattr(user, "staff_profile", None)
    staff_payload = None
    if staff:
        staff_payload = {
            "name": staff.name,
            "phone": staff.phone,
            "email": staff.email,
            "employmentStatus": "ACTIVE" if staff.employment_status == 1 else "INACTIVE",
            "orgId": staff.org_id,
        }
    return {
        "userId": user.id,
        "username": user.username,
        "status": "ACTIVE",
        "createdAt": user.created_at,
        "updatedAt": user.updated_at,
        "staffProfile": staff_payload,
    }


class MeProfileView(IamAPIView):
    @extend_schema(
        responses={
            200: OpenApiResponse(
                response=object_envelope_serializer("IamMeProfileEnvelope", MeProfileResponseSerializer),
                description=(
                    "读取当前正式 IAM 业务账号自己的全局资料。`status` 的状态域固定为 ACTIVE、DISABLED，"
                    "但本接口成功响应实际固定返回 ACTIVE；仅允许 unassigned、tenant_member 调用。"
                ),
                examples=[
                    OpenApiExample(
                        "读取个人资料成功示例",
                        response_only=True,
                        status_codes=["200"],
                        value=ME_PROFILE_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: IAM_FORBIDDEN_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="读取当前业务账号自己的全局资料",
        description="仅允许正式 IAM 业务账号运行态中的 unassigned、tenant_member 调用；platform_operator 调用时返回 403 + A0403。",
    )
    def get(self, request):
        identity = require_business_identity(request.user)
        return Response(_me_profile_payload(identity.user), status=status.HTTP_200_OK)


class MeTenantsView(IamGenericAPIView):
    @extend_schema(
        parameters=[IAM_PAGE_NUM_PARAMETER, IAM_PAGE_SIZE_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=paginated_envelope_serializer("IamMeTenantsEnvelope", MeTenantsItemSerializer),
                description=(
                    "读取当前业务账号可进入的租户上下文分页列表。仅返回“租户状态为 ACTIVE 且当前用户在该租户下成员状态也为 ACTIVE”"
                    " 的成员关系；默认 pageNum=1、pageSize=20、最大 pageSize=100，默认按 tenantId asc、memberId asc 排序。"
                ),
                examples=[
                    OpenApiExample(
                        "读取可进入租户列表示例",
                        response_only=True,
                        status_codes=["200"],
                        value=ME_TENANTS_SUCCESS_EXAMPLE,
                    ),
                    OpenApiExample(
                        "未入租用户空列表示例",
                        response_only=True,
                        status_codes=["200"],
                        value=ME_TENANTS_EMPTY_EXAMPLE,
                    ),
                ],
            ),
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: IAM_FORBIDDEN_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="读取当前业务账号可进入的租户上下文列表",
        description=(
            "本接口只接受 `pageNum`、`pageSize` 两个 query 参数；不支持 tenantId、tenantCode、status、keywords、sortBy、sortOrder。"
            "unassigned 调用时返回空列表；platform_operator 调用时返回 403 + A0403。"
        ),
    )
    def get(self, request):
        identity = require_business_identity(request.user)
        ensure_no_extra_query_params(request, {"pageNum", "pageSize"})
        queryset = (
            TenantMember.objects.filter(
                user=identity.user,
                status=TenantMemberStatus.ACTIVE,
                tenant__status=TenantStatus.ACTIVE,
            )
            .select_related("tenant")
            .prefetch_related("role_bindings__system_role")
            .order_by("tenant_id", "id")
        )
        page = self.paginate_queryset(queryset)
        items = page if page is not None else queryset
        payload = [
            {
                "tenantId": member.tenant_id,
                "tenantCode": member.tenant.code,
                "name": member.tenant.name,
                "memberId": member.id,
                "roleCodes": list_member_role_codes(member),
            }
            for member in items
        ]
        return self.get_paginated_response(payload)
