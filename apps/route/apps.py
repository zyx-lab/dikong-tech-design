from django.apps import AppConfig


class RouteConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.route"
    label = "route"
    verbose_name = "航线管理"
