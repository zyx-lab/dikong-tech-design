from django.urls import path

from apps.access import views

urlpatterns = [
    path("", views.ApiRootView.as_view(), name="api-root"),
    path("session-status", views.SessionStatusView.as_view(), name="session-status"),
    path("users", views.UserListCreateView.as_view(), name="user-list-create"),
    path("users/<int:pk>", views.UserDetailView.as_view(), name="user-detail"),
    path("me/permissions", views.MePermissionsView.as_view(), name="me-permissions"),
    path("permissions", views.PermissionCatalogView.as_view(), name="permission-catalog"),
    path("groups", views.GroupListCreateView.as_view(), name="group-list-create"),
    path("groups/<int:pk>", views.GroupDetailView.as_view(), name="group-detail"),
    path("groups/<int:group_id>/permissions", views.GroupPermissionAssignView.as_view(), name="group-permissions-assign"),
    path("groups/<int:group_id>/scopes", views.GroupScopeAssignView.as_view(), name="group-scopes-assign"),
    path("staff-types", views.StaffTypeListCreateView.as_view(), name="staff-type-list-create"),
    path("staff-types/<int:pk>", views.StaffTypeDetailView.as_view(), name="staff-type-detail"),
    path("staff-types/<int:staff_type_id>/groups", views.StaffTypeGroupAssignView.as_view(), name="staff-type-group-assign"),
    path("scopes/matrix", views.ScopeMatrixView.as_view(), name="scope-matrix"),
    path("audit-logs", views.AuditLogListView.as_view(), name="audit-log-list"),
]
