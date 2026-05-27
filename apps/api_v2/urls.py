from django.urls import path
from rest_framework.routers import SimpleRouter

from apps.api_v2 import views

router = SimpleRouter(trailing_slash=False)
router.register("drones", views.V2DroneViewSet, basename="v2-drone")
router.register("routes", views.V2RouteViewSet, basename="v2-route")
router.register("missions", views.V2MissionViewSet, basename="v2-mission")
router.register("flight-records", views.V2FlightRecordViewSet, basename="v2-flight-record")
router.register("media-files", views.V2MediaFileViewSet, basename="v2-media-file")

urlpatterns = [
    path("iam/tenant/dji-platforms", views.DjiPlatformListCreateView.as_view(), name="v2-dji-platform-list"),
    path(
        "iam/tenant/dji-platforms/<int:platform_id>",
        views.DjiPlatformDetailView.as_view(),
        name="v2-dji-platform-detail",
    ),
    path(
        "iam/tenant/dji-platforms/<int:platform_id>/test-connection",
        views.DjiPlatformTestConnectionView.as_view(),
        name="v2-dji-platform-test-connection",
    ),
    path(
        "iam/tenant/dji-platforms/<int:platform_id>/set-default",
        views.DjiPlatformSetDefaultView.as_view(),
        name="v2-dji-platform-set-default",
    ),
    path(
        "iam/tenant/dji-platforms/<int:platform_id>/disable",
        views.DjiPlatformDisableView.as_view(),
        name="v2-dji-platform-disable",
    ),
] + router.urls
