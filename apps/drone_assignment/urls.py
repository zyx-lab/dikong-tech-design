from rest_framework.routers import SimpleRouter

from apps.drone_assignment.views import DroneAssignmentViewSet

router = SimpleRouter(trailing_slash=False)
router.register("drone-assignments", DroneAssignmentViewSet, basename="drone-assignment")

urlpatterns = router.urls
