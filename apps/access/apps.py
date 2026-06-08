from django.apps import AppConfig


class AccessConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.access"
    label = "access"
    verbose_name = "权限与身份中心"

    def ready(self):
        # 在应用加载完成后注册信号，确保禁止用户直绑权限/Group的规则生效。
        from apps.access import signals  # noqa: F401
