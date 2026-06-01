from django.urls import path

from apps.dji_cloud import views


urlpatterns = [
    path("sync/devices", views.sync_devices),
    path("sync/media", views.sync_media),
    path("callbacks/media-upload", views.media_upload_callback),
    path("callbacks/media-group-upload", views.media_group_upload_callback),
]
