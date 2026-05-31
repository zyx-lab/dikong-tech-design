from django.urls import include, path


urlpatterns = [
    path("api/v2/", include("apps.api_v2.urls")),
]
