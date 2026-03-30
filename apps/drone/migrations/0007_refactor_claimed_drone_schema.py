from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("drone", "0006_remove_drone_created_by_staff_id_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="drone",
            old_name="serial_no",
            new_name="device_sn",
        ),
        migrations.AlterModelOptions(
            name="drone",
            options={
                "db_table": "drones",
                "ordering": ["-id"],
                "default_permissions": (),
                "permissions": [("view_drone", "可查看无人机"), ("manage_drone", "可新增与编辑无人机")],
            },
        ),
        migrations.AlterField(
            model_name="drone",
            name="status",
            field=models.CharField(
                choices=[("ENABLED", "启用"), ("DISABLED", "停用")],
                default="DISABLED",
                max_length=16,
                verbose_name="状态",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="drone",
            name="uniq_drone_tenant_serial_no",
        ),
        migrations.AddConstraint(
            model_name="drone",
            constraint=models.UniqueConstraint(fields=("tenant", "device_sn"), name="uniq_drone_tenant_device_sn"),
        ),
    ]
