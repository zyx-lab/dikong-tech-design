from django import forms
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet

from apps.access.models import (
    AuditLog,
    GroupPermissionScope,
    StaffProfile,
    SystemRole,
    SystemRoleGroup,
    Tenant,
    TenantMember,
    TenantMemberPosition,
    TenantMemberQualification,
    TenantMemberRole,
    User,
)

for model in (Group,):
    try:
        admin.site.unregister(model)
    except admin.sites.NotRegistered:
        pass

_PERMISSION_ACTION_LABELS = {
    "add": "新增",
    "change": "编辑",
    "delete": "删除",
    "view": "查看",
}

_PERMISSION_CODE_LABELS = {
    "access.manage_auth_groups": "管理能力组与权限",
    "access.manage_auth_scopes": "管理权限范围策略",
    "access.manage_user_accounts": "管理账号与人员档案",
    "access.view_auth_audit_logs": "查看授权审计日志",
    "access.view_user": "查看账号",
    "access.view_staffprofile": "查看人员档案",
}

_MODEL_LABEL_OVERRIDES = {
    "auditlog": "审计日志",
    "grouppermissionscope": "能力组权限范围",
    "staffprofile": "人员档案",
    "systemrole": "固定角色",
    "systemrolegroup": "角色能力组映射",
    "tenantmember": "租户成员",
    "tenantmemberposition": "租户成员岗位",
    "tenantmemberqualification": "租户成员资质",
    "user": "账号",
}


def _permission_display_label(permission: Permission) -> str:
    code = f"{permission.content_type.app_label}.{permission.codename}"
    model_class = permission.content_type.model_class()
    app_label = model_class._meta.app_config.verbose_name if model_class else permission.content_type.app_label
    model_label = _MODEL_LABEL_OVERRIDES.get(permission.content_type.model)
    if not model_label:
        model_label = model_class._meta.verbose_name if model_class else permission.content_type.model

    action_label = _PERMISSION_CODE_LABELS.get(code)
    if not action_label:
        for action, label in _PERMISSION_ACTION_LABELS.items():
            if permission.codename.startswith(f"{action}_"):
                action_label = label
                break
    if not action_label:
        action_label = permission.name
    return f"{app_label} | {model_label} | {action_label} ({code})"


class PermissionSelectField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        return _permission_display_label(obj)


class AccessUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "is_staff" in self.fields:
            self.fields["is_staff"].label = "可登录后台（Admin）"
            self.fields["is_staff"].help_text = "仅控制能否登录 Django Admin，不代表业务角色。"
        if "is_superuser" in self.fields:
            self.fields["is_superuser"].label = "超级管理员（Root）"
            self.fields["is_superuser"].help_text = "拥有全量系统/业务权限；仅允许命令行创建，不允许在后台修改。"
            self.fields["is_superuser"].disabled = True


class AccessUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "is_staff" in self.fields:
            self.fields["is_staff"].label = "可登录后台（Admin）"
            self.fields["is_staff"].help_text = "仅控制能否登录 Django Admin，不代表业务角色。"
        if "is_superuser" in self.fields:
            self.fields["is_superuser"].label = "超级管理员（Root）"
            self.fields["is_superuser"].disabled = True


class StaffProfileInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        effective_forms = []
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            cleaned_data = form.cleaned_data
            if not cleaned_data or cleaned_data.get("DELETE"):
                continue
            if form.instance.pk or any(cleaned_data.get(field) for field in ("staff_no", "name")):
                effective_forms.append(form)

        if self.instance.is_superuser:
            if effective_forms:
                raise ValidationError("superuser 账号不允许绑定 Staff。")
            return
        if not effective_forms:
            raise ValidationError("非 superuser 账号必须绑定 1 条 Staff 记录。")


class StaffProfileByUserInline(admin.StackedInline):
    model = StaffProfile
    fk_name = "user"
    formset = StaffProfileInlineFormSet
    extra = 1
    max_num = 1
    can_delete = False
    fields = (
        "staff_no",
        "name",
        "phone",
        "email",
        "employment_status",
        "org_id",
        "created_at",
        "updated_at",
    )
    readonly_fields = ("created_at", "updated_at")

    def get_extra(self, request, obj=None, **kwargs):
        if obj and hasattr(obj, "staff_profile"):
            return 0
        return 1


