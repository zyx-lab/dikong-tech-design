from dataclasses import dataclass

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.access.management.commands.seed_system_roles import SYSTEM_ROLE_DEFINITIONS
from apps.access.models import GroupPermissionScope, ScopeStatus, ScopeType, SystemRole, SystemRoleGroup, SystemRoleStatus


@dataclass(frozen=True)
class CapabilityGroupDefinition:
    display_name: str


CAPABILITY_GROUP_DEFINITIONS = {
    "cap_tenant_governance": CapabilityGroupDefinition("租户治理权限组"),
    "cap_business_admin": CapabilityGroupDefinition("业务管理员权限组"),
    "cap_route_planner": CapabilityGroupDefinition("航线规划权限组"),
    "cap_dispatcher": CapabilityGroupDefinition("任务调度权限组"),
    "cap_pilot_operator": CapabilityGroupDefinition("飞手操作权限组"),
    "cap_auditor": CapabilityGroupDefinition("审计查看权限组"),
}

GROUP_MATRIX = {
    "cap_tenant_governance": {
        "access.view_tenant_member": ScopeType.ALL,
        "access.manage_tenant_member": ScopeType.ALL,
        "access.assign_tenant_member_role": ScopeType.ALL,
        "access.view_auth_audit_logs": ScopeType.ALL,
        "access.view_user": ScopeType.ALL,
    },
    "cap_business_admin": {
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
    "cap_route_planner": {
        "drone.view_drone": ScopeType.ALL,
        "route.view_route": ScopeType.ALL,
        "route.manage_route": ScopeType.ALL,
        "waypoint.view_waypoint": ScopeType.ALL,
        "waypoint.manage_waypoint": ScopeType.ALL,
    },
    "cap_dispatcher": {
        "drone.view_drone": ScopeType.ALL,
        "mission.view_mission": ScopeType.ALL,
        "mission.manage_mission": ScopeType.ALL,
        "drone_assignment.manage_drone_assignment": ScopeType.ALL,
    },
    "cap_pilot_operator": {
        "drone.view_drone": ScopeType.ASSIGNED,
        "mission.view_mission": ScopeType.ALL,
        "flight_record.view_flight_record": ScopeType.ALL,
        "flight_record.manage_flight_record": ScopeType.ALL,
        "media_file.view_media_file": ScopeType.ALL,
        "media_file.manage_media_file": ScopeType.ALL,
    },
    "cap_auditor": {
        "access.view_auth_audit_logs": ScopeType.ALL,
        "drone.view_drone": ScopeType.ALL,
        "route.view_route": ScopeType.ALL,
        "waypoint.view_waypoint": ScopeType.ALL,
        "mission.view_mission": ScopeType.ALL,
        "flight_record.view_flight_record": ScopeType.ALL,
        "media_file.view_media_file": ScopeType.ALL,
    },
}

SYSTEM_ROLE_GROUPS = {
    "platform_admin": [],
    "tenant_admin": ["cap_tenant_governance", "cap_business_admin"],
    "business_admin": ["cap_business_admin"],
    "route_planner": ["cap_route_planner"],
    "dispatcher": ["cap_dispatcher"],
    "pilot_operator": ["cap_pilot_operator"],
    "auditor": ["cap_auditor"],
}


def _parse_permission_code(permission_code: str) -> tuple[str, str]:
    if "." not in permission_code:
        raise CommandError(f"invalid permission code: {permission_code}")
    app_label, codename = permission_code.split(".", 1)
    if not app_label or not codename:
        raise CommandError(f"invalid permission code: {permission_code}")
    return app_label, codename


class Command(BaseCommand):
    help = "初始化 system_role -> group -> permission(scope) 权限矩阵"

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

    def _resolve_permissions(self) -> dict[str, Permission]:
        required_codes = sorted({code for matrix in GROUP_MATRIX.values() for code in matrix.keys()})

        resolved = {}
        missing_codes = []
        for code in required_codes:
            app_label, codename = _parse_permission_code(code)
            permission = Permission.objects.filter(
                content_type__app_label=app_label,
                codename=codename,
            ).first()
            if not permission:
                missing_codes.append(code)
                continue
            resolved[code] = permission

        if missing_codes:
            raise CommandError(f"missing permissions: {', '.join(missing_codes)}")

        return resolved

    def _get_or_create_group(self, definition: CapabilityGroupDefinition) -> Group:
        group, _ = Group.objects.get_or_create(name=definition.display_name)
        return group

    def _get_or_create_system_role(self, role_def: dict) -> SystemRole:
        role, _ = SystemRole.objects.update_or_create(
            code=role_def["code"],
            defaults={
                "name": role_def["name"],
                "description": role_def["description"],
                "status": SystemRoleStatus.ACTIVE,
            },
        )
        return role

    @transaction.atomic
    def handle(self, *args, **options):
        mode = options["mode"]
        dry_run = options["dry_run"]

        permission_map = self._resolve_permissions()

        group_map = {}
        for group_code, group_definition in CAPABILITY_GROUP_DEFINITIONS.items():
            group = self._get_or_create_group(group_definition)
            group_map[group_code] = group

            mapping = GROUP_MATRIX[group_code]
            permission_objects = [permission_map[permission_code] for permission_code in mapping.keys()]

            if mode == "replace":
                group.permissions.set(permission_objects)
                GroupPermissionScope.objects.filter(group=group).exclude(permission__in=permission_objects).delete()
            else:
                group.permissions.add(*permission_objects)

            for permission_code, scope_type in mapping.items():
                permission = permission_map[permission_code]
                GroupPermissionScope.objects.update_or_create(
                    group=group,
                    permission=permission,
                    defaults={
                        "scope_type": scope_type,
                        "status": ScopeStatus.ACTIVE,
                    },
                )

            self.stdout.write(
                self.style.SUCCESS(
                    f"group_key={group_code} group_name={group.name} perms={len(mapping)} mode={mode} applied group_id={group.id}"
                )
            )

        for role_def in SYSTEM_ROLE_DEFINITIONS:
            system_role = self._get_or_create_system_role(role_def)
            group_codes = SYSTEM_ROLE_GROUPS.get(system_role.code, [])
            desired_group_ids = [group_map[group_code].id for group_code in group_codes]

            if mode == "replace":
                SystemRoleGroup.objects.filter(system_role=system_role).exclude(group_id__in=desired_group_ids).update(
                    status=ScopeStatus.DISABLED
                )

            for group_code in group_codes:
                SystemRoleGroup.objects.update_or_create(
                    system_role=system_role,
                    group=group_map[group_code],
                    defaults={"status": ScopeStatus.ACTIVE},
                )

            self.stdout.write(
                self.style.SUCCESS(
                    f"system_role_code={system_role.code} system_role_name={system_role.name} groups={len(group_codes)} mode={mode} applied system_role_id={system_role.id}"
                )
            )

        if dry_run:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("dry-run enabled, changes rolled back"))
        else:
            self.stdout.write(self.style.SUCCESS("system_role-group-permission matrix initialized"))
