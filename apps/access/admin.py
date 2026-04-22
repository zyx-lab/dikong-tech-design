import json
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.db.models import Count, Prefetch, Q
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join

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
    TenantMemberRoleStatus,
    User,
)
from apps.media_file.models import MediaFile
from apps.mission.models import Mission


def pretty_json(value):
    if value in (None, "", {}, []):
        return "-"
    return format_html("<pre>{}</pre>", json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def render_lines(items):
    rows = [(item,) for item in items if item not in (None, "")]
    if not rows:
        return "-"
    return format_html_join("<br>", "{}", rows)


def truncate_items(items, limit=5):
    items = list(items)
    if len(items) <= limit:
        return items
    return [*items[:limit], f"... 共 {len(items)} 条"]


def admin_change_link(obj, label=None):
    if obj is None or getattr(obj, "pk", None) is None:
        return "-"
    url = reverse(f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change", args=[obj.pk])
    return format_html('<a href="{}">{}</a>', url, label or str(obj))


def get_staff_profile(user):
    try:
        return user.staff_profile
    except StaffProfile.DoesNotExist:
        return None


def format_role_bindings(bindings, *, limit=5):
    items = [f"{binding.system_role.code}:{binding.system_role.name}" for binding in bindings if binding.status == TenantMemberRoleStatus.GRANTED]
    return render_lines(truncate_items(items, limit=limit))


class YesNoListFilter(admin.SimpleListFilter):
    def lookups(self, request, model_admin):
        return (("yes", "是"), ("no", "否"))


class HasStaffProfileFilter(YesNoListFilter):
    title = "有人员档案"
    parameter_name = "has_staff_profile"

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(staff_profile__isnull=False).distinct()
        if self.value() == "no":
            return queryset.filter(staff_profile__isnull=True).distinct()
        return queryset


class HasTenantMembershipFilter(YesNoListFilter):
    title = "有租户成员关系"
    parameter_name = "has_tenant_membership"

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(tenant_members__isnull=False).distinct()
        if self.value() == "no":
            return queryset.filter(tenant_members__isnull=True).distinct()
        return queryset


class HasGrantedRoleFilter(YesNoListFilter):
    title = "有已生效角色"
    parameter_name = "has_granted_role"

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(role_bindings__status=TenantMemberRoleStatus.GRANTED).distinct()
        if self.value() == "no":
            return queryset.exclude(role_bindings__status=TenantMemberRoleStatus.GRANTED).distinct()
        return queryset


class HasQualificationFilter(YesNoListFilter):
    title = "有资质记录"
    parameter_name = "has_qualification"

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(qualifications__isnull=False).distinct()
        if self.value() == "no":
            return queryset.filter(qualifications__isnull=True).distinct()
        return queryset


class HasBoundMissionFilter(YesNoListFilter):
    title = "已绑定任务"
    parameter_name = "has_bound_mission"

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(mission__isnull=False).distinct()
        if self.value() == "no":
            return queryset.filter(mission__isnull=True).distinct()
        return queryset


class ReadOnlyAdminMixin:
    readonly_fields = ()

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in {"GET", "HEAD", "OPTIONS"}

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        fields.extend(field.name for field in self.model._meta.fields)
        fields.extend(field.name for field in self.model._meta.many_to_many)
        return tuple(dict.fromkeys(fields))

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        context.update(
            show_save=False,
            show_save_and_continue=False,
            show_save_and_add_another=False,
            show_delete=False,
        )
        return super().render_change_form(request, context, add=add, change=change, form_url=form_url, obj=obj)


class ReadOnlyInlineMixin:
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        fields.extend(field.name for field in self.model._meta.fields)
        fields.extend(field.name for field in self.model._meta.many_to_many)
        return tuple(dict.fromkeys(fields))


class StaffProfileInline(ReadOnlyInlineMixin, admin.StackedInline):
    model = StaffProfile
    fk_name = "user"
    fields = ("name", "phone", "email", "employment_status", "org_id", "created_at", "updated_at")
    verbose_name = "人员档案"
    verbose_name_plural = "人员档案"
    max_num = 1


class TenantMemberInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = TenantMember
    fields = ("user_link", "staff_name", "display_name", "member_no", "status", "role_summary", "joined_at", "created_at")
    readonly_fields = ("user_link", "staff_name", "role_summary")

    def get_queryset(self, request):
        role_queryset = TenantMemberRole.objects.select_related("system_role").order_by("system_role__code")
        return (
            super()
            .get_queryset(request)
            .select_related("user", "user__staff_profile")
            .prefetch_related(Prefetch("role_bindings", queryset=role_queryset))
        )

    @admin.display(description="账号")
    def user_link(self, obj):
        return admin_change_link(obj.user, obj.user.username)

    @admin.display(description="姓名")
    def staff_name(self, obj):
        profile = get_staff_profile(obj.user)
        return profile.name if profile else "-"

    @admin.display(description="角色")
    def role_summary(self, obj):
        return format_role_bindings(obj.role_bindings.all(), limit=3)


class UserTenantMemberInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = TenantMember
    fk_name = "user"
    fields = ("tenant_link", "display_name", "member_no", "status", "role_summary", "joined_at", "created_at")
    readonly_fields = ("tenant_link", "role_summary")

    def get_queryset(self, request):
        role_queryset = TenantMemberRole.objects.select_related("system_role").order_by("system_role__code")
        return (
            super()
            .get_queryset(request)
            .select_related("tenant")
            .prefetch_related(Prefetch("role_bindings", queryset=role_queryset))
        )

    @admin.display(description="租户")
    def tenant_link(self, obj):
        return admin_change_link(obj.tenant, f"{obj.tenant.code}:{obj.tenant.name}")

    @admin.display(description="角色")
    def role_summary(self, obj):
        return format_role_bindings(obj.role_bindings.all(), limit=3)


class TenantMemberRoleInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = TenantMemberRole
    fields = ("system_role", "status", "assigned_by_user", "assigned_at", "created_at")


class TenantMemberQualificationInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = TenantMemberQualification
    fields = (
        "qualification_type",
        "status",
        "certificate_no",
        "level",
        "valid_from",
        "valid_until",
        "updated_at",
    )


class RolePermissionGrantInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = RolePermissionGrant
    fields = ("permission", "scope_type", "created_at")


class RoleMemberBindingInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = TenantMemberRole
    fk_name = "system_role"
    fields = ("tenant_member_link", "tenant_link", "user_link", "status", "assigned_at", "created_at")
    readonly_fields = ("tenant_member_link", "tenant_link", "user_link")
    verbose_name = "角色成员绑定"
    verbose_name_plural = "角色成员绑定"

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                "tenant_member",
                "tenant_member__tenant",
                "tenant_member__user",
                "tenant_member__user__staff_profile",
            )
            .order_by("tenant_member__tenant__code", "tenant_member__member_no", "tenant_member__user__username")
        )

    @admin.display(description="成员")
    def tenant_member_link(self, obj):
        member_label = obj.tenant_member.member_no or obj.tenant_member.display_name or obj.tenant_member.user.username
        return admin_change_link(obj.tenant_member, member_label)

    @admin.display(description="租户")
    def tenant_link(self, obj):
        return admin_change_link(obj.tenant_member.tenant, f"{obj.tenant_member.tenant.code}:{obj.tenant_member.tenant.name}")

    @admin.display(description="账号")
    def user_link(self, obj):
        return admin_change_link(obj.tenant_member.user, obj.tenant_member.user.username)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = (
        "id",
        "username",
        "staff_name",
        "employment_status",
        "status",
        "tenant_member_count",
        "tenant_membership_summary",
        "is_platform_admin",
        "is_superuser",
        "is_active",
        "created_at",
    )
    list_filter = (
        HasStaffProfileFilter,
        HasTenantMembershipFilter,
        "status",
        "is_active",
        "is_staff",
        "is_superuser",
        "is_platform_admin",
        "staff_profile__employment_status",
    )
    search_fields = ("username", "staff_profile__name", "staff_profile__phone", "staff_profile__email")
    ordering = ("id",)
    readonly_fields = ("tenant_member_count", "tenant_membership_summary", "last_login", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("状态", {"fields": ("status", "is_active")}),
        ("平台身份", {"fields": ("is_staff", "is_superuser", "is_platform_admin")}),
        ("租户关系", {"fields": ("tenant_member_count", "tenant_membership_summary")}),
        ("时间", {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("username", "password1", "password2"),
            },
        ),
    )
    inlines = (StaffProfileInline, UserTenantMemberInline)
    list_per_page = 50

    def get_queryset(self, request):
        membership_queryset = (
            TenantMember.objects.select_related("tenant")
            .prefetch_related(
                Prefetch(
                    "role_bindings",
                    queryset=TenantMemberRole.objects.select_related("system_role").order_by("system_role__code"),
                )
            )
            .order_by("tenant__code", "id")
        )
        return (
            super()
            .get_queryset(request)
            .select_related("staff_profile")
            .annotate(tenant_member_total=Count("tenant_members", distinct=True))
            .prefetch_related(Prefetch("tenant_members", queryset=membership_queryset))
        )

    @admin.display(description="人员姓名", ordering="staff_profile__name")
    def staff_name(self, obj):
        profile = get_staff_profile(obj)
        return profile.name if profile else "-"

    @admin.display(description="在职状态", ordering="staff_profile__employment_status")
    def employment_status(self, obj):
        profile = get_staff_profile(obj)
        if profile is None:
            return "-"
        return profile.get_employment_status_display()

    @admin.display(description="租户成员数", ordering="tenant_member_total")
    def tenant_member_count(self, obj):
        return getattr(obj, "tenant_member_total", obj.tenant_members.count())

    @admin.display(description="租户关系")
    def tenant_membership_summary(self, obj):
        items = []
        for member in obj.tenant_members.all():
            granted_roles = [binding.system_role.code for binding in member.role_bindings.all() if binding.status == TenantMemberRoleStatus.GRANTED]
            parts = [member.tenant.code]
            if member.member_no:
                parts.append(member.member_no)
            if granted_roles:
                parts.append(", ".join(granted_roles))
            items.append(" / ".join(parts))
        return render_lines(truncate_items(items, limit=4))


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "user", "phone", "email", "employment_status", "org_id", "created_at")
    list_filter = ("employment_status",)
    search_fields = ("name", "user__username", "phone", "email")
    autocomplete_fields = ("user",)
    list_select_related = ("user",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name", "status", "plan", "member_count", "active_member_count", "created_at")
    list_filter = ("status",)
    search_fields = ("code", "name", "plan", "remark")
    readonly_fields = (
        "member_count",
        "active_member_count",
        "role_distribution_summary",
        "recent_member_summary",
        "created_at",
        "updated_at",
    )
    fields = (
        "code",
        "name",
        "status",
        "plan",
        "remark",
        "member_count",
        "active_member_count",
        "role_distribution_summary",
        "recent_member_summary",
        "created_at",
        "updated_at",
    )
    inlines = (TenantMemberInline,)
    list_per_page = 50

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("code", "name", "status", "plan", "remark")
        return super().get_fields(request, obj)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        return super().get_readonly_fields(request, obj)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                member_total=Count("members", distinct=True),
                active_member_total=Count("members", filter=Q(members__status=1), distinct=True),
            )
        )

    @admin.display(description="成员数", ordering="member_total")
    def member_count(self, obj):
        return getattr(obj, "member_total", obj.members.count())

    @admin.display(description="激活成员数", ordering="active_member_total")
    def active_member_count(self, obj):
        return getattr(obj, "active_member_total", obj.members.filter(status=1).count())

    @admin.display(description="角色分布")
    def role_distribution_summary(self, obj):
        distributions = (
            TenantMemberRole.objects.filter(
                tenant_member__tenant=obj,
                status=TenantMemberRoleStatus.GRANTED,
            )
            .values("system_role__code", "system_role__name")
            .annotate(total=Count("id"))
            .order_by("-total", "system_role__code")
        )
        return render_lines(
            f"{row['system_role__code']}:{row['system_role__name']} ({row['total']})"
            for row in distributions
        )

    @admin.display(description="最近成员")
    def recent_member_summary(self, obj):
        members = (
            obj.members.select_related("user", "user__staff_profile")
            .order_by("-joined_at", "-created_at", "-id")[:6]
        )
        items = []
        for member in members:
            profile = get_staff_profile(member.user)
            name = profile.name if profile else member.user.username
            member_label = member.member_no or "-"
            items.append(f"{name} / {member_label} / {member.get_status_display()}")
        return render_lines(items)


