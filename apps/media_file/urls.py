from django.urls import path

from apps.media_file.views import MediaFileViewSet

media_file_list = MediaFileViewSet.as_view({"get": "list"})
media_file_detail = MediaFileViewSet.as_view({"get": "retrieve", "delete": "destroy"})
media_file_download = MediaFileViewSet.as_view({"get": "download"})

urlpatterns = [
    path("media-files", media_file_list, name="media-file-list"),
    path("media-files/<int:pk>", media_file_detail, name="media-file-detail"),
    path("media-files/<int:pk>/download", media_file_download, name="media-file-download"),
]
