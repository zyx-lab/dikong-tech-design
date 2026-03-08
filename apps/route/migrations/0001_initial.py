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
                ("code", models.CharField(max_length=64, unique=True, verbose_name="航线编码")),
                ("name", models.CharField(max_length=128, verbose_name="航线名称")),
                (
                    "description",
                    models.CharField(blank=True, default="", max_length=255, verbose_name="航线描述"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("ACTIVE", "启用"), ("DISABLED", "停用")],
                        default="ACTIVE",
                        max_length=16,
                        verbose_name="状态",
                    ),
                ),
                ("org_id", models.BigIntegerField(blank=True, null=True, verbose_name="组织 ID")),
                (
                    "created_by_staff_id",
                    models.BigIntegerField(blank=True, null=True, verbose_name="创建人 Staff ID"),
                ),
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
