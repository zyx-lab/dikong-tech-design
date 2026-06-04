import django.db.models.deletion
from django.db import migrations, models


def seed_default_profile_types(apps, schema_editor):
    del schema_editor
    from apps.iam_v2.permissions import ROLE_SPECS

    V2ProfileType = apps.get_model("iam_v2", "V2ProfileType")
    for spec in ROLE_SPECS:
        V2ProfileType.objects.update_or_create(
            code=spec.code,
            defaults={
                "name": f"{spec.name}档案",
                "role_code": spec.code,
                "status": 1,
                "is_system": True,
                "sort": spec.sort,
                "remark": spec.remark,
            },
        )


def unseed_default_profile_types(apps, schema_editor):
    del schema_editor
    V2ProfileType = apps.get_model("iam_v2", "V2ProfileType")
    V2ProfileType.objects.filter(is_system=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("iam_v2", "0005_seed_v2_permission_catalog"),
    ]

    operations = [
        migrations.CreateModel(
            name="V2ProfileType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("code", models.CharField(max_length=64, unique=True)),
                ("name", models.CharField(max_length=128)),
                ("role_code", models.CharField(max_length=64)),
                ("status", models.PositiveSmallIntegerField(choices=[(0, "disabled"), (1, "active")], default=1)),
                ("is_system", models.BooleanField(default=False)),
                ("sort", models.IntegerField(default=100)),
                ("remark", models.TextField(blank=True, default="")),
            ],
            options={
                "db_table": "v2_profile_types",
                "ordering": ["sort", "id"],
            },
        ),
        migrations.CreateModel(
            name="V2AccountRoleProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("profile_type", models.CharField(max_length=64)),
                ("display_name", models.CharField(max_length=128)),
                ("level", models.CharField(blank=True, default="", max_length=64)),
                ("status", models.PositiveSmallIntegerField(choices=[(0, "disabled"), (1, "active")], default=1)),
                ("remark", models.TextField(blank=True, default="")),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                (
                    "account_profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="role_profiles",
                        to="iam_v2.v2accountprofile",
                    ),
                ),
            ],
            options={
                "db_table": "v2_account_role_profiles",
                "ordering": ["account_profile_id", "profile_type"],
            },
        ),
        migrations.CreateModel(
            name="V2AccountQualification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("profile_type", models.CharField(max_length=64)),
                ("qualification_type", models.CharField(max_length=128)),
                ("certificate_no", models.CharField(max_length=128)),
                ("issued_at", models.DateField()),
                ("expires_at", models.DateField()),
                ("status", models.PositiveSmallIntegerField(choices=[(0, "disabled"), (1, "active")])),
                ("remark", models.TextField(blank=True, default="")),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                (
                    "account_profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="qualifications",
                        to="iam_v2.v2accountprofile",
                    ),
                ),
            ],
            options={
                "db_table": "v2_account_qualifications",
                "ordering": ["account_profile_id", "profile_type", "-expires_at", "-id"],
            },
        ),
        migrations.AddConstraint(
            model_name="v2accountroleprofile",
            constraint=models.UniqueConstraint(fields=("account_profile", "profile_type"), name="uniq_v2_account_role_profile"),
        ),
        migrations.AddIndex(
            model_name="v2accountroleprofile",
            index=models.Index(fields=["profile_type", "status", "deleted_at"], name="idx_v2_role_profile_type"),
        ),
        migrations.AddConstraint(
            model_name="v2accountqualification",
            constraint=models.UniqueConstraint(
                fields=("account_profile", "profile_type", "qualification_type", "certificate_no"),
                name="uniq_v2_account_qualification",
            ),
        ),
        migrations.AddIndex(
            model_name="v2accountqualification",
            index=models.Index(fields=["account_profile", "profile_type", "deleted_at"], name="idx_v2_account_qual_profile"),
        ),
        migrations.RunPython(seed_default_profile_types, unseed_default_profile_types),
    ]
