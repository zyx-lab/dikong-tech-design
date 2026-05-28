import apps.route.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("route", "0008_route_dji_platform"),
    ]

    operations = [
        migrations.AddField(
            model_name="route",
            name="kmz_file",
            field=models.FileField(
                blank=True,
                default="",
                upload_to=apps.route.models.route_kmz_upload_to,
                verbose_name="航线 KMZ 文件",
            ),
        ),
    ]
