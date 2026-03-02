from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema

from apps.access.models import RegistrationApplication
from apps.access.serializers import RegistrationApplicationCreateSerializer, RegistrationApplicationStatusSerializer
from apps.access.services import log_action


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
                    "registration_applications": reverse("registration-application-create", request=request),
                    "drones": reverse("drone-list", request=request),
                    "drone_assignments": reverse("drone-assignment-list", request=request),
                },
            }
        )


class ApiV1HealthView(APIView):
    """业务平面健康检查。"""

    permission_classes = [AllowAny]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response({"status": "ok", "service": "business-api-v1"})


class RegistrationApplicationCreateView(generics.CreateAPIView):
    """业务侧：提交注册申请。"""

    serializer_class = RegistrationApplicationCreateSerializer
    permission_classes = [AllowAny]
    queryset = RegistrationApplication.objects.all()

    def perform_create(self, serializer):
        application = serializer.save()
        log_action(
            request=self.request,
            action="REGISTRATION_APPLICATION_CREATE",
            target_type="registration_application",
            target_id=application.id,
            after_data={
                "application_no": application.application_no,
                "status": application.status,
                "requested_staff_type_code": application.requested_staff_type_code,
            },
        )


class RegistrationApplicationStatusView(generics.RetrieveAPIView):
    """业务侧：按申请号查询状态。"""

    serializer_class = RegistrationApplicationStatusSerializer
    permission_classes = [AllowAny]
    queryset = RegistrationApplication.objects.all()
    lookup_field = "application_no"
    lookup_url_kwarg = "application_no"
