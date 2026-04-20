from __future__ import annotations

import json
import logging
import logging.config
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import path
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework.views import APIView

from apps.api_v1.business_response import BusinessApiResponseMixin
from apps.access.request_logging import redact_payload, truncate_text
from config.logging_config import build_logging_config
from config.urls import urlpatterns as project_urlpatterns

User = get_user_model()


class LoggingConfigTests(SimpleTestCase):
    def test_build_logging_config_should_write_rotating_json_files(self):
        config = build_logging_config(base_dir=Path("/tmp/dikong"))

        self.assertEqual(config["version"], 1)
        self.assertFalse(config["disable_existing_loggers"])
        self.assertEqual(config["formatters"]["json_lines"]["format"], "%(message)s")
        self.assertEqual(config["handlers"]["app_file"]["class"], "logging.handlers.RotatingFileHandler")
        self.assertEqual(config["handlers"]["app_file"]["filename"], "/tmp/dikong/logs/app.log")
        self.assertEqual(config["handlers"]["error_file"]["filename"], "/tmp/dikong/logs/error.log")
        self.assertEqual(config["handlers"]["sync_file"]["filename"], "/tmp/dikong/logs/sync.log")
        self.assertEqual(config["handlers"]["sync_error_file"]["filename"], "/tmp/dikong/logs/sync.error.log")
        self.assertEqual(config["handlers"]["console"]["class"], "logging.StreamHandler")
        self.assertEqual(config["root"]["handlers"], ["app_file", "error_file", "sync_file", "sync_error_file"])
        self.assertEqual(config["loggers"]["django.server"]["handlers"], ["console"])


class RedactionTests(SimpleTestCase):
    def test_redact_payload_should_mask_secrets_and_keep_shape(self):
        payload = {
            "Authorization": "Bearer abc",
            "password": "pw",
            "nested": {"token": "secret"},
            "query": "x" * 2000,
        }

        redacted = redact_payload(payload, max_text_chars=64)

        self.assertEqual(redacted["Authorization"], "***REDACTED***")
        self.assertEqual(redacted["password"], "***REDACTED***")
        self.assertEqual(redacted["nested"]["token"], "***REDACTED***")
        self.assertLessEqual(len(redacted["query"]), 64)
        self.assertEqual(truncate_text("short text", 64), "short text")


class EchoBodyView(BusinessApiResponseMixin, APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        return Response({"received": request.data})


class AuthenticatedEchoView(BusinessApiResponseMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"userId": request.user.id, "username": request.user.username})


class BrokenBusinessView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        raise RuntimeError("boom")


class PlainBusinessView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"plain": True})


urlpatterns = [
    path("api/v1/__tests__/echo-body", EchoBodyView.as_view(), name="test-echo-body"),
    path("api/v1/__tests__/whoami", AuthenticatedEchoView.as_view(), name="test-whoami"),
    path("api/v1/__tests__/broken", BrokenBusinessView.as_view(), name="test-broken"),
    path("api/v1/__tests__/plain", PlainBusinessView.as_view(), name="test-plain"),
] + project_urlpatterns


