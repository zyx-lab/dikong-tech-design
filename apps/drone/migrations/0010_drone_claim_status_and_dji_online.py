from django.db import migrations, models


def migrate_status_and_online_forward(apps, schema_editor):
    Drone = apps.get_model("drone", "Drone")
    Drone.objects.filter(status="ENABLED").update(status="CLAIMED", dji_online=True)
    Drone.objects.filter(status="DISABLED").update(status="CLAIMED", dji_online=False)


def migrate_status_and_online_backward(apps, schema_editor):
    Drone = apps.get_model("drone", "Drone")
    Drone.objects.filter(status="CLAIMED", dji_online=True).update(status="ENABLED")
    Drone.objects.filter(status="CLAIMED", dji_online=False).update(status="DISABLED")


class Migration(migrations.Migration):

    dependencies = [
        ("drone", "0009_soft_release_drone_claim"),
    ]

    operations = [
        migrations.AddField(
            model_name="drone",
            name="dji_online",
            field=models.BooleanField(default=False, verbose_name="DJI 在线状态"),
        ),
        migrations.RunPython(migrate_status_and_online_forward, migrate_status_and_online_backward),
        migrations.AlterField(
            model_name="drone",
            name="status",
            field=models.CharField(
                choices=[("CLAIMED", "已认领"), ("RELEASED", "已释放")],
                default="CLAIMED",
                max_length=16,
                verbose_name="状态",
            ),
        ),
    ]
