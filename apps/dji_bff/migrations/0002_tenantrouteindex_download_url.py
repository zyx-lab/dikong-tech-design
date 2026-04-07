from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dji_bff", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantrouteindex",
            name="download_url",
            field=models.CharField(blank=True, default="", max_length=500, verbose_name="DJI 航线下载地址"),
        ),
    ]
