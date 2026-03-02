from django.urls import include, path

from apps.api_v1 import views

urlpatterns = [
    path("", views.ApiV1RootView.as_view(), name="api-v1-root"),
    path("health", views.ApiV1HealthView.as_view(), name="api-v1-health"),
    path(
        "registration-applications",
        views.RegistrationApplicationCreateView.as_view(),
        name="registration-application-create",
    ),
    path(
        "registration-applications/<str:application_no>/status",
        views.RegistrationApplicationStatusView.as_view(),
        name="registration-application-status",
    ),
    path("", include("apps.drone.urls")),
]
