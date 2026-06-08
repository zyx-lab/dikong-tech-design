from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularJSONAPIView, SpectacularSwaggerView

from apps.api_v2.docs_metadata import API_V2_FRONTEND_GUIDE_DESCRIPTION

admin.site.site_header = "低空平台权限中心"
admin.site.site_title = "权限后台"
admin.site.index_title = "权限与账号管理"

API_V2_SPECTACULAR_SETTINGS = {
    "TITLE": "低空平台 API v2",
    "DESCRIPTION": API_V2_FRONTEND_GUIDE_DESCRIPTION,
    "VERSION": "2.0.0",
    "POSTPROCESSING_HOOKS": [
        "apps.api_v2.openapi_hooks.standardize_v2_response_schema_hook",
        "apps.api_v2.openapi_hooks.enrich_v2_frontend_docs_hook",
    ],
}

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "api/v2/docs/schema/",
        SpectacularJSONAPIView.as_view(
            urlconf="config.api_v2_urlconf",
            custom_settings=API_V2_SPECTACULAR_SETTINGS,
        ),
        name="api-v2-docs-schema",
    ),
    path(
        "api/v2/docs/",
        SpectacularSwaggerView.as_view(url_name="api-v2-docs-schema", title="低空平台 API v2"),
        name="api-v2-docs",
    ),
    path("api/v2/", include("apps.api_v2.urls")),
    path("api/internal/dji/", include("apps.dji_cloud.urls")),
    path("__mock-dji__/", include("apps.dji_mock.urls")),
]
