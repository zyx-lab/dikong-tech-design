from dataclasses import dataclass

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.access.models import GroupPermissionScope, ScopeStatus, ScopeType, StaffType, StaffTypeGroup, StaffTypeStatus


@dataclass(frozen=True)
class StaffTypeDefinition:
    code: str
    name: str
    description: str


@dataclass(frozen=True)
class CapabilityGroupDefinition:
    display_name: str


STAFF_TYPE_DEFINITIONS = {
    "ops_admin": StaffTypeDefinition("ops_admin", "Admin管理员", "负责授权配置、账号治理与审计查看"),
    "business_admin": StaffTypeDefinition("business_admin", "业务管理员", "负责业务平面全量权限操作"),
    "route_planner": StaffTypeDefinition("route_planner", "航线规划员", "负责航线与航点规划"),
    "dispatcher": StaffTypeDefinition("dispatcher", "任务调度员", "负责任务编排、调度与下发"),
    "pilot_operator": StaffTypeDefinition("pilot_operator", "飞手操作员", "负责任务执行与飞行记录"),
    "auditor": StaffTypeDefinition("auditor", "审计员", "负责审计查询与合规核查"),
}

CAPABILITY_GROUP_DEFINITIONS = {
    "cap_auth_admin": CapabilityGroupDefinition("权限策略管理组"),
    "cap_user_admin": CapabilityGroupDefinition("账号与人员查看组"),
    "cap_staff_self": CapabilityGroupDefinition("员工自助访问组"),
    "cap_audit_reader": CapabilityGroupDefinition("审计日志只读组"),
    "cap_drone_admin": CapabilityGroupDefinition("无人机管理组"),
    "cap_business_admin": CapabilityGroupDefinition("业务管理员权限组"),
    "cap_drone_viewer": CapabilityGroupDefinition("无人机全量查看组"),
    "cap_drone_dispatch": CapabilityGroupDefinition("无人机分配管理组"),
    "cap_drone_assigned_viewer": CapabilityGroupDefinition("无人机按分配查看组"),
}

GROUP_MATRIX = {
    # 这里定义“能力组 -> 权限码 + scope”矩阵，是权限系统的初始化基线。
    "cap_auth_admin": {
        "access.manage_auth_groups": ScopeType.ALL,
        "access.manage_auth_scopes": ScopeType.ALL,
        "access.manage_staff_type_groups": ScopeType.ALL,
        "access.manage_user_accounts": ScopeType.ALL,
    },
    "cap_user_admin": {
        "access.view_user": ScopeType.ALL,
        "access.view_staffprofile": ScopeType.ALL,
    },
    "cap_staff_self": {
        "access.view_staffprofile": ScopeType.OWN,
    },
    "cap_audit_reader": {
        "access.view_auth_audit_logs": ScopeType.ALL,
    },
    "cap_drone_admin": {
        "drone.view_drone": ScopeType.ALL,
        "drone.manage_drone": ScopeType.ALL,
        "drone.change_drone_status": ScopeType.ALL,
        "drone_assignment.manage_drone_assignment": ScopeType.ALL,
        "route.view_route": ScopeType.ALL,
        "route.manage_route": ScopeType.ALL,
        "mission.view_mission": ScopeType.ALL,
        "mission.manage_mission": ScopeType.ALL,
        "flight_record.view_flight_record": ScopeType.ALL,
        "flight_record.manage_flight_record": ScopeType.ALL,
        "media_file.view_media_file": ScopeType.ALL,
        "media_file.manage_media_file": ScopeType.ALL,
    },
    "cap_business_admin": {
        "drone.view_drone": ScopeType.ALL,
        "drone.manage_drone": ScopeType.ALL,
        "drone.change_drone_status": ScopeType.ALL,
        "drone_assignment.manage_drone_assignment": ScopeType.ALL,
        "route.view_route": ScopeType.ALL,
        "route.manage_route": ScopeType.ALL,
        "mission.view_mission": ScopeType.ALL,
        "mission.manage_mission": ScopeType.ALL,
        "flight_record.view_flight_record": ScopeType.ALL,
        "flight_record.manage_flight_record": ScopeType.ALL,
        "media_file.view_media_file": ScopeType.ALL,
        "media_file.manage_media_file": ScopeType.ALL,
    },
    "cap_drone_viewer": {
        "drone.view_drone": ScopeType.ALL,
    },
    "cap_drone_dispatch": {
        "drone_assignment.manage_drone_assignment": ScopeType.ALL,
        "mission.manage_mission": ScopeType.ALL,
        "flight_record.view_flight_record": ScopeType.ALL,
        "flight_record.manage_flight_record": ScopeType.ALL,
        "media_file.view_media_file": ScopeType.ALL,
    },
    "cap_drone_assigned_viewer": {
        "drone.view_drone": ScopeType.ASSIGNED,
    },
}

STAFF_TYPE_GROUPS = {
    "ops_admin": ["cap_auth_admin", "cap_user_admin", "cap_audit_reader", "cap_drone_admin"],
    "business_admin": ["cap_business_admin"],
    "route_planner": ["cap_staff_self"],
    "dispatcher": ["cap_user_admin", "cap_drone_viewer", "cap_drone_dispatch"],
    "pilot_operator": ["cap_staff_self", "cap_drone_assigned_viewer"],
    "auditor": ["cap_user_admin", "cap_audit_reader"],
}


def _parse_permission_code(permission_code: str) -> tuple[str, str]:
    if "." not in permission_code:
        raise CommandError(f"invalid permission code: {permission_code}")
    app_label, codename = permission_code.split(".", 1)
    if not app_label or not codename:
        raise CommandError(f"invalid permission code: {permission_code}")
    return app_label, codename


class Command(BaseCommand):
    help = "初始化 staff_type -> group -> permission(scope) 权限矩阵"

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

    def _get_or_create_staff_type(self, definition: StaffTypeDefinition) -> StaffType:
        staff_type, _ = StaffType.objects.get_or_create(
            code=definition.code,
            defaults={
                "name": definition.name,
                "description": definition.description,
                "status": StaffTypeStatus.ACTIVE,
            },
        )

        changed = False
        if staff_type.name != definition.name:
            staff_type.name = definition.name
            changed = True
        if staff_type.description != definition.description:
            staff_type.description = definition.description
            changed = True
        if staff_type.status != StaffTypeStatus.ACTIVE:
            staff_type.status = StaffTypeStatus.ACTIVE
            changed = True

        if changed:
            staff_type.save(update_fields=["name", "description", "status", "updated_at"])

        return staff_type

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

        for staff_type_code, definition in STAFF_TYPE_DEFINITIONS.items():
            staff_type = self._get_or_create_staff_type(definition)
            group_codes = STAFF_TYPE_GROUPS[staff_type_code]
            desired_group_ids = [group_map[group_code].id for group_code in group_codes]

            if mode == "replace":
                StaffTypeGroup.objects.filter(staff_type=staff_type).exclude(group_id__in=desired_group_ids).update(
                    status=ScopeStatus.DISABLED
                )

            for group_code in group_codes:
                StaffTypeGroup.objects.update_or_create(
                    staff_type=staff_type,
                    group=group_map[group_code],
                    defaults={"status": ScopeStatus.ACTIVE},
                )

            self.stdout.write(
                self.style.SUCCESS(
                    f"staff_type_code={staff_type_code} staff_type_name={staff_type.name} groups={len(group_codes)} mode={mode} applied staff_type_id={staff_type.id}"
                )
            )

        if dry_run:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("dry-run enabled, changes rolled back"))
        else:
            self.stdout.write(self.style.SUCCESS("staff_type-group-permission matrix initialized"))
