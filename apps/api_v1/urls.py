from django.urls import include, path

from apps.api_v1 import views

urlpatterns = [
    path("", views.ApiV1RootView.as_view(), name="api-v1-root"),
    path("health", views.ApiV1HealthView.as_view(), name="api-v1-health"),
    path("", include("apps.drone.urls")),
    path("", include("apps.drone_assignment.urls")),
    path("", include("apps.route.urls")),
    path("", include("apps.waypoint.urls")),
    path("", include("apps.mission.urls")),
    path("", include("apps.flight_record.urls")),
    path("", include("apps.media_file.urls")),
]
