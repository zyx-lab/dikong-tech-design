from django.apps import AppConfig


class WaypointConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.waypoint"
    label = "waypoint"
    verbose_name = "航点管理"
