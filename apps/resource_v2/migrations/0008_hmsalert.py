from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("resource_v2", "0007_cameraresource_api_username"),
    ]

    operations = [
        migrations.CreateModel(
            name="HmsAlert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("gateway_sn", models.CharField(max_length=128)),
                ("from_sn", models.CharField(max_length=128)),
                ("alarm_key", models.CharField(max_length=64)),
                ("code", models.CharField(max_length=64)),
                ("device_domain", models.IntegerField(blank=True, null=True)),
                ("level", models.IntegerField(blank=True, null=True)),
                ("module", models.IntegerField(blank=True, null=True)),
                ("raw_item", models.JSONField(default=dict)),
                ("first_reported_at", models.DateTimeField()),
                ("last_reported_at", models.DateTimeField()),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                (
                    "dji_connection",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hms_alerts",
                        to="resource_v2.djiconnection",
                    ),
                ),
            ],
            options={
                "db_table": "v2_hms_alerts",
                "ordering": ["-first_reported_at", "-id"],
            },
        ),
        migrations.AddConstraint(
            model_name="hmsalert",
            constraint=models.UniqueConstraint(
                condition=models.Q(("resolved_at__isnull", True)),
                fields=("dji_connection", "gateway_sn", "from_sn", "alarm_key"),
                name="uniq_v2_active_hms_alert",
            ),
        ),
    ]
