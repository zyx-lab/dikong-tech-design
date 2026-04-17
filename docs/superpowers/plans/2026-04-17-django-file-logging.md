# Django File Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add file-backed, JSON-line logging for Django request lifecycles, exceptions, and upstream calls so a single request can be reconstructed from disk with request, response, and upstream context.

**Architecture:** Keep file output in `config/settings.py` and centralize log record shaping in reusable helpers instead of scattering JSON formatting across the codebase. Log request lifecycle events at the middleware/response/exception seams, and log upstream traffic inside the gateway where method, path, payload, retries, and failures are visible. Preserve existing business envelopes while making `request_id` and `trace_id` the same value so files, headers, and responses can be correlated.

**Tech Stack:** Django 5.1, Django REST Framework, Python stdlib `logging`, `RotatingFileHandler`, `json`, `TemporaryDirectory`, `unittest.mock`, Django test runner.

---

## File map and responsibilities

- Create: `config/logging_config.py`
  - Build the `LOGGING` dict, file handlers, rotation policy, and message-only formatter that writes one JSON object per line.
- Create: `apps/access/request_logging.py`
  - Build and redact request/response/upstream payloads, assemble request context, and emit JSON log records.
- Create: `apps/access/test_logging.py`
  - Hold focused tests for logging config, redaction, request lifecycle logging, and exception logging.
- Create: `apps/dji_bff/test_logging.py`
  - Hold focused tests for gateway request, retry, timeout, and login logging.
- Modify: `config/settings.py`
  - Wire the logging config, read logging env vars, and point the default file output at `BASE_DIR/logs`.
- Modify: `apps/access/middleware.py`
  - Set `request_id`, `trace_id`, request start time, and base request context before the view runs.
- Modify: `apps/api_v1/business_response.py`
  - Log successful responses at finalize time with the response body and total duration.
- Modify: `apps/access/exceptions.py`
  - Log handled and unhandled exceptions with request context and stack traces before returning the existing envelopes.
- Modify: `apps/dji_bff/gateway.py`
  - Log upstream login, refresh, request, retry, timeout, and business-error events with redacted payloads.
- Modify: `.gitignore`
  - Ignore the runtime `logs/` directory so file output does not dirty the worktree.

---

### Task 1: Add logging primitives and file-logging settings

**Files:**
- Create: `config/logging_config.py`
- Create: `apps/access/request_logging.py`
- Create: `apps/access/test_logging.py`
- Modify: `config/settings.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write the failing tests for the logging config and redaction helpers**

Create `apps/access/test_logging.py` with these tests:

```python
from pathlib import Path

from django.test import SimpleTestCase

from apps.access.request_logging import redact_payload, truncate_text
from config.logging_config import build_logging_config


class LoggingConfigTests(SimpleTestCase):
    def test_build_logging_config_should_write_rotating_json_files(self):
        config = build_logging_config(base_dir=Path("/tmp/dikong"))

        self.assertEqual(config["version"], 1)
        self.assertFalse(config["disable_existing_loggers"])
        self.assertEqual(config["formatters"]["json_lines"]["format"], "%(message)s")
        self.assertEqual(config["handlers"]["app_file"]["class"], "logging.handlers.RotatingFileHandler")
        self.assertEqual(config["handlers"]["app_file"]["filename"], "/tmp/dikong/logs/app.log")
        self.assertEqual(config["handlers"]["error_file"]["filename"], "/tmp/dikong/logs/error.log")


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
```

- [ ] **Step 2: Run the focused logging tests to confirm they fail before implementation**

Run:

```bash
.venv/bin/python manage.py test apps.access.test_logging.LoggingConfigTests apps.access.test_logging.RedactionTests -v 2
```

Expected:

- FAIL with `ImportError` for the missing modules, or with assertion failures because `build_logging_config`, `redact_payload`, and `truncate_text` do not exist yet

- [ ] **Step 3: Implement the minimal logging config and payload helpers**

Add `config/logging_config.py` with a pure builder that creates the log directory and returns a `dictConfig` structure using message-only JSON lines:

```python
from pathlib import Path


