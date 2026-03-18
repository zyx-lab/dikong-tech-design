from django.db.models import Case, IntegerField, Q, Value, When
from django.db.models.functions import Lower, NullIf, Trim
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiParameter, extend_schema

from apps.access.api_v1.base import IamGenericAPIView, IamAPIView, ensure_empty_body, ensure_no_extra_query_params
from apps.access.api_v1.context import resolve_tenant_request_context, tenant_context_has_permission
from apps.access.exceptions import StandardForbidden
from apps.access.api_v1.serializers.tenant import (
    TenantAuditLogSerializer,
    TenantMemberCreateSerializer,
    TenantMemberResponseSerializer,
    TenantMemberRolesReplaceSerializer,
    TenantMemberUpdateSerializer,
    TenantMeResponseSerializer,
    TenantRoleSerializer,
)
from apps.access.api_v1.services.members import (
    create_tenant_member,
    disable_member,
    enable_member,
    get_tenant_member_or_404,
    require_tenant_admin,
    serialize_member_payload,
    update_member_display_name,
    replace_member_roles,
    assignable_roles_queryset,
)
from apps.access.models import AuditLog, TenantMember, TenantMemberStatus
from apps.api_v1.schema import BUSINESS_INTERNAL_ERROR_RESPONSE, array_envelope_serializer, object_envelope_serializer, paginated_envelope_serializer


def _tenant_context_or_validation_error(request):
    try:
        return resolve_tenant_request_context(request)
    except ValueError as exc:
        raise serializers.ValidationError({"X-TENANT-CODE": [str(exc)]}) from exc


def _parse_datetime(value: str | None, *, field_name: str):
    if not value:
        return None
    try:
        parsed = timezone.datetime.fromisoformat(value)
    except ValueError as exc:
        raise serializers.ValidationError({field_name: ["时间格式必须为 ISO 8601"]}) from exc
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _parse_status_filters(request):
    raw_values = request.query_params.getlist("status")
    if not raw_values:
        return [TenantMemberStatus.ACTIVE, TenantMemberStatus.DISABLED]
    mapping = {
        "ACTIVE": TenantMemberStatus.ACTIVE,
        "DISABLED": TenantMemberStatus.DISABLED,
    }
    values = []
    invalid = []
    for item in raw_values:
        normalized = str(item).strip().upper()
        if normalized not in mapping:
            invalid.append(item)
            continue
        values.append(mapping[normalized])
    if invalid:
        raise serializers.ValidationError({"status": [f"非法状态值: {', '.join(map(str, invalid))}"]})
    return values


