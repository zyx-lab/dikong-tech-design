from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema

from apps.api_v1.business_response import BusinessApiResponseMixin

class ApiV1RootView(BusinessApiResponseMixin, APIView):
    """业务 API 入口（对外开放平面）。"""

    permission_classes = [AllowAny]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(
            {
                "name": "Business API v1",
                "version": "v1",
                "endpoints": {
                    "all_docs": reverse("docs", request=request),
                    "all_docs_schema": reverse("docs-schema", request=request),
                    "docs": reverse("business-docs", request=request),
                    "health": reverse("api-v1-health", request=request),
                    "drones": reverse("drone-list", request=request),
                    "drone_assignments": reverse("drone-assignment-list", request=request),
                    "routes": reverse("route-list", request=request),
                    "missions": reverse("mission-list", request=request),
                    "media_files": reverse("media-file-list", request=request),
                },
            }
        )


class ApiV1HealthView(BusinessApiResponseMixin, APIView):
    """业务平面健康检查。"""

    permission_classes = [AllowAny]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response({"status": "ok", "service": "business-api-v1"})
