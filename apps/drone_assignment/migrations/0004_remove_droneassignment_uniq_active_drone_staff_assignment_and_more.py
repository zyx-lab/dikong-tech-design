from django.db import migrations, models
import django.db.models.deletion


def purge_legacy_drone_assignments(apps, schema_editor):
    DroneAssignment = apps.get_model("drone_assignment", "DroneAssignment")
    DroneAssignment.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0011_user_is_platform_admin"),
        ("drone_assignment", "0003_alter_droneassignment_tenant"),
    ]

    operations = [
        migrations.RunPython(purge_legacy_drone_assignments, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="droneassignment",
            name="uniq_active_drone_staff_assignment",
        ),
        migrations.RemoveField(
            model_name="droneassignment",
            name="created_by_staff_id",
        ),
        migrations.RemoveField(
            model_name="droneassignment",
            name="staff",
        ),
        migrations.AddField(
            model_name="droneassignment",
            name="created_by_tenant_member_id",
            field=models.BigIntegerField(blank=True, null=True, verbose_name="创建人 TenantMember ID"),
        ),
        migrations.AddField(
            model_name="droneassignment",
            name="tenant_member",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="drone_assignments",
                to="access.tenantmember",
                verbose_name="成员",
            ),
        ),
        migrations.AlterField(
            model_name="droneassignment",
            name="tenant_member",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="drone_assignments",
                to="access.tenantmember",
                verbose_name="成员",
            ),
        ),
        migrations.AddConstraint(
            model_name="droneassignment",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "ACTIVE")),
                fields=("drone", "tenant_member"),
                name="uniq_active_drone_tenant_member_assignment",
            ),
        ),
    ]
