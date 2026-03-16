from django.core.management.base import BaseCommand
from django.db import transaction

from apps.access.management.commands.seed_system_roles import ROLE_DEFINITIONS
from apps.access.models import DirectoryStatus, Permission, Role, RolePermissionGrant, ScopeType


ROLE_PERMISSION_MATRIX = {
    "platform_admin": {
        "access.view_user": ScopeType.ALL,
        "access.manage_user_accounts": ScopeType.ALL,
        "access.view_permission_catalog": ScopeType.ALL,
        "access.view_role": ScopeType.ALL,
        "access.manage_role": ScopeType.ALL,
        "access.view_tenant": ScopeType.ALL,
        "access.manage_tenant": ScopeType.ALL,
        "access.view_auth_audit_logs": ScopeType.ALL,
    },
    "tenant_admin": {
        "access.view_user": ScopeType.ALL,
        "access.view_tenant_member": ScopeType.ALL,
        "access.manage_tenant_member": ScopeType.ALL,
        "access.assign_tenant_member_role": ScopeType.ALL,
        "access.view_auth_audit_logs": ScopeType.ALL,
        "drone.view_drone": ScopeType.ALL,
        "drone.manage_drone": ScopeType.ALL,
        "drone.change_drone_status": ScopeType.ALL,
        "drone_assignment.manage_drone_assignment": ScopeType.ALL,
        "route.view_route": ScopeType.ALL,
        "route.manage_route": ScopeType.ALL,
        "waypoint.view_waypoint": ScopeType.ALL,
        "waypoint.manage_waypoint": ScopeType.ALL,
        "mission.view_mission": ScopeType.ALL,
        "mission.manage_mission": ScopeType.ALL,
        "flight_record.view_flight_record": ScopeType.ALL,
        "flight_record.manage_flight_record": ScopeType.ALL,
        "media_file.view_media_file": ScopeType.ALL,
        "media_file.manage_media_file": ScopeType.ALL,
    },
    "business_admin": {
        "drone.view_drone": ScopeType.ALL,
        "drone.manage_drone": ScopeType.ALL,
        "drone.change_drone_status": ScopeType.ALL,
        "drone_assignment.manage_drone_assignment": ScopeType.ALL,
        "route.view_route": ScopeType.ALL,
        "route.manage_route": ScopeType.ALL,
        "waypoint.view_waypoint": ScopeType.ALL,
        "waypoint.manage_waypoint": ScopeType.ALL,
        "mission.view_mission": ScopeType.ALL,
        "mission.manage_mission": ScopeType.ALL,
        "flight_record.view_flight_record": ScopeType.ALL,
        "flight_record.manage_flight_record": ScopeType.ALL,
        "media_file.view_media_file": ScopeType.ALL,
        "media_file.manage_media_file": ScopeType.ALL,
    },
    "route_planner": {
        "drone.view_drone": ScopeType.ALL,
        "route.view_route": ScopeType.ALL,
        "route.manage_route": ScopeType.ALL,
        "waypoint.view_waypoint": ScopeType.ALL,
        "waypoint.manage_waypoint": ScopeType.ALL,
    },
    "dispatcher": {
        "drone.view_drone": ScopeType.ALL,
        "mission.view_mission": ScopeType.ALL,
        "mission.manage_mission": ScopeType.ALL,
        "drone_assignment.manage_drone_assignment": ScopeType.ALL,
    },
    "pilot_operator": {
        "drone.view_drone": ScopeType.ASSIGNED,
        "mission.view_mission": ScopeType.ASSIGNED,
        "flight_record.view_flight_record": ScopeType.ASSIGNED,
        "flight_record.manage_flight_record": ScopeType.ASSIGNED,
        "media_file.view_media_file": ScopeType.ASSIGNED,
        "media_file.manage_media_file": ScopeType.ASSIGNED,
    },
    "auditor": {
        "access.view_auth_audit_logs": ScopeType.ALL,
        "drone.view_drone": ScopeType.ALL,
        "route.view_route": ScopeType.ALL,
        "waypoint.view_waypoint": ScopeType.ALL,
        "mission.view_mission": ScopeType.ALL,
        "flight_record.view_flight_record": ScopeType.ALL,
        "media_file.view_media_file": ScopeType.ALL,
    },
}


def _permission_definition(permission_code: str, scope_type: str) -> dict:
    module = permission_code.split(".", 1)[0] if "." in permission_code else "access"
    return {
        "code": permission_code,
        "name": permission_code,
        "module": module,
        "resource_code": module if scope_type in {ScopeType.OWN, ScopeType.ASSIGNED} else "",
        "status": DirectoryStatus.ACTIVE,
    }


class Command(BaseCommand):
    help = "初始化 role -> permission(scope) 权限矩阵"

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

        for role_def in ROLE_DEFINITIONS:
            Role.objects.update_or_create(
                code=role_def["code"],
                defaults={
                    "name": role_def["name"],
                    "description": role_def["description"],
                    "status": DirectoryStatus.ACTIVE,
                },
            )

        desired_pairs: set[tuple[str, str]] = set()

        for role_code, mapping in ROLE_PERMISSION_MATRIX.items():
            role = Role.objects.get(code=role_code)
            for permission_code, scope_type in mapping.items():
                permission, _ = Permission.objects.update_or_create(
                    code=permission_code,
                    defaults=_permission_definition(permission_code, scope_type),
                )
                RolePermissionGrant.objects.update_or_create(
                    role=role,
                    permission=permission,
                    defaults={"scope_type": scope_type},
                )
                desired_pairs.add((role.code, permission.code))
                self.stdout.write(
                    self.style.SUCCESS(
                        f"role_code={role.code} permission_code={permission.code} scope={scope_type} applied"
                    )
                )

        if mode == "replace":
            stale_grants = []
            for grant in RolePermissionGrant.objects.select_related("role", "permission"):
                pair = (grant.role.code, grant.permission.code)
                if pair not in desired_pairs:
                    stale_grants.append(grant.id)
            if stale_grants:
                RolePermissionGrant.objects.filter(id__in=stale_grants).delete()
                self.stdout.write(self.style.WARNING(f"removed stale grants: {len(stale_grants)}"))

        if dry_run:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("dry-run enabled, changes rolled back"))
        else:
            self.stdout.write(self.style.SUCCESS("role-permission matrix initialized"))
