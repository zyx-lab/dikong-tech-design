from django.urls import path

from apps.workforce_v2 import views


urlpatterns = [
    path("pilots", views.PilotListCreateView.as_view(), name="v2-workforce-pilots"),
    path("pilots/<int:id>", views.PilotDetailView.as_view(), name="v2-workforce-pilot-detail"),
    path(
        "pilots/<int:pilot_id>/qualifications",
        views.PilotQualificationListCreateView.as_view(),
        name="v2-workforce-pilot-qualifications",
    ),
    path(
        "pilots/<int:pilot_id>/qualifications/<int:id>",
        views.PilotQualificationDetailView.as_view(),
        name="v2-workforce-pilot-qualification-detail",
    ),
]
