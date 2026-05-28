import os
from pathlib import Path

from config.logging_config import build_logging_config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-secret-key")
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = [host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",") if host.strip()]

DJANGO_LOG_DIR = Path(os.getenv("DJANGO_LOG_DIR", str(BASE_DIR / "logs")))
DJANGO_LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "INFO")
DJANGO_LOG_MAX_BYTES = int(os.getenv("DJANGO_LOG_MAX_BYTES", str(10 * 1024 * 1024)))
DJANGO_LOG_BACKUP_COUNT = int(os.getenv("DJANGO_LOG_BACKUP_COUNT", "10"))
DJANGO_LOG_BODY_MAX_CHARS = int(os.getenv("DJANGO_LOG_BODY_MAX_CHARS", "20000"))
DJANGO_LOG_HEADER_MAX_CHARS = int(os.getenv("DJANGO_LOG_HEADER_MAX_CHARS", "4096"))

LOGGING = build_logging_config(
    base_dir=BASE_DIR,
    log_dir=DJANGO_LOG_DIR,
    level=DJANGO_LOG_LEVEL,
    max_bytes=DJANGO_LOG_MAX_BYTES,
    backup_count=DJANGO_LOG_BACKUP_COUNT,
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.access",
    "apps.api_v1",
    "apps.api_v2",
    "apps.drone",
    "apps.drone_assignment",
    "apps.route",
    "apps.waypoint",
    "apps.mission",
    "apps.flight_record",
    "apps.media_file",
    "apps.dji_bff",
    "drf_spectacular",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.access.middleware.RequestContextMiddleware",
    "apps.access.middleware.RequestLifecycleLoggingMiddleware",
    "apps.access.middleware.TenantContextMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DB_ENGINE = os.getenv("DB_ENGINE", "sqlite").lower()
if DB_ENGINE == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME", "dikong"),
            "USER": os.getenv("DB_USER", "postgres"),
            "PASSWORD": os.getenv("DB_PASSWORD", "postgres"),
            "HOST": os.getenv("DB_HOST", "127.0.0.1"),
            "PORT": os.getenv("DB_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            "OPTIONS": {
                "timeout": 20,
            },
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
MEDIA_ROOT = Path(os.getenv("DJANGO_MEDIA_ROOT", str(BASE_DIR / "media")))
MEDIA_URL = os.getenv("DJANGO_MEDIA_URL", "/media/")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "access.User"

OBJECT_STORAGE_BACKEND = os.getenv("OBJECT_STORAGE_BACKEND", "filesystem").lower()
STORAGES = {
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
if OBJECT_STORAGE_BACKEND in {"s3", "minio"}:
    AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", os.getenv("OBJECT_STORAGE_ACCESS_KEY_ID", ""))
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", os.getenv("OBJECT_STORAGE_SECRET_ACCESS_KEY", ""))
    AWS_STORAGE_BUCKET_NAME = os.getenv("AWS_STORAGE_BUCKET_NAME", os.getenv("OBJECT_STORAGE_BUCKET_NAME", ""))
    AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL", os.getenv("OBJECT_STORAGE_ENDPOINT_URL", ""))
    AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", os.getenv("OBJECT_STORAGE_REGION_NAME", ""))
    AWS_S3_ADDRESSING_STYLE = os.getenv("AWS_S3_ADDRESSING_STYLE", "path")
    AWS_QUERYSTRING_AUTH = os.getenv("AWS_QUERYSTRING_AUTH", "true").lower() == "true"
    AWS_DEFAULT_ACL = os.getenv("AWS_DEFAULT_ACL", "private")
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
    }
else:
    STORAGES["default"] = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {
            "location": str(MEDIA_ROOT),
            "base_url": MEDIA_URL,
        },
    }

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.access.api_v1.authentication.BearerAuthSessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "apps.api_v1.pagination.StandardPageNumberPagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "apps.access.exceptions.custom_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "低空平台 API",
    "DESCRIPTION": "正式 `/api/v1/*` 接口文档，其中 IAM 正式能力统一挂载在 `/api/v1/iam/*`。",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "POSTPROCESSING_HOOKS": [
        "apps.api_v1.openapi_hooks.standardize_response_schema_hook",
    ],
    "ENUM_NAME_OVERRIDES": {
        "ActiveDisabledStatusEnum": "apps.access.models.DirectoryStatus",
        "DroneAssignmentStatusEnum": "apps.drone_assignment.models.DroneAssignmentStatus",
        "DroneStatusEnum": "apps.drone.models.DroneStatus",
        "FlightRecordStatusEnum": "apps.flight_record.models.FlightRecordStatus",
        "MediaTypeEnum": "apps.media_file.models.MediaType",
        "MissionStatusEnum": "apps.mission.models.MissionStatus",
    },
}

DJI_UPSTREAM_BASE_URL = os.getenv("DJI_UPSTREAM_BASE_URL", "")
DJI_UPSTREAM_USERNAME = os.getenv("DJI_UPSTREAM_USERNAME", "")
DJI_UPSTREAM_PASSWORD = os.getenv("DJI_UPSTREAM_PASSWORD", "")
DJI_UPSTREAM_LOGIN_FLAG = int(os.getenv("DJI_UPSTREAM_LOGIN_FLAG", "1"))
DJI_UPSTREAM_TIMEOUT_SECONDS = int(os.getenv("DJI_UPSTREAM_TIMEOUT_SECONDS", "10"))
ENABLE_DJI_MOCK_SERVER = os.getenv("ENABLE_DJI_MOCK_SERVER", "false").lower() == "true"
DJI_INTERNAL_API_TOKEN = os.getenv("DJI_INTERNAL_API_TOKEN", "")
DJI_MQTT_WATCHER_ENABLED = os.getenv("DJI_MQTT_WATCHER_ENABLED", "true").lower() == "true"
DJI_MQTT_WATCHER_REFRESH_SECONDS = int(os.getenv("DJI_MQTT_WATCHER_REFRESH_SECONDS", "5"))
DJI_MQTT_OSD_FRESHNESS_SECONDS = int(os.getenv("DJI_MQTT_OSD_FRESHNESS_SECONDS", "10"))
