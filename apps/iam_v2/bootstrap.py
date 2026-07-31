from datetime import date

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.access.models import DirectoryStatus, UserStatus
from apps.iam_v2.models import (
    Department,
    FixedRole,
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

FRONTEND_TEST_ACCOUNT_SPECS = (
    ("super", FixedRole.PLATFORM_SUPER_ADMIN, "前端测试-平台超级管理员", ("platform_super_admin", "biz_admin", "super")),
    ("admin", FixedRole.DEPARTMENT_ADMIN, "前端测试-部门管理员", ("department_admin", "tenant_admin", "admin")),
    (
        "dispatcher",
        FixedRole.TASK_MONITOR_DISPATCHER,
        "前端测试-任务监控调度员",
        ("task_monitor_dispatcher", "dispatcher"),
    ),
    ("pilot", FixedRole.PILOT, "前端测试-飞手", ("pilot",)),
    ("handler", FixedRole.WORK_ORDER_HANDLER, "前端测试-工单处理员", ("work_order_handler", "auditor", "handler")),
)

FRONTEND_TEST_OBSOLETE_SUFFIXES = ("route",)


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
def sync_frontend_test_accounts(*, username_prefix: str = "jnu", password: str = "FrontTest@123") -> dict:
    username_prefix = username_prefix.strip()
    if not username_prefix:
        raise ValueError("username_prefix cannot be empty")
    if not password:
        raise ValueError("password cannot be empty")

    root = Department.objects.filter(parent__isnull=True).order_by("id").first()
    if root is None:
        root = Department.objects.create(name="总部")

    created = 0
    updated = 0
    renamed = 0
    deleted_obsolete = 0
    prepared_usernames = []
    prepared_user_ids = set()
    obsolete_usernames = set()

    for suffix, role_code, display_name, legacy_suffixes in FRONTEND_TEST_ACCOUNT_SPECS:
        username = f"{username_prefix}_{suffix}"
        candidate_usernames = [username]
        for legacy_suffix in legacy_suffixes:
            candidate_usernames.extend(
                [
                    f"{username_prefix}_{legacy_suffix}",
                    f"fe_frontend_lab_{legacy_suffix}",
                ]
            )
        candidate_usernames = list(dict.fromkeys(candidate_usernames))
        obsolete_usernames.update(candidate_usernames[1:])

        user = None
        for candidate_username in candidate_usernames:
            user = User.objects.filter(username=candidate_username).first()
            if user is not None:
                break
        if user is None:
            user = User(username=username)
            created += 1
        elif user.username != username:
            user.username = username
            renamed += 1
        else:
            updated += 1

        user.status = UserStatus.ACTIVE
        user.is_active = True
        user.is_staff = False
        user.is_superuser = False
        user.is_platform_admin = False
        user.set_password(password)
        user.save()

        profile, _ = V2AccountProfile.objects.update_or_create(
            user=user,
            defaults={
                "department": root,
                "name": display_name,
                "phone": f"137{user.id:08d}",
                "email": "",
                "status": DirectoryStatus.ACTIVE,
            },
        )
        V2AccountRoleAssignment.objects.update_or_create(
            account_profile=profile,
            role_code=role_code,
            defaults={"assigned_by_user": user},
        )
        V2AccountRoleAssignment.objects.filter(account_profile=profile).exclude(role_code=role_code).delete()
        if role_code == FixedRole.PILOT:
            V2AccountRoleProfile.objects.update_or_create(
                account_profile=profile,
                profile_type=FixedRole.PILOT,
                deleted_at__isnull=True,
                defaults={
                    "display_name": display_name,
                    "level": "多旋翼",
                    "status": DirectoryStatus.ACTIVE,
                    "remark": "联调飞手档案",
                    "deleted_at": None,
                },
            )
            V2AccountQualification.objects.update_or_create(
                account_profile=profile,
                profile_type=FixedRole.PILOT,
                qualification_type="多旋翼巡检",
                certificate_no=f"{username.upper()}-001",
                deleted_at__isnull=True,
                defaults={
                    "issued_at": date(2020, 1, 1),
                    "expires_at": date(2099, 12, 31),
                    "status": DirectoryStatus.ACTIVE,
                    "remark": "联调飞手资质",
                    "deleted_at": None,
                },
            )
        prepared_usernames.append(username)
        prepared_user_ids.add(user.id)

    for obsolete_suffix in FRONTEND_TEST_OBSOLETE_SUFFIXES:
        obsolete_usernames.update(
            {
                f"{username_prefix}_{obsolete_suffix}",
                f"fe_frontend_lab_{obsolete_suffix}",
            }
        )
    deleted_obsolete = (
        User.objects.filter(username__in=obsolete_usernames)
        .exclude(id__in=prepared_user_ids)
        .delete()[0]
    )

    return {
        "created": created,
        "updated": updated,
        "renamed": renamed,
        "deleted_obsolete": deleted_obsolete,
        "usernames": prepared_usernames,
    }


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
