from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("drone", "0005_alter_drone_code_alter_drone_serial_no_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="drone",
            name="created_by_staff_id",
        ),
        migrations.AddField(
            model_name="drone",
            name="created_by_tenant_member_id",
            field=models.BigIntegerField(blank=True, null=True, verbose_name="创建人 TenantMember ID"),
        ),
    ]
