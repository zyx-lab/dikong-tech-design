from __future__ import annotations

import json
import logging
import logging.config
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from apps.dji_bff.gateway import DjiGateway, DjiGatewayUpstreamError, GatewayResponse
from apps.access.request_logging import sync_log_context
from config.logging_config import build_logging_config


class DjiGatewayLoggingTests(TestCase):
    def setUp(self):
        super().setUp()
        logging.shutdown()
        self.tempdir = TemporaryDirectory()
        self.log_dir = Path(self.tempdir.name) / "logs"
        logging.config.dictConfig(
            build_logging_config(base_dir=Path(self.tempdir.name), log_dir=self.log_dir)
        )
        self.gateway = DjiGateway(base_url="http://mock-dji")

    def tearDown(self):
        logging.shutdown()
        self.tempdir.cleanup()
        super().tearDown()

    def _read_log_lines(self, filename: str):
        path = self.log_dir / filename
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_get_current_user_should_log_upstream_request_and_response(self):
        with patch.object(self.gateway, "_ensure_authenticated", return_value=SimpleNamespace(access_token="token-1")):
            with patch(
                "apps.dji_bff.gateway.DjiGateway._request",
                return_value=GatewayResponse(
                    status_code=200,
                    headers={"Content-Type": "application/json"},
                    data={"username": "admin"},
                ),
            ):
                payload = self.gateway.get_current_user()

        self.assertEqual(payload["username"], "admin")
        log_lines = self._read_log_lines("app.log")
        request_event = next(item for item in log_lines if item["event"] == "upstream_request")
        response_event = next(item for item in log_lines if item["event"] == "upstream_response")

        self.assertEqual(request_event["request"]["method"], "GET")
        self.assertEqual(request_event["request"]["path"], "/api/v1/manage/users/current")
        self.assertEqual(request_event["request"]["headers"]["x-auth-token"], "***REDACTED***")
        self.assertEqual(response_event["response"]["status_code"], 200)
        self.assertEqual(response_event["response"]["body"]["username"], "admin")
        self.assertGreaterEqual(response_event["duration_ms"], 0)

    def test_sync_context_should_route_upstream_request_and_response_to_sync_log(self):
        with sync_log_context("sync-run-1"):
            with patch.object(self.gateway, "_ensure_authenticated", return_value=SimpleNamespace(access_token="token-1")):
                with patch(
                    "apps.dji_bff.gateway.DjiGateway._request",
                    return_value=GatewayResponse(
                        status_code=200,
                        headers={"Content-Type": "application/json"},
                        data={"username": "admin"},
                    ),
                ):
                    payload = self.gateway.get_current_user()

        self.assertEqual(payload["username"], "admin")
        self.assertTrue((self.log_dir / "sync.log").exists())
        sync_lines = self._read_log_lines("sync.log")
        request_event = next(item for item in sync_lines if item["event"] == "upstream_request")
        response_event = next(item for item in sync_lines if item["event"] == "upstream_response")

        self.assertEqual(request_event["sync_run_id"], "sync-run-1")
        self.assertEqual(response_event["sync_run_id"], "sync-run-1")
        self.assertEqual(response_event["response"]["body"]["username"], "admin")
        self.assertFalse((self.log_dir / "app.log").read_text(encoding="utf-8").strip())

    def test_login_session_should_log_redacted_credentials(self):
        with patch.object(self.gateway, "_configured_credentials", return_value=("adminPC", "adminPC1234567890")):
            with patch(
                "apps.dji_bff.gateway.DjiGateway._request",
                return_value=GatewayResponse(
                    status_code=200,
                    headers={"Content-Type": "application/json"},
                    data={
                        "workspace_id": "workspace-1",
                        "access_token": "token-2",
                        "mqtt_password": "mqtt-secret",
                    },
                ),
            ):
                with patch.object(
                    self.gateway,
                    "_save_session",
                    return_value=SimpleNamespace(workspace_id="workspace-1", access_token="token-2"),
                ):
                    self.gateway._login_session()

        log_lines = self._read_log_lines("app.log")
        request_event = next(item for item in log_lines if item["event"] == "upstream_login_request")
        response_event = next(item for item in log_lines if item["event"] == "upstream_login_response")

        self.assertEqual(request_event["request"]["path"], "/api/v1/manage/login")
        self.assertEqual(request_event["request"]["body"]["username"], "adminPC")
        self.assertEqual(request_event["request"]["body"]["password"], "***REDACTED***")
        self.assertEqual(response_event["response"]["body"]["access_token"], "***REDACTED***")
        self.assertEqual(response_event["response"]["body"]["mqtt_password"], "***REDACTED***")

    def test_timeout_should_log_upstream_error(self):
        with patch.object(self.gateway, "_ensure_authenticated", return_value=SimpleNamespace(access_token="token-3")):
            with patch(
                "apps.dji_bff.gateway.DjiGateway._request",
                side_effect=DjiGatewayUpstreamError("DJI upstream timed out", status_code=502),
            ):
                with self.assertRaises(DjiGatewayUpstreamError):
                    self.gateway.get_current_user()

        error_lines = self._read_log_lines("error.log")
        error_event = next(item for item in error_lines if item["event"] == "upstream_error")

        self.assertEqual(error_event["error"]["type"], "timeout")
        self.assertIn("timed out", error_event["error"]["message"])
        self.assertEqual(error_event["request"]["path"], "/api/v1/manage/users/current")

    def test_sync_context_should_route_upstream_errors_to_sync_error_log(self):
        with sync_log_context("sync-run-2"):
            with patch.object(self.gateway, "_ensure_authenticated", return_value=SimpleNamespace(access_token="token-3")):
                with patch(
                    "apps.dji_bff.gateway.DjiGateway._request",
                    side_effect=DjiGatewayUpstreamError("DJI upstream timed out", status_code=502),
                ):
                    with self.assertRaises(DjiGatewayUpstreamError):
                        self.gateway.get_current_user()

        self.assertTrue((self.log_dir / "sync.error.log").exists())
        error_lines = self._read_log_lines("sync.error.log")
        error_event = next(item for item in error_lines if item["event"] == "upstream_error")

        self.assertEqual(error_event["sync_run_id"], "sync-run-2")
        self.assertEqual(error_event["error"]["type"], "timeout")
        self.assertEqual(error_event["request"]["path"], "/api/v1/manage/users/current")

    def test_auth_retry_should_log_retry_event(self):
        with patch.object(self.gateway, "_ensure_authenticated", return_value=SimpleNamespace(access_token="stale-token")):
            with patch.object(self.gateway, "_reauthenticate", return_value=SimpleNamespace(access_token="fresh-token")):
                with patch(
                    "apps.dji_bff.gateway.DjiGateway._request",
                    side_effect=[
                        DjiGatewayUpstreamError("DJI upstream request failed", status_code=401),
                        GatewayResponse(
                            status_code=200,
                            headers={"Content-Type": "application/json"},
                            data={"username": "admin"},
                        ),
                    ],
                ):
                    payload = self.gateway.get_current_user()

        self.assertEqual(payload["username"], "admin")
        log_lines = self._read_log_lines("app.log")
        retry_event = next(item for item in log_lines if item["event"] == "upstream_retry")
        response_event = next(item for item in log_lines if item["event"] == "upstream_response")

        self.assertEqual(retry_event["reason"], "auth_error")
        self.assertEqual(retry_event["attempt"], 2)
        self.assertEqual(response_event["request"]["attempt"], 2)
