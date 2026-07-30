from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("iam_v2", "0006_account_role_profiles"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="v2accountroleprofile",
            name="uniq_v2_account_role_profile",
        ),
        migrations.RemoveConstraint(
            model_name="v2accountqualification",
            name="uniq_v2_account_qualification",
        ),
        migrations.AddConstraint(
            model_name="v2accountroleprofile",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("account_profile", "profile_type"),
                name="uniq_v2_account_role_profile",
            ),
        ),
        migrations.AddConstraint(
            model_name="v2accountqualification",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("account_profile", "profile_type", "qualification_type", "certificate_no"),
                name="uniq_v2_account_qualification",
            ),
        ),
    ]
