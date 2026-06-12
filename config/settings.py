import os
import sys
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
DJANGO_LOG_REDACT_PAYLOADS = os.getenv("DJANGO_LOG_REDACT_PAYLOADS", "false").lower() in {"1", "true", "yes"}

LOGGING = build_logging_config(
    base_dir=BASE_DIR,
    log_dir=DJANGO_LOG_DIR,
    level=DJANGO_LOG_LEVEL,
    max_bytes=DJANGO_LOG_MAX_BYTES,
    backup_count=DJANGO_LOG_BACKUP_COUNT,
)

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "channels",
    "apps.access",
    "apps.iam_v2",
    "apps.resource_v2",
    "apps.inspection_v2",
    "apps.system_v2",
    "apps.api_v2",
    "apps.dji_mock",
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
ASGI_APPLICATION = "config.asgi.application"

def _is_django_test_command(argv):
    options_with_value = {"--settings", "--pythonpath"}
    args = iter(argv[1:])
    for arg in args:
        if arg in options_with_value:
            next(args, None)
            continue
        if arg.startswith("--settings=") or arg.startswith("--pythonpath="):
            continue
        if arg.startswith("-"):
            continue
        return arg == "test"
    return False


def _sqlite_database_config(env, base_dir):
    sqlite_name = env.get("SQLITE_DB_NAME", "db.sqlite3")
    sqlite_path = Path(sqlite_name)
    if not sqlite_path.is_absolute():
        sqlite_path = base_dir / sqlite_path
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": sqlite_path,
        "OPTIONS": {
            "timeout": 20,
        },
    }


def _postgres_database_config(env):
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env.get("DB_NAME", "dikong"),
        "USER": env.get("DB_USER", "postgres"),
        "PASSWORD": env.get("DB_PASSWORD", "postgres"),
        "HOST": env.get("DB_HOST", "127.0.0.1"),
        "PORT": env.get("DB_PORT", "5432"),
    }


def _database_engine(env, argv):
    if _is_django_test_command(argv):
        return "sqlite"
    return env.get("DB_ENGINE", "postgres").lower()


def _database_config(env, base_dir, argv):
    engine = _database_engine(env, argv)
    if engine in {"postgres", "postgresql"}:
        return {"default": _postgres_database_config(env)}
    return {"default": _sqlite_database_config(env, base_dir)}


DB_ENGINE = _database_engine(os.environ, sys.argv)
DATABASES = _database_config(os.environ, BASE_DIR, sys.argv)

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

if os.getenv("DJANGO_TEST_FAST_PASSWORD_HASHERS", "false").lower() in {"1", "true", "yes"}:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = Path(os.getenv("DJANGO_STATIC_ROOT", str(BASE_DIR / "staticfiles")))
MEDIA_ROOT = Path(os.getenv("DJANGO_MEDIA_ROOT", str(BASE_DIR / "media")))
MEDIA_URL = os.getenv("DJANGO_MEDIA_URL", "/media/")
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE", str(10 * 1024 * 1024)))
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
    AWS_S3_CUSTOM_DOMAIN = os.getenv("AWS_S3_CUSTOM_DOMAIN", os.getenv("OBJECT_STORAGE_PUBLIC_DOMAIN", "")).strip() or None
    AWS_QUERYSTRING_EXPIRE = int(os.getenv("AWS_QUERYSTRING_EXPIRE", os.getenv("OBJECT_STORAGE_URL_EXPIRE_SECONDS", "3600")))
    AWS_S3_ADDRESSING_STYLE = os.getenv("AWS_S3_ADDRESSING_STYLE", "path" if OBJECT_STORAGE_BACKEND == "minio" else "auto")
    AWS_QUERYSTRING_AUTH = os.getenv("AWS_QUERYSTRING_AUTH", "true").lower() == "true"
    AWS_DEFAULT_ACL = os.getenv("AWS_DEFAULT_ACL", "private")
    STORAGES["default"] = {
        "BACKEND": "apps.access.storage_backends.LoggedS3Storage",
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
        "apps.access.authentication.BearerAuthSessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.StandardPageNumberPagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "apps.access.exceptions.custom_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "低空平台 API v2",
    "DESCRIPTION": "正式 `/api/v2/*` 接口文档。DJI 上游协议路径只在网关和 mock 中保留，不属于本系统对外业务 API。",
    "VERSION": "2.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "POSTPROCESSING_HOOKS": [
        "apps.api_v2.openapi_hooks.standardize_v2_response_schema_hook",
        "apps.api_v2.openapi_hooks.enrich_v2_frontend_docs_hook",
    ],
    "ENUM_NAME_OVERRIDES": {
        "ActiveDisabledStatusEnum": "apps.access.models.DirectoryStatus",
    },
}

DJI_UPSTREAM_TIMEOUT_SECONDS = int(os.getenv("DJI_UPSTREAM_TIMEOUT_SECONDS", "10"))
ENABLE_DJI_MOCK_SERVER = os.getenv("ENABLE_DJI_MOCK_SERVER", "false").lower() == "true"
DJI_INTERNAL_API_TOKEN = os.getenv("DJI_INTERNAL_API_TOKEN", "")
DJI_MQTT_WATCHER_ENABLED = os.getenv("DJI_MQTT_WATCHER_ENABLED", "true").lower() == "true"
DJI_MQTT_WATCHER_REFRESH_SECONDS = int(os.getenv("DJI_MQTT_WATCHER_REFRESH_SECONDS", "5"))
DJI_MQTT_OSD_FRESHNESS_SECONDS = int(os.getenv("DJI_MQTT_OSD_FRESHNESS_SECONDS", "10"))
DJI_V2_RESOURCE_STATUS_SYNC_SECONDS = int(os.getenv("DJI_V2_RESOURCE_STATUS_SYNC_SECONDS", "60"))
DJI_V2_MQTT_EXTRA_TOPICS = os.getenv("DJI_V2_MQTT_EXTRA_TOPICS", "")
DJI_V2_MQTT_HEARTBEAT_TTL_SECONDS = int(os.getenv("DJI_V2_MQTT_HEARTBEAT_TTL_SECONDS", "30"))
CHANNEL_REDIS_URL = os.getenv("CHANNEL_REDIS_URL", os.getenv("REDIS_URL", ""))
DJI_V2_MQTT_REDIS_URL = os.getenv("DJI_V2_MQTT_REDIS_URL", CHANNEL_REDIS_URL)
if CHANNEL_REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [CHANNEL_REDIS_URL],
            },
        }
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        }
    }
