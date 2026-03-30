import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mission", "0004_alter_mission_pilot"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mission",
            name="route",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="missions",
                to="route.route",
                verbose_name="航线",
            ),
        ),
        migrations.AddField(
            model_name="mission",
            name="dji_job_id",
            field=models.CharField(blank=True, default="", max_length=128, verbose_name="DJI 任务 ID"),
        ),
    ]
