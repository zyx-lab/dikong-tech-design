from django.db import migrations, models

import apps.inspection_v2.models


class Migration(migrations.Migration):

    dependencies = [
        ("inspection_v2", "0008_remove_v2_tenant_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="waypointroute",
            name="cover_image",
            field=models.FileField(blank=True, default="", upload_to=apps.inspection_v2.models.route_cover_upload_to),
        ),
    ]
