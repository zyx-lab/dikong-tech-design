from rest_framework.routers import SimpleRouter

from apps.drone.views import DroneViewSet

router = SimpleRouter(trailing_slash=False)
router.register("drones", DroneViewSet, basename="drone")

urlpatterns = router.urls
