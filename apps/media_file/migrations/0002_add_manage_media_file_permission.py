from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("media_file", "0001_initial"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="mediafile",
            options={
                "default_permissions": (),
                "ordering": ["-id"],
                "permissions": [
                    ("view_media_file", "可查看媒体文件"),
                    ("manage_media_file", "可管理媒体文件"),
                ],
            },
        ),
    ]
