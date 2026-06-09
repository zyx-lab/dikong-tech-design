from io import BytesIO
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings


class LoggedS3StorageTests(SimpleTestCase):
    @override_settings(
        AWS_STORAGE_BUCKET_NAME="dikong-route-covers",
        AWS_S3_ENDPOINT_URL="https://minio.example.test",
        DJANGO_LOG_REDACT_PAYLOADS=False,
    )
    def test_logged_s3_storage_should_log_save_success_with_file_summary(self):
        from apps.access.storage_backends import LoggedS3Storage

        storage = LoggedS3Storage()
        content = BytesIO(b"cover-image-bytes")
        content.name = "cover.png"
        content.content_type = "image/png"

        with patch("storages.backends.s3.S3Storage._save", return_value="inspection/routes/covers/cover.png"), patch(
            "apps.access.external_call_logging.log_json"
        ) as log_json:
            saved_name = storage._save("inspection/routes/covers/cover.png", content)

        self.assertEqual(saved_name, "inspection/routes/covers/cover.png")
        events = [item.args[2] for item in log_json.call_args_list]
        self.assertEqual(events, ["external_call_started", "external_call_finished"])
        started = log_json.call_args_list[0].kwargs
        self.assertEqual(started["service"], "object_storage")
        self.assertEqual(started["operation"], "save")
        self.assertEqual(started["request"]["bucket"], "dikong-route-covers")
        self.assertEqual(started["request"]["key"], "inspection/routes/covers/cover.png")
        self.assertEqual(started["request"]["content"]["sha256"], "d5b7d440f2fb1afe2020ddccc8e56b6eb26c8176e72ee2a9897d287cb84da6b8")
        self.assertNotIn("cover-image-bytes", str(started))

    @override_settings(
        AWS_STORAGE_BUCKET_NAME="dikong-route-covers",
        AWS_S3_ENDPOINT_URL="https://minio.example.test",
    )
    def test_logged_s3_storage_should_log_delete_failure(self):
        from apps.access.storage_backends import LoggedS3Storage

        storage = LoggedS3Storage()

        with patch("storages.backends.s3.S3Storage.delete", side_effect=RuntimeError("storage down")), patch(
            "apps.access.external_call_logging.log_json"
        ) as log_json:
            with self.assertRaises(RuntimeError):
                storage.delete("inspection/routes/covers/missing.png")

        events = [item.args[2] for item in log_json.call_args_list]
        self.assertEqual(events, ["external_call_started", "external_call_failed"])
        failed = log_json.call_args_list[1].kwargs
        self.assertEqual(failed["service"], "object_storage")
        self.assertEqual(failed["operation"], "delete")
        self.assertEqual(failed["error"]["type"], "RuntimeError")
        self.assertEqual(failed["error"]["message"], "storage down")
