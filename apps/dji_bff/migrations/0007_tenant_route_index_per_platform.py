import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("dji_bff", "0006_djicloudplatform_and_more"),
        ("route", "0009_route_kmz_file"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tenantrouteindex",
            name="route",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="dji_indexes",
                to="route.route",
            ),
        ),
        migrations.AddConstraint(
            model_name="tenantrouteindex",
            constraint=models.UniqueConstraint(
                condition=Q(("dji_platform__isnull", True)),
                fields=("route",),
                name="uniq_route_legacy_index",
            ),
        ),
        migrations.AddConstraint(
            model_name="tenantrouteindex",
            constraint=models.UniqueConstraint(
                condition=Q(("dji_platform__isnull", False)),
                fields=("route", "dji_platform"),
                name="uniq_route_platform_index",
            ),
        ),
    ]
