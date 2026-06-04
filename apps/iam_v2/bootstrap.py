from django.contrib.auth import get_user_model
from django.db import transaction

from apps.access.models import DirectoryStatus, UserStatus
from apps.iam_v2.models import (
    Department,
    V2AccountQualification,
    V2AccountProfile,
    V2AccountRoleProfile,
    V2AccountRoleAssignment,
    V2Menu,
    V2MenuPermissionBinding,
    V2Permission,
    V2ProfileType,
    V2Role,
    V2RoleMenuGrant,
    V2RolePermissionGrant,
)
from apps.iam_v2.permissions import MENU_SPECS, PERMISSION_CODES, PERMISSION_SPECS, ROLE_SPECS

User = get_user_model()


def sync_registered_permissions(*, disable_stale: bool = False) -> dict:
    seen_codes = set()
    created = 0
    updated = 0
    for spec in PERMISSION_SPECS:
        domain, resource, action = spec.parts
        permission, was_created = V2Permission.objects.update_or_create(
            code=spec.code,
            defaults={
                "name": spec.name,
                "domain": domain,
                "resource": resource,
                "action": action,
                "status": DirectoryStatus.ACTIVE,
                "is_system": True,
            },
        )
        del permission
        seen_codes.add(spec.code)
        if was_created:
            created += 1
        else:
            updated += 1

    disabled = 0
    if disable_stale:
        disabled = V2Permission.objects.filter(is_system=True).exclude(code__in=seen_codes).update(
            status=DirectoryStatus.DISABLED
        )
    return {"created": created, "updated": updated, "disabled": disabled}


def sync_default_roles(*, replace_grants: bool = False) -> dict:
    permissions_by_code = {permission.code: permission for permission in V2Permission.objects.filter(code__in=PERMISSION_CODES)}
    created = 0
    updated = 0
    grant_count = 0

    for spec in ROLE_SPECS:
        role, was_created = V2Role.objects.update_or_create(
            code=spec.code,
            defaults={
                "name": spec.name,
                "status": DirectoryStatus.ACTIVE,
                "is_system": spec.is_system,
                "is_super_admin": spec.is_super_admin,
                "assignable_by_department_admin": spec.assignable_by_department_admin,
                "data_scope": spec.data_scope,
                "sort": spec.sort,
                "remark": spec.remark,
            },
        )
        if was_created:
            created += 1
        else:
            updated += 1

        desired_permission_ids = [permissions_by_code[code].id for code in spec.permissions if code in permissions_by_code]
        if replace_grants:
            V2RolePermissionGrant.objects.filter(role=role, source=V2RolePermissionGrant.GrantSource.DIRECT).exclude(
                permission_id__in=desired_permission_ids
            ).delete()
        for permission_id in desired_permission_ids:
            V2RolePermissionGrant.objects.get_or_create(
                role=role,
                permission_id=permission_id,
                source=V2RolePermissionGrant.GrantSource.DIRECT,
            )
            grant_count += 1
    return {"created": created, "updated": updated, "grants": grant_count}


def sync_default_menus(*, replace_bindings: bool = False) -> dict:
    permissions_by_code = {permission.code: permission for permission in V2Permission.objects.filter(code__in=PERMISSION_CODES)}
    menus_by_code: dict[str, V2Menu] = {}
    created = 0
    updated = 0
    bindings = 0

    for spec in MENU_SPECS:
        parent = menus_by_code.get(spec.parent_code) if spec.parent_code else None
        menu, was_created = V2Menu.objects.update_or_create(
            code=spec.code,
            defaults={
                "parent": parent,
                "name": spec.name,
                "menu_type": spec.menu_type,
                "path": spec.path,
                "component": spec.component,
                "icon": spec.icon,
                "sort": spec.sort,
                "status": DirectoryStatus.ACTIVE,
                "is_system": True,
            },
        )
        menus_by_code[spec.code] = menu
        if was_created:
            created += 1
        else:
            updated += 1

        permission_ids = [permissions_by_code[code].id for code in spec.permissions if code in permissions_by_code]
        if replace_bindings:
            V2MenuPermissionBinding.objects.filter(menu=menu).exclude(permission_id__in=permission_ids).delete()
        for permission_id in permission_ids:
            V2MenuPermissionBinding.objects.get_or_create(menu=menu, permission_id=permission_id)
            bindings += 1
    return {"created": created, "updated": updated, "bindings": bindings}


def sync_default_profile_types() -> dict:
    created = 0
    updated = 0
    for spec in ROLE_SPECS:
        profile_type, was_created = V2ProfileType.objects.update_or_create(
            code=spec.code,
            defaults={
                "name": f"{spec.name}档案",
                "role_code": spec.code,
                "status": DirectoryStatus.ACTIVE,
                "is_system": True,
                "sort": spec.sort,
                "remark": spec.remark,
            },
        )
        del profile_type
        if was_created:
            created += 1
        else:
            updated += 1
    return {"created": created, "updated": updated}


