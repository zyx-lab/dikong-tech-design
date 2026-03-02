from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema


class ApiV1RootView(APIView):
    """业务 API 入口（对外开放平面）。"""

    permission_classes = [AllowAny]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(
            {
                "name": "Business API v1",
                "version": "v1",
                "endpoints": {
                    "docs": reverse("business-docs", request=request),
                    "health": reverse("api-v1-health", request=request),
                },
            }
        )


class ApiV1HealthView(APIView):
    """业务平面健康检查。"""

    permission_classes = [AllowAny]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response({"status": "ok", "service": "business-api-v1"})
