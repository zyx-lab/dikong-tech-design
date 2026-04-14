from django.apps import AppConfig


class MissionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.mission"

    def ready(self):
        from apps.dji_bff.mqtt_watcher import ensure_dji_mqtt_watcher_started

        ensure_dji_mqtt_watcher_started()