def refresh_menu_permission_grants(role: V2Role) -> int:
    V2RolePermissionGrant.objects.filter(role=role, source=V2RolePermissionGrant.GrantSource.MENU).delete()
    permission_ids = (
        V2MenuPermissionBinding.objects.filter(menu__role_grants__role=role)
        .values_list("permission_id", flat=True)
        .distinct()
    )
    created = 0
    for permission_id in permission_ids:
        V2RolePermissionGrant.objects.get_or_create(
            role=role,
            permission_id=permission_id,
            source=V2RolePermissionGrant.GrantSource.MENU,
        )
        created += 1
    return created


def replace_role_menus(*, role: V2Role, menu_ids: list[int]) -> None:
    menus = list(V2Menu.objects.filter(id__in=menu_ids, status=DirectoryStatus.ACTIVE).order_by("id"))
    requested_ids = {int(menu_id) for menu_id in menu_ids}
    found_ids = {menu.id for menu in menus}
    missing = requested_ids - found_ids
    if missing:
        from rest_framework import serializers

        raise serializers.ValidationError({"menuIds": [f"菜单不存在或已停用: {', '.join(str(item) for item in sorted(missing))}"]})

    V2RoleMenuGrant.objects.filter(role=role).delete()
    for menu in menus:
        V2RoleMenuGrant.objects.create(role=role, menu=menu)
    refresh_menu_permission_grants(role)


def replace_role_direct_permissions(*, role: V2Role, permission_ids: list[int]) -> None:
    permissions = list(V2Permission.objects.filter(id__in=permission_ids, status=DirectoryStatus.ACTIVE).order_by("id"))
    requested_ids = {int(permission_id) for permission_id in permission_ids}
    found_ids = {permission.id for permission in permissions}
    missing = requested_ids - found_ids
    if missing:
        from rest_framework import serializers

        raise serializers.ValidationError({"permissionIds": [f"权限不存在或已停用: {', '.join(str(item) for item in sorted(missing))}"]})

    V2RolePermissionGrant.objects.filter(role=role, source=V2RolePermissionGrant.GrantSource.DIRECT).delete()
    for permission in permissions:
        V2RolePermissionGrant.objects.create(
            role=role,
            permission=permission,
            source=V2RolePermissionGrant.GrantSource.DIRECT,
        )


@transaction.atomic
def reset_v2_system(*, username: str, password: str) -> dict:
    v2_user_ids = list(V2AccountProfile.objects.values_list("user_id", flat=True))
    V2RoleMenuGrant.objects.all().delete()
    V2MenuPermissionBinding.objects.all().delete()
    V2RolePermissionGrant.objects.all().delete()
    V2Menu.objects.all().delete()
    V2AccountQualification.objects.all().delete()
    V2AccountRoleProfile.objects.all().delete()
    V2ProfileType.objects.all().delete()
    V2Permission.objects.all().delete()
    V2Role.objects.all().delete()
    V2AccountRoleAssignment.objects.all().delete()
    V2AccountProfile.objects.all().delete()
    for department in Department.objects.order_by("-depth", "-id"):
        department.delete()
    User.objects.filter(id__in=v2_user_ids, is_superuser=False).delete()

    root = Department.objects.create(name="总部")
    sync_registered_permissions(disable_stale=True)
    sync_default_roles(replace_grants=True)
    sync_default_profile_types()
    sync_default_menus(replace_bindings=True)

    user = User.objects.filter(username=username).first()
    if user is None:
        user = User.objects.create_user(
            username=username,
            password=password,
            status=UserStatus.ACTIVE,
            is_active=True,
            is_staff=False,
            is_superuser=False,
            is_platform_admin=False,
        )
    else:
        user.status = UserStatus.ACTIVE
        user.is_active = True
        user.is_staff = False
        user.is_superuser = False
        user.is_platform_admin = False
        user.set_password(password)
        user.save(update_fields=["password", "status", "is_active", "is_staff", "is_superuser", "is_platform_admin", "updated_at"])
    profile = V2AccountProfile.objects.create(
        user=user,
        department=root,
        name="平台超级管理员",
        phone=f"139{user.id:08d}",
        email="",
        status=DirectoryStatus.ACTIVE,
    )
    V2AccountRoleAssignment.objects.create(
        account_profile=profile,
        role_code="platform_super_admin",
        assigned_by_user=user,
    )
    return {"root_department_id": root.id, "user_id": user.id, "profile_id": profile.id}
