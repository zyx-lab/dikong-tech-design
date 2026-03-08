from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("access", "0001_initial"),
        ("drone", "0002_move_drone_assignment_to_separate_app"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="DroneAssignment",
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
                        (
                            "status",
                            models.CharField(
                                choices=[("ACTIVE", "生效中"), ("INACTIVE", "已失效")],
                                default="ACTIVE",
                                max_length=16,
                                verbose_name="分配状态",
                            ),
                        ),
                        ("start_at", models.DateTimeField(auto_now_add=True, verbose_name="开始时间")),
                        ("end_at", models.DateTimeField(blank=True, null=True, verbose_name="结束时间")),
                        (
                            "created_by_staff_id",
                            models.BigIntegerField(blank=True, null=True, verbose_name="创建人 Staff ID"),
                        ),
                        ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                        ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                        (
                            "drone",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="assignments",
                                to="drone.drone",
                                verbose_name="无人机",
                            ),
                        ),
                        (
                            "staff",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="drone_assignments",
                                to="access.staffprofile",
                                verbose_name="飞手",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "drone_assignments",
                        "ordering": ["-id"],
                        "permissions": [("manage_drone_assignment", "可管理无人机分配")],
                        "default_permissions": (),
                        "constraints": [
                            models.UniqueConstraint(
                                condition=models.Q(("status", "ACTIVE")),
                                fields=("drone", "staff"),
                                name="uniq_active_drone_staff_assignment",
                            )
                        ],
                    },
                ),
            ],
        )
    ]
