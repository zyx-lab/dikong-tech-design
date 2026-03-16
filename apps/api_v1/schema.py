import copy

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, inline_serializer
from rest_framework import serializers


def _serializer_instance(serializer, *, many=False, allow_null=False):
    if isinstance(serializer, type):
        return serializer(many=many, allow_null=allow_null)
    return serializer.__class__(many=many, allow_null=allow_null)


def _clone_fields(serializer):
    serializer_instance = _serializer_instance(serializer)
    return {
        field_name: copy.deepcopy(field)
        for field_name, field in serializer_instance.fields.items()
    }


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


class BusinessErrorResponseSerializer(serializers.Serializer):
    business_code = serializers.CharField(help_text="稳定业务状态码，例如 INVALID_PARAMS、PERMISSION_DENIED、STATE_CONFLICT、INTERNAL_ERROR。")
    business_detail_code = serializers.CharField(help_text="更细的业务细分码，例如 OK、NOT_AUTHENTICATED、FORBIDDEN、INTERNAL_ERROR。")
    detail = serializers.CharField(required=False, help_text="对当前错误场景的简要解释。")
    errors = serializers.JSONField(required=False, help_text="字段级校验错误明细；仅在校验失败场景返回。")


class BusinessDeleteResultSerializer(serializers.Serializer):
    business_code = serializers.CharField()
    business_detail_code = serializers.CharField()
    id = serializers.IntegerField()
    deleted = serializers.BooleanField()


def business_error_example(
    name,
    *,
    business_code,
    business_detail_code,
    detail,
    status_codes,
    extras=None,
    summary="",
):
    value = {
        "business_code": business_code,
        "business_detail_code": business_detail_code,
        "detail": detail,
    }
    value.update(extras or {})
    return OpenApiExample(
        name=name,
        value=value,
        summary=summary or detail,
        response_only=True,
        status_codes=status_codes,
    )


def business_error_response(*, description, examples):
    return OpenApiResponse(
        response=BusinessErrorResponseSerializer,
        description=description,
        examples=examples,
    )


BUSINESS_INTERNAL_ERROR_RESPONSE = business_error_response(
    description="服务内部错误或未处理异常，business_code 固定为 INTERNAL_ERROR。",
    examples=[
        business_error_example(
            "内部错误",
            business_code="INTERNAL_ERROR",
            business_detail_code="INTERNAL_ERROR",
            detail="internal server error",
            status_codes=["500"],
        )
    ],
)


def object_envelope_serializer(name, serializer):
    serializer_class = serializer if isinstance(serializer, type) else serializer.__class__
    base_meta = getattr(serializer_class, "Meta", None)

    if base_meta is not None and hasattr(base_meta, "fields") and base_meta.fields != "__all__":
        class Meta(base_meta):
            fields = tuple(base_meta.fields) + ("business_code", "business_detail_code")
            read_only_fields = tuple(getattr(base_meta, "read_only_fields", ())) + ("business_code", "business_detail_code")
    else:
        class Meta:
            fields = "__all__"

    class EnvelopeSerializer(serializer_class):
        business_code = serializers.CharField(help_text="稳定业务状态码。成功通常为 SUCCESS。")
        business_detail_code = serializers.CharField(help_text="稳定业务细分码。成功通常为 OK。")

    EnvelopeSerializer.__name__ = name
    EnvelopeSerializer.Meta = Meta
    return EnvelopeSerializer


def paginated_envelope_serializer(name, item_serializer):
    return inline_serializer(
        name=name,
        fields={
            "business_code": serializers.CharField(help_text="稳定业务状态码。成功通常为 SUCCESS。"),
            "business_detail_code": serializers.CharField(help_text="稳定业务细分码。成功通常为 OK。"),
            "count": serializers.IntegerField(help_text="符合当前查询条件的总记录数。"),
            "next": serializers.CharField(required=False, allow_null=True, help_text="下一页 URL；无下一页时为 null。"),
            "previous": serializers.CharField(required=False, allow_null=True, help_text="上一页 URL；无上一页时为 null。"),
            "results": _serializer_instance(item_serializer, many=True),
        },
    )


def collection_envelope_serializer(name, item_serializer, *, extra_fields=None):
    fields = {
        "business_code": serializers.CharField(help_text="稳定业务状态码。成功通常为 SUCCESS。"),
        "business_detail_code": serializers.CharField(help_text="稳定业务细分码。成功通常为 OK。"),
        "count": serializers.IntegerField(help_text="当前返回结果数量。"),
        "results": _serializer_instance(item_serializer, many=True),
    }
    fields.update(extra_fields or {})
    return inline_serializer(name=name, fields=fields)


def nullable_result_envelope_serializer(name, item_serializer, *, extra_fields=None):
    fields = {
        "business_code": serializers.CharField(help_text="稳定业务状态码。成功通常为 SUCCESS。"),
        "business_detail_code": serializers.CharField(help_text="稳定业务细分码。成功通常为 OK。"),
        "result": _serializer_instance(item_serializer, allow_null=True),
    }
    fields.update(extra_fields or {})
    return inline_serializer(name=name, fields=fields)
