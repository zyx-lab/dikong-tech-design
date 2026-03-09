from rest_framework.routers import SimpleRouter

from apps.waypoint.views import WaypointViewSet

router = SimpleRouter(trailing_slash=False)
router.register("waypoints", WaypointViewSet, basename="waypoint")

urlpatterns = router.urls
