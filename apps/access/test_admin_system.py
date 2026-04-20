import tempfile
import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings


User = get_user_model()


class SuperuserAdminAccessTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(username="admin_root", password="pass1234")
        self.staff_user = User.objects.create_user(
            username="admin_staff",
            password="pass1234",
            is_staff=True,
            is_superuser=False,
        )

    def test_admin_index_should_allow_superuser(self):
        self.client.force_login(self.superuser)

        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 200)

    def test_admin_index_should_include_system_logs_entry(self):
        self.client.force_login(self.superuser)

        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/admin/system/logs/")
        self.assertContains(response, "系统日志")

    def test_admin_index_should_reject_non_superuser_staff(self):
        self.client.force_login(self.staff_user)

        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.headers["Location"])


class AdminLogViewerTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(username="admin_logs", password="pass1234")
        self.tempdir = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.tempdir.name)
        structured_line = json.dumps(
            {
                "timestamp": "2026-04-17T14:00:00+08:00",
                "event": "request_finished",
                "level": "INFO",
                "request_id": "req-1",
                "request": {"method": "GET", "path": "/api/v1/health"},
                "response": {"status_code": 200},
            },
            ensure_ascii=False,
        )
        (self.log_dir / "app.log").write_text(f"line-1\n{structured_line}\nline-2\n", encoding="utf-8")
        (self.log_dir / "error.log").write_text("err-1\n", encoding="utf-8")

    def tearDown(self):
        self.tempdir.cleanup()

    @override_settings(DJANGO_LOG_DIR=Path("/tmp/will_be_overridden"))
    def test_admin_logs_view_should_render_app_log_family_without_file_selector(self):
        self.client.force_login(self.superuser)

        with override_settings(DJANGO_LOG_DIR=self.log_dir):
            response = self.client.get("/admin/system/logs/", {"show_details": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "line-1")
        self.assertContains(response, "line-2")
        self.assertContains(response, "日志范围: <code>app.log*</code>")
        self.assertNotContains(response, "id_file")
        self.assertNotContains(response, "日志文件")

    @override_settings(DJANGO_LOG_DIR=Path("/tmp/will_be_overridden"))
    def test_admin_logs_view_should_ignore_file_query_parameter(self):
        self.client.force_login(self.superuser)

        with override_settings(DJANGO_LOG_DIR=self.log_dir):
            response = self.client.get("/admin/system/logs/", {"file": "../secrets.txt", "show_details": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "line-1")
        self.assertContains(response, "line-2")

    @override_settings(DJANGO_LOG_DIR=Path("/tmp/will_be_overridden"))
    def test_admin_logs_view_should_support_keyword_filter(self):
        self.client.force_login(self.superuser)

        with override_settings(DJANGO_LOG_DIR=self.log_dir):
            response = self.client.get("/admin/system/logs/", {"q": "line-2"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "line-2")
        self.assertNotContains(response, "line-1")

    @override_settings(DJANGO_LOG_DIR=Path("/tmp/will_be_overridden"))
    def test_admin_logs_view_should_search_full_file_when_keyword_present(self):
        self.client.force_login(self.superuser)
        lines = [f"line-{index}" for index in range(1, 161)]
        lines[0] = "target-line-1"
        (self.log_dir / "app.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

        with override_settings(DJANGO_LOG_DIR=self.log_dir):
            response = self.client.get("/admin/system/logs/", {"tail": "100", "q": "target-line-1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "target-line-1")

    @override_settings(DJANGO_LOG_DIR=Path("/tmp/will_be_overridden"))
    def test_admin_logs_view_should_list_rotated_log_files(self):
        self.client.force_login(self.superuser)
        (self.log_dir / "app.log.1").write_text("rotated\n", encoding="utf-8")

        with override_settings(DJANGO_LOG_DIR=self.log_dir):
            response = self.client.get("/admin/system/logs/", {})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "app.log.1")

    @override_settings(DJANGO_LOG_DIR=Path("/tmp/will_be_overridden"))
    def test_admin_logs_view_should_render_structured_columns_for_json_log(self):
        self.client.force_login(self.superuser)

        with override_settings(DJANGO_LOG_DIR=self.log_dir):
            response = self.client.get("/admin/system/logs/", {"q": "request_finished", "show_details": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "request_finished")
        self.assertContains(response, "/api/v1/health")
        self.assertContains(response, "req-1")
        self.assertContains(response, "日志明细")

    @override_settings(DJANGO_LOG_DIR=Path("/tmp/will_be_overridden"))
    def test_admin_logs_view_should_group_rows_into_trace_chains(self):
        self.client.force_login(self.superuser)
        chain_lines = [
            json.dumps(
                {
                    "timestamp": "2026-04-17T14:00:00+08:00",
                    "event": "request_started",
                    "level": "INFO",
                    "request_id": "chain-1",
                    "request": {"method": "POST", "path": "/api/v1/drones/1/live/start"},
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "timestamp": "2026-04-17T14:00:01+08:00",
                    "event": "upstream_request",
                    "level": "INFO",
                    "trace_id": "chain-1",
                    "request": {"method": "POST", "path": "/api/v1/manage/live/streams/start"},
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "timestamp": "2026-04-17T14:00:02+08:00",
                    "event": "request_exception",
                    "level": "ERROR",
                    "request_id": "chain-1",
                    "request": {"method": "POST", "path": "/api/v1/drones/1/live/start"},
                    "response": {"status_code": 502},
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "timestamp": "2026-04-17T14:01:00+08:00",
                    "event": "request_finished",
                    "level": "INFO",
                    "request_id": "chain-2",
                    "request": {"method": "GET", "path": "/api/v1/health"},
                    "response": {"status_code": 200},
                },
                ensure_ascii=False,
            ),
        ]
        (self.log_dir / "app.log").write_text("\n".join(chain_lines) + "\n", encoding="utf-8")

        with override_settings(DJANGO_LOG_DIR=self.log_dir):
            response = self.client.get("/admin/system/logs/", {"chain_id": "chain-1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "链路总览")
        self.assertContains(response, "chain-1")
        self.assertContains(response, 'data-chain-id="chain-1"')
        self.assertContains(response, "上游请求")
        self.assertContains(response, "/api/v1/drones/1/live/start")
        self.assertContains(response, "sessionStorage")
        self.assertNotContains(response, "chain-2")

    @override_settings(DJANGO_LOG_DIR=Path("/tmp/will_be_overridden"))
    def test_admin_logs_view_should_stitch_chain_across_rotated_log_family(self):
        self.client.force_login(self.superuser)
        rotated_lines = [
            json.dumps(
                {
                    "timestamp": "2026-04-17T14:00:00+08:00",
                    "event": "request_started",
                    "level": "INFO",
                    "request_id": "chain-rotated",
                    "request": {"method": "GET", "path": "/api/v1/media-files/3/playback-url"},
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "timestamp": "2026-04-17T14:00:01+08:00",
                    "event": "upstream_request",
                    "level": "INFO",
                    "trace_id": "chain-rotated",
                    "request": {
                        "method": "GET",
                        "path": "/api/v1/media/workspaces/mock/files/3/playback-url",
                    },
                },
                ensure_ascii=False,
            ),
        ]
        current_lines = [
            json.dumps(
                {
                    "timestamp": "2026-04-17T14:00:02+08:00",
                    "event": "request_finished",
                    "level": "INFO",
                    "request_id": "chain-rotated",
                    "request": {"method": "GET", "path": "/api/v1/media-files/3/playback-url"},
                    "response": {"status_code": 200},
                },
                ensure_ascii=False,
            )
        ]
        (self.log_dir / "app.log.1").write_text("\n".join(rotated_lines) + "\n", encoding="utf-8")
        (self.log_dir / "app.log").write_text("\n".join(current_lines) + "\n", encoding="utf-8")

        with override_settings(DJANGO_LOG_DIR=self.log_dir):
            response = self.client.get("/admin/system/logs/", {"chain_id": "chain-rotated"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "chain-rotated")
        self.assertContains(response, "3 steps")
        self.assertContains(response, "app.log.1#1")
        self.assertContains(response, "app.log#1")
        self.assertContains(response, "/api/v1/media/workspaces/mock/files/3/playback-url")

    @override_settings(DJANGO_LOG_DIR=Path("/tmp/will_be_overridden"))
    def test_admin_logs_view_should_filter_by_tenant_code_at_chain_level(self):
        self.client.force_login(self.superuser)
        tenant_lines = [
            json.dumps(
                {
                    "timestamp": "2026-04-17T14:00:00+08:00",
                    "event": "request_started",
                    "level": "INFO",
                    "request_id": "chain-tenant-a",
                    "request": {
                        "method": "GET",
                        "path": "/api/v1/media-files/1/playback-url",
                        "headers": {"X-Tenant-Code": "tenant-a"},
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "timestamp": "2026-04-17T14:00:01+08:00",
                    "event": "upstream_request",
                    "level": "INFO",
                    "trace_id": "chain-tenant-a",
                    "request": {"method": "GET", "path": "/api/v1/media/workspaces/a/files/1/playback-url"},
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "timestamp": "2026-04-17T14:00:02+08:00",
                    "event": "request_finished",
                    "level": "INFO",
                    "request_id": "chain-tenant-a",
                    "request": {"method": "GET", "path": "/api/v1/media-files/1/playback-url"},
                    "response": {"status_code": 200},
                    "context": {"tenant_code": "tenant-a"},
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "timestamp": "2026-04-17T14:01:00+08:00",
                    "event": "request_finished",
                    "level": "INFO",
                    "request_id": "chain-tenant-b",
                    "request": {"method": "GET", "path": "/api/v1/media-files/2/playback-url"},
                    "response": {"status_code": 200},
                    "context": {"tenant_code": "tenant-b"},
                },
                ensure_ascii=False,
            ),
        ]
        (self.log_dir / "app.log").write_text("\n".join(tenant_lines) + "\n", encoding="utf-8")

        with override_settings(DJANGO_LOG_DIR=self.log_dir):
            response = self.client.get("/admin/system/logs/", {"tenant_code": "tenant-a"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "chain-tenant-a")
        self.assertContains(response, "租户: tenant-a")
        self.assertContains(response, "/api/v1/media/workspaces/a/files/1/playback-url")
        self.assertNotContains(response, "chain-tenant-b")

    @override_settings(DJANGO_LOG_DIR=Path("/tmp/will_be_overridden"))
    def test_admin_logs_view_should_default_to_summary_only(self):
        self.client.force_login(self.superuser)

        with override_settings(DJANGO_LOG_DIR=self.log_dir):
            response = self.client.get("/admin/system/logs/", {})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "默认只显示链路总览")
        self.assertNotContains(response, "日志明细")
        self.assertNotContains(response, "id_file")
