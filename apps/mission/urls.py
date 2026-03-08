from rest_framework.routers import SimpleRouter

from apps.mission.views import MissionViewSet

router = SimpleRouter(trailing_slash=False)
router.register("missions", MissionViewSet, basename="mission")

urlpatterns = router.urls
