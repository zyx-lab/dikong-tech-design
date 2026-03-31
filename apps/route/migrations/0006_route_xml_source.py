from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("route", "0005_alter_route_tenant"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="route",
            name="creator_name",
        ),
        migrations.RemoveField(
            model_name="route",
            name="drone_type_id",
        ),
        migrations.RemoveField(
            model_name="route",
            name="estimated_duration",
        ),
        migrations.RemoveField(
            model_name="route",
            name="route_type",
        ),
        migrations.RemoveField(
            model_name="route",
            name="total_distance",
        ),
        migrations.RemoveField(
            model_name="route",
            name="waypoint_count",
        ),
        migrations.AddField(
            model_name="route",
            name="xml_file",
            field=models.FileField(blank=True, default="", upload_to="routes/xml", verbose_name="航线 XML 文件"),
        ),
    ]
