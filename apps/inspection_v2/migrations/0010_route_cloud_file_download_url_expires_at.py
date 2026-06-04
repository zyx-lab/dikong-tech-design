from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inspection_v2", "0009_waypointroute_cover_image"),
    ]

    operations = [
        migrations.AddField(
            model_name="waypointroutecloudfile",
            name="download_url_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
