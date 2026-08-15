from django.urls import path

from apps.dji_mock import views


urlpatterns = [
    path("api/v1/manage/login", views.login),
    path("api/v1/manage/token/refresh", views.refresh_token),
    path("api/v1/manage/users/current", views.current_user),
    path("api/v1/manage/workspaces/current", views.current_workspace),
    path("api/v1/manage/workspaces/<str:workspace_id>/devices", views.workspace_devices),
    path("api/v1/manage/workspaces/<str:workspace_id>/devices/bound", views.workspace_bound_devices),
    path("api/v1/manage/live/capacity", views.live_capacity),
    path("api/v1/manage/live/streams/start", views.live_start),
    path("api/v1/manage/live/streams/stop", views.live_stop),
    path("api/v1/manage/live/streams/update", views.live_update),
    path("api/v1/manage/live/streams/switch", views.live_switch),
    path("api/v1/manage/live/streams/camera-change", views.live_camera_change),
    path("api/v1/control/devices/<str:gateway_sn>/authority/payload", views.payload_authority),
    path("api/v1/control/devices/<str:gateway_sn>/payload/commands", views.payload_commands),
    path("api/v1/control/workspaces/<str:workspace_id>/drc/connect", views.drc_connect),
    path("api/v1/control/workspaces/<str:workspace_id>/drc/enter", views.drc_enter),
    path("api/v1/control/workspaces/<str:workspace_id>/drc/exit", views.drc_exit),
    path("api/v1/wayline/workspaces/<str:workspace_id>/waylines", views.wayline_list),
    path(
        "api/v1/wayline/workspaces/<str:workspace_id>/waylines/duplicate-names",
        views.duplicate_wayline_names,
    ),
    path(
        "api/v1/wayline/workspaces/<str:workspace_id>/waylines/files/upload",
        views.upload_wayline,
    ),
    path(
        "api/v1/wayline/workspaces/<str:workspace_id>/waylines/<str:wayline_id>/url",
        views.wayline_download_url,
    ),
    path(
        "api/v1/wayline/workspaces/<str:workspace_id>/waylines/<str:wayline_id>",
        views.delete_wayline,
    ),
    path("api/v1/wayline/workspaces/<str:workspace_id>/flight-tasks", views.create_job),
    path("api/v1/wayline/workspaces/<str:workspace_id>/jobs", views.jobs_collection),
    path("api/v1/media/workspaces/<str:workspace_id>/files", views.list_media_files),
    path("api/v1/media/workspaces/<str:workspace_id>/files/<str:file_id>/url", views.media_download_url),
    path("api/v1/media/workspaces/<str:workspace_id>/files/<str:file_id>/playback-url", views.media_playback_url),
    path("api/v1/media/workspaces/<str:workspace_id>/files/<str:file_id>/preview-url", views.media_preview_url),
    path("_downloads/waylines/<str:filename>", views.download_wayline_binary),
    path("_downloads/media/<path:file_id>", views.download_media_binary),
]
