from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("route", "0002_align_route_with_overall_design"),
    ]

    operations = [
        migrations.AlterField(
            model_name="route",
            name="route_type",
            field=models.PositiveSmallIntegerField(
                choices=[(0, "待扩展")],
                default=0,
                verbose_name="航线类型扩展位",
            ),
        ),
    ]
