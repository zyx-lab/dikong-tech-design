from django.urls import path

from apps.inspection_v2 import views


urlpatterns = [
    path("routes", views.RouteListCreateView.as_view(), name="v2-inspection-routes"),
    path("routes/<int:id>", views.RouteDetailView.as_view(), name="v2-inspection-route-detail"),
    path("routes/<int:id>/kmz", views.RouteKmzView.as_view(), name="v2-inspection-route-kmz"),
    path("missions", views.MissionListCreateView.as_view(), name="v2-inspection-missions"),
    path("missions/<int:id>", views.MissionDetailView.as_view(), name="v2-inspection-mission-detail"),
    path("missions/<int:id>/start", views.MissionStartView.as_view(), name="v2-inspection-mission-start"),
    path("missions/<int:id>/complete", views.MissionCompleteView.as_view(), name="v2-inspection-mission-complete"),
    path("missions/<int:id>/cancel", views.MissionCancelView.as_view(), name="v2-inspection-mission-cancel"),
    path("missions/<int:id>/fail", views.MissionFailView.as_view(), name="v2-inspection-mission-fail"),
    path("missions/<int:id>/abort", views.MissionAbortView.as_view(), name="v2-inspection-mission-abort"),
    path("active-flights", views.ActiveFlightListView.as_view(), name="v2-inspection-active-flights"),
    path("active-flights/<int:id>", views.ActiveFlightDetailView.as_view(), name="v2-inspection-active-flight-detail"),
    path("telemetry/snapshots", views.TelemetrySnapshotView.as_view(), name="v2-inspection-telemetry-snapshots"),
    path("live/capacity", views.LiveCapacityView.as_view(), name="v2-inspection-live-capacity"),
    path("live/start", views.LiveStartView.as_view(), name="v2-inspection-live-start"),
    path("live/stop", views.LiveStopView.as_view(), name="v2-inspection-live-stop"),
    path("live/update", views.LiveUpdateView.as_view(), name="v2-inspection-live-update"),
    path("live/switch", views.LiveSwitchView.as_view(), name="v2-inspection-live-switch"),
    path("flight-records", views.FlightRecordListView.as_view(), name="v2-inspection-flight-records"),
    path("flight-records/<int:id>", views.FlightRecordDetailView.as_view(), name="v2-inspection-flight-record-detail"),
    path(
        "flight-records/<int:id>/refresh-media",
        views.FlightRecordMediaRefreshView.as_view(),
        name="v2-inspection-flight-record-refresh-media",
    ),
    path("media-files", views.MediaFileListView.as_view(), name="v2-inspection-media-files"),
    path("media-files/<int:id>", views.MediaFileDetailView.as_view(), name="v2-inspection-media-file-detail"),
]
