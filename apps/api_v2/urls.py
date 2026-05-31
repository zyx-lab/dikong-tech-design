from django.urls import include, path

# v2 先复用当前可共用的 v1 业务与 IAM 路由，后续在此增量接入 v2 专属接口。
# dji_bff 属于旧资源模式下的内部桥接入口，不挂载到 v2。
urlpatterns = [
    path("iam/", include("apps.access.api_v1.urls")),
    path("", include("apps.drone.urls")),
    path("", include("apps.drone_assignment.urls")),
    path("", include("apps.route.urls")),
    path("", include("apps.mission.urls")),
    path("", include("apps.flight_record.urls")),
    path("", include("apps.media_file.urls")),
]