@admin.register(TenantMember)
class TenantMemberAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant_link",
        "user_link",
        "staff_name",
        "display_name",
        "member_no",
        "status",
        "role_summary",
        "qualification_count",
        "joined_at",
    )
    list_filter = ("status", HasGrantedRoleFilter, HasQualificationFilter, "tenant")
    search_fields = (
        "tenant__code",
        "tenant__name",
        "user__username",
        "user__staff_profile__name",
        "member_no",
        "display_name",
        "invitation_token",
    )
    autocomplete_fields = ("tenant", "user", "invited_by_user")
    list_select_related = ("tenant", "user", "user__staff_profile", "invited_by_user")
    readonly_fields = (
        "tenant_link",
        "user_link",
        "role_summary",
        "qualification_summary",
        "qualification_count",
        "invitation_token",
        "invited_by_user",
        "invited_at",
        "expires_at",
        "responded_at",
        "joined_at",
        "created_at",
        "updated_at",
    )
    fields = (
        "tenant_link",
        "user_link",
        "display_name",
        "member_no",
        "status",
        "role_summary",
        "qualification_count",
        "qualification_summary",
        "invitation_token",
        "invited_by_user",
        "invited_at",
        "expires_at",
        "responded_at",
        "joined_at",
        "created_at",
        "updated_at",
    )
    inlines = (TenantMemberRoleInline, TenantMemberQualificationInline)
    list_per_page = 50

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("tenant", "user", "display_name", "member_no", "status")
        return super().get_fields(request, obj)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        return super().get_readonly_fields(request, obj)

    def get_queryset(self, request):
        role_queryset = TenantMemberRole.objects.select_related("system_role").order_by("system_role__code")
        qualification_queryset = TenantMemberQualification.objects.select_related("qualification_type")
        return (
            super()
            .get_queryset(request)
            .annotate(
                granted_role_total=Count("role_bindings", filter=Q(role_bindings__status=TenantMemberRoleStatus.GRANTED), distinct=True),
                qualification_total=Count("qualifications", distinct=True),
            )
            .select_related("tenant", "user", "user__staff_profile", "invited_by_user")
            .prefetch_related(
                Prefetch("role_bindings", queryset=role_queryset),
                Prefetch("qualifications", queryset=qualification_queryset),
            )
        )

    @admin.display(description="租户", ordering="tenant__code")
    def tenant_link(self, obj):
        return admin_change_link(obj.tenant, f"{obj.tenant.code}:{obj.tenant.name}")

    @admin.display(description="账号", ordering="user__username")
    def user_link(self, obj):
        return admin_change_link(obj.user, obj.user.username)

    @admin.display(description="人员姓名", ordering="user__staff_profile__name")
    def staff_name(self, obj):
        profile = get_staff_profile(obj.user)
        return profile.name if profile else "-"

    @admin.display(description="角色")
    def role_summary(self, obj):
        return format_role_bindings(obj.role_bindings.all(), limit=4)

    @admin.display(description="资质数", ordering="qualification_total")
    def qualification_count(self, obj):
        return getattr(obj, "qualification_total", obj.qualifications.count())

    @admin.display(description="资质摘要")
    def qualification_summary(self, obj):
        items = []
        for qualification in obj.qualifications.all():
            status_label = qualification.get_status_display()
            validity = ""
            if qualification.valid_until:
                validity = f" / 截止 {qualification.valid_until}"
            items.append(f"{qualification.qualification_type.code}:{qualification.qualification_type.name} / {status_label}{validity}")
        return render_lines(truncate_items(items, limit=5))


