from django.db.models import Case, IntegerField, Q, Value, When
from django.db.models.functions import Lower, NullIf, Trim
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema

from apps.access.api_v1.base import IamGenericAPIView, IamAPIView, ensure_empty_body, ensure_no_extra_query_params
from apps.access.api_v1.context import resolve_tenant_request_context, tenant_context_has_permission
from apps.access.api_v1.openapi import (
    IAM_BEARER_AUTH,
    IAM_CONSTRAINT_CONFLICT_RESPONSE,
    IAM_FORBIDDEN_RESPONSE,
    IAM_INVALID_PARAMS_RESPONSE,
    IAM_NOT_FOUND_RESPONSE,
    IAM_PAGE_NUM_PARAMETER,
    IAM_PAGE_SIZE_PARAMETER,
    IAM_TENANT_CODE_HEADER_PARAMETER,
    IAM_UNAUTHORIZED_RESPONSE,
    iam_path_int_parameter,
)
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
from apps.api_v1.schema import (
    BUSINESS_INTERNAL_ERROR_RESPONSE,
    array_envelope_serializer,
    business_error_example,
    business_error_response,
    object_envelope_serializer,
    paginated_envelope_serializer,
)


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


TENANT_ME_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": {
        "tenant": {
            "tenantId": 2001,
            "tenantCode": "demo_tenant",
            "name": "演示租户",
            "status": "ACTIVE",
            "plan": None,
            "remark": "正式租户",
            "createdAt": "2026-03-19T10:00:00+08:00",
            "updatedAt": "2026-03-19T10:00:00+08:00",
        },
        "member": {
            "memberId": 3001,
            "userId": 1001,
            "displayName": "张三",
            "status": "ACTIVE",
            "roleCodes": ["tenant_admin"],
        },
    },
}

TENANT_MEMBERS_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": {
        "list": [
            {
                "memberId": 3001,
                "userId": 1001,
                "username": "demo_user",
                "displayName": "张三",
                "status": "ACTIVE",
                "roleCodes": ["tenant_admin"],
            }
        ],
        "total": 1,
    },
}

TENANT_MEMBER_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": {
        "memberId": 3002,
        "userId": 1002,
        "username": "member_user",
        "displayName": None,
        "status": "ACTIVE",
        "roleCodes": ["dispatcher"],
    },
}

TENANT_ROLES_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": [
        {
            "roleId": 1,
            "code": "tenant_admin",
            "name": "租户管理员",
            "description": "负责当前租户成员与角色管理",
        }
    ],
}

TENANT_AUDIT_LOGS_SUCCESS_EXAMPLE = {
    "code": "00000",
    "msg": "success",
    "data": {
        "list": [
            {
                "auditLogId": 5001,
                "action": "IAM_TENANT_MEMBER_CREATED",
                "targetType": "tenant_member",
                "targetId": "3002",
                "operatorUserId": 1001,
                "operatorDisplayName": "张三",
                "createdAt": "2026-03-19T10:20:00+08:00",
                "summary": "IAM_TENANT_MEMBER_CREATED",
            }
        ],
        "total": 1,
    },
}

TENANT_ADMIN_REQUIRED_RESPONSE = business_error_response(
    description="当前租户成员未持有 tenant_admin 角色，或当前运行态不满足 tenant/* 前提时返回 403 + A0403。",
    examples=[
        business_error_example(
            "缺少 tenant_admin 角色",
            code="A0403",
            msg="无操作权限",
            status_codes=["403"],
        )
    ],
)

TENANT_MEMBER_CONFLICT_RESPONSE = business_error_response(
    description="租户成员状态流转、重复入租或最后一个 ACTIVE tenant_admin 保护触发时返回 409。",
    examples=[
        business_error_example(
            "重复入租",
            code="C0103",
            msg="当前用户已在该租户中",
            status_codes=["409"],
        ),
        business_error_example(
            "最后一个租户管理员保护",
            code="C0203",
            msg="不能移除当前租户最后一个启用的 tenant_admin",
            status_codes=["409"],
        ),
    ],
)


