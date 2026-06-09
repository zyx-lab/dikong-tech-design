import logging
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from apps.access import request_logging


class RequestLoggingPayloadTests(SimpleTestCase):
    @override_settings(DJANGO_LOG_REDACT_PAYLOADS=False)
    def test_payload_logging_should_keep_sensitive_values_when_redaction_disabled(self):
        payload = {
            "Authorization": "Bearer access-token",
            "password": "plain-password",
            "nested": {"refreshToken": "refresh-token"},
        }

        logged = request_logging.redact_payload(payload)

        self.assertEqual(logged["Authorization"], "Bearer access-token")
        self.assertEqual(logged["password"], "plain-password")
        self.assertEqual(logged["nested"]["refreshToken"], "refresh-token")

    @override_settings(DJANGO_LOG_REDACT_PAYLOADS=True)
    def test_payload_logging_should_redact_sensitive_values_when_enabled(self):
        payload = {
            "Authorization": "Bearer access-token",
            "password": "plain-password",
            "nested": {"refreshToken": "refresh-token"},
        }

        logged = request_logging.redact_payload(payload)

        self.assertEqual(logged["Authorization"], "***REDACTED***")
        self.assertEqual(logged["password"], "***REDACTED***")
        self.assertEqual(logged["nested"]["refreshToken"], "***REDACTED***")

    @override_settings(DJANGO_LOG_REDACT_PAYLOADS=False)
    def test_file_payload_logging_should_store_summary_not_raw_content(self):
        upload = SimpleUploadedFile("route.kmz", b"kmz-binary-content", content_type="application/vnd.google-earth.kmz")

        logged = request_logging.redact_payload({"file": upload})

        self.assertEqual(logged["file"]["type"], "SimpleUploadedFile")
        self.assertEqual(logged["file"]["name"], "route.kmz")
        self.assertEqual(logged["file"]["content_type"], "application/vnd.google-earth.kmz")
        self.assertEqual(logged["file"]["size"], len(b"kmz-binary-content"))
        self.assertEqual(logged["file"]["sha256"], "2d3d1310253b8ecd48cbe47f061d90afa6eb0f487226642cd776771a6b578b2b")
        self.assertNotIn("kmz-binary-content", str(logged))

    @override_settings(DJANGO_LOG_REDACT_PAYLOADS=False)
    def test_file_payload_logging_should_not_consume_unseekable_streams(self):
        class NonSeekableStream:
            name = "route.kmz"
            content_type = "application/vnd.google-earth.kmz"

            def __init__(self):
                self._consumed = False

            def read(self):
                if self._consumed:
                    return b""
                self._consumed = True
                return b"kmz-binary-content"

        upload = NonSeekableStream()

        logged = request_logging.redact_payload({"file": upload})

        self.assertEqual(logged["file"]["type"], "NonSeekableStream")
        self.assertEqual(logged["file"]["name"], "route.kmz")
        self.assertEqual(upload.read(), b"kmz-binary-content")


class ExternalCallLoggingTests(SimpleTestCase):
    @override_settings(DJANGO_LOG_REDACT_PAYLOADS=False)
    def test_external_call_helpers_should_emit_consistent_events(self):
        from apps.access.external_call_logging import (
            log_external_call_failed,
            log_external_call_finished,
            log_external_call_retry,
            log_external_call_started,
        )

        with patch("apps.access.external_call_logging.log_json") as log_json:
            call = log_external_call_started(
                service="dji_cloud",
                operation="login",
                method="POST",
                url="https://dji.example.test/api/v1/manage/login",
                path="/api/v1/manage/login",
                request={"headers": {"x-auth-token": "token"}, "body": {"password": "secret"}},
                attempt=1,
            )
            log_external_call_retry(
                call,
                reason="auth_error",
                error={"type": "auth_error", "message": "expired"},
                attempt=2,
            )
            log_external_call_finished(
                call,
                response={"status_code": 200, "body": {"access_token": "new-token"}},
                attempt=2,
            )
            log_external_call_failed(
                call,
                error=RuntimeError("boom"),
                response={"status_code": 502},
                attempt=2,
            )

        events = [item.args[2] for item in log_json.call_args_list]
        self.assertEqual(
            events,
            ["external_call_started", "external_call_retry", "external_call_finished", "external_call_failed"],
        )
        started_payload = log_json.call_args_list[0].kwargs
        self.assertEqual(started_payload["service"], "dji_cloud")
        self.assertEqual(started_payload["operation"], "login")
        self.assertEqual(started_payload["method"], "POST")
        self.assertEqual(started_payload["attempt"], 1)
        self.assertEqual(started_payload["request"]["body"]["password"], "secret")
        self.assertTrue(started_payload["call_id"])
        for call_args in log_json.call_args_list:
            self.assertEqual(call_args.args[1], logging.INFO if call_args.args[2] != "external_call_failed" else logging.ERROR)
            self.assertEqual(call_args.kwargs["call_id"], started_payload["call_id"])
            self.assertIn("duration_ms", call_args.kwargs)