@admin.register(TenantMemberRole)
class TenantMemberRoleAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "tenant_code",
        "tenant_member",
        "member_no",
        "role_code",
        "status",
        "assigned_by_user",
        "assigned_at",
        "created_at",
    )
    list_filter = ("status", "system_role", "tenant_member__tenant")
    search_fields = (
        "tenant_member__tenant__code",
        "tenant_member__tenant__name",
        "tenant_member__user__username",
        "tenant_member__user__staff_profile__name",
        "tenant_member__member_no",
        "system_role__code",
        "system_role__name",
    )
    list_select_related = (
        "tenant_member",
        "tenant_member__tenant",
        "tenant_member__user",
        "tenant_member__user__staff_profile",
        "system_role",
        "assigned_by_user",
    )
    fields = ("tenant_member", "system_role", "status", "assigned_by_user", "assigned_at", "created_at", "updated_at")
    list_per_page = 50

    @admin.display(description="租户编码", ordering="tenant_member__tenant__code")
    def tenant_code(self, obj):
        return obj.tenant_member.tenant.code

    @admin.display(description="工号", ordering="tenant_member__member_no")
    def member_no(self, obj):
        return obj.tenant_member.member_no or "-"

    @admin.display(description="角色编码", ordering="system_role__code")
    def role_code(self, obj):
        return obj.system_role.code


