from django.urls import path

from apps.iam_v2 import views


urlpatterns = [
    path("me/context", views.MeContextView.as_view(), name="v2-iam-me-context"),
    path("departments", views.DepartmentListCreateView.as_view(), name="v2-iam-departments"),
    path("departments/<int:id>", views.DepartmentDetailView.as_view(), name="v2-iam-department-detail"),
    path("departments/<int:id>/enable", views.DepartmentEnableView.as_view(), name="v2-iam-department-enable"),
    path("departments/<int:id>/disable", views.DepartmentDisableView.as_view(), name="v2-iam-department-disable"),
    path("accounts", views.AccountListCreateView.as_view(), name="v2-iam-accounts"),
    path("accounts/<int:id>", views.AccountDetailView.as_view(), name="v2-iam-account-detail"),
    path("accounts/<int:id>/roles", views.AccountRolesView.as_view(), name="v2-iam-account-roles"),
    path("roles", views.RoleListView.as_view(), name="v2-iam-roles"),
]
