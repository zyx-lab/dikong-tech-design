from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("route", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="route",
            name="code",
        ),
        migrations.RemoveField(
            model_name="route",
            name="created_by_staff_id",
        ),
        migrations.RemoveField(
            model_name="route",
            name="description",
        ),
        migrations.RemoveField(
            model_name="route",
            name="org_id",
        ),
        migrations.AddField(
            model_name="route",
            name="creator_name",
            field=models.CharField(blank=True, default="", max_length=50, verbose_name="创建人姓名"),
        ),
        migrations.AddField(
            model_name="route",
            name="drone_type_id",
            field=models.BigIntegerField(blank=True, null=True, verbose_name="适用无人机类型 ID"),
        ),
        migrations.AddField(
            model_name="route",
            name="estimated_duration",
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name="预计飞行时长(秒)"),
        ),
        migrations.AddField(
            model_name="route",
            name="route_type",
            field=models.PositiveSmallIntegerField(
                choices=[(1, "点状航线"), (2, "环状航线"), (3, "面状航线")],
                default=1,
                verbose_name="航线类型",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="route",
            name="total_distance",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=12,
                null=True,
                verbose_name="航线总长度(米)",
            ),
        ),
        migrations.AddField(
            model_name="route",
            name="waypoint_count",
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name="航点数量"),
        ),
        migrations.AlterField(
            model_name="route",
            name="name",
            field=models.CharField(max_length=100, verbose_name="航线名称"),
        ),
        migrations.AlterField(
            model_name="route",
            name="status",
            field=models.PositiveSmallIntegerField(
                choices=[(0, "禁用"), (1, "正常")],
                default=1,
                verbose_name="状态",
            ),
        ),
    ]
