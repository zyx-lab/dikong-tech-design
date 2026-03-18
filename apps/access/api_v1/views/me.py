from rest_framework import status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiParameter, extend_schema

from apps.access.api_v1.base import IamGenericAPIView, IamAPIView, ensure_no_extra_query_params
from apps.access.api_v1.context import require_business_identity
from apps.access.api_v1.serializers.me import MeProfileResponseSerializer, MeTenantsItemSerializer
from apps.access.api_v1.services.members import list_member_role_codes
from apps.access.models import TenantMember, TenantMemberStatus, TenantStatus
from apps.api_v1.schema import BUSINESS_INTERNAL_ERROR_RESPONSE, object_envelope_serializer, paginated_envelope_serializer


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
            200: object_envelope_serializer("IamMeProfileEnvelope", MeProfileResponseSerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="读取当前业务账号自己的全局资料",
    )
    def get(self, request):
        identity = require_business_identity(request.user)
        return Response(_me_profile_payload(identity.user), status=status.HTTP_200_OK)


class MeTenantsView(IamGenericAPIView):
    @extend_schema(
        parameters=[
            OpenApiParameter(name="pageNum", type=int, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="pageSize", type=int, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={
            200: paginated_envelope_serializer("IamMeTenantsEnvelope", MeTenantsItemSerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="读取当前业务账号可进入的租户上下文列表",
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
