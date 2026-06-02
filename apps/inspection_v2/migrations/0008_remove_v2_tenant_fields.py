from django.db import migrations, models


def assert_media_uniqueness(apps, schema_editor):
    from django.db.models import Count

    CloudMediaFile = apps.get_model("inspection_v2", "CloudMediaFile")
    duplicates = list(
        CloudMediaFile.objects.values("workspace_id", "cloud_file_id")
        .annotate(row_count=Count("id"))
        .filter(row_count__gt=1)
        .order_by("workspace_id", "cloud_file_id")
        .values_list("workspace_id", "cloud_file_id", "row_count")[:20]
    )
    if duplicates:
        raise RuntimeError(
            "v2 cloud media files must be unique by workspace_id/cloud_file_id before removing tenant: "
            f"{duplicates}"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("iam_v2", "0002_remove_department_tenant"),
        ("inspection_v2", "0007_missioncloudexecution_live_fields"),
    ]

    operations = [
        migrations.RunPython(assert_media_uniqueness, migrations.RunPython.noop),
        migrations.RemoveIndex(
            model_name="inspectionmission",
            name="idx_v2_mission_tenant_status",
        ),
        migrations.RemoveIndex(
            model_name="inspectionflightrecord",
            name="idx_v2_record_tenant_status",
        ),
        migrations.RemoveConstraint(
            model_name="cloudmediafile",
            name="uniq_v2_cloud_media_file",
        ),
        migrations.RemoveField(
            model_name="waypointroute",
            name="tenant",
        ),
        migrations.RemoveField(
            model_name="inspectionmission",
            name="tenant",
        ),
        migrations.RemoveField(
            model_name="inspectionflightrecord",
            name="tenant",
        ),
        migrations.RemoveField(
            model_name="cloudmediafile",
            name="tenant",
        ),
        migrations.AddIndex(
            model_name="inspectionmission",
            index=models.Index(fields=["status"], name="idx_v2_mission_status"),
        ),
        migrations.AddIndex(
            model_name="inspectionflightrecord",
            index=models.Index(fields=["status"], name="idx_v2_record_status"),
        ),
        migrations.AddConstraint(
            model_name="cloudmediafile",
            constraint=models.UniqueConstraint(fields=("workspace_id", "cloud_file_id"), name="uniq_v2_cloud_media_file"),
        ),
    ]
