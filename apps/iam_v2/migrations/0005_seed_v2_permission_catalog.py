from django.db import migrations


def seed_v2_permission_catalog(apps, schema_editor):
    del schema_editor
    from apps.iam_v2.permissions import MENU_SPECS, PERMISSION_SPECS, ROLE_SPECS

    V2Permission = apps.get_model("iam_v2", "V2Permission")
    V2Role = apps.get_model("iam_v2", "V2Role")
    V2RolePermissionGrant = apps.get_model("iam_v2", "V2RolePermissionGrant")
    V2Menu = apps.get_model("iam_v2", "V2Menu")
    V2MenuPermissionBinding = apps.get_model("iam_v2", "V2MenuPermissionBinding")

    permissions_by_code = {}
    for spec in PERMISSION_SPECS:
        domain, resource, action = spec.code.split(":", 2)
        permission, _created = V2Permission.objects.update_or_create(
            code=spec.code,
            defaults={
                "name": spec.name,
                "domain": domain,
                "resource": resource,
                "action": action,
                "status": 1,
                "is_system": True,
            },
        )
        permissions_by_code[spec.code] = permission

    roles_by_code = {}
    for spec in ROLE_SPECS:
        role, _created = V2Role.objects.update_or_create(
            code=spec.code,
            defaults={
                "name": spec.name,
                "status": 1,
                "is_system": spec.is_system,
                "is_super_admin": spec.is_super_admin,
                "assignable_by_department_admin": spec.assignable_by_department_admin,
                "data_scope": spec.data_scope,
                "sort": spec.sort,
                "remark": spec.remark,
            },
        )
        roles_by_code[spec.code] = role
        for permission_code in spec.permissions:
            permission = permissions_by_code.get(permission_code)
            if permission is None:
                continue
            V2RolePermissionGrant.objects.get_or_create(
                role=role,
                permission=permission,
                source="DIRECT",
            )

    menus_by_code = {}
    for spec in MENU_SPECS:
        parent = menus_by_code.get(spec.parent_code) if spec.parent_code else None
        menu, _created = V2Menu.objects.update_or_create(
            code=spec.code,
            defaults={
                "parent": parent,
                "name": spec.name,
                "menu_type": spec.menu_type,
                "path": spec.path,
                "component": spec.component,
                "icon": spec.icon,
                "sort": spec.sort,
                "status": 1,
                "is_system": True,
            },
        )
        menus_by_code[spec.code] = menu
        for permission_code in spec.permissions:
            permission = permissions_by_code.get(permission_code)
            if permission is None:
                continue
            V2MenuPermissionBinding.objects.get_or_create(menu=menu, permission=permission)


def unseed_v2_permission_catalog(apps, schema_editor):
    del schema_editor
    V2Permission = apps.get_model("iam_v2", "V2Permission")
    V2Role = apps.get_model("iam_v2", "V2Role")
    V2Menu = apps.get_model("iam_v2", "V2Menu")
    V2Menu.objects.filter(is_system=True).delete()
    V2Role.objects.filter(is_system=True).delete()
    V2Permission.objects.filter(is_system=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("iam_v2", "0004_v2role_alter_v2accountroleassignment_role_code_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_v2_permission_catalog, unseed_v2_permission_catalog),
    ]
