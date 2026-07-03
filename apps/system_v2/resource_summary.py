from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response

from apps.access.models import DirectoryStatus
from apps.api_contracts.openapi import V2ResourceSummarySerializer
from apps.iam_v2.models import FixedRole, V2AccountRoleProfile
from apps.iam_v2.services import department_tree_filter, require_v2_operation_permission, resolve_v2_context
from apps.inspection_v2.models import FlightSession, FlightSessionStatus
from apps.resource_v2.models import ResourceType
from apps.resource_v2.serializers import serialize_resource_binding
from apps.resource_v2.services import visible_bindings_queryset
from apps.system_v2.base import SystemV2APIView


class ResourceSummaryView(SystemV2APIView):
    @extend_schema(
        operation_id="v2_resource_summary",
        summary="查询资源总览",
        responses={200: OpenApiResponse(response=V2ResourceSummarySerializer, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        require_v2_operation_permission(context, "view")
        summary = {}
        department_rows = {}

        def department_row(department):
            if department.id not in department_rows:
                department_rows[department.id] = {
                    "departmentId": department.id,
                    "departmentName": department.name,
                    "departmentPath": department.path,
                    "drones": 0,
                    "docks": 0,
                    "gateways": 0,
                    "payloads": 0,
                    "pilots": 0,
                }
            return department_rows[department.id]

        for resource_type in (ResourceType.DRONE, ResourceType.DOCK, ResourceType.GATEWAY, ResourceType.PAYLOAD):
            queryset = visible_bindings_queryset(context, resource_type=resource_type)
            items = [serialize_resource_binding(binding, context=context) for binding in queryset]
            visible_ids = [item["id"] for item in items]
            if resource_type == ResourceType.DRONE:
                occupied = FlightSession.objects.filter(status=FlightSessionStatus.RUNNING, drone_id__in=visible_ids).count()
            elif resource_type == ResourceType.DOCK:
                occupied = FlightSession.objects.filter(status=FlightSessionStatus.RUNNING, dock_id__in=visible_ids).count()
            elif resource_type == ResourceType.GATEWAY:
                occupied = FlightSession.objects.filter(status=FlightSessionStatus.RUNNING, executor_id__in=visible_ids).count()
            else:
                occupied = FlightSession.objects.filter(status=FlightSessionStatus.RUNNING, payload_id__in=visible_ids).count()
            summary[f"{resource_type}s"] = {
                "total": len(items),
                "online": sum(1 for item in items if item.get("onlineStatus")),
                "available": max(0, len(items) - occupied),
                "occupied": occupied,
            }
            for binding in queryset:
                department_row(binding.owner_department)[f"{resource_type}s"] += 1

        pilot_queryset = V2AccountRoleProfile.objects.select_related("account_profile__department").filter(
            profile_type=FixedRole.PILOT,
            deleted_at__isnull=True,
        )
        if not context.is_super_admin:
            pilot_queryset = pilot_queryset.filter(department_tree_filter(context, "account_profile__department"))
        summary["pilots"] = {
            "total": pilot_queryset.count(),
            "active": pilot_queryset.filter(status=DirectoryStatus.ACTIVE).count(),
            "disabled": pilot_queryset.filter(status=DirectoryStatus.DISABLED).count(),
        }
        for pilot in pilot_queryset:
            department_row(pilot.department)["pilots"] += 1
        summary["departments"] = sorted(
            department_rows.values(),
            key=lambda item: (item["departmentPath"], item["departmentId"]),
        )
        return Response(summary, status=status.HTTP_200_OK)
