from django.urls import include, path

from apps.system_v2 import resource_audit_logs
from apps.system_v2 import resource_summary


urlpatterns = [
    path("iam/", include("apps.iam_v2.urls")),
    path("resource/summary", resource_summary.ResourceSummaryView.as_view(), name="v2-resource-summary"),
    path("resource/audit-logs", resource_audit_logs.ResourceAuditLogListView.as_view(), name="v2-resource-audit-logs"),
    path("resource/", include("apps.resource_v2.urls")),
    path("inspection/", include("apps.inspection_v2.urls")),
    path("system/", include("apps.system_v2.urls")),
]