@admin.register(TenantMemberQualification)
class TenantMemberQualificationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant_code",
        "tenant_member",
        "qualification_type",
        "status",
        "certificate_no",
        "valid_from",
        "valid_until",
        "updated_at",
    )
    list_filter = ("status", "qualification_type", "qualification_type__requires_validity", "tenant_member__tenant")
    search_fields = (
        "tenant_member__tenant__code",
        "tenant_member__tenant__name",
        "tenant_member__user__username",
        "tenant_member__user__staff_profile__name",
        "tenant_member__member_no",
        "qualification_type__code",
        "qualification_type__name",
        "certificate_no",
    )
    autocomplete_fields = ("tenant_member", "qualification_type")
    readonly_fields = ("created_at", "updated_at")
    list_per_page = 50

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("tenant_member", "tenant_member__tenant", "tenant_member__user", "qualification_type")
        )

    @admin.display(description="租户编码", ordering="tenant_member__tenant__code")
    def tenant_code(self, obj):
        return obj.tenant_member.tenant.code


@admin.register(Role)
class RoleAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "code",
        "name",
        "status",
        "permission_count",
        "member_binding_count",
        "module_scope_summary",
        "member_binding_preview",
    )
    list_filter = ("status",)
    search_fields = ("code", "name", "description", "permission_grants__permission__code")
    readonly_fields = (
        "permission_count",
        "member_binding_count",
        "module_scope_summary",
        "permission_matrix_summary",
        "member_binding_preview",
        "created_at",
        "updated_at",
    )
    fields = (
        "code",
        "name",
        "description",
        "status",
        "permission_count",
        "member_binding_count",
        "module_scope_summary",
        "permission_matrix_summary",
        "member_binding_preview",
        "created_at",
        "updated_at",
    )
    inlines = (RolePermissionGrantInline, RoleMemberBindingInline)
    list_per_page = 50

    def get_queryset(self, request):
        grant_queryset = RolePermissionGrant.objects.select_related("permission").order_by(
            "permission__module",
            "permission__code",
        )
        binding_queryset = TenantMemberRole.objects.select_related(
            "tenant_member",
            "tenant_member__tenant",
            "tenant_member__user",
            "tenant_member__user__staff_profile",
        ).order_by("tenant_member__tenant__code", "tenant_member__member_no", "tenant_member__user__username")
        return (
            super()
            .get_queryset(request)
            .annotate(
                permission_total=Count("permission_grants", distinct=True),
                member_binding_total=Count("member_bindings", distinct=True),
            )
            .prefetch_related(
                Prefetch("permission_grants", queryset=grant_queryset),
                Prefetch("member_bindings", queryset=binding_queryset),
            )
        )

    @admin.display(description="权限数", ordering="permission_total")
    def permission_count(self, obj):
        return getattr(obj, "permission_total", obj.permission_grants.count())

    @admin.display(description="成员绑定数", ordering="member_binding_total")
    def member_binding_count(self, obj):
        return getattr(obj, "member_binding_total", obj.member_bindings.count())

    @admin.display(description="权限聚合")
    def module_scope_summary(self, obj):
        grants = list(obj.permission_grants.all())
        if not grants:
            return "-"
        grouped = defaultdict(lambda: {"count": 0, "scopes": set()})
        for grant in grants:
            grouped[grant.permission.module]["count"] += 1
            grouped[grant.permission.module]["scopes"].add(grant.scope_type)
        rows = [
            (f"{module} ({meta['count']})", ", ".join(sorted(meta["scopes"])))
            for module, meta in sorted(grouped.items())
        ]
        return format_html_join("<br>", "<strong>{}</strong>: {}", rows)

    @admin.display(description="权限明细")
    def permission_matrix_summary(self, obj):
        grants = list(obj.permission_grants.all())
        if not grants:
            return "-"
        grouped = defaultdict(list)
        for grant in grants:
            grouped[grant.permission.module].append(f"{grant.permission.code} [{grant.scope_type}]")
        rendered_rows = []
        for module, items in sorted(grouped.items()):
            rendered_items = format_html_join("<br>", "{}", ((item,) for item in items))
            rendered_rows.append(format_html("<strong>{}</strong><br>{}", module, rendered_items))
        return format_html_join("<br><br>", "{}", ((row,) for row in rendered_rows))

    @admin.display(description="已绑定成员")
    def member_binding_preview(self, obj):
        items = []
        for binding in obj.member_bindings.all():
            if binding.status != TenantMemberRoleStatus.GRANTED:
                continue
            member = binding.tenant_member
            member_label = member.member_no or member.display_name or member.user.username
            items.append(
                format_html(
                    "{} / {}",
                    admin_change_link(member.tenant, member.tenant.code),
                    admin_change_link(member, member_label),
                )
            )
        return render_lines(truncate_items(items, limit=5))


@admin.register(Permission)
class PermissionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "code", "module", "resource_code", "status", "role_count")
    list_filter = ("status", "module", ("resource_code", admin.EmptyFieldListFilter))
    search_fields = ("code", "name", "module", "resource_code", "description")
    readonly_fields = ("role_count", "used_by_roles_summary", "created_at", "updated_at")
    list_per_page = 50
    fields = (
        "code",
        "name",
        "module",
        "resource_code",
        "description",
        "status",
        "role_count",
        "used_by_roles_summary",
        "created_at",
        "updated_at",
    )

    def get_queryset(self, request):
        grant_queryset = RolePermissionGrant.objects.select_related("role").order_by("role__code")
        return (
            super()
            .get_queryset(request)
            .annotate(role_total=Count("role_grants", distinct=True))
            .prefetch_related(Prefetch("role_grants", queryset=grant_queryset))
        )

    @admin.display(description="角色数", ordering="role_total")
    def role_count(self, obj):
        return getattr(obj, "role_total", obj.role_grants.count())

    @admin.display(description="被哪些角色使用")
    def used_by_roles_summary(self, obj):
        grants = list(obj.role_grants.all())
        if not grants:
            return "-"
        rows = ((f"{grant.role.code}:{grant.scope_type}",) for grant in grants)
        return format_html_join("<br>", "{}", rows)


