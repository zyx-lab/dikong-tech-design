from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.utils import timezone

from apps.access.models import (
    EmploymentStatus,
    GroupPermissionScope,
    ScopeStatus,
    SystemRole,
    SystemRoleGroup,
    SystemRoleStatus,
    TenantMember,
    TenantMemberAttributeStatus,
    TenantMemberRole,
    TenantMemberRoleStatus,
    TenantMemberStatus,
    User,
)


class Command(BaseCommand):
    help = "校验当前多租户授权链与租户内岗位/资质配置是否存在断裂"

    def handle(self, *args, **options):
        self.issues = []
        self.warnings = []
        self.stats = {"users_checked": 0, "issues_found": 0, "warnings_found": 0}

        self.stdout.write("=" * 60)
        self.stdout.write("开始多租户授权链完整性校验...")
        self.stdout.write("=" * 60)

        self.check_user_staff_link()
        self.check_active_member_role_link()
        self.check_system_role_group_link()
        self.check_group_permissions_link()
        self.check_member_qualifications()

        self.print_results()

    def check_user_staff_link(self):
        self.stdout.write("\n[1/5] 检查 User -> StaffProfile 链路...")

        users_without_staff = User.objects.filter(is_superuser=False).exclude(staff_profile__isnull=False)
        count = users_without_staff.count()
        self.stats["users_checked"] = User.objects.filter(is_superuser=False).count()
        if count <= 0:
            return

        self.stats["issues_found"] += count
        self.issues.append(
            {
                "type": "用户无全局人员档案",
                "count": count,
                "details": list(users_without_staff.values_list("username", flat=True)[:10]),
            }
        )
        self.stdout.write(self.style.ERROR(f"  发现 {count} 个非 superuser 用户缺少 StaffProfile"))

    def check_active_member_role_link(self):
        self.stdout.write("\n[2/5] 检查 TenantMember -> TenantMemberRole 链路...")

        active_members_without_role = TenantMember.objects.annotate(
            active_role_count=Count(
                "role_bindings",
                filter=Q(role_bindings__status=TenantMemberRoleStatus.ACTIVE),
            )
        ).filter(
            status=TenantMemberStatus.ACTIVE,
            active_role_count=0,
        )

        count = active_members_without_role.count()
        if count <= 0:
            return

        self.stats["warnings_found"] += count
        self.warnings.append(
            {
                "type": "活跃成员无角色绑定",
                "count": count,
                "details": list(active_members_without_role.values_list("tenant__code", "user__username")[:10]),
            }
        )
        self.stdout.write(self.style.WARNING(f"  发现 {count} 个活跃租户成员未绑定任何 SystemRole"))

    def check_system_role_group_link(self):
        self.stdout.write("\n[3/5] 检查 SystemRole -> SystemRoleGroup 链路...")

        roles_without_groups = SystemRole.objects.annotate(
            active_group_count=Count(
                "group_links",
                filter=Q(group_links__status=ScopeStatus.ACTIVE),
            )
        ).filter(
            status=SystemRoleStatus.ACTIVE,
            active_group_count=0,
        )

        count = roles_without_groups.count()
        if count <= 0:
            return

        self.stats["issues_found"] += count
        self.issues.append(
            {
                "type": "固定角色无能力组",
                "count": count,
                "details": list(roles_without_groups.values_list("code", "name")[:10]),
            }
        )
        self.stdout.write(self.style.ERROR(f"  发现 {count} 个活跃 SystemRole 未关联任何 Group"))

    def check_group_permissions_link(self):
        self.stdout.write("\n[4/5] 检查 Group -> GroupPermissionScope 链路...")

        active_group_ids = SystemRoleGroup.objects.filter(status=ScopeStatus.ACTIVE).values_list("group_id", flat=True)
        groups_without_scopes = Group.objects.filter(id__in=active_group_ids).annotate(
            scope_count=Count(
                "permission_scopes",
                filter=Q(permission_scopes__status=ScopeStatus.ACTIVE),
            )
        ).filter(scope_count=0)

        count = groups_without_scopes.count()
        if count <= 0:
            return

        self.stats["warnings_found"] += count
        self.warnings.append(
            {
                "type": "能力组无有效 scope 配置",
                "count": count,
                "details": list(groups_without_scopes.values_list("name", flat=True)[:10]),
            }
        )
        self.stdout.write(self.style.WARNING(f"  发现 {count} 个已被角色引用的 Group 未配置有效 GroupPermissionScope"))

    def check_member_qualifications(self):
        self.stdout.write("\n[5/5] 检查租户成员岗位/资质状态...")

        inactive_staffs = User.objects.filter(
            staff_profile__employment_status=EmploymentStatus.INACTIVE,
            tenant_members__status=TenantMemberStatus.ACTIVE,
        ).distinct()
        if inactive_staffs.exists():
            count = inactive_staffs.count()
            self.stats["warnings_found"] += count
            self.warnings.append(
                {
                    "type": "离职档案仍保留活跃租户成员",
                    "count": count,
                    "details": list(inactive_staffs.values_list("username", flat=True)[:10]),
                }
            )
            self.stdout.write(self.style.WARNING(f"  发现 {count} 个离职用户仍存在活跃租户成员"))

        expired_qualifications = TenantMember.objects.filter(
            qualifications__status=TenantMemberAttributeStatus.ACTIVE,
            qualifications__valid_until__lt=timezone.localdate(),
        ).distinct()
        if expired_qualifications.exists():
            count = expired_qualifications.count()
            self.stats["warnings_found"] += count
            self.warnings.append(
                {
                    "type": "成员存在已过期资质",
                    "count": count,
                    "details": list(expired_qualifications.values_list("tenant__code", "user__username")[:10]),
                }
            )
            self.stdout.write(self.style.WARNING(f"  发现 {count} 个成员存在已过期但仍为 ACTIVE 的资质"))

    def print_results(self):
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("校验结果汇总")
        self.stdout.write("=" * 60)
        self.stdout.write(f"\n检查用户总数: {self.stats['users_checked']}")

        if self.stats["issues_found"] == 0 and self.stats["warnings_found"] == 0:
            self.stdout.write(self.style.SUCCESS("\n✓ 多租户授权链完整，无问题发现"))
            return

        if self.stats["issues_found"] > 0:
            self.stdout.write(self.style.ERROR(f"\n发现问题: {self.stats['issues_found']} 项"))
            for index, issue in enumerate(self.issues, start=1):
                self.stdout.write(f"\n  [{index}] {issue['type']} (影响 {issue['count']} 条)")
                for detail in issue.get("details", []):
                    self.stdout.write(f"      - {detail}")

        if self.stats["warnings_found"] > 0:
            self.stdout.write(self.style.WARNING(f"\n警告: {self.stats['warnings_found']} 项"))
            for index, warning in enumerate(self.warnings, start=1):
                self.stdout.write(f"\n  [{index}] {warning['type']} (影响 {warning['count']} 条)")
                for detail in warning.get("details", []):
                    self.stdout.write(f"      - {detail}")

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("建议操作:")
        self.stdout.write("=" * 60)
        self.stdout.write("  1. 为缺少档案的账号补齐 StaffProfile")
        self.stdout.write("  2. 为活跃 TenantMember 分配至少一个 SystemRole")
        self.stdout.write("  3. 为活跃 SystemRole 绑定 Group，并为 Group 配置 scope")
        self.stdout.write("  4. 将业务岗位迁移到 TenantMemberPosition，资质迁移到 TenantMemberQualification")
