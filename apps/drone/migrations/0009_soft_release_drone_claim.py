from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("drone", "0008_drone_uniq_drone_device_sn_global"),
    ]

    operations = [
        migrations.AlterField(
            model_name="drone",
            name="status",
            field=models.CharField(
                choices=[("ENABLED", "启用"), ("DISABLED", "停用"), ("RELEASED", "已释放")],
                default="DISABLED",
                max_length=16,
                verbose_name="状态",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="drone",
            name="uniq_drone_tenant_device_sn",
        ),
        migrations.RemoveConstraint(
            model_name="drone",
            name="uniq_drone_device_sn_global",
        ),
        migrations.AddConstraint(
            model_name="drone",
            constraint=models.UniqueConstraint(
                condition=~models.Q(status="RELEASED"),
                fields=("tenant", "device_sn"),
                name="uniq_drone_tenant_device_sn",
            ),
        ),
        migrations.AddConstraint(
            model_name="drone",
            constraint=models.UniqueConstraint(
                condition=~models.Q(status="RELEASED"),
                fields=("device_sn",),
                name="uniq_drone_device_sn_global",
            ),
        ),
    ]
