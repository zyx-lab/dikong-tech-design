from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.iam_v2.bootstrap import (
    reset_v2_system,
    sync_default_menus,
    sync_default_roles,
    sync_registered_permissions,
)


class Command(BaseCommand):
    help = "初始化 v2 根部门、权限点、角色、菜单和首个超管账号"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="清空 v2 IAM 初始化数据后重建")
        parser.add_argument("--username", default="", help="首个 v2 超管用户名；--reset 时必填")
        parser.add_argument("--password", default="", help="首个 v2 超管密码；--reset 时必填")
        parser.add_argument("--noinput", action="store_true", help="非交互执行，缺少必要参数时直接失败")

    @transaction.atomic
    def handle(self, *args, **options):
        username = str(options["username"] or "").strip()
        password = str(options["password"] or "")
        if options["reset"]:
            if not username or not password:
                raise CommandError("--reset requires --username and --password; no fixed seed account is built in")
            result = reset_v2_system(username=username, password=password)
            self.stdout.write(
                self.style.SUCCESS(
                    "v2 system reset complete: "
                    f"root_department_id={result['root_department_id']} user_id={result['user_id']}"
                )
            )
            return

        permission_result = sync_registered_permissions(disable_stale=True)
        role_result = sync_default_roles(replace_grants=True)
        menu_result = sync_default_menus(replace_bindings=True)
        self.stdout.write(
            self.style.SUCCESS(
                "v2 system catalog synced: "
                f"permissions={permission_result} roles={role_result} menus={menu_result}"
            )
        )
