from rest_framework.routers import SimpleRouter

from apps.drone.views import DroneAssignmentViewSet, DroneViewSet

router = SimpleRouter(trailing_slash=False)
router.register("drones", DroneViewSet, basename="drone")
router.register("drone-assignments", DroneAssignmentViewSet, basename="drone-assignment")

urlpatterns = router.urls
