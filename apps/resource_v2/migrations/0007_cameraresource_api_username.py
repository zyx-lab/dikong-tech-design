from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("resource_v2", "0006_cameraresource_and_camera_bindings"),
    ]

    operations = [
        migrations.AddField(
            model_name="cameraresource",
            name="api_username",
            field=models.CharField(default="", max_length=512),
            preserve_default=False,
        ),
    ]