def _resolve_member_sorting(queryset, *, sort_by: str, sort_order: str):
    descending = sort_order == "desc"
    if sort_by == "displayName":
        queryset = queryset.annotate(
            normalized_display_name=NullIf(Trim("display_name"), Value("")),
            display_name_is_null=Case(
                When(Q(display_name__isnull=True) | Q(display_name=""), then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
            normalized_display_name_lower=Lower(NullIf(Trim("display_name"), Value(""))),
        )
        if descending:
            return queryset.order_by("display_name_is_null", "-normalized_display_name_lower", "id")
        return queryset.order_by("display_name_is_null", "normalized_display_name_lower", "id")

    field_mapping = {
        "username": "user__username",
        "status": "status",
        "createdAt": "created_at",
        "updatedAt": "updated_at",
    }
    order_field = field_mapping[sort_by]
    if descending:
        return queryset.order_by(f"-{order_field}", "id")
    return queryset.order_by(order_field, "id")


class TenantMeView(IamAPIView):
    @extend_schema(
        parameters=[
            OpenApiParameter(name="X-TENANT-CODE", type=str, location=OpenApiParameter.HEADER, required=True),
        ],
        responses={
            200: object_envelope_serializer("IamTenantMeEnvelope", TenantMeResponseSerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="读取当前租户上下文中的我",
    )
    def get(self, request):
        context = _tenant_context_or_validation_error(request)
        return Response(
            {
                "tenant": {
                    "tenantId": context.tenant.id,
                    "tenantCode": context.tenant.code,
                    "name": context.tenant.name,
                    "status": "ACTIVE",
                    "plan": None,
                    "remark": context.tenant.remark,
                    "createdAt": context.tenant.created_at,
                    "updatedAt": context.tenant.updated_at,
                },
                "member": {
                    "memberId": context.member.id,
                    "userId": context.member.user_id,
                    "displayName": context.member.display_name.strip() or None if context.member.display_name else None,
                    "status": "ACTIVE",
                    "roleCodes": context.role_codes,
                },
            },
            status=status.HTTP_200_OK,
        )


class TenantMembersView(IamGenericAPIView):
    @extend_schema(
        operation_id="iam_tenant_member_list",
        parameters=[
            OpenApiParameter(name="X-TENANT-CODE", type=str, location=OpenApiParameter.HEADER, required=True),
            OpenApiParameter(name="status", type=str, location=OpenApiParameter.QUERY, many=True, required=False),
            OpenApiParameter(name="keywords", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="userId", type=int, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="sortBy", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="sortOrder", type=str, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="pageNum", type=int, location=OpenApiParameter.QUERY, required=False),
            OpenApiParameter(name="pageSize", type=int, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={
            200: paginated_envelope_serializer("IamTenantMembersEnvelope", TenantMemberResponseSerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="租户成员目录与创建",
    )
    def get(self, request):
        context = _tenant_context_or_validation_error(request)
        require_tenant_admin(context)
        ensure_no_extra_query_params(request, {"status", "keywords", "userId", "sortBy", "sortOrder", "pageNum", "pageSize"})

        queryset = (
            TenantMember.objects.filter(tenant=context.tenant, status__in=_parse_status_filters(request))
            .select_related("user")
            .prefetch_related("role_bindings__system_role")
        )
        keywords = (request.query_params.get("keywords") or "").strip()
        if keywords:
            queryset = queryset.filter(Q(user__username__icontains=keywords) | Q(display_name__icontains=keywords))

        user_id = request.query_params.get("userId")
        if user_id:
            try:
                queryset = queryset.filter(user_id=int(user_id))
            except ValueError as exc:
                raise serializers.ValidationError({"userId": ["必须为整数"]}) from exc

        sort_by = request.query_params.get("sortBy") or "displayName"
        sort_order = (request.query_params.get("sortOrder") or "asc").lower()
        if sort_by not in {"displayName", "username", "status", "createdAt", "updatedAt"}:
            raise serializers.ValidationError({"sortBy": ["不支持的排序字段"]})
        if sort_order not in {"asc", "desc"}:
            raise serializers.ValidationError({"sortOrder": ["只允许 asc 或 desc"]})
        queryset = _resolve_member_sorting(queryset, sort_by=sort_by, sort_order=sort_order)

        page = self.paginate_queryset(queryset)
        items = page if page is not None else queryset
        return self.get_paginated_response([serialize_member_payload(item) for item in items])

    @extend_schema(
        operation_id="iam_tenant_member_create",
        parameters=[OpenApiParameter(name="X-TENANT-CODE", type=str, location=OpenApiParameter.HEADER, required=True)],
        request=TenantMemberCreateSerializer,
        responses={
            201: object_envelope_serializer("IamTenantMemberCreatedEnvelope", TenantMemberResponseSerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="将已存在账号加入当前租户",
    )
    def post(self, request):
        context = _tenant_context_or_validation_error(request)
        require_tenant_admin(context)
        serializer = TenantMemberCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from apps.access.api_v1.context import eligible_user_queryset

        user = eligible_user_queryset().filter(id=serializer.validated_data["userId"]).select_related("staff_profile").first()
        if user is None:
            from apps.access.exceptions import StandardNotFound

            raise StandardNotFound()
        member = create_tenant_member(
            tenant=context.tenant,
            actor=context.identity.user,
            user=user,
            display_name=serializer.validated_data.get("displayName"),
            role_codes=serializer.validated_data.get("roleCodes") or [],
        )
        return Response(serialize_member_payload(member), status=status.HTTP_201_CREATED)


class TenantMemberDetailView(IamAPIView):
    @extend_schema(
        operation_id="iam_tenant_member_detail",
        parameters=[OpenApiParameter(name="X-TENANT-CODE", type=str, location=OpenApiParameter.HEADER, required=True)],
        responses={
            200: object_envelope_serializer("IamTenantMemberDetailEnvelope", TenantMemberResponseSerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="读取单个租户成员",
    )
    def get(self, request, memberId: int):
        context = _tenant_context_or_validation_error(request)
        require_tenant_admin(context)
        member = get_tenant_member_or_404(tenant=context.tenant, member_id=memberId)
        return Response(serialize_member_payload(member), status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="iam_tenant_member_update",
        parameters=[OpenApiParameter(name="X-TENANT-CODE", type=str, location=OpenApiParameter.HEADER, required=True)],
        request=TenantMemberUpdateSerializer,
        responses={
            200: object_envelope_serializer("IamTenantMemberUpdateEnvelope", TenantMemberResponseSerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="更新租户成员 displayName",
    )
    def patch(self, request, memberId: int):
        context = _tenant_context_or_validation_error(request)
        require_tenant_admin(context)
        serializer = TenantMemberUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = get_tenant_member_or_404(tenant=context.tenant, member_id=memberId)
        updated = update_member_display_name(
            member=member,
            actor=context.identity.user,
            display_name=serializer.validated_data.get("displayName"),
        )
        return Response(serialize_member_payload(updated), status=status.HTTP_200_OK)


class TenantRolesView(IamAPIView):
    @extend_schema(
        parameters=[OpenApiParameter(name="X-TENANT-CODE", type=str, location=OpenApiParameter.HEADER, required=True)],
        responses={
            200: array_envelope_serializer("IamTenantRolesEnvelope", TenantRoleSerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="读取当前租户可分配角色目录",
    )
    def get(self, request):
        context = _tenant_context_or_validation_error(request)
        require_tenant_admin(context)
        roles = list(assignable_roles_queryset())
        payload = [
            {
                "roleId": role.id,
                "code": role.code,
                "name": role.name,
                "description": role.description,
            }
            for role in roles
        ]
        return Response(payload, status=status.HTTP_200_OK)


class TenantMemberRolesReplaceView(IamAPIView):
    @extend_schema(
        parameters=[OpenApiParameter(name="X-TENANT-CODE", type=str, location=OpenApiParameter.HEADER, required=True)],
        request=TenantMemberRolesReplaceSerializer,
        responses={
            200: object_envelope_serializer("IamTenantMemberRolesEnvelope", TenantMemberResponseSerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="全量覆盖租户成员角色集合",
    )
    def put(self, request, memberId: int):
        context = _tenant_context_or_validation_error(request)
        require_tenant_admin(context)
        serializer = TenantMemberRolesReplaceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = get_tenant_member_or_404(tenant=context.tenant, member_id=memberId)
        updated = replace_member_roles(
            tenant=context.tenant,
            member=member,
            actor=context.identity.user,
            role_codes=serializer.validated_data["roleCodes"],
        )
        return Response(serialize_member_payload(updated), status=status.HTTP_200_OK)


class TenantMemberEnableView(IamAPIView):
    @extend_schema(
        parameters=[OpenApiParameter(name="X-TENANT-CODE", type=str, location=OpenApiParameter.HEADER, required=True)],
        request=None,
        responses={
            200: object_envelope_serializer("IamTenantMemberEnableEnvelope", TenantMemberResponseSerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="启用租户成员",
    )
    def post(self, request, memberId: int):
        ensure_empty_body(request)
        context = _tenant_context_or_validation_error(request)
        require_tenant_admin(context)
        member = get_tenant_member_or_404(tenant=context.tenant, member_id=memberId)
        updated = enable_member(member=member, actor=context.identity.user)
        return Response(serialize_member_payload(updated), status=status.HTTP_200_OK)


class TenantMemberDisableView(IamAPIView):
    @extend_schema(
        parameters=[OpenApiParameter(name="X-TENANT-CODE", type=str, location=OpenApiParameter.HEADER, required=True)],
        request=None,
        responses={
            200: object_envelope_serializer("IamTenantMemberDisableEnvelope", TenantMemberResponseSerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="停用租户成员",
    )
    def post(self, request, memberId: int):
        ensure_empty_body(request)
        context = _tenant_context_or_validation_error(request)
        require_tenant_admin(context)
        member = get_tenant_member_or_404(tenant=context.tenant, member_id=memberId)
        updated = disable_member(member=member, actor=context.identity.user)
        return Response(serialize_member_payload(updated), status=status.HTTP_200_OK)


class TenantAuditLogsView(IamGenericAPIView):
    @extend_schema(
        parameters=[
            OpenApiParameter(name="X-TENANT-CODE", type=str, location=OpenApiParameter.HEADER, required=True),
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
            200: paginated_envelope_serializer("IamTenantAuditLogsEnvelope", TenantAuditLogSerializer),
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="查询当前租户审计日志",
    )
    def get(self, request):
        context = _tenant_context_or_validation_error(request)
        if not tenant_context_has_permission(context, "access.view_auth_audit_logs"):
            raise StandardForbidden()
        ensure_no_extra_query_params(request, {"action", "operatorUserId", "startAt", "endAt", "sortBy", "sortOrder", "pageNum", "pageSize"})
        queryset = AuditLog.objects.filter(tenant=context.tenant).select_related("actor_user", "actor_user__staff_profile")
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
        payload = TenantAuditLogSerializer(items, many=True).data
        return self.get_paginated_response(payload)
