from django.db import migrations, models


def backfill_account_contacts(apps, schema_editor):
    V2AccountProfile = apps.get_model("iam_v2", "V2AccountProfile")
    for profile in V2AccountProfile.objects.select_related("user").order_by("id").iterator():
        username = getattr(profile.user, "username", "") or f"account-{profile.id}"
        profile.name = username
        profile.phone = f"legacy-{profile.id}"
        profile.email = ""
        profile.save(update_fields=["name", "phone", "email"])


class Migration(migrations.Migration):

    dependencies = [
        ("iam_v2", "0002_remove_department_tenant"),
    ]

    operations = [
        migrations.AddField(
            model_name="v2accountprofile",
            name="email",
            field=models.EmailField(blank=True, default="", max_length=254),
        ),
        migrations.AddField(
            model_name="v2accountprofile",
            name="name",
            field=models.CharField(default="", max_length=128),
        ),
        migrations.AddField(
            model_name="v2accountprofile",
            name="phone",
            field=models.CharField(default="", max_length=32),
        ),
        migrations.RunPython(backfill_account_contacts, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="v2accountprofile",
            name="name",
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name="v2accountprofile",
            name="phone",
            field=models.CharField(max_length=32),
        ),
        migrations.AddConstraint(
            model_name="v2accountprofile",
            constraint=models.CheckConstraint(condition=models.Q(phone__gt=""), name="chk_v2_account_phone_not_blank"),
        ),
        migrations.AddConstraint(
            model_name="v2accountprofile",
            constraint=models.UniqueConstraint(fields=("phone",), name="uniq_v2_account_phone"),
        ),
    ]
