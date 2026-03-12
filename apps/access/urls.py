from django.urls import path

from apps.access import views

urlpatterns = [
    path("", views.ApiRootView.as_view(), name="api-root"),
    path("session-status", views.SessionStatusView.as_view(), name="session-status"),
    path("login", views.LoginView.as_view(), name="login"),
    path("logout", views.LogoutView.as_view(), name="logout"),
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
    path("audit-logs", views.AuditLogListView.as_view(), name="audit-log-list"),
    # 租户管理
    path("tenants", views.TenantViewSet.as_view(), name="tenant-create"),
    # 平台固定角色
    path("system-roles", views.SystemRoleListCreateView.as_view(), name="system-role-list-create"),
    path("system-roles/<int:pk>", views.SystemRoleDetailView.as_view(), name="system-role-detail"),
    # 租户成员管理
    path("tenant-members", views.TenantMemberListCreateView.as_view(), name="tenant-member-list-create"),
    path("tenant-members/<int:pk>", views.TenantMemberDetailView.as_view(), name="tenant-member-detail"),
    path("tenant-members/<int:pk>/roles", views.TenantMemberRoleAssignView.as_view(), name="tenant-member-role-assign"),
]