@override_settings(ROOT_URLCONF="apps.access.test_logging")
class RequestLifecycleLoggingTests(TestCase):
    def setUp(self):
        super().setUp()
        logging.shutdown()
        self.tempdir = TemporaryDirectory()
        self.log_dir = Path(self.tempdir.name) / "logs"
        logging.config.dictConfig(
            build_logging_config(
                base_dir=Path(self.tempdir.name),
                log_dir=self.log_dir,
            )
        )
        self.client = APIClient()

    def tearDown(self):
        logging.shutdown()
        self.tempdir.cleanup()
        super().tearDown()

    def _read_log_lines(self, filename: str):
        path = self.log_dir / filename
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_post_request_should_log_body_response_and_trace_id(self):
        response = self.client.post(
            "/api/v1/__tests__/echo-body",
            {"name": "demo", "password": "secret"},
            format="json",
            HTTP_X_REQUEST_ID="req-echo-1",
        )

        self.assertEqual(response.status_code, 200)
        log_lines = self._read_log_lines("app.log")
        finished = next(item for item in reversed(log_lines) if item["event"] == "request_finished")

        self.assertEqual(finished["request_id"], "req-echo-1")
        self.assertEqual(finished["trace_id"], "req-echo-1")
        self.assertEqual(finished["request"]["method"], "POST")
        self.assertEqual(finished["request"]["path"], "/api/v1/__tests__/echo-body")
        self.assertEqual(finished["request"]["body"]["name"], "demo")
        self.assertEqual(finished["request"]["body"]["password"], "***REDACTED***")
        self.assertEqual(finished["response"]["body"]["data"]["received"]["name"], "demo")
        self.assertEqual(finished["request_id"], response.data["traceId"])
        self.assertGreaterEqual(finished["duration_ms"], 0)

    def test_request_should_log_request_started_event(self):
        response = self.client.get("/api/v1/__tests__/plain", HTTP_X_REQUEST_ID="req-started-1")

        self.assertEqual(response.status_code, 200)
        log_lines = self._read_log_lines("app.log")
        started = next(item for item in reversed(log_lines) if item["event"] == "request_started")
        self.assertEqual(started["request_id"], "req-started-1")
        self.assertEqual(started["request"]["method"], "GET")
        self.assertEqual(started["request"]["path"], "/api/v1/__tests__/plain")

    def test_plain_api_view_should_log_request_finished_via_middleware_fallback(self):
        response = self.client.get("/api/v1/__tests__/plain", HTTP_X_REQUEST_ID="req-plain-1")

        self.assertEqual(response.status_code, 200)
        log_lines = self._read_log_lines("app.log")
        finished = next(
            item
            for item in reversed(log_lines)
            if item["event"] == "request_finished" and item["request"]["path"] == "/api/v1/__tests__/plain"
        )
        self.assertEqual(finished["request_id"], "req-plain-1")
        self.assertEqual(finished["status_code"], 200)

    def test_business_api_response_mixin_should_not_duplicate_request_finished(self):
        response = self.client.post(
            "/api/v1/__tests__/echo-body",
            {"name": "demo"},
            format="json",
            HTTP_X_REQUEST_ID="req-finished-once-1",
        )

        self.assertEqual(response.status_code, 200)
        log_lines = self._read_log_lines("app.log")
        finished_events = [
            item
            for item in log_lines
            if item["event"] == "request_finished" and item["request"]["path"] == "/api/v1/__tests__/echo-body"
        ]
        self.assertEqual(len(finished_events), 1)

    def test_authenticated_request_should_log_user_context(self):
        user = User.objects.create_user(username="logging_user", password="pass1234", status=1)
        self.client.force_authenticate(user=user)

        response = self.client.get("/api/v1/__tests__/whoami", HTTP_X_REQUEST_ID="req-user-1")

        self.assertEqual(response.status_code, 200)
        log_lines = self._read_log_lines("app.log")
        finished = next(item for item in reversed(log_lines) if item["event"] == "request_finished")

        self.assertEqual(finished["request_id"], "req-user-1")
        self.assertEqual(finished["context"]["request_id"], "req-user-1")
        self.assertEqual(finished["context"]["trace_id"], "req-user-1")
        self.assertEqual(finished["context"]["user_id"], user.id)
        self.assertEqual(finished["context"]["username"], "logging_user")

    def test_unhandled_exception_should_write_stack_to_error_log(self):
        response = self.client.get("/api/v1/__tests__/broken", HTTP_X_REQUEST_ID="req-broken-1")

        self.assertEqual(response.status_code, 500)
        error_lines = self._read_log_lines("error.log")
        failure = next(item for item in reversed(error_lines) if item["event"] == "request_exception")

        self.assertEqual(failure["request_id"], "req-broken-1")
        self.assertEqual(failure["trace_id"], "req-broken-1")
        self.assertEqual(failure["request"]["path"], "/api/v1/__tests__/broken")
        self.assertEqual(failure["status_code"], 500)
        self.assertEqual(failure["exception"]["type"], "RuntimeError")
        self.assertIn("boom", failure["exception"]["message"])
        self.assertIn("RuntimeError", failure["exception"]["stack"])
