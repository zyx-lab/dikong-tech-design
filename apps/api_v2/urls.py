from django.urls import include, path


urlpatterns = [
    path("iam/", include("apps.iam_v2.urls")),
    path("resource/", include("apps.resource_v2.urls")),
]
