from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularJSONAPIView, SpectacularSwaggerView

admin.site.site_header = "低空平台权限中心"
admin.site.site_title = "权限后台"
admin.site.index_title = "权限与账号管理"

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "api/v1/docs/schema/",
        SpectacularJSONAPIView.as_view(urlconf="config.business_api_urlconf"),
        name="business-docs-schema",
    ),
    path("api/v1/docs/", SpectacularSwaggerView.as_view(url_name="business-docs-schema"), name="business-docs"),
    path(
        "api/v2/docs/schema/",
        SpectacularJSONAPIView.as_view(urlconf="config.api_v2_urlconf"),
        name="api-v2-docs-schema",
    ),
    path("api/v2/docs/", SpectacularSwaggerView.as_view(url_name="api-v2-docs-schema"), name="api-v2-docs"),
    path("api/v1/", include("apps.api_v1.urls")),
    path("api/v2/", include("apps.api_v2.urls")),
    path("__mock-dji__/", include("apps.dji_mock.urls")),
]
