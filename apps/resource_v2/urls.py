from django.urls import path

from apps.resource_v2 import views


urlpatterns = [
    path("dji-connections", views.DjiConnectionListCreateView.as_view(), name="v2-resource-dji-connections"),
    path("dji-connections/<int:id>", views.DjiConnectionDetailView.as_view(), name="v2-resource-dji-connection-detail"),
    path(
        "dji-connections/<int:id>/discover",
        views.DjiConnectionDiscoverView.as_view(),
        name="v2-resource-dji-connection-discover",
    ),
    path("drones", views.DroneResourceListView.as_view(), name="v2-resource-drones"),
    path("docks", views.DockResourceListView.as_view(), name="v2-resource-docks"),
    path("bindings", views.BindingListCreateView.as_view(), name="v2-resource-bindings"),
    path("bindings/<int:id>", views.BindingDetailView.as_view(), name="v2-resource-binding-detail"),
    path("share-groups", views.ShareGroupListCreateView.as_view(), name="v2-resource-share-groups"),
    path("share-groups/<int:id>", views.ShareGroupDetailView.as_view(), name="v2-resource-share-group-detail"),
    path(
        "share-groups/<int:id>/departments",
        views.ShareGroupDepartmentListCreateView.as_view(),
        name="v2-resource-share-group-departments",
    ),
    path(
        "share-groups/<int:id>/departments/<int:department_id>",
        views.ShareGroupDepartmentDetailView.as_view(),
        name="v2-resource-share-group-department-detail",
    ),
    path(
        "share-groups/<int:id>/resources",
        views.ShareGroupResourceListCreateView.as_view(),
        name="v2-resource-share-group-resources",
    ),
    path(
        "share-groups/<int:id>/resources/<int:resource_share_id>",
        views.ShareGroupResourceDetailView.as_view(),
        name="v2-resource-share-group-resource-detail",
    ),
    path("audit-logs", views.AuditLogListView.as_view(), name="v2-resource-audit-logs"),
]
