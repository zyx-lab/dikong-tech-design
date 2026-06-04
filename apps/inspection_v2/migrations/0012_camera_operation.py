import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inspection_v2", "0011_account_profile_pilot"),
        ("resource_v2", "0004_dronetelemetrysnapshot_mqttconnectionhealth_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CameraOperation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("action", models.CharField(max_length=64)),
                ("status", models.CharField(choices=[("SUCCEEDED", "成功"), ("FAILED", "失败")], max_length=16)),
                ("payload_index", models.CharField(max_length=64)),
                ("started_at", models.DateTimeField()),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("upstream_request", models.JSONField(blank=True, default=dict)),
                ("upstream_response", models.JSONField(blank=True, default=dict)),
                ("error_message", models.TextField(blank=True, default="")),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="v2_camera_operations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "dji_connection",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="v2_camera_operations",
                        to="resource_v2.djiconnection",
                    ),
                ),
                (
                    "drone",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="v2_camera_operations",
                        to="resource_v2.droneresource",
                    ),
                ),
                (
                    "executor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="v2_camera_operations",
                        to="resource_v2.gatewayresource",
                    ),
                ),
            ],
            options={
                "db_table": "v2_camera_operations",
                "ordering": ["-id"],
                "indexes": [
                    models.Index(fields=["drone", "started_at"], name="idx_v2_camera_drone_time"),
                    models.Index(fields=["status", "started_at"], name="idx_v2_camera_status_time"),
                ],
            },
        ),
    ]
