from django.urls import path

from apps.access.session_views import (
    SessionLogoutView,
    SessionRefreshView,
)
from apps.iam_v2 import profile_views, session_views, views


urlpatterns = [
    path("session/login", session_views.SessionLoginView.as_view(), name="v2-iam-session-login"),
    path("session/refresh", SessionRefreshView.as_view(), name="v2-iam-session-refresh"),
    path("session/logout", SessionLogoutView.as_view(), name="v2-iam-session-logout"),
    path("me/context", views.MeContextView.as_view(), name="v2-iam-me-context"),
    path("me/profile", profile_views.MeProfileView.as_view(), name="v2-iam-me-profile"),
    path("departments", views.DepartmentListCreateView.as_view(), name="v2-iam-departments"),
    path("departments/<int:id>", views.DepartmentDetailView.as_view(), name="v2-iam-department-detail"),
    path("departments/<int:id>/enable", views.DepartmentEnableView.as_view(), name="v2-iam-department-enable"),
    path("departments/<int:id>/disable", views.DepartmentDisableView.as_view(), name="v2-iam-department-disable"),
    path("profile-types", views.ProfileTypeListCreateView.as_view(), name="v2-iam-profile-types"),
    path("profile-types/<str:code>", views.ProfileTypeDetailView.as_view(), name="v2-iam-profile-type-detail"),
    path("accounts", views.AccountListCreateView.as_view(), name="v2-iam-accounts"),
    path("accounts/<int:id>", views.AccountDetailView.as_view(), name="v2-iam-account-detail"),
    path("accounts/<int:id>/profiles", views.AccountProfilesListCreateView.as_view(), name="v2-iam-account-profiles"),
    path("accounts/<int:id>/profiles/<str:profile_type>", views.AccountProfileDetailView.as_view(), name="v2-iam-account-profile-detail"),
    path("accounts/<int:id>/qualifications", views.AccountQualificationsListCreateView.as_view(), name="v2-iam-account-qualifications"),
    path(
        "accounts/<int:id>/qualifications/<int:qualification_id>",
        views.AccountQualificationDetailView.as_view(),
        name="v2-iam-account-qualification-detail",
    ),
    path("accounts/<int:id>/roles", views.AccountRolesView.as_view(), name="v2-iam-account-roles"),
    path("accounts/<int:id>/enable", views.AccountEnableView.as_view(), name="v2-iam-account-enable"),
    path("accounts/<int:id>/disable", views.AccountDisableView.as_view(), name="v2-iam-account-disable"),
    path("accounts/<int:id>/reset-password", views.AccountResetPasswordView.as_view(), name="v2-iam-account-reset-password"),
    path("roles", views.RoleListView.as_view(), name="v2-iam-roles"),
    path("roles/<int:id>", views.RoleDetailView.as_view(), name="v2-iam-role-detail"),
    path("roles/<int:id>/menus", views.RoleMenusView.as_view(), name="v2-iam-role-menus"),
    path("roles/<int:id>/permissions", views.RolePermissionsView.as_view(), name="v2-iam-role-permissions"),
    path("roles/<int:id>/data-scope", views.RoleDataScopeView.as_view(), name="v2-iam-role-data-scope"),
    path("permissions", views.PermissionListView.as_view(), name="v2-iam-permissions"),
    path("permissions/sync", views.PermissionSyncView.as_view(), name="v2-iam-permissions-sync"),
]
