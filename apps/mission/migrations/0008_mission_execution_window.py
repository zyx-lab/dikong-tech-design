from django.db import migrations, models


def remap_legacy_statuses(apps, schema_editor):
    Mission = apps.get_model("mission", "Mission")
    Mission.objects.filter(status__in=[0, 1]).update(status=0)


class Migration(migrations.Migration):
    dependencies = [("mission", "0007_localize_mission_status_and_drop_dji_job")]

    operations = [
        migrations.AddField(
            model_name="mission",
            name="started_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="开始执行时间"),
        ),
        migrations.AddField(
            model_name="mission",
            name="finished_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="执行完成时间"),
        ),
        migrations.RunPython(remap_legacy_statuses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="mission",
            name="status",
            field=models.PositiveSmallIntegerField(
                choices=[(0, "待执行"), (1, "执行中"), (2, "执行完成")],
                default=0,
                verbose_name="任务状态",
            ),
        ),
    ]
