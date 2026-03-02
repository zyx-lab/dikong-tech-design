from django import forms
from django.core.exceptions import ValidationError
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.contrib.auth.models import Group, Permission
from django.forms.models import BaseInlineFormSet

from apps.access.models import (
    AuditLog,
    GroupPermissionScope,
    ScopeStatus,
    StaffProfile,
    StaffType,
    StaffTypeGroup,
    User,
)
from apps.access.services import log_action

_PERMISSION_ACTION_LABELS = {
    "add": "新增",
    "change": "编辑",
    "delete": "删除",
    "view": "查看",
}

_PERMISSION_CODE_LABELS = {
    "access.manage_auth_groups": "管理能力组与权限",
    "access.manage_auth_scopes": "管理权限范围策略",
    "access.manage_staff_type_groups": "管理身份类型能力组映射",
    "access.manage_user_accounts": "管理账号与人员档案",
    "access.view_auth_audit_logs": "查看授权审计日志",
    "access.view_user": "查看账号",
    "access.view_staffprofile": "查看人员档案",
}

_MODEL_LABEL_OVERRIDES = {
    "auditlog": "审计日志",
    "stafftypegroup": "身份类型能力组映射",
    "grouppermissionscope": "能力组权限范围",
    "stafftype": "身份类型",
    "staffprofile": "人员档案",
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
    """优化 Group 权限选择框的可读性。"""

    def label_from_instance(self, obj):
        return _permission_display_label(obj)


class AccessUserChangeForm(UserChangeForm):
    """统一 User 编辑页字段文案，避免把 Django 后台状态误解为业务角色。"""

    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "is_staff" in self.fields:
            self.fields["is_staff"].label = "可登录后台（Admin）"
            self.fields["is_staff"].help_text = "仅控制能否登录 Django Admin，不代表业务角色。"
        if "is_superuser" in self.fields:
            self.fields["is_superuser"].label = "超级管理员（系统运维）"
            self.fields["is_superuser"].help_text = "仅用于系统运维，不参与业务授权链。"


class AccessUserCreationForm(UserCreationForm):
    """统一 User 新增页字段文案。"""

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "is_staff" in self.fields:
            self.fields["is_staff"].label = "可登录后台（Admin）"
            self.fields["is_staff"].help_text = "仅控制能否登录 Django Admin，不代表业务角色。"
        if "is_superuser" in self.fields:
            self.fields["is_superuser"].label = "超级管理员（系统运维）"
            self.fields["is_superuser"].help_text = "仅用于系统运维，不参与业务授权链。"


class StaffProfileInlineFormSet(BaseInlineFormSet):
    """User 页内联 staff 的规则校验。

    规则：
    - superuser：不允许绑定 staff。
    - 非 superuser：必须绑定且只能绑定 1 条 staff。
    """

    def clean(self):
        super().clean()

        effective_forms = []
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue

            cleaned_data = form.cleaned_data
            if not cleaned_data or cleaned_data.get("DELETE"):
                continue

            # 现有记录或在新增行里填写了关键字段，都视为有效 staff 输入。
            if form.instance.pk or any(cleaned_data.get(field) for field in ("staff_no", "name", "staff_type")):
                effective_forms.append(form)

        if self.instance.is_superuser:
            if effective_forms:
                raise ValidationError("superuser 账号不允许绑定 Staff。")
            return

        if not effective_forms:
            raise ValidationError("非 superuser 账号必须绑定 1 条 Staff 记录。")


class StaffProfileByUserInline(admin.StackedInline):
    """仅在 User 页面维护 Staff，避免出现多入口配置。"""

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
        "staff_type",
        "org_id",
        "created_at",
        "updated_at",
    )
    readonly_fields = ("created_at", "updated_at")

    def get_extra(self, request, obj=None, **kwargs):
        # 已有 staff 时不再显示额外空行；新增或缺失时显示 1 行固定填写区。
        if obj and hasattr(obj, "staff_profile"):
            return 0
        return 1


