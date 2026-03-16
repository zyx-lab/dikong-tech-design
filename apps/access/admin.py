import json
from collections import defaultdict

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.db.models import Count, Prefetch, Q
from django.urls import reverse
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
