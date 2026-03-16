from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularJSONAPIView, SpectacularSwaggerView

admin.site.site_header = "低空平台权限中心"
admin.site.site_title = "权限后台"
admin.site.index_title = "权限与账号管理"

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "docs/schema/",
        SpectacularJSONAPIView.as_view(urlconf="config.openapi_urlconf"),
        name="docs-schema",
    ),
    path("docs/", SpectacularSwaggerView.as_view(url_name="docs-schema"), name="docs"),
    path(
        "internal/docs/schema/",
        SpectacularAPIView.as_view(urlconf="config.internal_api_urlconf"),
        name="internal-docs-schema",
    ),
    path("internal/docs/", SpectacularSwaggerView.as_view(url_name="internal-docs-schema"), name="internal-docs"),
    path(
        "api/v1/docs/schema/",
        SpectacularJSONAPIView.as_view(urlconf="config.business_api_urlconf"),
        name="business-docs-schema",
    ),
    path("api/v1/docs/", SpectacularSwaggerView.as_view(url_name="business-docs-schema"), name="business-docs"),
    path("internal/auth/", include("apps.access.urls")),
    path("api/v1/", include("apps.api_v1.urls")),
]
