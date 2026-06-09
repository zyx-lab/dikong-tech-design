import os
import subprocess
import sys

from django.test import SimpleTestCase, override_settings

from apps.api_v2.checks import check_object_storage_settings


def check_ids() -> list[str]:
    return [error.id for error in check_object_storage_settings(None)]


class ObjectStorageSettingsTests(SimpleTestCase):
    @override_settings(OBJECT_STORAGE_BACKEND="ftp")
    def test_object_storage_check_should_report_exact_ids_for_invalid_backend(self):
        self.assertEqual(check_ids(), ["api_v2.E001"])

    @override_settings(
        OBJECT_STORAGE_BACKEND="minio",
        AWS_ACCESS_KEY_ID="",
        AWS_SECRET_ACCESS_KEY="",
        AWS_STORAGE_BUCKET_NAME="",
        AWS_S3_ENDPOINT_URL="",
        AWS_S3_CUSTOM_DOMAIN=None,
    )
    def test_object_storage_check_should_report_exact_ids_for_missing_minio_required_values(self):
        self.assertEqual(check_ids(), ["api_v2.E002", "api_v2.E003", "api_v2.E004", "api_v2.E005"])

    @override_settings(
        OBJECT_STORAGE_BACKEND="minio",
        AWS_ACCESS_KEY_ID="minioadmin",
        AWS_SECRET_ACCESS_KEY="minioadmin123",
        AWS_STORAGE_BUCKET_NAME="dikong-route-covers",
        AWS_S3_ENDPOINT_URL="https://minio.example.test",
        AWS_S3_CUSTOM_DOMAIN="https://assets.example.test/covers",
    )
    def test_object_storage_check_should_report_exact_id_for_invalid_public_domain(self):
        self.assertEqual(check_ids(), ["api_v2.E006"])

    @override_settings(
        OBJECT_STORAGE_BACKEND="minio",
        AWS_ACCESS_KEY_ID="minioadmin",
        AWS_SECRET_ACCESS_KEY="minioadmin123",
        AWS_STORAGE_BUCKET_NAME="dikong-route-covers",
        AWS_S3_ENDPOINT_URL="https://minio.example.test",
        AWS_S3_CUSTOM_DOMAIN=None,
    )
    def test_object_storage_check_should_pass_with_minio_required_env(self):
        self.assertEqual(check_ids(), [])

    def test_object_storage_settings_should_configure_s3_storage_backend(self):
        env = os.environ.copy()
        env.update(
            {
                "OBJECT_STORAGE_BACKEND": "minio",
                "OBJECT_STORAGE_ACCESS_KEY_ID": "minioadmin",
                "OBJECT_STORAGE_SECRET_ACCESS_KEY": "minioadmin123",
                "OBJECT_STORAGE_BUCKET_NAME": "dikong-route-covers",
                "OBJECT_STORAGE_ENDPOINT_URL": "https://minio.example.test",
                "AWS_S3_ADDRESSING_STYLE": "path",
                "DB_ENGINE": "sqlite",
            }
        )
        result = subprocess.run(
            [
                sys.executable,
                "manage.py",
                "shell",
                "-c",
                (
                    "from django.conf import settings; "
                    "assert settings.STORAGES['default']['BACKEND'] == 'apps.access.storage_backends.LoggedS3Storage'; "
                    "assert settings.AWS_S3_ADDRESSING_STYLE == 'path'; "
                    "assert settings.AWS_QUERYSTRING_EXPIRE == 3600"
                ),
            ],
            check=False,
            capture_output=True,
            env=env,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
