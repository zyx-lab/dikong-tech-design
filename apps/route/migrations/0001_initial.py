from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Route",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=100, verbose_name="航线名称")),
                (
                    "route_type",
                    models.PositiveSmallIntegerField(
                        choices=[(0, "待扩展")],
                        default=0,
                        verbose_name="航线类型扩展位",
                    ),
                ),
                ("drone_type_id", models.BigIntegerField(blank=True, null=True, verbose_name="适用无人机类型 ID")),
                (
                    "total_distance",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=12,
                        null=True,
                        verbose_name="航线总长度(米)",
                    ),
                ),
                (
                    "estimated_duration",
                    models.PositiveIntegerField(blank=True, null=True, verbose_name="预计飞行时长(秒)"),
                ),
                ("waypoint_count", models.PositiveIntegerField(default=0, verbose_name="航点数量")),
                ("creator_name", models.CharField(blank=True, default="", max_length=50, verbose_name="创建人姓名")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
            ],
            options={
                "db_table": "routes",
                "ordering": ["-id"],
                "permissions": [
                    ("view_route", "可查看航线"),
                    ("manage_route", "可新增与编辑航线"),
                ],
                "default_permissions": (),
            },
        )
    ]
