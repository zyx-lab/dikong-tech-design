from django.urls import include, path

urlpatterns = [
    path("internal/auth/", include("apps.access.urls")),
]
