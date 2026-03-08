from rest_framework.routers import SimpleRouter

from apps.flight_record.views import FlightRecordViewSet

router = SimpleRouter(trailing_slash=False)
router.register("flight-records", FlightRecordViewSet, basename="flight-record")

urlpatterns = router.urls