@admin.register(RolePermissionGrant)
class RolePermissionGrantAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "role", "permission", "permission_module", "permission_resource_code", "scope_type", "created_at")
    list_filter = ("scope_type", "role", "permission__module")
    search_fields = ("role__code", "role__name", "permission__code", "permission__name")
    list_select_related = ("role", "permission")
    fields = ("role", "permission", "scope_type", "created_at", "updated_at")
    list_per_page = 50

    @admin.display(description="模块", ordering="permission__module")
    def permission_module(self, obj):
        return obj.permission.module

    @admin.display(description="资源编码", ordering="permission__resource_code")
    def permission_resource_code(self, obj):
        return obj.permission.resource_code or "-"


@admin.register(QualificationType)
class QualificationTypeAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name", "requires_validity", "status", "member_record_count", "created_at")
    list_filter = ("requires_validity", "status")
    search_fields = ("code", "name")
    readonly_fields = ("member_record_count", "created_at", "updated_at")
    list_per_page = 50

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(member_record_total=Count("member_records", distinct=True))

    @admin.display(description="成员记录数", ordering="member_record_total")
    def member_record_count(self, obj):
        return getattr(obj, "member_record_total", obj.member_records.count())


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "created_at", "tenant", "actor_user", "action", "target_type", "target_id", "request_id")
    list_filter = ("action", "target_type", "tenant")
    search_fields = ("action", "target_type", "target_id", "request_id", "actor_user__username", "tenant__code")
    date_hierarchy = "created_at"
    readonly_fields = ("before_data_pretty", "after_data_pretty")
    list_select_related = ("tenant", "actor_user")
    list_per_page = 50
    fields = (
        "tenant",
        "actor_user",
        "action",
        "target_type",
        "target_id",
        "before_data_pretty",
        "after_data_pretty",
        "ip",
        "request_id",
        "created_at",
    )

    @admin.display(description="变更前")
    def before_data_pretty(self, obj):
        return pretty_json(obj.before_data)

    @admin.display(description="变更后")
    def after_data_pretty(self, obj):
        return pretty_json(obj.after_data)


@admin.register(Mission)
class MissionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "tenant_link",
        "name",
        "status_label",
        "pilot_link",
        "device_sn",
        "scheduled_at",
        "started_at",
        "finished_at",
        "media_count",
    )
    list_filter = ("tenant", "status", "is_deleted")
    search_fields = (
        "tenant__code",
        "tenant__name",
        "name",
        "route_name",
        "drone_name",
        "device_sn",
        "pilot_name",
    )
    readonly_fields = ("tenant_link", "pilot_link", "media_file_summary")
    fields = (
        "tenant_link",
        "name",
        "status",
        "pilot_link",
        "pilot_name",
        "route",
        "route_name",
        "drone",
        "drone_name",
        "device_sn",
        "scheduled_at",
        "started_at",
        "finished_at",
        "remark",
        "media_file_summary",
        "created_at",
        "updated_at",
    )
    list_per_page = 50

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("tenant", "pilot", "pilot__user", "pilot__user__staff_profile", "route", "drone")
            .annotate(media_total=Count("media_files", distinct=True))
        )

    @admin.display(description="租户", ordering="tenant__code")
    def tenant_link(self, obj):
        return admin_change_link(obj.tenant, f"{obj.tenant.code}:{obj.tenant.name}")

    @admin.display(description="状态", ordering="status")
    def status_label(self, obj):
        return obj.get_status_display()

    @admin.display(description="飞手", ordering="pilot__user__staff_profile__name")
    def pilot_link(self, obj):
        label = obj.pilot_name or obj.pilot.display_name or obj.pilot.user.username
        return admin_change_link(obj.pilot, label)

    @admin.display(description="关联媒体数", ordering="media_total")
    def media_count(self, obj):
        return getattr(obj, "media_total", obj.media_files.count())

    @admin.display(description="关联媒体")
    def media_file_summary(self, obj):
        media_files = obj.media_files.select_related("dji_index").order_by("-captured_at", "-id")[:10]
        items = []
        for media in media_files:
            sync_status = ""
            if hasattr(media, "dji_index"):
                sync_status = media.dji_index.get_sync_status_display()
            suffix = f" / {sync_status}" if sync_status else ""
            items.append(admin_change_link(media, f"{media.file_name}{suffix}"))
        return render_lines(items)


