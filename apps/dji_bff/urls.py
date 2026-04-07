from django.urls import path

from apps.dji_bff import views


urlpatterns = [
    path("__internal__/dji/sync/devices", views.sync_devices),
    path("__internal__/dji/sync/missions", views.sync_missions),
    path("__internal__/dji/sync/media", views.sync_media),
    path("__internal__/dji/callbacks/media-upload", views.media_upload_callback),
    path("__internal__/dji/callbacks/media-group-upload", views.media_group_upload_callback),
]
