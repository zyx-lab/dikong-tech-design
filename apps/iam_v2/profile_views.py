from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.access.api_base import EmptySerializer
from apps.access.authentication import BearerAuthSessionAuthentication
from apps.common.api_response import BusinessApiResponseMixin
from apps.iam_v2.profile_payloads import v2_me_profile_payload
from apps.iam_v2.profile_serializers import V2MeProfileSerializer
from apps.iam_v2.services import resolve_v2_context


class V2IamProfileAPIView(BusinessApiResponseMixin, GenericAPIView):
    authentication_classes = [BearerAuthSessionAuthentication]
    serializer_class = EmptySerializer


class MeProfileView(V2IamProfileAPIView):
    @extend_schema(
        operation_id="v2_iam_me_profile",
        summary="获取当前 v2 账号个人资料",
        description="返回当前 Bearer Token 对应 v2 账号资料、部门角色、账号角色档案和账号资质。",
        responses={200: OpenApiResponse(response=V2MeProfileSerializer, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        return Response(v2_me_profile_payload(context), status=status.HTTP_200_OK)
