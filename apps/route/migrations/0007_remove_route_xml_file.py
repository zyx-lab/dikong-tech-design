from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("route", "0006_route_xml_source"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="route",
            name="xml_file",
        ),
    ]
