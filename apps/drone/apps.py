from django.apps import AppConfig


class DroneConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.drone"
    label = "drone"
    verbose_name = "无人机管理"
