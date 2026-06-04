import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("iam_v2", "0006_account_role_profiles"),
        ("inspection_v2", "0010_route_cloud_file_download_url_expires_at"),
    ]

    operations = [
        migrations.DeleteModel(
            name="PilotQualification",
        ),
        migrations.RemoveField(
            model_name="inspectionmission",
            name="pilot",
        ),
        migrations.DeleteModel(
            name="PilotProfile",
        ),
        migrations.AddField(
            model_name="inspectionmission",
            name="pilot_account_profile",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="v2_inspection_missions",
                to="iam_v2.v2accountprofile",
            ),
        ),
    ]
