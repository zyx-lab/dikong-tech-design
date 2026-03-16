from django.db import migrations


def purge_legacy_business_rows(apps, schema_editor):
    del schema_editor

    purge_order = [
        ("media_file", "MediaFile"),
        ("flight_record", "FlightRecord"),
        ("drone_assignment", "DroneAssignment"),
        ("mission", "Mission"),
        ("waypoint", "Waypoint"),
        ("route", "Route"),
        ("drone", "Drone"),
    ]

    for app_label, model_name in purge_order:
        apps.get_model(app_label, model_name).objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("access", "0008_role_permission_refactor"),
        ("drone", "0003_add_tenant_id_to_business_tables"),
        ("drone_assignment", "0002_add_tenant_id_to_business_tables"),
        ("flight_record", "0003_add_tenant_id_to_business_tables"),
        ("media_file", "0003_add_tenant_id_to_business_tables"),
        ("mission", "0002_add_tenant_id_to_business_tables"),
        ("route", "0004_add_tenant_id_to_business_tables"),
        ("waypoint", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(purge_legacy_business_rows, migrations.RunPython.noop),
    ]
