from django.urls import path

from apps.inspection_v2 import dji_callbacks


urlpatterns = [
    path("callbacks/media-upload", dji_callbacks.media_upload_callback),
    path("callbacks/media-group-upload", dji_callbacks.media_group_upload_callback),
]
