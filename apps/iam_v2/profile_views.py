from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.access.api_base import EmptySerializer
from apps.access.authentication import BearerAuthSessionAuthentication
from apps.access.exceptions import StandardForbidden, StandardNotFound
from apps.access.models import DirectoryStatus
from apps.api_v2.openapi import list_data_serializer
from apps.common.api_response import BusinessApiResponseMixin, StandardCode, standard_error_payload
from apps.iam_v2.models import V2AccountProfile, V2AccountQualification
from apps.iam_v2.profile_payloads import v2_me_profile_payload
from apps.iam_v2.profile_serializers import (
    AccountQualificationReadSerializer,
    AccountQualificationWriteSerializer,
    V2MeProfileSerializer,
)
from apps.iam_v2.services import is_department_admin, is_platform_super_admin, resolve_v2_context
from apps.resource_v2.audit import log_v2_action


class V2IamProfileAPIView(BusinessApiResponseMixin, GenericAPIView):
    authentication_classes = [BearerAuthSessionAuthentication]
    serializer_class = EmptySerializer


ACCOUNT_QUALIFICATION_LIST_RESPONSE = list_data_serializer(
    "V2AccountQualificationListData",
    AccountQualificationReadSerializer,
)


def _duplicate_payload_response(data):
    return Response(
        standard_error_payload(StandardCode.DUPLICATE, "资源已存在", data),
        status=status.HTTP_409_CONFLICT,
    )


def _account_queryset():
    return V2AccountProfile.objects.select_related("department", "user").prefetch_related("role_assignments")


def _require_qualification_manager(context) -> None:
    if not (is_platform_super_admin(context) or is_department_admin(context)):
        raise StandardForbidden()


def _managed_account_or_404(context, account_id: int) -> V2AccountProfile:
    _require_qualification_manager(context)
    account = _account_queryset().filter(pk=account_id).first()
    if account is None:
        raise StandardNotFound()
    if not is_platform_super_admin(context) and account.department_id != context.department.id:
        raise StandardForbidden()
    return account


def _qualification_or_404(account: V2AccountProfile, qualification_id: int) -> V2AccountQualification:
    qualification = account.qualifications.filter(pk=qualification_id).first()
    if qualification is None:
        raise StandardNotFound()
    return qualification


class MeProfileView(V2IamProfileAPIView):
    @extend_schema(
        operation_id="v2_iam_me_profile",
        summary="获取当前 v2 账号个人资料",
        description="返回当前 Bearer Token 对应 v2 账号资料、部门角色、角色档案和当前角色相关资质。",
        responses={200: OpenApiResponse(response=V2MeProfileSerializer, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        return Response(v2_me_profile_payload(context), status=status.HTTP_200_OK)


class AccountQualificationListCreateView(V2IamProfileAPIView):
    @extend_schema(
        operation_id="v2_iam_account_qualifications_list",
        summary="查询 v2 账号资质列表",
        description="平台超管可查看全部账号资质；部门管理员仅可查看本部门账号资质。",
        responses={200: OpenApiResponse(response=ACCOUNT_QUALIFICATION_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request, id: int):
        context = resolve_v2_context(request)
        account = _managed_account_or_404(context, id)
        queryset = account.qualifications.order_by("-expires_at", "-id")
        return Response(
            {"list": AccountQualificationReadSerializer(queryset, many=True).data, "total": queryset.count()},
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="v2_iam_account_qualifications_create",
        summary="创建 v2 账号资质",
        request=AccountQualificationWriteSerializer,
        responses={201: OpenApiResponse(response=AccountQualificationReadSerializer, description="创建成功。")},
    )
    @transaction.atomic
    def post(self, request, id: int):
        context = resolve_v2_context(request)
        account = _managed_account_or_404(context, id)
        serializer = AccountQualificationWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            qualification = V2AccountQualification.objects.create(
                account_profile=account,
                role_code=serializer.validated_data["roleCode"],
                qualification_type=serializer.validated_data["qualificationType"],
                certificate_no=serializer.validated_data["certificateNo"],
                issued_at=serializer.validated_data["issuedAt"],
                expires_at=serializer.validated_data["expiresAt"],
                status=serializer.validated_data["status"],
                remark=serializer.validated_data["remark"],
            )
        except (DjangoValidationError, IntegrityError) as exc:
            return _duplicate_payload_response({"detail": str(exc)})
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


class AccountQualificationDetailView(V2IamProfileAPIView):
    @extend_schema(
        operation_id="v2_iam_account_qualifications_update",
        summary="更新 v2 账号资质",
        request=AccountQualificationWriteSerializer,
        responses={200: OpenApiResponse(response=AccountQualificationReadSerializer, description="更新成功。")},
    )
    @transaction.atomic
    def put(self, request, id: int, qualification_id: int):
        context = resolve_v2_context(request)
        account = _managed_account_or_404(context, id)
        qualification = _qualification_or_404(account, qualification_id)
        serializer = AccountQualificationWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before_data = AccountQualificationReadSerializer(qualification).data
        qualification.role_code = serializer.validated_data["roleCode"]
        qualification.qualification_type = serializer.validated_data["qualificationType"]
        qualification.certificate_no = serializer.validated_data["certificateNo"]
        qualification.issued_at = serializer.validated_data["issuedAt"]
        qualification.expires_at = serializer.validated_data["expiresAt"]
        qualification.status = serializer.validated_data["status"]
        qualification.remark = serializer.validated_data["remark"]
        try:
            qualification.save(
                update_fields=[
                    "role_code",
                    "qualification_type",
                    "certificate_no",
                    "issued_at",
                    "expires_at",
                    "status",
                    "remark",
                    "updated_at",
                ]
            )
        except (DjangoValidationError, IntegrityError) as exc:
            return _duplicate_payload_response({"detail": str(exc)})
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
