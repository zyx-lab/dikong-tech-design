from rest_framework.routers import SimpleRouter

from apps.route.views import RouteViewSet

router = SimpleRouter(trailing_slash=False)
router.register("routes", RouteViewSet, basename="route")

urlpatterns = router.urls
