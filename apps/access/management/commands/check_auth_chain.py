from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from apps.access.models import (
    EmploymentStatus,
    GroupPermissionScope,
    ScopeStatus,
    StaffProfile,
    StaffType,
    StaffTypeGroup,
    StaffTypeStatus,
    User,
    UserStatus,
)


class Command(BaseCommand):
    help = "校验授权链完整性，检查用户权限配置是否存在断裂"

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help="尝试自动修复可修复的问题",
        )

    def handle(self, *args, **options):
        self.fix_mode = options.get("fix", False)
        self.issues = []
        self.warnings = []
        self.stats = {"users_checked": 0, "issues_found": 0, "warnings_found": 0}

        self.stdout.write("=" * 60)
        self.stdout.write("开始授权链完整性校验...")
        self.stdout.write("=" * 60)

        # 1. 检查 User -> StaffProfile 链路
        self.check_user_staff_link()

        # 2. 检查 StaffProfile -> StaffType 链路
        self.check_staff_stafftype_link()

        # 3. 检查 StaffType -> StaffTypeGroup 链路
        self.check_stafftype_groups_link()

        # 4. 检查 StaffTypeGroup -> Group 有效性
        self.check_stafftype_group_validity()

        # 5. 检查 Group -> GroupPermissionScope 链路
        self.check_group_permissions_link()

        # 6. 检查禁用状态的配置
        self.check_disabled_configurations()

        # 输出结果
        self.print_results()

    def check_user_staff_link(self):
        """检查用户是否有对应的 StaffProfile"""
        self.stdout.write("\n[1/6] 检查 User -> StaffProfile 链路...")

        # 查找没有 StaffProfile 的非 superuser 用户
        users_without_staff = User.objects.filter(
            is_superuser=False,
            is_staff=False,
        ).exclude(
            staff_profile__isnull=False
        )

        count = users_without_staff.count()
        if count > 0:
            self.stats["issues_found"] += count
            self.issues.append({
                "type": "用户无员工档案",
                "count": count,
                "details": list(users_without_staff.values_list("username", flat=True)[:10]),
            })
            self.stdout.write(self.style.ERROR(f"  发现 {count} 个用户无员工档案"))

        # 检查 is_staff=True 但无 StaffProfile 的用户
        staff_users_without_profile = User.objects.filter(
            is_staff=True,
            is_superuser=False,
        ).exclude(
            staff_profile__isnull=False
        )

        count = staff_users_without_profile.count()
        if count > 0:
            self.stats["warnings_found"] += count
            self.warnings.append({
                "type": "管理员用户无员工档案",
                "count": count,
                "details": list(staff_users_without_profile.values_list("username", flat=True)[:10]),
            })
            self.stdout.write(self.style.WARNING(f"  发现 {count} 个管理员用户无员工档案"))

        self.stats["users_checked"] = User.objects.filter(is_superuser=False).count()

    def check_staff_stafftype_link(self):
        """检查 StaffProfile 是否关联了 StaffType"""
        self.stdout.write("\n[2/6] 检查 StaffProfile -> StaffType 链路...")

        # 查找未关联 StaffType 的员工
        staffs_without_type = StaffProfile.objects.filter(
            staff_type__isnull=True
        )

        count = staffs_without_type.count()
        if count > 0:
            self.stats["issues_found"] += count
            self.issues.append({
                "type": "员工无身份类型",
                "count": count,
                "details": list(staffs_without_type.values_list("staff_no", "name")[:10]),
            })
            self.stdout.write(self.style.ERROR(f"  发现 {count} 个员工未关联身份类型"))

        # 查找已离职但仍关联的员工
        inactive_staffs = StaffProfile.objects.filter(
            employment_status=EmploymentStatus.INACTIVE
        )

        if inactive_staffs.exists():
            self.stats["warnings_found"] += 1
            self.warnings.append({
                "type": "存在已离职员工档案",
                "count": inactive_staffs.count(),
            })
            self.stdout.write(self.style.WARNING(f"  发现 {inactive_staffs.count()} 个已离职员工档案"))

    def check_stafftype_groups_link(self):
        """检查 StaffType 是否关联了 Group"""
        self.stdout.write("\n[3/6] 检查 StaffType -> StaffTypeGroup 链路...")

        # 查找没有关联任何 Group 的 StaffType
        staff_types_without_groups = StaffType.objects.annotate(
            group_count=Count("group_links")
        ).filter(
            group_count=0,
            status=StaffTypeStatus.ACTIVE,
        )

        count = staff_types_without_groups.count()
        if count > 0:
            self.stats["issues_found"] += count
            self.issues.append({
                "type": "身份类型无权限组",
                "count": count,
                "details": list(staff_types_without_groups.values_list("code", "name")[:10]),
            })
            self.stdout.write(self.style.ERROR(f"  发现 {count} 个活跃身份类型未关联权限组"))

    def check_stafftype_group_validity(self):
        """检查 StaffTypeGroup 关联的 Group 是否存在"""
        self.stdout.write("\n[4/6] 检查 StaffTypeGroup -> Group 有效性...")

        # 查找关联了不存在的 Group 的 StaffTypeGroup
        invalid_links = StaffTypeGroup.objects.filter(
            group__isnull=True
        )

        count = invalid_links.count()
        if count > 0:
            self.stats["issues_found"] += count
            self.issues.append({
                "type": "权限组链接无效",
                "count": count,
            })
            self.stdout.write(self.style.ERROR(f"  发现 {count} 个无效的权限组链接"))

    def check_group_permissions_link(self):
        """检查 Group 是否关联了权限"""
        self.stdout.write("\n[5/6] 检查 Group -> GroupPermissionScope 链路...")

        # 查找没有关联任何权限的 Group
        from django.contrib.auth.models import Group

        groups_without_permissions = Group.objects.annotate(
            permission_count=Count("group_permission_scopes")
        ).filter(
            permission_count=0,
        )

        count = groups_without_permissions.count()
        if count > 0:
            self.stats["warnings_found"] += count
            self.warnings.append({
                "type": "权限组无权限",
                "count": count,
                "details": list(groups_without_permissions.values_list("name", flat=True)[:10]),
            })
            self.stdout.write(self.style.WARNING(f"  发现 {count} 个权限组未关联任何权限"))

    def check_disabled_configurations(self):
        """检查禁用状态的配置"""
        self.stdout.write("\n[6/6] 检查禁用状态的配置...")

        # 查找已禁用的 StaffType 仍有员工关联
        disabled_staff_types = StaffType.objects.filter(
            status=StaffTypeStatus.DISABLED
        )

        for st in disabled_staff_types:
            active_staffs = st.staff_profiles.filter(
                employment_status=EmploymentStatus.ACTIVE
            )
            if active_staffs.exists():
                self.stats["issues_found"] += 1
                self.issues.append({
                    "type": "禁用身份类型仍有活跃员工",
                    "staff_type": f"{st.code}:{st.name}",
                    "count": active_staffs.count(),
                })
                self.stdout.write(self.style.ERROR(
                    f"  禁用身份类型 '{st.code}' 仍有 {active_staffs.count()} 个活跃员工"
                ))

    def print_results(self):
        """打印校验结果"""
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("校验结果汇总")
        self.stdout.write("=" * 60)

        self.stdout.write(f"\n检查用户总数: {self.stats['users_checked']}")

        if self.stats["issues_found"] == 0 and self.stats["warnings_found"] == 0:
            self.stdout.write(self.style.SUCCESS("\n✓ 授权链完整，无问题发现"))
            return

        if self.stats["issues_found"] > 0:
            self.stdout.write(self.style.ERROR(f"\n发现问题: {self.stats['issues_found']} 项"))
            for i, issue in enumerate(self.issues, 1):
                self.stdout.write(f"\n  [{i}] {issue['type']} (影响 {issue['count']} 条)")
                if issue.get("details"):
                    for detail in issue["details"]:
                        self.stdout.write(f"      - {detail}")
                if issue.get("staff_type"):
                    self.stdout.write(f"      - {issue['staff_type']}: {issue['count']} 人")

        if self.stats["warnings_found"] > 0:
            self.stdout.write(self.style.WARNING(f"\n警告: {self.stats['warnings_found']} 项"))
            for i, warning in enumerate(self.warnings, 1):
                self.stdout.write(f"\n  [{i}] {warning['type']} (影响 {warning['count']} 条)")
                if warning.get("details"):
                    for detail in warning["details"]:
                        self.stdout.write(f"      - {detail}")

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("建议操作:")
        self.stdout.write("=" * 60)
        self.stdout.write("  1. 为无员工档案的用户创建 StaffProfile")
        self.stdout.write("  2. 为员工关联正确的身份类型 (StaffType)")
        self.stdout.write("  3. 为身份类型关联权限组 (StaffTypeGroup)")
        self.stdout.write("  4. 为权限组关联具体的权限码 (GroupPermissionScope)")
        self.stdout.write("  5. 清理已禁用身份类型下的活跃员工")
