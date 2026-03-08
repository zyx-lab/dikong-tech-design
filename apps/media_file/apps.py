from django.apps import AppConfig


class MediaFileConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.media_file"
    label = "media_file"
    verbose_name = "媒体文件管理"