class TenantMeView(IamAPIView):
    @extend_schema(
        auth=IAM_BEARER_AUTH,
        parameters=[IAM_TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=object_envelope_serializer("IamTenantMeEnvelope", TenantMeResponseSerializer),
                description=(
                    "权限标识：`iam.tenant.me.read`。读取当前租户上下文中的“我”。成功前提是当前租户与当前成员关系均为 ACTIVE，"
                    "因此成功响应中的 `tenant.status` 与 `member.status` 实际固定返回 ACTIVE；`member.roleCodes` 允许为空数组。"
                ),
                examples=[
                    OpenApiExample(
                        "读取当前租户上下文示例",
                        response_only=True,
                        status_codes=["200"],
                        value=TENANT_ME_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: IAM_INVALID_PARAMS_RESPONSE,
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: IAM_FORBIDDEN_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="读取当前租户上下文中的我",
        description="只返回当前登录用户在当前 `X-TENANT-CODE` 租户下的成员上下文；不要求当前成员已分配业务角色。",
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
        auth=IAM_BEARER_AUTH,
        operation_id="iam_tenant_member_list",
        parameters=[
            IAM_TENANT_CODE_HEADER_PARAMETER,
            OpenApiParameter(name="status", type=str, location=OpenApiParameter.QUERY, many=True, required=False, description="成员状态筛选；允许重复 query 参数表达多值，例如 status=ACTIVE&status=DISABLED。"),
            OpenApiParameter(name="keywords", type=str, location=OpenApiParameter.QUERY, required=False, description="按 username、displayName 模糊检索。"),
            OpenApiParameter(name="userId", type=int, location=OpenApiParameter.QUERY, required=False, description="按当前租户内全局用户 ID 做单值精确筛选。"),
            OpenApiParameter(name="sortBy", type=str, location=OpenApiParameter.QUERY, required=False, description="仅允许 displayName、username、status、createdAt、updatedAt；默认 displayName。"),
            OpenApiParameter(name="sortOrder", type=str, location=OpenApiParameter.QUERY, required=False, description="仅允许 asc、desc；默认 asc。"),
            IAM_PAGE_NUM_PARAMETER,
            IAM_PAGE_SIZE_PARAMETER,
        ],
        responses={
            200: OpenApiResponse(
                response=paginated_envelope_serializer("IamTenantMembersEnvelope", TenantMemberResponseSerializer),
                description=(
                    "权限标识：`iam.tenant.member.read`。租户管理员工作侧成员目录；默认返回 ACTIVE + DISABLED，"
                    "默认按 displayName asc 排序，displayName=null 统一排在有值记录之后，并以 memberId asc 作为稳定次排序。"
                ),
                examples=[
                    OpenApiExample(
                        "租户成员列表成功示例",
                        response_only=True,
                        status_codes=["200"],
                        value=TENANT_MEMBERS_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: IAM_INVALID_PARAMS_RESPONSE,
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: TENANT_ADMIN_REQUIRED_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="租户成员目录与创建",
        description="仅 tenant_admin 可调用；属于当前租户用户目录与成员管理列表的正式入口。",
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
        auth=IAM_BEARER_AUTH,
        operation_id="iam_tenant_member_create",
        parameters=[IAM_TENANT_CODE_HEADER_PARAMETER],
        request=TenantMemberCreateSerializer,
        examples=[
            OpenApiExample(
                "创建租户成员请求示例",
                request_only=True,
                value={"userId": 1002, "displayName": "李四", "roleCodes": ["dispatcher"]},
            )
        ],
        responses={
            201: OpenApiResponse(
                response=object_envelope_serializer("IamTenantMemberCreatedEnvelope", TenantMemberResponseSerializer),
                description="权限标识：`iam.tenant.member.create`。创建成功后成员状态固定为 ACTIVE；displayName 省略或空字符串时成功响应统一返回 null。",
                examples=[
                    OpenApiExample(
                        "创建租户成员成功示例",
                        response_only=True,
                        status_codes=["201"],
                        value=TENANT_MEMBER_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: IAM_INVALID_PARAMS_RESPONSE,
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: TENANT_ADMIN_REQUIRED_RESPONSE,
            404: IAM_NOT_FOUND_RESPONSE,
            409: TENANT_MEMBER_CONFLICT_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="将已存在账号加入当前租户",
        description="请求体只允许 userId、displayName、roleCodes；不接受 tenantId、tenantCode、qualifications 或 snake_case 别名。",
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
        auth=IAM_BEARER_AUTH,
        operation_id="iam_tenant_member_detail",
        parameters=[IAM_TENANT_CODE_HEADER_PARAMETER, iam_path_int_parameter("memberId", "租户成员 ID。")],
        responses={
            200: OpenApiResponse(
                response=object_envelope_serializer("IamTenantMemberDetailEnvelope", TenantMemberResponseSerializer),
                description="权限标识：`iam.tenant.member.read`。读取当前租户下单个成员详情；只收口为 memberId、userId、username、displayName、status、roleCodes。",
                examples=[
                    OpenApiExample(
                        "读取租户成员详情示例",
                        response_only=True,
                        status_codes=["200"],
                        value=TENANT_MEMBER_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: IAM_INVALID_PARAMS_RESPONSE,
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: TENANT_ADMIN_REQUIRED_RESPONSE,
            404: IAM_NOT_FOUND_RESPONSE,
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
        auth=IAM_BEARER_AUTH,
        operation_id="iam_tenant_member_update",
        parameters=[IAM_TENANT_CODE_HEADER_PARAMETER, iam_path_int_parameter("memberId", "租户成员 ID。")],
        request=TenantMemberUpdateSerializer,
        examples=[
            OpenApiExample(
                "更新成员显示名请求示例",
                request_only=True,
                value={"displayName": "新的显示名"},
            ),
            OpenApiExample(
                "清空成员显示名请求示例",
                request_only=True,
                value={"displayName": ""},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=object_envelope_serializer("IamTenantMemberUpdateEnvelope", TenantMemberResponseSerializer),
                description="权限标识：`iam.tenant.member.update`。正式可写字段只允许 displayName；显式传空字符串表示清空，成功响应统一返回 displayName=null。",
                examples=[
                    OpenApiExample(
                        "更新租户成员成功示例",
                        response_only=True,
                        status_codes=["200"],
                        value=TENANT_MEMBER_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: IAM_INVALID_PARAMS_RESPONSE,
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: TENANT_ADMIN_REQUIRED_RESPONSE,
            404: IAM_NOT_FOUND_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="更新租户成员 displayName",
        description="不接受 tenantId、userId、status、roleCodes、username、tenantCode、qualifications 或任何 snake_case 旧字段名。",
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
        auth=IAM_BEARER_AUTH,
        parameters=[IAM_TENANT_CODE_HEADER_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=array_envelope_serializer("IamTenantRolesEnvelope", TenantRoleSerializer),
                description="权限标识：`iam.tenant.role.readAssignable`。固定目录型非分页接口；只返回允许分配给租户成员的角色模板，允许包含 tenant_admin。",
                examples=[
                    OpenApiExample(
                        "租户可分配角色目录示例",
                        response_only=True,
                        status_codes=["200"],
                        value=TENANT_ROLES_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: IAM_INVALID_PARAMS_RESPONSE,
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: TENANT_ADMIN_REQUIRED_RESPONSE,
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
        auth=IAM_BEARER_AUTH,
        parameters=[IAM_TENANT_CODE_HEADER_PARAMETER, iam_path_int_parameter("memberId", "租户成员 ID。")],
        request=TenantMemberRolesReplaceSerializer,
        examples=[
            OpenApiExample(
                "全量覆盖角色集合请求示例",
                request_only=True,
                value={"roleCodes": ["tenant_admin", "dispatcher"]},
            ),
            OpenApiExample(
                "清空角色集合请求示例",
                request_only=True,
                value={"roleCodes": []},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=object_envelope_serializer("IamTenantMemberRolesEnvelope", TenantMemberResponseSerializer),
                description="权限标识：`iam.tenant.member.assignRoles`。roleCodes 使用全量覆盖语义；空数组表示清空角色集合，重复提交幂等。",
                examples=[
                    OpenApiExample(
                        "更新成员角色成功示例",
                        response_only=True,
                        status_codes=["200"],
                        value=TENANT_MEMBER_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: IAM_INVALID_PARAMS_RESPONSE,
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: TENANT_ADMIN_REQUIRED_RESPONSE,
            404: IAM_NOT_FOUND_RESPONSE,
            409: IAM_CONSTRAINT_CONFLICT_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="全量覆盖租户成员角色集合",
        description="只接受 roleCodes 数组；不接受 role_codes、roles 等旧字段名。若会移除当前租户最后一个 ACTIVE tenant_admin，则返回 409 + C0203。",
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
        auth=IAM_BEARER_AUTH,
        parameters=[IAM_TENANT_CODE_HEADER_PARAMETER, iam_path_int_parameter("memberId", "租户成员 ID。")],
        request=None,
        responses={
            200: OpenApiResponse(
                response=object_envelope_serializer("IamTenantMemberEnableEnvelope", TenantMemberResponseSerializer),
                description="权限标识：`iam.tenant.member.enable`。请求体固定为空；成功后返回更新后的成员对象，status 固定为 ACTIVE。",
                examples=[
                    OpenApiExample(
                        "启用成员成功示例",
                        response_only=True,
                        status_codes=["200"],
                        value=TENANT_MEMBER_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: IAM_INVALID_PARAMS_RESPONSE,
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: TENANT_ADMIN_REQUIRED_RESPONSE,
            404: IAM_NOT_FOUND_RESPONSE,
            409: IAM_CONSTRAINT_CONFLICT_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="启用租户成员",
        description="目标成员状态必须允许启用；重复启用返回 409 + C0203。",
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
        auth=IAM_BEARER_AUTH,
        parameters=[IAM_TENANT_CODE_HEADER_PARAMETER, iam_path_int_parameter("memberId", "租户成员 ID。")],
        request=None,
        responses={
            200: OpenApiResponse(
                response=object_envelope_serializer("IamTenantMemberDisableEnvelope", TenantMemberResponseSerializer),
                description="权限标识：`iam.tenant.member.disable`。请求体固定为空；成功后返回更新后的成员对象，status 固定为 DISABLED。",
                examples=[
                    OpenApiExample(
                        "停用成员成功示例",
                        response_only=True,
                        status_codes=["200"],
                        value={
                            "code": "00000",
                            "msg": "success",
                            "data": {
                                "memberId": 3002,
                                "userId": 1002,
                                "username": "member_user",
                                "displayName": None,
                                "status": "DISABLED",
                                "roleCodes": ["dispatcher"],
                            },
                        },
                    )
                ],
            ),
            400: IAM_INVALID_PARAMS_RESPONSE,
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: TENANT_ADMIN_REQUIRED_RESPONSE,
            404: IAM_NOT_FOUND_RESPONSE,
            409: IAM_CONSTRAINT_CONFLICT_RESPONSE,
            500: BUSINESS_INTERNAL_ERROR_RESPONSE,
        },
        summary="停用租户成员",
        description="目标成员状态必须允许停用；若会移除当前租户最后一个 ACTIVE tenant_admin，也返回 409 + C0203。",
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
        auth=IAM_BEARER_AUTH,
        parameters=[
            IAM_TENANT_CODE_HEADER_PARAMETER,
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
                response=paginated_envelope_serializer("IamTenantAuditLogsEnvelope", TenantAuditLogSerializer),
                description="权限标识：`iam.tenant.auditLog.read`。当前租户范围内的审计日志分页列表；默认按 createdAt desc 排序。",
                examples=[
                    OpenApiExample(
                        "租户审计日志成功示例",
                        response_only=True,
                        status_codes=["200"],
                        value=TENANT_AUDIT_LOGS_SUCCESS_EXAMPLE,
                    )
                ],
            ),
            400: IAM_INVALID_PARAMS_RESPONSE,
            401: IAM_UNAUTHORIZED_RESPONSE,
            403: IAM_FORBIDDEN_RESPONSE,
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
