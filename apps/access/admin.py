from django.contrib import admin

from apps.access.models import (
    AuditLog,
    Permission,
    QualificationType,
    Role,
    RolePermissionGrant,
    StaffProfile,
    Tenant,
    TenantMember,
    TenantMemberQualification,
    TenantMemberRole,
    User,
)


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("id", "username", "status", "is_staff", "is_superuser", "is_platform_admin", "is_active")
    search_fields = ("username",)
    list_filter = ("status", "is_staff", "is_superuser", "is_platform_admin", "is_active")


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "user", "employment_status")
    search_fields = ("name", "user__username", "phone", "email")
    list_filter = ("employment_status",)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name", "status", "plan")
    search_fields = ("code", "name")
    list_filter = ("status",)


class TenantMemberRoleInline(admin.TabularInline):
    model = TenantMemberRole
    extra = 0


class TenantMemberQualificationInline(admin.TabularInline):
    model = TenantMemberQualification
    extra = 0


@admin.register(TenantMember)
class TenantMemberAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "user", "display_name", "member_no", "status", "joined_at")
    search_fields = ("tenant__code", "user__username", "display_name", "member_no")
    list_filter = ("status", "tenant")
    inlines = (TenantMemberRoleInline, TenantMemberQualificationInline)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name", "status")
    search_fields = ("code", "name")
    list_filter = ("status",)


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "module", "resource_code", "status")
    search_fields = ("code", "name", "module", "resource_code")
    list_filter = ("status", "module")


@admin.register(RolePermissionGrant)
class RolePermissionGrantAdmin(admin.ModelAdmin):
    list_display = ("id", "role", "permission", "scope_type")
    search_fields = ("role__code", "permission__code")
    list_filter = ("scope_type", "role")


@admin.register(QualificationType)
class QualificationTypeAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name", "requires_validity", "status")
    search_fields = ("code", "name")
    list_filter = ("requires_validity", "status")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "actor_user", "action", "target_type", "target_id", "created_at")
    search_fields = ("action", "target_type", "target_id", "request_id")
    list_filter = ("action", "target_type", "tenant")
    readonly_fields = ("before_data", "after_data", "ip", "request_id", "created_at")
