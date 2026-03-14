from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.utils import timezone

from apps.access.models import (
    DirectoryStatus,
    EmploymentStatus,
    QualificationRecordStatus,
    Role,
    RolePermissionGrant,
    TenantMember,
    TenantMemberRoleStatus,
    TenantMemberStatus,
    User,
)


class Command(BaseCommand):
    help = "校验当前多租户授权链与成员资质配置是否存在断裂"

    def handle(self, *args, **options):
        self.issues = []
        self.warnings = []
        self.stats = {"users_checked": 0, "issues_found": 0, "warnings_found": 0}

        self.stdout.write("=" * 60)
        self.stdout.write("开始多租户授权链完整性校验...")
        self.stdout.write("=" * 60)

        self.check_user_staff_link()
        self.check_active_member_role_link()
        self.check_role_permission_grants()
        self.check_member_invitations()
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
            granted_role_count=Count(
                "role_bindings",
                filter=Q(role_bindings__status=TenantMemberRoleStatus.GRANTED),
            )
        ).filter(
            status=TenantMemberStatus.ACTIVE,
            granted_role_count=0,
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
        self.stdout.write(self.style.WARNING(f"  发现 {count} 个活跃租户成员未绑定任何角色"))

    def check_role_permission_grants(self):
        self.stdout.write("\n[3/5] 检查 Role -> RolePermissionGrant 链路...")
        roles_without_grants = Role.objects.annotate(
            grant_count=Count("permission_grants")
        ).filter(
            status=DirectoryStatus.ACTIVE,
            grant_count=0,
        )
        count = roles_without_grants.count()
        if count <= 0:
            return
        self.stats["issues_found"] += count
        self.issues.append(
            {
                "type": "活跃角色无权限映射",
                "count": count,
                "details": list(roles_without_grants.values_list("code", "name")[:10]),
            }
        )
        self.stdout.write(self.style.ERROR(f"  发现 {count} 个活跃角色未配置任何 RolePermissionGrant"))

        duplicated = (
            RolePermissionGrant.objects.values("role_id", "permission_id")
            .annotate(total=Count("id"))
            .filter(total__gt=1)
        )
        duplicate_count = duplicated.count()
        if duplicate_count <= 0:
            return
        self.stats["issues_found"] += duplicate_count
        self.issues.append(
            {
                "type": "角色权限映射重复",
                "count": duplicate_count,
                "details": list(duplicated.values_list("role_id", "permission_id", "total")[:10]),
            }
        )
        self.stdout.write(self.style.ERROR(f"  发现 {duplicate_count} 组重复的 RolePermissionGrant"))

    def check_member_invitations(self):
        self.stdout.write("\n[4/5] 检查成员邀请状态...")
        invalid_invited_members = TenantMember.objects.filter(
            status=TenantMemberStatus.INVITED,
        ).filter(
            Q(invitation_token__isnull=True)
            | Q(invitation_token="")
            | Q(invited_at__isnull=True)
            | Q(expires_at__isnull=True)
        )
        count = invalid_invited_members.count()
        if count > 0:
            self.stats["issues_found"] += count
            self.issues.append(
                {
                    "type": "邀请态成员缺少关键字段",
                    "count": count,
                    "details": list(invalid_invited_members.values_list("tenant__code", "user__username")[:10]),
                }
            )
            self.stdout.write(self.style.ERROR(f"  发现 {count} 个 INVITED 成员缺少邀请关键字段"))

        expired_but_not_flipped = TenantMember.objects.filter(
            status=TenantMemberStatus.INVITED,
            expires_at__lt=timezone.now(),
        )
        expired_count = expired_but_not_flipped.count()
        if expired_count > 0:
            self.stats["warnings_found"] += expired_count
            self.warnings.append(
                {
                    "type": "邀请已过期但状态未切换",
                    "count": expired_count,
                    "details": list(expired_but_not_flipped.values_list("tenant__code", "user__username")[:10]),
                }
            )
            self.stdout.write(self.style.WARNING(f"  发现 {expired_count} 个已过期但仍是 INVITED 的成员"))

    def check_member_qualifications(self):
        self.stdout.write("\n[5/5] 检查成员资质状态...")
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

        invalid_qualifications = TenantMember.objects.filter(
            qualifications__status=QualificationRecordStatus.INVALID,
        ).distinct()
        if invalid_qualifications.exists():
            count = invalid_qualifications.count()
            self.stats["warnings_found"] += count
            self.warnings.append(
                {
                    "type": "成员存在 INVALID 资质记录",
                    "count": count,
                    "details": list(invalid_qualifications.values_list("tenant__code", "user__username")[:10]),
                }
            )
            self.stdout.write(self.style.WARNING(f"  发现 {count} 个成员存在 INVALID 资质记录"))

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
        self.stdout.write("  2. 为活跃 TenantMember 分配至少一个 Role")
        self.stdout.write("  3. 为活跃 Role 配置 RolePermissionGrant")
        self.stdout.write("  4. 修复 INVALID 资质记录和过期邀请")
