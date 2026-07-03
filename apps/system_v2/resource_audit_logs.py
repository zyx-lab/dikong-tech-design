from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response

from apps.access.exceptions import StandardForbidden
from apps.api_contracts.openapi import list_data_serializer
from apps.audit_v2.models import V2AuditLog
from apps.audit_v2.serializers import AuditLogReadSerializer
from apps.iam_v2.services import is_department_admin, is_platform_super_admin, resolve_v2_context
from apps.system_v2.base import SystemV2APIView


AUDIT_LOG_LIST_RESPONSE = list_data_serializer("V2AuditLogListData", AuditLogReadSerializer)


class ResourceAuditLogListView(SystemV2APIView):
    @extend_schema(
        operation_id="v2_resource_audit_logs_list",
        summary="查询 v2 资源审计日志",
        parameters=[
            OpenApiParameter("resourceType", str, OpenApiParameter.QUERY, required=False, description="按资源类型过滤。"),
            OpenApiParameter(
                "resourceObjectId",
                str,
                OpenApiParameter.QUERY,
                required=False,
                description="按资源对象 ID 过滤。",
            ),
        ],
        responses={200: OpenApiResponse(response=AUDIT_LOG_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        if not (is_platform_super_admin(context) or is_department_admin(context)):
            raise StandardForbidden()
        queryset = V2AuditLog.objects.select_related("actor_department", "resource_owner_department")
        if not is_platform_super_admin(context):
            queryset = queryset.filter(actor_department=context.department)
        resource_type = request.query_params.get("resourceType")
        resource_id = request.query_params.get("resourceObjectId")
        if resource_type:
            queryset = queryset.filter(resource_type=resource_type)
        if resource_id:
            queryset = queryset.filter(resource_object_id=str(resource_id))
        queryset = queryset.order_by("-created_at", "-id")
        serializer = AuditLogReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)
