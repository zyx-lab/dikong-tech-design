from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.access.models import AuthSession, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    model = User
    ordering = ("id",)
    list_display = ("id", "username", "status", "is_active", "is_staff", "is_superuser", "is_platform_admin", "last_login")
    list_filter = ("status", "is_active", "is_staff", "is_superuser", "is_platform_admin")
    search_fields = ("username",)
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("状态", {"fields": ("status", "is_active", "is_staff", "is_superuser", "is_platform_admin")}),
        ("登录", {"fields": ("last_login",)}),
        ("时间", {"fields": ("created_at", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("username", "password1", "password2", "status", "is_active", "is_staff", "is_platform_admin"),
            },
        ),
    )
    readonly_fields = ("created_at", "updated_at", "last_login")


@admin.register(AuthSession)
class AuthSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "session_type", "access_token_expires_at", "refresh_token_expires_at", "revoked_at", "last_used_at")
    list_filter = ("session_type", "revoked_at")
    search_fields = ("user__username",)
    readonly_fields = tuple(field.name for field in AuthSession._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in {"GET", "HEAD", "OPTIONS"}

    def has_delete_permission(self, request, obj=None):
        return False