class StaffTypeGroupByStaffTypeInline(admin.TabularInline):
    model = StaffTypeGroup
    fk_name = "staff_type"
    extra = 0
    fields = ("group", "status", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")


class GroupPermissionScopeInline(admin.TabularInline):
    model = GroupPermissionScope
    extra = 0
    fields = ("permission", "scope_type", "status", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")


class StaffTypeGroupByGroupInline(admin.TabularInline):
    model = StaffTypeGroup
    fk_name = "group"
    extra = 0
    fields = ("staff_type", "status", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")


def _permission_codes(group: Group) -> list[str]:
    perms = group.permissions.select_related("content_type").all()
    return sorted([f"{perm.content_type.app_label}.{perm.codename}" for perm in perms])


def _group_scope_payload(group: Group):
    scopes = (
        GroupPermissionScope.objects.filter(group=group)
        .select_related("permission__content_type")
        .order_by("permission__content_type__app_label", "permission__codename")
    )
    return [
        {
            "permission": f"{scope.permission.content_type.app_label}.{scope.permission.codename}",
            "scope_type": scope.scope_type,
            "status": scope.status,
        }
        for scope in scopes
    ]


def _group_staff_type_payload(group: Group):
    links = (
        StaffTypeGroup.objects.filter(group=group, status=ScopeStatus.ACTIVE)
        .select_related("staff_type")
        .order_by("staff_type_id")
    )
    return [{"staff_type_id": link.staff_type_id, "staff_type": link.staff_type.code} for link in links]


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = AccessUserChangeForm
    add_form = AccessUserCreationForm

    list_display = (
        "id",
        "username",
        "is_active",
        "is_staff",
        "is_superuser",
        "status",
        "staff_summary",
        "created_at",
    )
    list_filter = ("is_staff", "is_superuser", "is_active", "status")
    search_fields = ("username", "staff_profile__staff_no", "staff_profile__name")
    inlines = (StaffProfileByUserInline,)

    fieldsets = (
        ("账号", {"fields": ("username", "password")}),
        ("后台", {"fields": ("is_active", "is_staff", "is_superuser")}),
        ("时间", {"fields": ("last_login",)}),
        ("业务状态", {"fields": ("status", "staff_summary", "direct_group_count")}),
    )
    add_fieldsets = (
        (
            "新建账号",
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "password1",
                    "password2",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "status",
                ),
            },
        ),
    )
    readonly_fields = ("staff_summary", "direct_group_count")

    def get_inlines(self, request, obj):
        # superuser 不参与业务授权链，不展示 staff 内联编辑区。
        if obj and obj.is_superuser:
            return ()
        return super().get_inlines(request, obj)

    @admin.display(description="绑定 Staff")
    def staff_summary(self, obj):
        staff = getattr(obj, "staff_profile", None)
        if not staff:
            return "-"
        return f"{staff.staff_no} / {staff.name} / {staff.staff_type.code}"

    @admin.display(description="直绑 Group 数")
    def direct_group_count(self, obj):
        return obj.groups.count()

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("staff_profile__staff_type").prefetch_related("groups")


@admin.register(StaffType)
class StaffTypeAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name", "status", "created_at", "updated_at")
    list_filter = ("status",)
    search_fields = ("code", "name", "description")
    inlines = (StaffTypeGroupByStaffTypeInline,)


try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass


@admin.register(Group)
class GroupAdmin(DjangoGroupAdmin):
    """能力模块中心：在同一页维护权限、scope 和 StaffType 关联。"""

    list_display = ("id", "name", "permission_count", "scope_count", "staff_type_count")
    search_fields = ("name",)
    ordering = ("id",)
    inlines = (GroupPermissionScopeInline, StaffTypeGroupByGroupInline)

    @admin.display(description="权限数")
    def permission_count(self, obj):
        return obj.permissions.count()

    @admin.display(description="范围数")
    def scope_count(self, obj):
        return obj.permission_scopes.count()

    @admin.display(description="StaffType 数")
    def staff_type_count(self, obj):
        return obj.staff_type_links.filter(status=ScopeStatus.ACTIVE).count()

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related("permissions", "permission_scopes", "staff_type_links")

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "permissions":
            kwargs["queryset"] = Permission.objects.select_related("content_type").all().order_by(
                "content_type__app_label", "content_type__model", "codename"
            )
            kwargs["form_class"] = PermissionSelectField
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def save_related(self, request, form, formsets, change):
        before_permissions = _permission_codes(form.instance) if change else []
        before_scopes = _group_scope_payload(form.instance) if change else []
        before_staff_types = _group_staff_type_payload(form.instance) if change else []

        super().save_related(request, form, formsets, change)

        after_permissions = _permission_codes(form.instance)
        after_scopes = _group_scope_payload(form.instance)
        after_staff_types = _group_staff_type_payload(form.instance)

        if before_permissions != after_permissions:
            log_action(
                request=request,
                action="GROUP_PERMISSION_ASSIGN_ADMIN",
                target_type="group",
                target_id=form.instance.id,
                before_data={"permissions": before_permissions},
                after_data={"permissions": after_permissions},
            )

        if before_scopes != after_scopes:
            log_action(
                request=request,
                action="GROUP_SCOPE_BULK_UPDATE_ADMIN",
                target_type="group",
                target_id=form.instance.id,
                before_data={"scopes": before_scopes},
                after_data={"scopes": after_scopes},
            )

        if before_staff_types != after_staff_types:
            log_action(
                request=request,
                action="GROUP_STAFF_TYPE_BULK_UPDATE_ADMIN",
                target_type="group",
                target_id=form.instance.id,
                before_data={"staff_types": before_staff_types},
                after_data={"staff_types": after_staff_types},
            )


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("id", "action", "target_type", "target_id", "actor_user", "request_id", "created_at")
    search_fields = ("action", "target_type", "target_id", "request_id", "actor_user__username")
    list_filter = ("action", "target_type", "actor_user")
    date_hierarchy = "created_at"
    readonly_fields = (
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

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StaffTypeGroup)
class StaffTypeGroupAdmin(admin.ModelAdmin):
    list_display = ("id", "staff_type", "group", "status", "updated_at")
    search_fields = ("staff_type__code", "staff_type__name", "group__name")
    list_filter = ("status",)

    def get_model_perms(self, request):
        # 降低菜单噪声：优先在 StaffType/Group 页面维护。
        return {}


@admin.register(GroupPermissionScope)
class GroupPermissionScopeAdmin(admin.ModelAdmin):
    list_display = ("id", "group", "permission", "scope_type", "status", "updated_at")
    search_fields = ("group__name", "permission__codename", "permission__content_type__app_label")
    list_filter = ("scope_type", "status")

    def get_model_perms(self, request):
        return {}
