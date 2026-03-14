from django.core.management.base import BaseCommand
from django.db import transaction

from apps.access.models import DirectoryStatus, Role


ROLE_DEFINITIONS = [
    {
        "code": "platform_admin",
        "name": "平台管理员",
        "description": "平台运营，管理租户、平台目录与平台级审计。",
    },
    {
        "code": "tenant_admin",
        "name": "租户管理员",
        "description": "租户最高权限。管理本租户成员、角色分配、租户审计及全部业务数据。",
    },
    {
        "code": "business_admin",
        "name": "业务管理员",
        "description": "管理本租户全部业务数据，但不管理成员与角色分配。",
    },
    {
        "code": "route_planner",
        "name": "航线规划员",
        "description": "负责航线与航点规划。",
    },
    {
        "code": "dispatcher",
        "name": "任务调度员",
        "description": "负责任务调度与无人机分配。",
    },
    {
        "code": "pilot_operator",
        "name": "飞手操作员",
        "description": "负责飞行执行、飞行记录与媒体文件操作。",
    },
    {
        "code": "auditor",
        "name": "审计员",
        "description": "负责租户级审计和业务数据查看。",
    },
]


class Command(BaseCommand):
    help = "初始化平台角色目录 (Role)"

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
            Role.objects.all().update(status=DirectoryStatus.DISABLED)
            self.stdout.write(self.style.WARNING("All existing roles disabled"))

        created_count = 0
        updated_count = 0

        for role_def in ROLE_DEFINITIONS:
            role, was_created = Role.objects.update_or_create(
                code=role_def["code"],
                defaults={
                    "name": role_def["name"],
                    "description": role_def["description"],
                    "status": DirectoryStatus.ACTIVE,
                },
            )
            if was_created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"created role: {role.code} (id={role.id})"))
            else:
                updated_count += 1
                self.stdout.write(self.style.SUCCESS(f"updated role: {role.code} (id={role.id})"))

        if dry_run:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("dry-run enabled, changes rolled back"))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Role initialization complete: {created_count} created, {updated_count} updated"
                )
            )
