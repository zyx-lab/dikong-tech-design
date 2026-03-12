from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("access", "0004_add_tenant_id_to_business_tables"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantmember",
            name="invitation_token",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True, verbose_name="邀请令牌"),
        ),
    ]
