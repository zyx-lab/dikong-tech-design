from django.apps import AppConfig


class FlightRecordConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.flight_record"
    label = "flight_record"
    verbose_name = "飞行记录管理"