def build_logging_config(
    *,
    base_dir: Path,
    log_dir: Path | None = None,
    level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 10,
) -> dict:
    resolved_log_dir = Path(log_dir or (base_dir / "logs"))
    resolved_log_dir.mkdir(parents=True, exist_ok=True)
    app_log = resolved_log_dir / "app.log"
    error_log = resolved_log_dir / "error.log"

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json_lines": {
                "format": "%(message)s",
            },
        },
        "handlers": {
            "app_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": level,
                "formatter": "json_lines",
                "filename": str(app_log),
                "maxBytes": max_bytes,
                "backupCount": backup_count,
                "encoding": "utf-8",
            },
            "error_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "ERROR",
                "formatter": "json_lines",
                "filename": str(error_log),
                "maxBytes": max_bytes,
                "backupCount": backup_count,
                "encoding": "utf-8",
            },
        },
        "root": {
            "handlers": ["app_file", "error_file"],
            "level": level,
        },
    }
```

Add `apps/access/request_logging.py` with helpers that:

```python
import json
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "access_token",
    "refresh_token",
    "token",
    "mqtt_password",
}


def truncate_text(value: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."


def redact_payload(value: Any, *, max_text_chars: int = 20_000) -> Any:
    if isinstance(value, Mapping):
        redacted = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in SENSITIVE_KEYS:
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = redact_payload(item, max_text_chars=max_text_chars)
        return redacted
    if isinstance(value, list):
        return [redact_payload(item, max_text_chars=max_text_chars) for item in value]
    if isinstance(value, tuple):
        return [redact_payload(item, max_text_chars=max_text_chars) for item in value]
    if isinstance(value, str):
        return truncate_text(value, max_text_chars)
    return value


def log_json(logger: logging.Logger, level: int, event: str, **payload: Any) -> None:
    record = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "level": logging.getLevelName(level),
        "logger": logger.name,
        "event": event,
        **payload,
    }
    logger.log(level, json.dumps(record, ensure_ascii=False, default=str))
```

Update `config/settings.py` so the canonical runtime defaults live in one place:

```python
from config.logging_config import build_logging_config

DJANGO_LOG_DIR = Path(os.getenv("DJANGO_LOG_DIR", BASE_DIR / "logs"))
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
```

Add `logs/` to `.gitignore` so runtime log files do not pollute the worktree.

- [ ] **Step 4: Re-run the focused logging tests to confirm GREEN**

Run:

```bash
.venv/bin/python manage.py test apps.access.test_logging.LoggingConfigTests apps.access.test_logging.RedactionTests -v 2
```

Expected:

- PASS
- The config test sees `BASE_DIR/logs/app.log` and `BASE_DIR/logs/error.log`
- The redaction test confirms secrets are masked and long text is truncated

- [ ] **Step 5: Commit**

```bash
git add config/logging_config.py apps/access/request_logging.py apps/access/test_logging.py config/settings.py .gitignore
git commit -m "feat(logging): add file logging primitives"
```

### Task 2: Log request lifecycle events and unify request IDs

**Files:**
- Modify: `apps/access/middleware.py`
- Modify: `apps/api_v1/business_response.py`
- Modify: `apps/access/exceptions.py`
- Modify: `apps/access/request_logging.py`
- Modify: `apps/access/test_logging.py`

- [ ] **Step 1: Write integration tests that read the log files after a request completes**

Extend `apps/access/test_logging.py` with temporary test views and file assertions:

```python
import json
import logging
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework.views import APIView

from apps.api_v1.business_response import BusinessApiResponseMixin
from config.logging_config import build_logging_config
from config.urls import urlpatterns as project_urlpatterns

User = get_user_model()


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


urlpatterns = [
    path("api/v1/__tests__/echo-body", EchoBodyView.as_view(), name="test-echo-body"),
    path("api/v1/__tests__/whoami", AuthenticatedEchoView.as_view(), name="test-whoami"),
    path("api/v1/__tests__/broken", BrokenBusinessView.as_view(), name="test-broken"),
] + project_urlpatterns


