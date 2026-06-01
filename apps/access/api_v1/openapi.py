from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter

from apps.api_v1.schema import business_error_example, business_error_response


IAM_BEARER_AUTH = ["BearerAuth"]

IAM_PAGE_NUM_PARAMETER = OpenApiParameter(
    name="pageNum",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.QUERY,
    required=False,
    default=1,
    description="分页页码，默认 1。",
)

IAM_PAGE_SIZE_PARAMETER = OpenApiParameter(
    name="pageSize",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.QUERY,
    required=False,
    default=20,
    description="分页大小，默认 20，最大 100。",
)

IAM_TENANT_CODE_HEADER_PARAMETER = OpenApiParameter(
    name="X-TENANT-CODE",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description=(
        "当前租户上下文编码。tenant/* 接口必须通过该 header 指定当前租户；"
        "缺失或格式非法统一返回 400 + B0001。"
    ),
    examples=[
        OpenApiExample(
            "租户上下文 header 示例",
            value="demo_tenant",
            summary="以 tenantCode 声明当前租户上下文",
            parameter_only=("X-TENANT-CODE", "header"),
        )
    ],
)

IAM_UNAUTHORIZED_RESPONSE = business_error_response(
    description="未携带 Bearer Token、Token 无效/过期，或正式 IAM 登录态失效时返回 401 + A0401。",
    examples=[
        business_error_example(
            "登录态失效",
            code="A0401",
            msg="登录状态已失效",
            status_codes=["401"],
        )
    ],
)

IAM_INVALID_PARAMS_RESPONSE = business_error_response(
    description="请求体、查询参数或 header 不符合正式契约时返回 400 + B0001。",
    examples=[
        business_error_example(
            "参数校验失败",
            code="B0001",
            msg="参数校验失败",
            status_codes=["400"],
            data={"field": ["该字段不允许传入"]},
        )
    ],
)

IAM_FORBIDDEN_RESPONSE = business_error_response(
    description="已认证但当前运行态、租户上下文或权限不足时返回 403 + A0403。",
    examples=[
        business_error_example(
            "无操作权限",
            code="A0403",
            msg="无操作权限",
            status_codes=["403"],
        )
    ],
)

IAM_NOT_FOUND_RESPONSE = business_error_response(
    description="在当前已生效作用域内找不到目标资源时返回 404 + C0404。",
    examples=[
        business_error_example(
            "资源不存在",
            code="C0404",
            msg="资源不存在",
            status_codes=["404"],
        )
    ],
)

IAM_CONSTRAINT_CONFLICT_RESPONSE = business_error_response(
    description="当前资源状态或业务约束不允许执行该操作时返回 409 + C0203。",
    examples=[
        business_error_example(
            "状态或约束冲突",
            code="C0203",
            msg="当前资源状态或业务约束不允许执行该操作",
            status_codes=["409"],
        )
    ],
)


def iam_path_int_parameter(name: str, description: str) -> OpenApiParameter:
    return OpenApiParameter(
        name=name,
        type=OpenApiTypes.INT,
        location=OpenApiParameter.PATH,
        required=True,
        description=description,
    )


class BearerAuthSessionScheme(OpenApiAuthenticationExtension):
    target_class = "apps.access.authentication.BearerAuthSessionAuthentication"
    name = "BearerAuth"

    def get_security_definition(self, auto_schema):
        del auto_schema
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "opaque token",
            "description": "正式 IAM Bearer Token，需通过 /api/v1/iam/session/login 获取。",
        }
