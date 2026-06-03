import django.db.models.deletion
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
        migrations.CreateModel(
            name="V2AccountQualification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "role_code",
                    models.CharField(
                        choices=[
                            ("platform_super_admin", "平台超级管理员"),
                            ("department_admin", "部门管理员"),
                            ("task_monitor_dispatcher", "任务监控调度员"),
                            ("pilot", "飞手"),
                            ("work_order_handler", "工单处理员"),
                        ],
                        max_length=64,
                    ),
                ),
                ("qualification_type", models.CharField(max_length=128)),
                ("certificate_no", models.CharField(max_length=128)),
                ("issued_at", models.DateField()),
                ("expires_at", models.DateField()),
                ("status", models.PositiveSmallIntegerField(choices=[(0, "disabled"), (1, "active")])),
                ("remark", models.TextField()),
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
                "ordering": ["-expires_at", "-id"],
            },
        ),
        migrations.AddConstraint(
            model_name="v2accountqualification",
            constraint=models.CheckConstraint(
                condition=models.Q(role_code__in=["department_admin", "pilot", "task_monitor_dispatcher", "work_order_handler"]),
                name="chk_v2_account_qualification_role",
            ),
        ),
        migrations.AddConstraint(
            model_name="v2accountqualification",
            constraint=models.UniqueConstraint(
                fields=("account_profile", "role_code", "qualification_type", "certificate_no"),
                name="uniq_v2_account_qualification",
            ),
        ),
    ]
