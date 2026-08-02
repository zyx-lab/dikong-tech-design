import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


RESOURCE_CHOICES = [
    ("drone", "无人机"),
    ("dock", "机场"),
    ("gateway", "执行端/网关"),
    ("payload", "负载"),
    ("camera", "固定摄像头"),
]


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("resource_v2", "0005_move_audit_log_state_to_audit_v2"),
    ]

    operations = [
        migrations.CreateModel(
            name="CameraResource",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("device_sn", models.CharField(max_length=128, unique=True)),
                ("name", models.CharField(max_length=128)),
                ("model", models.CharField(blank=True, default="", max_length=128)),
                ("webrtc_url", models.CharField(max_length=1000)),
                ("results_ws_url", models.CharField(max_length=1000)),
                ("api_key", models.CharField(max_length=512)),
                ("online_status", models.BooleanField(default=True)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_v2_camera_resources",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "v2_camera_resources", "ordering": ["device_sn"]},
        ),
        migrations.AlterField(
            model_name="resourcebinding",
            name="dji_connection",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="resource_bindings",
                to="resource_v2.djiconnection",
            ),
        ),
        migrations.AlterField(
            model_name="resourcebinding",
            name="resource_type",
            field=models.CharField(choices=RESOURCE_CHOICES, max_length=16),
        ),
        migrations.AlterField(
            model_name="resourcebindinghistory",
            name="resource_type",
            field=models.CharField(choices=RESOURCE_CHOICES, max_length=16),
        ),
        migrations.AlterField(
            model_name="resourcesharepermission",
            name="resource_type",
            field=models.CharField(choices=RESOURCE_CHOICES, max_length=16),
        ),
        migrations.AddConstraint(
            model_name="resourcebinding",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(resource_type="camera", dji_connection__isnull=True)
                    | (~models.Q(resource_type="camera") & models.Q(dji_connection__isnull=False))
                ),
                name="chk_v2_binding_connection",
            ),
        ),
    ]
