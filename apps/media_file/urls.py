from rest_framework.routers import SimpleRouter

from apps.media_file.views import MediaFileViewSet

router = SimpleRouter(trailing_slash=False)
router.register("media-files", MediaFileViewSet, basename="media-file")

urlpatterns = router.urls
