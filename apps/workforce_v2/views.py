from django.db import IntegrityError, transaction
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.access.authentication import BearerAuthSessionAuthentication
from apps.access.api_base import EmptySerializer
from apps.api_v2.openapi import list_data_serializer
from apps.common.api_response import BusinessApiResponseMixin, StandardCode, standard_error_payload
from apps.iam_v2.services import resolve_v2_context
from apps.resource_v2.audit import log_v2_action
from apps.workforce_v2.models import PilotProfile
from apps.workforce_v2.serializers import (
    PilotProfileReadSerializer,
    PilotProfileUpdateSerializer,
    PilotProfileWriteSerializer,
)
from apps.workforce_v2.services import (
    account_profile_for_pilot,
    get_manageable_pilot_or_404,
    get_visible_pilot_or_404,
    require_pilot_manager,
    visible_pilots_queryset,
)


class WorkforceV2APIView(BusinessApiResponseMixin, GenericAPIView):
    authentication_classes = [BearerAuthSessionAuthentication]
    serializer_class = EmptySerializer


PILOT_LIST_RESPONSE = list_data_serializer("V2PilotProfileListData", PilotProfileReadSerializer)


def _duplicate_response(errors=None):
    return Response(standard_error_payload(StandardCode.DUPLICATE, "资源已存在", errors), status=status.HTTP_409_CONFLICT)


class PilotListCreateView(WorkforceV2APIView):
    @extend_schema(
        operation_id="v2_workforce_pilots_list",
        summary="查询飞手档案列表",
        responses={200: OpenApiResponse(response=PILOT_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        queryset = visible_pilots_queryset(context)
        serializer = PilotProfileReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_workforce_pilots_create",
        summary="创建飞手档案",
        request=PilotProfileWriteSerializer,
        responses={201: OpenApiResponse(response=PilotProfileReadSerializer, description="创建成功。")},
    )
    @transaction.atomic
    def post(self, request):
        context = resolve_v2_context(request)
        require_pilot_manager(context)
        serializer = PilotProfileWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account_profile = account_profile_for_pilot(context, serializer.validated_data["accountProfileId"])
        try:
            pilot = PilotProfile.objects.create(
                account_profile=account_profile,
                display_name=serializer.validated_data["displayName"],
                level=serializer.validated_data.get("level", ""),
                status=serializer.validated_data.get("status", account_profile.status),
                remark=serializer.validated_data.get("remark", ""),
            )
        except IntegrityError as exc:
            return _duplicate_response({"detail": str(exc)})
        data = PilotProfileReadSerializer(pilot).data
        log_v2_action(
            request=request,
            context=context,
            action="create_pilot_profile",
            target_type="pilot_profile",
            target_id=pilot.id,
            resource_owner_department=pilot.account_profile.department,
            after_data=data,
        )
        return Response(data, status=status.HTTP_201_CREATED)


class PilotDetailView(WorkforceV2APIView):
    @extend_schema(
        operation_id="v2_workforce_pilots_retrieve",
        summary="读取飞手档案详情",
        responses={200: OpenApiResponse(response=PilotProfileReadSerializer, description="读取成功。")},
    )
    def get(self, request, id: int):
        context = resolve_v2_context(request)
        pilot = get_visible_pilot_or_404(context, id)
        return Response(PilotProfileReadSerializer(pilot).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_workforce_pilots_update",
        summary="更新飞手档案",
        request=PilotProfileUpdateSerializer,
        responses={200: OpenApiResponse(response=PilotProfileReadSerializer, description="更新成功。")},
    )
    @transaction.atomic
    def put(self, request, id: int):
        context = resolve_v2_context(request)
        require_pilot_manager(context)
        pilot = get_manageable_pilot_or_404(context, id)
        serializer = PilotProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before_data = PilotProfileReadSerializer(pilot).data
        pilot.display_name = serializer.validated_data["displayName"]
        pilot.level = serializer.validated_data.get("level", "")
        pilot.status = serializer.validated_data.get("status", pilot.status)
        pilot.remark = serializer.validated_data.get("remark", "")
        pilot.save(update_fields=["display_name", "level", "status", "remark", "updated_at"])
        data = PilotProfileReadSerializer(pilot).data
        log_v2_action(
            request=request,
            context=context,
            action="update_pilot_profile",
            target_type="pilot_profile",
            target_id=pilot.id,
            resource_owner_department=pilot.account_profile.department,
            before_data=before_data,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)
