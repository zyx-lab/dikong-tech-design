from django.conf import settings
from django.core.checks import Error, Tags, register

VALID_OBJECT_STORAGE_BACKENDS = {"filesystem", "s3", "minio"}


def _setting_text(name: str) -> str:
    return str(getattr(settings, name, "") or "").strip()


def _domain_has_scheme_or_path(value: str) -> bool:
    return "://" in value or "/" in value or "?" in value or "#" in value


@register(Tags.compatibility)
def check_object_storage_settings(app_configs=None, **kwargs):
    backend = _setting_text("OBJECT_STORAGE_BACKEND").lower() or "filesystem"
    if backend not in VALID_OBJECT_STORAGE_BACKENDS:
        return [
            Error(
                "OBJECT_STORAGE_BACKEND must be one of filesystem, s3, or minio.",
                id="api_v2.E001",
            )
        ]

    errors = []
    if backend in {"s3", "minio"}:
        if not _setting_text("AWS_ACCESS_KEY_ID"):
            errors.append(Error("Object storage access key is required.", id="api_v2.E002"))
        if not _setting_text("AWS_SECRET_ACCESS_KEY"):
            errors.append(Error("Object storage secret key is required.", id="api_v2.E003"))
        if not _setting_text("AWS_STORAGE_BUCKET_NAME"):
            errors.append(Error("Object storage bucket name is required.", id="api_v2.E004"))
        if backend == "minio" and not _setting_text("AWS_S3_ENDPOINT_URL"):
            errors.append(Error("MinIO object storage endpoint URL is required.", id="api_v2.E005"))

    custom_domain = _setting_text("AWS_S3_CUSTOM_DOMAIN")
    if custom_domain and _domain_has_scheme_or_path(custom_domain):
        errors.append(
            Error(
                "OBJECT_STORAGE_PUBLIC_DOMAIN / AWS_S3_CUSTOM_DOMAIN must be a host-only value without scheme or path.",
                id="api_v2.E006",
            )
        )
    return errors
