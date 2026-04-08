from django.db import migrations, models
from django.db.models import CharField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce


def normalize_mission_drone_snapshot_fields(apps, schema_editor):
    Mission = apps.get_model("mission", "Mission")
    Drone = apps.get_model("drone", "Drone")
    drone_by_id = Drone.objects.filter(pk=OuterRef("drone_id"))
    Mission.objects.filter(drone_id__isnull=False).update(
        status=1,
        device_sn=Coalesce(
            Subquery(drone_by_id.values("device_sn")[:1]),
            Value("", output_field=CharField()),
        ),
        drone_name=Coalesce(
            Subquery(drone_by_id.values("name")[:1]),
            Value("", output_field=CharField()),
        ),
    )
    Mission.objects.filter(drone_id__isnull=True).update(
        status=0,
        device_sn="",
        drone_name="",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("mission", "0006_mission_deleted_at_mission_device_sn_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mission",
            name="drone",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.PROTECT,
                related_name="missions",
                to="drone.drone",
                verbose_name="无人机",
            ),
        ),
        migrations.AlterField(
            model_name="mission",
            name="status",
            field=models.PositiveSmallIntegerField(
                choices=[(0, "未绑定无人机"), (1, "已绑定无人机")],
                default=0,
                verbose_name="任务状态",
            ),
        ),
        migrations.RunPython(
            normalize_mission_drone_snapshot_fields,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="mission",
            name="dji_job_id",
        ),
    ]