@admin.register(MediaFile)
class MediaFileAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "tenant_link",
        "file_name",
        "media_type_label",
        "device_sn",
        "captured_at",
        "mission_link",
        "sync_status_display",
        "dji_file_id_display",
        "last_sync_at_display",
        "created_at",
    )
    list_filter = ("tenant", "media_type", "is_deleted", HasBoundMissionFilter, "dji_index__sync_status")
    search_fields = (
        "tenant__code",
        "tenant__name",
        "file_name",
        "device_sn",
        "mission__name",
        "dji_index__dji_file_id",
    )
    readonly_fields = (
        "tenant_link",
        "mission_link",
        "file_url_link",
        "thumbnail_url_link",
        "capture_location",
        "sync_status_display",
        "dji_file_id_display",
        "last_sync_at_display",
        "sync_error_display",
    )
    fields = (
        "tenant_link",
        "mission_link",
        "file_name",
        "media_type",
        "device_sn",
        "captured_at",
        "file_size",
        "capture_location",
        "file_url_link",
        "thumbnail_url_link",
        "sync_status_display",
        "dji_file_id_display",
        "last_sync_at_display",
        "sync_error_display",
        "is_deleted",
        "deleted_at",
        "created_at",
    )
    list_per_page = 50

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("tenant", "mission", "dji_index")
        )

    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        if obj is not None and obj.mission_id is None and "mission_link" in fields:
            fields.remove("mission_link")
        return fields

    @admin.display(description="租户", ordering="tenant__code")
    def tenant_link(self, obj):
        return admin_change_link(obj.tenant, f"{obj.tenant.code}:{obj.tenant.name}")

    @admin.display(description="媒体类型", ordering="media_type")
    def media_type_label(self, obj):
        return obj.get_media_type_display()

    @admin.display(description="关联任务", ordering="mission__id")
    def mission_link(self, obj):
        if obj.mission_id is None or obj.mission is None:
            return ""
        return admin_change_link(obj.mission, f"{obj.mission.id}:{obj.mission.name}")

    @admin.display(description="同步状态", ordering="dji_index__sync_status")
    def sync_status_display(self, obj):
        if not hasattr(obj, "dji_index"):
            return ""
        return obj.dji_index.get_sync_status_display()

    @admin.display(description="DJI 文件 ID", ordering="dji_index__dji_file_id")
    def dji_file_id_display(self, obj):
        if not hasattr(obj, "dji_index"):
            return ""
        return obj.dji_index.dji_file_id

    @admin.display(description="最近同步时间", ordering="dji_index__last_sync_at")
    def last_sync_at_display(self, obj):
        if not hasattr(obj, "dji_index"):
            return ""
        return obj.dji_index.last_sync_at

    @admin.display(description="同步错误")
    def sync_error_display(self, obj):
        if not hasattr(obj, "dji_index"):
            return ""
        return obj.dji_index.error_msg or ""

    @admin.display(description="拍摄位置")
    def capture_location(self, obj):
        if obj.latitude is None or obj.longitude is None:
            return ""
        return f"{obj.latitude}, {obj.longitude}"

    @admin.display(description="文件 URL")
    def file_url_link(self, obj):
        if not obj.file_url:
            return ""
        return format_html('<a href="{}" target="_blank" rel="noreferrer">打开文件</a>', obj.file_url)

    @admin.display(description="缩略图 URL")
    def thumbnail_url_link(self, obj):
        if not obj.thumbnail_url:
            return ""
        return format_html('<a href="{}" target="_blank" rel="noreferrer">打开缩略图</a>', obj.thumbnail_url)


def _superuser_only_admin_permission(self, request):
    return request.user.is_active and request.user.is_superuser


def _parse_tail(raw_value: str | None) -> int:
    try:
        tail = int(raw_value or "100")
    except (TypeError, ValueError):
        return 100
    return min(max(tail, 1), 2000)


def _parse_auto_refresh_seconds(raw_value: str | None) -> int:
    try:
        seconds = int(raw_value or "0")
    except (TypeError, ValueError):
        return 0
    return min(max(seconds, 0), 300)


def _parse_bool(raw_value: str | None, *, default: bool) -> bool:
    if raw_value is None:
        return default
    return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_log_scope(raw_value: str | None) -> str:
    scope = str(raw_value or "request").strip().lower()
    if scope in {"request", "sync", "error"}:
        return scope
    return "request"


def _log_scope_label(scope: str) -> str:
    mapping = {
        "request": "请求日志",
        "sync": "同步日志",
        "error": "错误日志",
    }
    return mapping.get(scope, "请求日志")


def _log_scope_family_roots(scope: str) -> list[str]:
    mapping = {
        "request": ["app.log"],
        "sync": ["sync.log", "sync.error.log"],
        "error": ["error.log"],
    }
    return mapping.get(scope, ["app.log"])


def _log_family_name(filename: str) -> str:
    marker = ".log"
    index = filename.find(marker)
    if index < 0:
        return filename
    return filename[: index + len(marker)]


def _log_family_sort_key(path: Path) -> tuple[int, int, str]:
    family = _log_family_name(path.name)
    suffix = path.name[len(family):]
    if not suffix:
        return (1, 0, path.name)

    normalized_suffix = suffix.lstrip(".")
    if normalized_suffix.isdigit():
        # Rotated files use .1/.2/... where larger suffixes are older.
        return (0, -int(normalized_suffix), path.name)
    return (0, 0, path.name)


def _resolve_log_paths(log_dir: Path, *, family_roots: list[str]) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for family_root in family_roots:
        selected_path = log_dir / family_root
        family = _log_family_name(family_root)
        family_paths = [path for path in log_dir.glob(f"{family}*") if path.is_file()]
        if selected_path.is_file() and selected_path not in family_paths:
            family_paths.append(selected_path)
        for path in sorted(family_paths, key=_log_family_sort_key):
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)
    return paths


def _load_log_entries(paths: list[Path], *, tail: int | None) -> list[tuple[str, int, str]]:
    entries: list[tuple[str, int, str]] = []
    for path in paths:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = [line.rstrip("\n") for line in fh.readlines()]
        entries.extend((path.name, line_no, line) for line_no, line in enumerate(lines, start=1))
    if tail is None:
        return entries
    return entries[-tail:]


def _apply_keyword_filter(entries: list[tuple[str, int, str]], *, keyword: str) -> list[tuple[str, int, str]]:
    if not keyword:
        return entries
    normalized = keyword.lower()
    return [entry for entry in entries if normalized in entry[2].lower()]


def _safe_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _log_stage_name(event: str) -> str:
    mapping = {
        "request_started": "入口请求",
        "request_finished": "出口响应",
        "request_exception": "请求异常",
        "upstream_request": "上游请求",
        "upstream_response": "上游响应",
        "upstream_error": "上游异常",
    }
    return mapping.get(event, event or "未知环节")


def _extract_chain_id(payload: dict, request_payload: dict, response_payload: dict, *, scope: str) -> str:
    context_payload = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    response_body = response_payload.get("body") if isinstance(response_payload.get("body"), dict) else {}
    candidates = []
    if scope == "sync":
        candidates.extend(
            [
                payload.get("sync_run_id"),
                payload.get("syncRunId"),
                context_payload.get("sync_run_id"),
                context_payload.get("syncRunId"),
                request_payload.get("sync_run_id"),
                request_payload.get("syncRunId"),
            ]
        )
    candidates.extend(
        [
            payload.get("trace_id"),
            payload.get("request_id"),
            payload.get("traceId"),
            payload.get("requestId"),
            context_payload.get("trace_id"),
            context_payload.get("request_id"),
            context_payload.get("traceId"),
            context_payload.get("requestId"),
            response_body.get("traceId"),
            request_payload.get("trace_id"),
            request_payload.get("request_id"),
        ]
    )
    for value in candidates:
        text = _safe_text(value).strip()
        if text:
            return text
    return ""


