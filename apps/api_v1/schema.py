import copy

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, inline_serializer
from rest_framework import serializers

from apps.api_v1.business_response import standard_error_payload


def _serializer_instance(serializer, *, many=False, allow_null=False):
    if isinstance(serializer, type):
        return serializer(many=many, allow_null=allow_null)
    return serializer.__class__(many=many, allow_null=allow_null)


TENANT_CODE_HEADER_PARAMETER = OpenApiParameter(
    name="X-TENANT-CODE",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description=(
        "当前业务租户编码。业务 API 默认在租户上下文内运行；普通业务账号必须提供该 header。"
        " superuser 可跨租户调试，platform_admin 不允许访问业务 API。"
    ),
    examples=[
        OpenApiExample(
            "租户 header 示例",
            value="demo_tenant",
            summary="以租户编码声明当前业务上下文",
            parameter_only=("X-TENANT-CODE", "header"),
        )
    ],
)


class StandardErrorResponseSerializer(serializers.Serializer):
    code = serializers.CharField(help_text="业务码。成功固定为 00000，失败按 A/B/C/E 码段区分。")
    msg = serializers.CharField(help_text="响应消息。优先返回可直接展示的中文提示。")
    data = serializers.JSONField(required=False, allow_null=True, help_text="错误上下文。校验失败时返回字段级错误，其他场景通常为 null。")


class BusinessDeleteResultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    deleted = serializers.BooleanField()


def _standard_envelope(name, data_field):
    return inline_serializer(
        name=name,
        fields={
            "code": serializers.CharField(help_text="业务码。成功固定为 00000。"),
            "msg": serializers.CharField(help_text="响应消息。成功通常为 success。"),
            "data": data_field,
        },
    )


def business_error_example(
    name,
    *,
    code,
    msg,
    status_codes,
    data=None,
    summary="",
):
    resolved_payload = standard_error_payload(code, msg, data)
    return OpenApiExample(
        name=name,
        value=resolved_payload,
        summary=summary or msg,
        response_only=True,
        status_codes=status_codes,
    )


def business_error_response(*, description, examples):
    return OpenApiResponse(
        response=StandardErrorResponseSerializer,
        description=description,
        examples=examples,
    )


BUSINESS_INTERNAL_ERROR_RESPONSE = business_error_response(
    description="服务内部错误或未处理异常，code 固定为 E0001。",
    examples=[
        business_error_example(
            "内部错误",
            code="E0001",
            msg="系统异常",
            status_codes=["500"],
            data=None,
        )
    ],
)

BUSINESS_PERMISSION_DENIED_RESPONSE = business_error_response(
    description="未认证、无权限、缺少租户上下文，或 platform_admin 访问业务 API 被拒绝。",
    examples=[
        business_error_example(
            "未登录",
            code="A0401",
            msg="登录状态已失效",
            status_codes=["401"],
        ),
        business_error_example(
            "无权限",
            code="A0403",
            msg="无操作权限",
            status_codes=["403"],
        ),
        business_error_example(
            "缺少租户上下文",
            code="A0403",
            msg="缺少租户上下文",
            status_codes=["403"],
        ),
        business_error_example(
            "平台管理员访问业务 API",
            code="A0403",
            msg="平台管理员不可访问租户业务接口",
            status_codes=["403"],
        ),
    ],
)

BUSINESS_INVALID_PARAMS_RESPONSE = business_error_response(
    description="请求体、查询参数或资源绑定关系不合法时返回 400 + B0001。",
    examples=[
        business_error_example(
            "字段校验失败",
            code="B0001",
            msg="参数校验失败",
            status_codes=["400"],
            data={"field": ["该字段是必填项。"]},
        ),
        business_error_example(
            "动作接口提交 body",
            code="B0001",
            msg="参数校验失败",
            status_codes=["400"],
            data={"body": "不支持请求体，请移除 body 后重试"},
        ),
    ],
)

BUSINESS_DUPLICATE_RESPONSE = business_error_response(
    description="唯一键或业务唯一约束冲突时返回 409 + C0101。",
    examples=[
        business_error_example(
            "资源已存在",
            code="C0101",
            msg="资源已存在",
            status_codes=["409"],
        )
    ],
)

BUSINESS_NOT_FOUND_RESPONSE = business_error_response(
    description="目标资源不存在，或在当前租户/授权作用域下不可见。",
    examples=[
        business_error_example(
            "资源不存在",
            code="C0404",
            msg="资源不存在",
            status_codes=["404"],
        )
    ],
)


def object_envelope_serializer(name, serializer):
    return _standard_envelope(
        name,
        _serializer_instance(serializer),
    )


def paginated_envelope_serializer(name, item_serializer):
    return _standard_envelope(
        name,
        inline_serializer(
            name=f"{name}Data",
            fields={
                "list": _serializer_instance(item_serializer, many=True),
                "total": serializers.IntegerField(help_text="符合当前查询条件的总记录数。"),
            },
        ),
    )


def collection_envelope_serializer(name, item_serializer, *, extra_fields=None):
    fields = {
        "list": _serializer_instance(item_serializer, many=True),
        "total": serializers.IntegerField(help_text="当前返回结果数量。"),
    }
    fields.update(copy.deepcopy(extra_fields or {}))
    return _standard_envelope(
        name,
        inline_serializer(name=f"{name}Data", fields=fields),
    )


def array_envelope_serializer(name, item_serializer):
    return _standard_envelope(
        name,
        _serializer_instance(item_serializer, many=True),
    )


def empty_envelope_serializer(name):
    return _standard_envelope(
        name,
        serializers.JSONField(allow_null=True, required=False),
    )


def nullable_result_envelope_serializer(name, item_serializer, *, extra_fields=None):
    fields = {
        "result": _serializer_instance(item_serializer, allow_null=True),
    }
    fields.update(copy.deepcopy(extra_fields or {}))
    return _standard_envelope(
        name,
        inline_serializer(name=f"{name}Data", fields=fields),
    )
