from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("workforce_v2", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="pilotprofile",
            name="phone",
        ),
        migrations.DeleteModel(
            name="PilotQualification",
        ),
    ]