def _extract_header_value(headers_payload: dict, header_name: str) -> str:
    normalized_name = header_name.strip().lower()
    for key, value in headers_payload.items():
        if _safe_text(key).strip().lower() == normalized_name:
            return _safe_text(value).strip()
    return ""


def _extract_tenant_code(payload: dict, request_payload: dict) -> str:
    context_payload = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    request_headers = request_payload.get("headers") if isinstance(request_payload.get("headers"), dict) else {}
    candidates = [
        context_payload.get("tenant_code"),
        context_payload.get("tenantCode"),
        payload.get("tenant_code"),
        payload.get("tenantCode"),
        _extract_header_value(request_headers, "X-Tenant-Code"),
    ]
    for value in candidates:
        text = _safe_text(value).strip()
        if text:
            return text
    return ""


def _build_row_summary(*, payload: dict, row: dict, request_payload: dict, response_payload: dict) -> str:
    event = row["event"]
    if event == "upstream_request":
        method = row["method"] or "-"
        path = row["path"] or "-"
        return f"调用上游: {method} {path}"
    if event == "upstream_response":
        status = row["status_code"] or "-"
        return f"上游返回: status={status}"
    if event == "upstream_error":
        error_payload = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        err_type = _safe_text(error_payload.get("type")) or "upstream_error"
        message = _safe_text(error_payload.get("message") or error_payload.get("msg")) or "上游调用失败"
        return f"{err_type}: {message}"
    if event in {"request_finished", "request_exception", "request_started"}:
        method = row["method"] or "-"
        path = row["path"] or "-"
        status = row["status_code"]
        if status:
            return f"{method} {path} -> {status}"
        return f"{method} {path}"
    return _safe_text(payload.get("message") or payload.get("msg") or row["event"] or row["raw"])[:200]


def _parse_log_row(*, source_file: str, line_no: int, raw_line: str, include_details: bool, scope: str) -> dict:
    row = {
        "source_file": source_file,
        "line_no": line_no,
        "raw": raw_line,
        "is_json": False,
        "timestamp": "",
        "level": "",
        "event": "",
        "request_id": "",
        "chain_id": "",
        "sync_run_id": "",
        "tenant_code": "",
        "method": "",
        "path": "",
        "status_code": "",
        "status_code_int": None,
        "logger": "",
        "duration_ms": "",
        "stage": "",
        "is_error": False,
        "summary": raw_line[:200],
        "pretty_json": "",
    }
    try:
        payload = json.loads(raw_line)
    except (TypeError, ValueError):
        return row

    row["is_json"] = True
    if include_details:
        row["pretty_json"] = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if not isinstance(payload, dict):
        row["summary"] = _safe_text(payload)[:200]
        return row

    request_payload = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    response_payload = payload.get("response") if isinstance(payload.get("response"), dict) else {}
    row["timestamp"] = _safe_text(payload.get("timestamp") or payload.get("time") or payload.get("@timestamp"))
    row["level"] = _safe_text(payload.get("level") or payload.get("levelname")).upper()
    row["event"] = _safe_text(payload.get("event"))
    row["request_id"] = _safe_text(payload.get("request_id") or payload.get("trace_id"))
    row["sync_run_id"] = _safe_text(payload.get("sync_run_id") or payload.get("syncRunId"))
    row["chain_id"] = _extract_chain_id(payload, request_payload, response_payload, scope=scope)
    row["tenant_code"] = _extract_tenant_code(payload, request_payload)
    row["method"] = _safe_text(request_payload.get("method"))
    row["path"] = _safe_text(request_payload.get("path"))
    row["status_code"] = _safe_text(response_payload.get("status_code") or payload.get("status_code"))
    row["status_code_int"] = _safe_int(row["status_code"])
    row["logger"] = _safe_text(payload.get("logger") or payload.get("name"))
    row["duration_ms"] = _safe_text(payload.get("duration_ms") or payload.get("durationMs"))
    row["stage"] = _log_stage_name(row["event"])
    row["summary"] = _build_row_summary(
        payload=payload,
        row=row,
        request_payload=request_payload,
        response_payload=response_payload,
    )[:200]
    row["is_error"] = (
        row["level"] in {"ERROR", "CRITICAL"}
        or row["event"] in {"request_exception", "upstream_error"}
        or (row["status_code_int"] is not None and row["status_code_int"] >= 500)
    )
    return row


def _build_log_rows(entries: list[tuple[str, int, str]], *, newest_first: bool, include_details: bool, scope: str) -> list[dict]:
    indexed_entries = list(entries)
    if newest_first:
        indexed_entries.reverse()
    return [
        _parse_log_row(source_file=source_file, line_no=line_no, raw_line=raw_line, include_details=include_details, scope=scope)
        for source_file, line_no, raw_line in indexed_entries
    ]


def _summarize_log_rows(rows: list[dict]) -> dict:
    level_counts: dict[str, int] = defaultdict(int)
    event_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        if row["level"]:
            level_counts[row["level"]] += 1
        if row["event"]:
            event_counts[row["event"]] += 1
    return {
        "levels": sorted(level_counts.items(), key=lambda item: (-item[1], item[0]))[:8],
        "events": sorted(event_counts.items(), key=lambda item: (-item[1], item[0]))[:8],
    }


