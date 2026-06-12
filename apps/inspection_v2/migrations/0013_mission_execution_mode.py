from django.db import migrations, models


def mark_existing_job_executions_as_dock_auto(apps, schema_editor):
    MissionCloudExecution = apps.get_model("inspection_v2", "MissionCloudExecution")
    MissionCloudExecution.objects.exclude(dji_job_id="").update(execution_mode="DOCK_AUTO")


def mark_all_executions_as_pilot2_manual(apps, schema_editor):
    MissionCloudExecution = apps.get_model("inspection_v2", "MissionCloudExecution")
    MissionCloudExecution.objects.update(execution_mode="PILOT2_MANUAL")


class Migration(migrations.Migration):
    dependencies = [
        ("inspection_v2", "0012_camera_operation"),
    ]

    operations = [
        migrations.AddField(
            model_name="missioncloudexecution",
            name="execution_mode",
            field=models.CharField(
                choices=[("DOCK_AUTO", "机场自动执行"), ("PILOT2_MANUAL", "Pilot2 手动执行")],
                default="PILOT2_MANUAL",
                max_length=16,
            ),
        ),
        migrations.RunPython(mark_existing_job_executions_as_dock_auto, mark_all_executions_as_pilot2_manual),
    ]
