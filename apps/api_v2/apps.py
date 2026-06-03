from django.apps import AppConfig


class ApiV2Config(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api_v2"

    def ready(self):
        from apps.api_v2 import checks  # noqa: F401
