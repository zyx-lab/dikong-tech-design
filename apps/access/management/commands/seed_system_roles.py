from django.core.management.base import BaseCommand
from django.db import transaction

from apps.access.models import SystemRole, SystemRoleStatus


# 根据多租户改造文档定义的 7 个固定角色
SYSTEM_ROLE_DEFINITIONS = [
    {
        "code": "platform_admin",
        "name": "平台管理员",
        "description": "平台运营，管理租户。管理租户账号（开通、停用、配置套餐）、管理平台级元数据（角色模板、权限目录）、查看平台级审计日志。",
    },
    {
        "code": "tenant_admin",
        "name": "租户管理员",
        "description": "租户最高权限。管理本租户成员（添加、移除、禁用）、给本租户成员分配固定角色、查看租户审计、管理本租户所有业务数据。",
    },
    {
        "code": "business_admin",
        "name": "业务管理员",
        "description": "租户业务管理员。管理本租户全部业务数据（无人机、航线、任务等），但无权管理成员和角色分配。",
    },
    {
        "code": "route_planner",
        "name": "航线规划员",
        "description": "航线与航点规划。拥有航线的查看和管理的权限。",
    },
    {
        "code": "dispatcher",
        "name": "任务调度员",
        "description": "任务调度。负责无人机分配、任务管理和飞行记录查看。",
    },
    {
        "code": "pilot_operator",
        "name": "飞手操作员",
        "description": "飞行执行。负责无人机查看、任务执行、飞行记录和媒体文件管理。",
    },
    {
        "code": "auditor",
        "name": "审计员",
        "description": "审计查看。负责审计查询与合规核查，可查看业务审计记录和业务数据的查看权限。",
    },
]


class Command(BaseCommand):
    help = "初始化平台固定角色 (SystemRole)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            choices=["replace", "merge"],
            default="replace",
            help="replace: 覆盖配置; merge: 只新增/更新声明项",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="仅校验并输出，不落库",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        mode = options["mode"]
        dry_run = options["dry_run"]

        if mode == "replace":
            # 先禁用所有现有角色
            SystemRole.objects.all().update(status=SystemRoleStatus.DISABLED)
            self.stdout.write(self.style.WARNING("All existing SystemRoles disabled"))

        created_count = 0
        updated_count = 0

        for role_def in SYSTEM_ROLE_DEFINITIONS:
            role, was_created = SystemRole.objects.update_or_create(
                code=role_def["code"],
                defaults={
                    "name": role_def["name"],
                    "description": role_def["description"],
                    "status": SystemRoleStatus.ACTIVE,
                },
            )

            if was_created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"created role: {role.code} (id={role.id})")
                )
            else:
                updated_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"updated role: {role.code} (id={role.id})")
                )

        if dry_run:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("dry-run enabled, changes rolled back"))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"SystemRole initialization complete: {created_count} created, {updated_count} updated"
                )
            )