class GroupPermissionScopeInline(admin.TabularInline):
    model = GroupPermissionScope
    extra = 0
    fields = ("permission", "scope_type", "status", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")


class SystemRoleGroupInline(admin.TabularInline):
    model = SystemRoleGroup
    fk_name = "system_role"
    extra = 0
    fields = ("group", "status", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")


class TenantMemberRoleInline(admin.TabularInline):
    model = TenantMemberRole
    fk_name = "tenant_member"
    extra = 0
    fields = ("system_role", "status", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")


class TenantMemberPositionInline(admin.TabularInline):
    model = TenantMemberPosition
    fk_name = "tenant_member"
    extra = 0
    fields = ("code", "name", "description", "status", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")


class TenantMemberQualificationInline(admin.TabularInline):
    model = TenantMemberQualification
    fk_name = "tenant_member"
    extra = 0
    fields = ("code", "name", "description", "status", "valid_until", "payload", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = AccessUserChangeForm
    add_form = AccessUserCreationForm
    inlines = (StaffProfileByUserInline,)
    list_display = ("id", "username", "status", "is_active", "is_staff", "is_superuser")
    search_fields = ("username", "staff_profile__staff_no", "staff_profile__name")
    ordering = ("id",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("staff_profile").prefetch_related("groups")


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ("id", "staff_no", "name", "user", "employment_status", "org_id")
    search_fields = ("staff_no", "name", "user__username", "phone", "email")
    list_filter = ("employment_status",)
    autocomplete_fields = ("user",)


@admin.register(SystemRole)
class SystemRoleAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name", "status", "updated_at")
    search_fields = ("code", "name")
    list_filter = ("status",)
    inlines = (SystemRoleGroupInline,)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name", "status", "plan", "updated_at")
    search_fields = ("code", "name")
    list_filter = ("status",)


@admin.register(TenantMember)
class TenantMemberAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "user", "display_name", "status", "joined_at")
    search_fields = ("tenant__code", "user__username", "display_name", "staff_no", "phone", "email")
    list_filter = ("status", "tenant")
    autocomplete_fields = ("tenant", "user")
    inlines = (
        TenantMemberRoleInline,
        TenantMemberPositionInline,
        TenantMemberQualificationInline,
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("tenant", "user")


@admin.register(TenantMemberPosition)
class TenantMemberPositionAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant_member", "code", "name", "status", "updated_at")
    search_fields = ("tenant_member__tenant__code", "tenant_member__user__username", "code", "name")
    list_filter = ("status",)


@admin.register(TenantMemberQualification)
class TenantMemberQualificationAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant_member", "code", "name", "status", "valid_until", "updated_at")
    search_fields = ("tenant_member__tenant__code", "tenant_member__user__username", "code", "name")
    list_filter = ("status",)


@admin.register(Group)
class GroupAdmin(DjangoGroupAdmin):
    inlines = (GroupPermissionScopeInline,)
    list_display = ("id", "name", "permission_count", "scope_count")
    search_fields = ("name",)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "permissions":
            kwargs["queryset"] = Permission.objects.select_related("content_type").all().order_by(
                "content_type__app_label",
                "content_type__model",
                "codename",
            )
            kwargs["form_class"] = PermissionSelectField
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    @admin.display(description="权限数")
    def permission_count(self, obj):
        return obj.permissions.count()

    @admin.display(description="Scope 数")
    def scope_count(self, obj):
        return obj.permission_scopes.count()

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("permissions", "permission_scopes")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("id", "action", "target_type", "target_id", "tenant", "actor_user", "created_at")
    list_filter = ("action", "target_type")
    search_fields = ("action", "target_type", "target_id", "actor_user__username", "tenant__code")
    readonly_fields = (
        "tenant",
        "actor_user",
        "action",
        "target_type",
        "target_id",
        "before_data",
        "after_data",
        "ip",
        "request_id",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
