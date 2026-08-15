from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("resource_v2", "0008_hmsalert"),
    ]

    operations = [
        migrations.AddField(
            model_name="dronetelemetrysnapshot",
            name="battery_cycles",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="dronetelemetrysnapshot",
            name="total_flight_distance",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=16, null=True),
        ),
        migrations.AddField(
            model_name="dronetelemetrysnapshot",
            name="total_flight_sorties",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="dronetelemetrysnapshot",
            name="total_flight_time",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
    ]
