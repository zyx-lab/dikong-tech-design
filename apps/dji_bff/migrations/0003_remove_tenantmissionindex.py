from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("dji_bff", "0002_tenantrouteindex_download_url")]

    operations = [
        migrations.DeleteModel(name="TenantMissionIndex"),
    ]
