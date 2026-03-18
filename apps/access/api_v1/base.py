from rest_framework.generics import GenericAPIView
from rest_framework import serializers
from rest_framework.views import APIView

from apps.access.api_v1.authentication import BearerAuthSessionAuthentication
from apps.api_v1.business_response import BusinessApiResponseMixin
from apps.api_v1.pagination import StandardPageNumberPagination


class EmptySerializer(serializers.Serializer):
    pass


class StrictSerializer(serializers.Serializer):
    """Reject unknown request fields to keep the formal contract strict."""

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            return super().to_internal_value(data)

        unexpected = sorted(set(data.keys()) - set(self.fields.keys()))
        if unexpected:
            raise serializers.ValidationError({field: ["该字段不允许传入"] for field in unexpected})
        return super().to_internal_value(data)


class IamAPIView(BusinessApiResponseMixin, APIView):
    authentication_classes = [BearerAuthSessionAuthentication]


class IamGenericAPIView(BusinessApiResponseMixin, GenericAPIView):
    authentication_classes = [BearerAuthSessionAuthentication]
    pagination_class = StandardPageNumberPagination
    serializer_class = EmptySerializer


def ensure_no_extra_query_params(request, allowed_params: set[str]) -> None:
    unexpected = sorted(set(request.query_params.keys()) - allowed_params)
    if unexpected:
        raise serializers.ValidationError({"query": [f"不支持的查询参数: {', '.join(unexpected)}"]})


def ensure_empty_body(request) -> None:
    data = getattr(request, "data", None)
    if data in (None, "", b""):
        return
    if isinstance(data, dict) and not data:
        return
    raise serializers.ValidationError({"body": ["该接口不接受业务字段"]})
