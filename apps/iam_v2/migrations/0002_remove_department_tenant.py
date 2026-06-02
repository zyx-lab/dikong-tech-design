from django.db import migrations, models


def assert_single_root_department(apps, schema_editor):
    Department = apps.get_model("iam_v2", "Department")
    roots = list(Department.objects.filter(parent__isnull=True).order_by("id").values_list("id", "name"))
    if len(roots) > 1:
        raise RuntimeError(f"v2 requires a single root department before removing tenant: {roots}")


class Migration(migrations.Migration):

    dependencies = [
        ("iam_v2", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(assert_single_root_department, migrations.RunPython.noop),
        migrations.RemoveIndex(
            model_name="department",
            name="idx_v2_dept_tenant_path",
        ),
        migrations.RemoveConstraint(
            model_name="department",
            name="uniq_v2_department_tenant_root",
        ),
        migrations.RemoveConstraint(
            model_name="department",
            name="uniq_v2_department_sibling_name",
        ),
        migrations.RemoveField(
            model_name="department",
            name="tenant",
        ),
        migrations.AddConstraint(
            model_name="department",
            constraint=models.UniqueConstraint(
                models.Value(1),
                condition=models.Q(parent__isnull=True),
                name="uniq_v2_department_single_root",
            ),
        ),
        migrations.AddConstraint(
            model_name="department",
            constraint=models.UniqueConstraint(fields=("parent", "name"), name="uniq_v2_department_sibling_name"),
        ),
    ]
