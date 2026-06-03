from django.urls import path

from apps.workforce_v2 import views


urlpatterns = [
    path("pilots", views.PilotListCreateView.as_view(), name="v2-workforce-pilots"),
    path("pilots/<int:id>", views.PilotDetailView.as_view(), name="v2-workforce-pilot-detail"),
]