@override_settings(ROOT_URLCONF="apps.access.test_logging")
class RequestLifecycleLoggingTests(TestCase):
    def setUp(self):
        super().setUp()
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
        )

        self.assertEqual(response.status_code, 200)
        log_lines = self._read_log_lines("app.log")
        finished = next(item for item in reversed(log_lines) if item["event"] == "request_finished")

        self.assertEqual(finished["request"]["method"], "POST")
        self.assertEqual(finished["request"]["path"], "/api/v1/__tests__/echo-body")
        self.assertEqual(finished["request"]["body"]["name"], "demo")
        self.assertEqual(finished["request"]["body"]["password"], "***REDACTED***")
        self.assertEqual(finished["response"]["body"]["data"]["received"]["name"], "demo")
        self.assertEqual(finished["request_id"], response.data["traceId"])
        self.assertGreaterEqual(finished["duration_ms"], 0)

    def test_authenticated_request_should_log_user_context(self):
        user = User.objects.create_user(username="logging_user", password="pass1234", status=1)
        self.client.force_authenticate(user=user)

        response = self.client.get("/api/v1/__tests__/whoami")

        self.assertEqual(response.status_code, 200)
        log_lines = self._read_log_lines("app.log")
        finished = next(item for item in reversed(log_lines) if item["event"] == "request_finished")

        self.assertEqual(finished["context"]["user_id"], user.id)
        self.assertEqual(finished["context"]["username"], "logging_user")

    def test_unhandled_exception_should_write_stack_to_error_log(self):
        response = self.client.get("/api/v1/__tests__/broken")

        self.assertEqual(response.status_code, 500)
        error_lines = self._read_log_lines("error.log")
        failure = next(item for item in reversed(error_lines) if item["event"] == "request_exception")

        self.assertEqual(failure["request"]["path"], "/api/v1/__tests__/broken")
        self.assertEqual(failure["status_code"], 500)
        self.assertEqual(failure["exception"]["type"], "RuntimeError")
        self.assertIn("boom", failure["exception"]["message"])
        self.assertIn("RuntimeError", failure["exception"]["stack"])
```

- [ ] **Step 2: Run the failing lifecycle tests and confirm the current code does not yet produce the file logs**

Run:

```bash
.venv/bin/python manage.py test apps.access.test_logging.RequestLifecycleLoggingTests -v 2
```

Expected:

- FAIL because `request.trace_id` is still inconsistent, the log helper calls do not exist yet, or the log files stay empty

- [ ] **Step 3: Wire request context, response logging, and exception logging into the existing seams**

Update `apps/access/middleware.py` so the request gets one shared identity and a start timestamp:

```python
request_id = request.META.get(self.header_name) or str(uuid.uuid4())
request.request_id = request_id
request.trace_id = request_id
request.log_started_at = time.monotonic()
request.log_context = build_request_context(request)
response = self.get_response(request)
response[self.response_header] = request_id
return response
```

Update `apps/api_v1/business_response.py` so `finalize_response()` emits a completed request event after the response envelope is finalized:

```python
trace_id = getattr(request, "request_id", None) or getattr(request, "trace_id", None)
finalized.data = build_standard_response(getattr(finalized, "data", None), int(finalized.status_code), trace_id=trace_id)
log_json(
    request_logger,
    logging.INFO,
    "request_finished",
    request=build_request_log_payload(request),
    response=build_response_log_payload(finalized),
    context=build_request_context(request),
    request_id=trace_id,
    duration_ms=elapsed_ms,
)
```

Update `apps/access/exceptions.py` so both handled API exceptions and unhandled exceptions are logged with the same request context:

```python
trace_id = getattr(request, "request_id", None) or getattr(request, "trace_id", None)
log_json(
    request_logger,
    logging.ERROR,
    "request_exception",
    request=build_request_log_payload(request),
    exception=build_exception_log_payload(exc),
    status_code=response.status_code if response is not None else 500,
    request_id=trace_id,
    context=build_request_context(request),
)
```

In `apps/access/request_logging.py`, add the payload builders used by those call sites:

```python
def build_request_context(request) -> dict[str, Any]:
    return {
        "request_id": getattr(request, "request_id", None),
        "trace_id": getattr(request, "trace_id", None),
        "method": getattr(request, "method", None),
        "path": getattr(request, "path", None),
        "query_string": getattr(request, "META", {}).get("QUERY_STRING", ""),
        "tenant_code": getattr(request, "tenant_context_code", None),
        "tenant_id": getattr(getattr(request, "tenant_context", None), "id", None),
        "user_id": getattr(getattr(request, "user", None), "id", None),
        "username": getattr(getattr(request, "user", None), "username", None),
        "remote_addr": _resolve_client_ip(request),
        "user_agent": getattr(request, "META", {}).get("HTTP_USER_AGENT", ""),
    }
