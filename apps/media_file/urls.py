from django.urls import path

from apps.media_file.views import MediaFileViewSet

media_file_list = MediaFileViewSet.as_view({"get": "list"})
media_file_detail = MediaFileViewSet.as_view({"get": "retrieve", "delete": "destroy"})
media_file_download = MediaFileViewSet.as_view({"get": "download"})
media_file_playback = MediaFileViewSet.as_view({"get": "playback"})
media_file_bind_mission = MediaFileViewSet.as_view({"post": "bind_mission"})

urlpatterns = [
    path("media-files", media_file_list, name="media-file-list"),
    path("media-files/bind-mission", media_file_bind_mission, name="media-file-bind-mission"),
    path("media-files/<int:pk>", media_file_detail, name="media-file-detail"),
    path("media-files/<int:pk>/download", media_file_download, name="media-file-download"),
    path("media-files/<int:pk>/playback", media_file_playback, name="media-file-playback"),
]