def _build_log_chains(rows: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for row in rows:
        chain_id = row.get("chain_id")
        if not chain_id:
            continue
        chain = grouped.setdefault(
            chain_id,
            {
                "chain_id": chain_id,
                "steps": [],
                "line_min": row["line_no"],
                "line_max": row["line_no"],
            },
        )
        chain["steps"].append(row)
        chain["line_min"] = min(chain["line_min"], row["line_no"])
        chain["line_max"] = max(chain["line_max"], row["line_no"])

    chains: list[dict] = []
    for chain in grouped.values():
        steps = sorted(chain["steps"], key=lambda item: item["line_no"])
        entry_step = next((step for step in steps if step["path"]), steps[0] if steps else None)
        final_step = steps[-1] if steps else None
        entrypoint = ""
        if entry_step:
            method = entry_step["method"].strip()
            path = entry_step["path"].strip()
            entrypoint = f"{method} {path}".strip()
        chain["steps"] = steps
        chain["step_count"] = len(steps)
        chain["error_count"] = sum(1 for step in steps if step["is_error"])
        chain["has_error"] = chain["error_count"] > 0
        chain["entrypoint"] = entrypoint
        chain["tenant_code"] = next((step["tenant_code"] for step in steps if step["tenant_code"]), "")
        chain["started_at"] = steps[0]["timestamp"] if steps else ""
        chain["ended_at"] = final_step["timestamp"] if final_step else ""
        chain["final_stage"] = final_step["stage"] if final_step else ""
        chain["final_status_code"] = final_step["status_code"] if final_step else ""
        chain["duration_ms"] = final_step["duration_ms"] if final_step else ""
        chains.append(chain)

    chains.sort(key=lambda item: item["line_max"], reverse=True)
    return chains


def _matches_tenant_code(row: dict, tenant_code: str) -> bool:
    normalized = tenant_code.strip().lower()
    if not normalized:
        return True
    return row.get("tenant_code", "").strip().lower() == normalized


def _apply_tenant_code_filter(rows: list[dict], *, tenant_code: str) -> list[dict]:
    if not tenant_code:
        return rows

    matched_chain_ids = {
        row["chain_id"]
        for row in rows
        if row.get("chain_id") and _matches_tenant_code(row, tenant_code)
    }
    filtered_rows: list[dict] = []
    for row in rows:
        chain_id = row.get("chain_id")
        if chain_id and chain_id in matched_chain_ids:
            filtered_rows.append(row)
            continue
        if _matches_tenant_code(row, tenant_code):
            filtered_rows.append(row)
    return filtered_rows


def system_logs_view(request):
    log_dir = Path(settings.DJANGO_LOG_DIR)
    scope = _normalize_log_scope(request.GET.get("scope"))
    scope_label = _log_scope_label(scope)
    tail = _parse_tail(request.GET.get("tail"))
    keyword = request.GET.get("q", "").strip()
    chain_id = request.GET.get("chain_id", "").strip()
    tenant_code = request.GET.get("tenant_code", "").strip()
    show_details = _parse_bool(request.GET.get("show_details"), default=False)
    newest_first = _parse_bool(request.GET.get("newest_first"), default=True)
    auto_refresh_seconds = _parse_auto_refresh_seconds(request.GET.get("refresh"))
    entries: list[tuple[str, int, str]] = []
    rows: list[dict] = []
    chains: list[dict] = []
    search_paths: list[Path] = []
    error_message = None

    family_roots = _log_scope_family_roots(scope)
    effective_tail = None if (keyword or chain_id or tenant_code) else tail
    search_paths = _resolve_log_paths(
        log_dir,
        family_roots=family_roots,
    )
    if search_paths:
        entries = _load_log_entries(search_paths, tail=effective_tail)
        entries = _apply_keyword_filter(entries, keyword=keyword)
        rows = _build_log_rows(entries, newest_first=newest_first, include_details=show_details, scope=scope)
        if chain_id:
            rows = [row for row in rows if row.get("chain_id") == chain_id]
        rows = _apply_tenant_code_filter(rows, tenant_code=tenant_code)
        chains = _build_log_chains(rows)
    else:
        error_message = f"日志文件不存在: {scope_label}"

    summary = _summarize_log_rows(rows)
    context = {
        **admin.site.each_context(request),
        "title": "系统日志",
        "log_dir": str(log_dir),
        "log_scope": scope_label,
        "scope": scope,
        "tail": tail,
        "query_text": keyword,
        "chain_id": chain_id,
        "tenant_code": tenant_code,
        "show_details": show_details,
        "newest_first": newest_first,
        "auto_refresh_seconds": auto_refresh_seconds,
        "rows": rows,
        "chains": chains,
        "lines": [row["raw"] for row in rows],
        "total_rows": len(rows),
        "json_rows": sum(1 for row in rows if row["is_json"]),
        "chain_count": len(chains),
        "level_summary": summary["levels"],
        "event_summary": summary["events"],
        "error_message": error_message,
    }
    return TemplateResponse(request, "admin/system_logs.html", context)


admin.AdminSite.has_permission = _superuser_only_admin_permission
_default_admin_get_urls = admin.site.get_urls
_default_admin_get_app_list = admin.AdminSite.get_app_list


def _admin_get_urls_with_system_logs():
    custom_urls = [
        path("system/logs/", admin.site.admin_view(system_logs_view), name="system_logs"),
    ]
    return custom_urls + _default_admin_get_urls()


admin.site.get_urls = _admin_get_urls_with_system_logs


def _admin_get_app_list_with_system_logs(self, request, app_label=None):
    app_list = _default_admin_get_app_list(self, request, app_label=app_label)
    if app_label not in (None, "system"):
        return app_list

    logs_url = reverse("admin:system_logs")
    system_entry = {
        "name": "系统工具",
        "app_label": "system",
        "app_url": logs_url,
        "has_module_perms": True,
        "models": [
            {
                "name": "系统日志",
                "object_name": "SystemLog",
                "admin_url": logs_url,
                "view_only": True,
                "perms": {
                    "add": False,
                    "change": False,
                    "delete": False,
                    "view": True,
                },
            }
        ],
    }
    if app_label == "system":
        return [system_entry]
    return [*app_list, system_entry]


admin.AdminSite.get_app_list = _admin_get_app_list_with_system_logs