```

- [ ] **Step 4: Re-run the lifecycle tests to confirm GREEN**

Run:

```bash
.venv/bin/python manage.py test apps.access.test_logging.RequestLifecycleLoggingTests -v 2
```

Expected:

- PASS
- `app.log` contains `request_finished` entries with request, response, context, and duration
- `error.log` contains `request_exception` entries with stack traces
- The emitted `traceId` in the API response matches the logged `request_id`

- [ ] **Step 5: Commit**

```bash
git add apps/access/middleware.py apps/api_v1/business_response.py apps/access/exceptions.py apps/access/request_logging.py apps/access/test_logging.py
git commit -m "feat(logging): log request lifecycle details"
```

### Task 3: Log upstream gateway requests, retries, and failures

**Files:**
- Modify: `apps/dji_bff/gateway.py`
- Create: `apps/dji_bff/test_logging.py`
- Modify: `apps/access/request_logging.py`

- [ ] **Step 1: Write gateway tests that inspect the file output from a fake upstream**

Create `apps/dji_bff/test_logging.py` with a fake HTTP response and file-based assertions:

```python
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from apps.dji_bff.gateway import DjiGateway, DjiGatewayUpstreamError, GatewayResponse
from config.logging_config import build_logging_config


class FakeHTTPResponse:
    def __init__(self, *, status=200, headers=None, body=b'{"code":"00000","data":{"username":"admin"}}'):
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DjiGatewayLoggingTests(TestCase):
    def setUp(self):
        super().setUp()
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
        return [json.loads(line) for line in (self.log_dir / filename).read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_get_current_user_should_log_upstream_request_and_response(self):
        with patch.object(self.gateway, "_ensure_authenticated", return_value=SimpleNamespace(access_token="token-1")):
            with patch("apps.dji_bff.gateway.urlopen", return_value=FakeHTTPResponse()):
                payload = self.gateway.get_current_user()

        self.assertEqual(payload["username"], "admin")
        log_lines = self._read_log_lines("app.log")
        request_event = next(item for item in log_lines if item["event"] == "upstream_request")
        response_event = next(item for item in log_lines if item["event"] == "upstream_response")

        self.assertEqual(request_event["path"], "/api/v1/manage/users/current")
        self.assertEqual(request_event["headers"]["x-auth-token"], "***REDACTED***")
        self.assertEqual(response_event["status_code"], 200)
        self.assertGreaterEqual(response_event["duration_ms"], 0)

    def test_login_session_should_log_redacted_credentials(self):
        with patch.object(self.gateway, "_save_session", return_value=SimpleNamespace(workspace_id="workspace-1", access_token="token-2")):
            with patch("apps.dji_bff.gateway.urlopen", return_value=FakeHTTPResponse()):
                with patch.object(self.gateway, "_configured_credentials", return_value=("adminPC", "adminPC1234567890")):
                    self.gateway._login_session()

        log_lines = self._read_log_lines("app.log")
        login_event = next(item for item in log_lines if item["event"] == "upstream_login_request")
        self.assertEqual(login_event["request"]["body"]["password"], "***REDACTED***")
        self.assertEqual(login_event["request"]["body"]["username"], "adminPC")

    def test_timeout_should_log_upstream_error(self):
        with patch.object(self.gateway, "_ensure_authenticated", return_value=SimpleNamespace(access_token="token-3")):
            with patch("apps.dji_bff.gateway.urlopen", side_effect=TimeoutError("timed out")):
                with self.assertRaises(DjiGatewayUpstreamError):
                    self.gateway.get_current_user()

        error_lines = self._read_log_lines("error.log")
        error_event = next(item for item in error_lines if item["event"] == "upstream_error")
        self.assertEqual(error_event["error"]["type"], "timeout")
        self.assertIn("timed out", error_event["error"]["message"])
```

- [ ] **Step 2: Run the gateway tests and confirm they fail before the logging hooks exist**

Run:

```bash
.venv/bin/python manage.py test apps.dji_bff.test_logging.DjiGatewayLoggingTests -v 2
```

Expected:

- FAIL because upstream events are not emitted yet or the file logs stay empty

- [ ] **Step 3: Instrument the gateway at the call sites that already own the upstream traffic**

Update `apps/dji_bff/gateway.py` so the helper methods log at the natural boundaries instead of wrapping the whole app:

```python
def _log_upstream_event(self, event: str, *, level: int = logging.INFO, **payload):
    log_json(logger, level, event, **payload)


def _login_session(self, *, config: DjiWorkspaceConfig | None = None) -> DjiWorkspaceConfig:
    username, password = self._configured_credentials()
    payload = {"username": username, "password": password, "flag": self._configured_login_flag()}
    self._log_upstream_event(
        "upstream_login_request",
        request={"method": "POST", "path": "/api/v1/manage/login", "body": redact_payload(payload)},
    )
    response = self._request(...)
    self._log_upstream_event(
        "upstream_login_response",
        response={"status_code": response.status_code, "body": redact_payload(response.data)},
    )
    return self._save_session(response.data, config=config)
```

For generic JSON and binary upstream calls, emit `upstream_request`, `upstream_response`, `upstream_retry`, `upstream_http_error`, `upstream_timeout`, and `upstream_unreachable` events around `_request()` and `_request_raw()`.

For auth retry paths, make the retry visible:

```python
except DjiGatewayUpstreamError as exc:
    if not authenticate or not self._is_auth_error(exc):
        raise
    self._log_upstream_event(
        "upstream_retry",
        level=logging.WARNING,
        reason="auth_error",
        method=method,
        path=path,
        attempt=2,
    )
    headers = self._headers(content_type="application/json", auth_token=self._reauthenticate().access_token)
    return self._request(method, path, data=body, headers=headers, follow_redirects=follow_redirects)
```

Keep payload redaction in `apps/access/request_logging.py` so the gateway can reuse the same secret-mask rules as the request lifecycle logs.

- [ ] **Step 4: Re-run the gateway tests to confirm GREEN**

Run:

```bash
.venv/bin/python manage.py test apps.dji_bff.test_logging.DjiGatewayLoggingTests -v 2
```

Expected:

- PASS
- `app.log` contains `upstream_request`, `upstream_response`, `upstream_login_request`, and `upstream_retry` records
- `error.log` contains `upstream_error` records for timeouts and other upstream failures

- [ ] **Step 5: Commit**

```bash
git add apps/dji_bff/gateway.py apps/dji_bff/test_logging.py apps/access/request_logging.py
git commit -m "feat(logging): trace upstream gateway calls"
```

### Task 4: Run the full regression slice and sanity-check the log files

**Files:**
- None

- [ ] **Step 1: Run the focused logging suites together**

Run:

```bash
.venv/bin/python manage.py test apps.access.test_logging apps.dji_bff.test_logging -v 2
```

Expected:

- PASS
- The file log assertions pass end to end for config, request lifecycle, exception handling, and upstream calls

- [ ] **Step 2: Run the broader existing suites that share the touched seams**

Run:

```bash
.venv/bin/python manage.py test apps.access.tests apps.api_v1.tests apps.dji_bff.tests -v 2
```

Expected:

- PASS
- No existing auth, envelope, gateway, or schema behavior regresses

- [ ] **Step 3: Run Django's config sanity check**

Run:

```bash
.venv/bin/python manage.py check
```

Expected:

- `System check identified no issues`

- [ ] **Step 4: Smoke-check that logs land on disk**

Run:

```bash
DJANGO_LOG_DIR=$(mktemp -d) .venv/bin/python manage.py test apps.access.test_logging.RequestLifecycleLoggingTests.test_post_request_should_log_body_response_and_trace_id -v 2
```

Then inspect the temporary directory and confirm:

```bash
ls "$DJANGO_LOG_DIR"
cat "$DJANGO_LOG_DIR/app.log"
```

Expected:

- `app.log` exists
- Each line is valid JSON
- The JSON contains `request_id`, `trace_id`, request, response, and duration fields

No commit for this task. It is validation only.
