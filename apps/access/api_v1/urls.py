from django.urls import path

from apps.access.api_v1.views.me import MeProfileView, MeTenantsView
from apps.access.api_v1.views.platform import (
    PlatformAuditLogsView,
    PlatformPermissionsView,
    PlatformRoleDetailView,
    PlatformRolesView,
    PlatformTenantDetailView,
    PlatformTenantDisableView,
    PlatformTenantEnableView,
    PlatformTenantInitializeAdminView,
    PlatformTenantsView,
)
from apps.access.api_v1.views.session import (
    SessionLoginView,
    SessionLogoutView,
    SessionRefreshView,
    SessionRegisterByPhoneView,
    SessionRegisterView,
)
from apps.access.api_v1.views.tenant import (
    TenantAuditLogsView,
    TenantMemberDetailView,
    TenantMemberDisableView,
    TenantMemberEnableView,
    TenantMemberRolesReplaceView,
    TenantMembersView,
    TenantMeView,
    TenantRolesView,
)

urlpatterns = [
    path("session/login", SessionLoginView.as_view(), name="iam-session-login"),
    path("session/refresh", SessionRefreshView.as_view(), name="iam-session-refresh"),
    path("session/logout", SessionLogoutView.as_view(), name="iam-session-logout"),
    path("session/register", SessionRegisterView.as_view(), name="iam-session-register"),
    path("session/register-by-phone", SessionRegisterByPhoneView.as_view(), name="iam-session-register-by-phone"),
    path("me/profile", MeProfileView.as_view(), name="iam-me-profile"),
    path("me/tenants", MeTenantsView.as_view(), name="iam-me-tenants"),
    path("tenant/me", TenantMeView.as_view(), name="iam-tenant-me"),
    path("tenant/members", TenantMembersView.as_view(), name="iam-tenant-members"),
    path("tenant/members/<int:memberId>", TenantMemberDetailView.as_view(), name="iam-tenant-member-detail"),
    path("tenant/roles", TenantRolesView.as_view(), name="iam-tenant-roles"),
    path("tenant/members/<int:memberId>/roles", TenantMemberRolesReplaceView.as_view(), name="iam-tenant-member-roles"),
    path("tenant/members/<int:memberId>/enable", TenantMemberEnableView.as_view(), name="iam-tenant-member-enable"),
    path("tenant/members/<int:memberId>/disable", TenantMemberDisableView.as_view(), name="iam-tenant-member-disable"),
    path("tenant/audit-logs", TenantAuditLogsView.as_view(), name="iam-tenant-audit-logs"),
    path("platform/permissions", PlatformPermissionsView.as_view(), name="iam-platform-permissions"),
    path("platform/roles", PlatformRolesView.as_view(), name="iam-platform-roles"),
    path("platform/roles/<int:roleId>", PlatformRoleDetailView.as_view(), name="iam-platform-role-detail"),
    path("platform/audit-logs", PlatformAuditLogsView.as_view(), name="iam-platform-audit-logs"),
    path("platform/tenants", PlatformTenantsView.as_view(), name="iam-platform-tenants"),
    path("platform/tenants/<int:tenantId>", PlatformTenantDetailView.as_view(), name="iam-platform-tenant-detail"),
    path("platform/tenants/<int:tenantId>/enable", PlatformTenantEnableView.as_view(), name="iam-platform-tenant-enable"),
    path("platform/tenants/<int:tenantId>/disable", PlatformTenantDisableView.as_view(), name="iam-platform-tenant-disable"),
    path(
        "platform/tenants/<int:tenantId>/initialize-admin",
        PlatformTenantInitializeAdminView.as_view(),
        name="iam-platform-tenant-initialize-admin",
    ),
]
