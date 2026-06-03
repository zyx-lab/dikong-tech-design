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
    path("accounts", views.AccountListCreateView.as_view(), name="v2-iam-accounts"),
    path("accounts/<int:id>", views.AccountDetailView.as_view(), name="v2-iam-account-detail"),
    path("accounts/<int:id>/roles", views.AccountRolesView.as_view(), name="v2-iam-account-roles"),
    path(
        "accounts/<int:id>/qualifications",
        profile_views.AccountQualificationListCreateView.as_view(),
        name="v2-iam-account-qualifications",
    ),
    path(
        "accounts/<int:id>/qualifications/<int:qualification_id>",
        profile_views.AccountQualificationDetailView.as_view(),
        name="v2-iam-account-qualification-detail",
    ),
    path("roles", views.RoleListView.as_view(), name="v2-iam-roles"),
]
