from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inspection_v2", "0014_inspectionflightrecordmediasyncstate"),
    ]

    operations = [
        migrations.AlterField(
            model_name="missionresourceassignment",
            name="resource_type",
            field=models.CharField(
                choices=[
                    ("drone", "无人机"),
                    ("dock", "机场"),
                    ("gateway", "执行端/网关"),
                    ("payload", "负载"),
                    ("camera", "固定摄像头"),
                ],
                max_length=16,
            ),
        ),
    ]
