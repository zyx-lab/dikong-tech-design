from django.db import migrations


LEGACY_TABLES = [
    "tenant_media_indexes",
    "tenant_route_indexes",
    "tenant_mission_indexes",
    "media_files",
    "flight_records",
    "drone_assignments",
    "missions",
    "waypoints",
    "drones",
    "routes",
    "dji_device_indexes",
    "dji_cloud_platforms",
    "dji_workspace_configs",
    "staff_types",
    "staff_type_groups",
    "auth_group_permission_scopes",
    "system_roles",
    "system_role_groups",
    "tenant_member_positions",
]

LEGACY_APP_LABELS = [
    "api_v1",
    "dji_bff",
    "drone",
    "drone_assignment",
    "route",
    "waypoint",
    "mission",
    "flight_record",
    "media_file",
]

LEGACY_ACCESS_MODELS = [
    "auditlog",
    "staffprofile",
    "tenant",
    "role",
    "permission",
    "rolepermissiongrant",
    "qualificationtype",
    "tenantmember",
    "tenantmemberrole",
    "tenantmemberqualification",
    "stafftype",
    "stafftypegroup",
    "grouppermissionscope",
    "systemrole",
    "systemrolegroup",
    "tenantmemberposition",
]


def drop_legacy_tables(apps, schema_editor):
    del apps

    quote_name = schema_editor.connection.ops.quote_name
    vendor = schema_editor.connection.vendor
    for table_name in LEGACY_TABLES:
        quoted = quote_name(table_name)
        if vendor == "postgresql":
            schema_editor.execute(f"DROP TABLE IF EXISTS {quoted} CASCADE")
        else:
            schema_editor.execute(f"DROP TABLE IF EXISTS {quoted}")


def clean_legacy_content_types(apps, schema_editor):
    del schema_editor

    ContentType = apps.get_model("contenttypes", "ContentType")
    AuthPermission = apps.get_model("auth", "Permission")
    legacy_content_types = ContentType.objects.filter(app_label__in=LEGACY_APP_LABELS) | ContentType.objects.filter(
        app_label="access",
        model__in=LEGACY_ACCESS_MODELS,
    )
    AuthPermission.objects.filter(content_type__in=legacy_content_types).delete()
    legacy_content_types.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("access", "0012_authsession"),
        ("iam_v2", "0006_account_role_profiles"),
        ("resource_v2", "0004_dronetelemetrysnapshot_mqttconnectionhealth_and_more"),
        ("inspection_v2", "0012_camera_operation"),
    ]

    operations = [
        migrations.RunPython(drop_legacy_tables, migrations.RunPython.noop),
        migrations.DeleteModel(name="TenantMemberQualification"),
        migrations.DeleteModel(name="TenantMemberRole"),
        migrations.DeleteModel(name="RolePermissionGrant"),
        migrations.DeleteModel(name="QualificationType"),
        migrations.DeleteModel(name="Permission"),
        migrations.DeleteModel(name="Role"),
        migrations.DeleteModel(name="TenantMember"),
        migrations.DeleteModel(name="StaffProfile"),
        migrations.DeleteModel(name="AuditLog"),
        migrations.DeleteModel(name="Tenant"),
        migrations.RunPython(clean_legacy_content_types, migrations.RunPython.noop),
    ]
