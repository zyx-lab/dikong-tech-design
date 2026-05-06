import tempfile
import json
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.access.test_support import ensure_tenant_role_binding
from apps.dji_bff.models import SyncStatus, TenantMediaIndex
from apps.media_file.models import MediaFile, MediaType
from apps.mission.models import Mission

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
        sync_request_line = json.dumps(
            {
                "timestamp": "2026-04-17T14:10:00+08:00",
                "event": "upstream_request",
                "level": "INFO",
                "sync_run_id": "sync-run-1",
                "request": {"method": "GET", "path": "/api/v1/manage/users/current"},
            },
            ensure_ascii=False,
        )
        sync_response_line = json.dumps(
            {
                "timestamp": "2026-04-17T14:10:01+08:00",
                "event": "upstream_response",
                "level": "INFO",
                "sync_run_id": "sync-run-1",
                "request": {"method": "GET", "path": "/api/v1/manage/users/current"},
                "response": {"status_code": 200},
            },
            ensure_ascii=False,
        )
        sync_error_line = json.dumps(
            {
                "timestamp": "2026-04-17T14:10:02+08:00",
                "event": "upstream_error",
                "level": "ERROR",
                "sync_run_id": "sync-run-1",
                "request": {"method": "GET", "path": "/api/v1/manage/users/current"},
                "error": {"type": "timeout", "message": "sync timeout"},
            },
            ensure_ascii=False,
        )
        (self.log_dir / "sync.log").write_text(f"{sync_request_line}\n{sync_response_line}\n", encoding="utf-8")
        (self.log_dir / "sync.error.log").write_text(f"{sync_error_line}\n", encoding="utf-8")

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
        self.assertContains(response, "日志范围: <code>请求日志</code>")
        self.assertNotContains(response, "id_file")
        self.assertNotContains(response, "日志文件")
        self.assertNotContains(response, "err-1")

    @override_settings(DJANGO_LOG_DIR=Path("/tmp/will_be_overridden"))
    def test_admin_logs_view_should_ignore_file_query_parameter(self):
        self.client.force_login(self.superuser)

        with override_settings(DJANGO_LOG_DIR=self.log_dir):
            response = self.client.get("/admin/system/logs/", {"file": "../secrets.txt", "show_details": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "line-1")
        self.assertContains(response, "line-2")

    @override_settings(DJANGO_LOG_DIR=Path("/tmp/will_be_overridden"))
    def test_admin_logs_view_should_render_sync_scope_from_sync_file_family(self):
        self.client.force_login(self.superuser)

        with override_settings(DJANGO_LOG_DIR=self.log_dir):
            response = self.client.get("/admin/system/logs/", {"scope": "sync", "show_details": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "日志范围: <code>同步日志</code>")
        self.assertContains(response, "sync-run-1")
        self.assertContains(response, 'data-chain-id="sync-run-1"')
        self.assertContains(response, "3 steps")
        self.assertContains(response, "上游请求")
        self.assertContains(response, "上游响应")
        self.assertContains(response, "上游异常")
        self.assertNotContains(response, "line-1")
        self.assertNotContains(response, "err-1")

    @override_settings(DJANGO_LOG_DIR=Path("/tmp/will_be_overridden"))
    def test_admin_logs_view_should_include_upstream_business_error_code_and_message_in_summary(self):
        self.client.force_login(self.superuser)
        sync_error_line = json.dumps(
            {
                "timestamp": "2026-05-06T04:22:51+00:00",
                "event": "upstream_error",
                "level": "ERROR",
                "sync_run_id": "sync-run-2",
                "request": {
                    "method": "GET",
                    "path": "/api/v1/media/workspaces/ws-1/files/file-1/preview-url",
                },
                "error": {
                    "type": "business_error",
                    "message": "DJI upstream business error",
                    "status_code": 200,
                    "data": {
                        "code": "M400404",
                        "msg": "media file not found",
                    },
                },
            },
            ensure_ascii=False,
        )
        (self.log_dir / "sync.error.log").write_text(f"{sync_error_line}\n", encoding="utf-8")

        with override_settings(DJANGO_LOG_DIR=self.log_dir):
            response = self.client.get("/admin/system/logs/", {"scope": "sync", "show_details": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "business_error: DJI upstream business error (M400404: media file not found)")

    @override_settings(DJANGO_LOG_DIR=Path("/tmp/will_be_overridden"))
    def test_admin_logs_view_should_render_error_scope_from_error_family(self):
        self.client.force_login(self.superuser)

        with override_settings(DJANGO_LOG_DIR=self.log_dir):
            response = self.client.get("/admin/system/logs/", {"scope": "error", "show_details": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "日志范围: <code>错误日志</code>")
        self.assertContains(response, "err-1")
        self.assertNotContains(response, "line-1")
        self.assertNotContains(response, "sync-run-1")

    @override_settings(DJANGO_LOG_DIR=Path("/tmp/will_be_overridden"))
    def test_admin_logs_view_should_group_multiline_traceback_error_block(self):
        self.client.force_login(self.superuser)
        traceback_lines = [
            "Traceback (most recent call last):",
            '  File "/www/wwwroot/dikong-tech-design/.venv/lib/python3.13/site-packages/django/middleware/common.py", line 48, in process_request',
            "    host = request.get_host()",
            '  File "/www/wwwroot/dikong-tech-design/.venv/lib/python3.13/site-packages/django/http/request.py", line 151, in get_host',
            "    raise DisallowedHost(msg)",
            "django.core.exceptions.DisallowedHost: Invalid HTTP_HOST header: '110.42.32.122:8000'. You may need to add '110.42.32.122' to ALLOWED_HOSTS.",
        ]
        (self.log_dir / "error.log").write_text("\n".join(traceback_lines) + "\n", encoding="utf-8")

        with override_settings(DJANGO_LOG_DIR=self.log_dir):
            response = self.client.get("/admin/system/logs/", {"scope": "error", "show_details": "1"})

        self.assertEqual(response.status_code, 200)
        rows = response.context["rows"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["is_multiline"])
        self.assertEqual(rows[0]["line_no"], 1)
        self.assertEqual(rows[0]["line_no_end"], 6)
        self.assertIn("DisallowedHost", rows[0]["summary"])
        self.assertIn("Traceback (most recent call last):", rows[0]["raw"])
        self.assertContains(response, "error.log#1-6")

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
    def test_admin_logs_view_should_include_rotated_request_log_content_without_file_names(self):
        self.client.force_login(self.superuser)
        (self.log_dir / "app.log.1").write_text("rotated\n", encoding="utf-8")

        with override_settings(DJANGO_LOG_DIR=self.log_dir):
            response = self.client.get("/admin/system/logs/", {"show_details": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "rotated")

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


class AdminSyncedMediaTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(username="admin_media", password="pass1234")
        self.pilot_user = User.objects.create_user(username="media_pilot", password="pass1234")
        self.tenant, self.pilot_member, _ = ensure_tenant_role_binding(
            self.pilot_user,
            tenant_code="admin_media_tenant",
            role_code="pilot_operator",
            role_name="飞手",
            display_name="飞手张三",
            member_no="P-001",
        )
        self.mission = Mission.objects.create(
            tenant=self.tenant,
            name="巡检任务A",
            pilot=self.pilot_member,
            pilot_name="飞手张三",
        )
        self.captured_at = timezone.now()
        self.bound_media = MediaFile.objects.create(
            tenant=self.tenant,
            mission=self.mission,
            device_sn="SN-001",
            media_type=MediaType.VIDEO,
            file_name="bound-video.mp4",
            file_url="https://example.com/media/bound-video.mp4",
            captured_at=self.captured_at,
            file_size=1024,
        )
        TenantMediaIndex.objects.create(
            tenant=self.tenant,
            media_file=self.bound_media,
            dji_file_id="DJI-FILE-001",
            device_sn="SN-001",
            mission=self.mission,
            sync_status=SyncStatus.SYNCED,
            last_sync_at=self.captured_at,
        )
        self.unbound_media = MediaFile.objects.create(
            tenant=self.tenant,
            device_sn="SN-002",
            media_type=MediaType.PHOTO,
            file_name="unbound-photo.jpg",
            file_url="https://example.com/media/unbound-photo.jpg",
            captured_at=self.captured_at,
            file_size=256,
        )
        TenantMediaIndex.objects.create(
            tenant=self.tenant,
            media_file=self.unbound_media,
            dji_file_id="DJI-FILE-002",
            device_sn="SN-002",
            sync_status=SyncStatus.PENDING,
            last_sync_at=self.captured_at,
        )

    def test_admin_index_should_include_media_and_mission_entries(self):
        self.client.force_login(self.superuser)

        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/admin/media_file/mediafile/")
        self.assertContains(response, "/admin/mission/mission/")

    def test_media_file_admin_changelist_should_show_sync_metadata_and_bound_mission(self):
        self.client.force_login(self.superuser)

        response = self.client.get("/admin/media_file/mediafile/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "bound-video.mp4")
        self.assertContains(response, "unbound-photo.jpg")
        self.assertContains(response, "DJI-FILE-001")
        self.assertContains(response, "DJI-FILE-002")
        self.assertContains(response, "同步成功")
        self.assertContains(response, "同步中")
        self.assertContains(response, "巡检任务A")
        self.assertContains(response, f"/admin/mission/mission/{self.mission.id}/change/")

    def test_media_file_admin_change_form_should_hide_mission_when_media_is_unbound(self):
        self.client.force_login(self.superuser)

        bound_response = self.client.get(f"/admin/media_file/mediafile/{self.bound_media.id}/change/")
        unbound_response = self.client.get(f"/admin/media_file/mediafile/{self.unbound_media.id}/change/")

        self.assertEqual(bound_response.status_code, 200)
        self.assertContains(bound_response, "关联任务")
        self.assertContains(bound_response, "巡检任务A")
        self.assertEqual(unbound_response.status_code, 200)
        self.assertNotContains(unbound_response, "关联任务")

    def test_media_file_admin_changelist_should_render_preview_buttons_and_modal(self):
        self.client.force_login(self.superuser)

        response = self.client.get("/admin/media_file/mediafile/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'/admin/media_file/mediafile/{self.bound_media.id}/preview/')
        self.assertContains(response, f'/admin/media_file/mediafile/{self.unbound_media.id}/preview/')
        self.assertContains(response, 'id="media-preview-modal"', html=False)

    def test_media_file_admin_preview_endpoint_should_return_preview_payload_for_photo_and_video(self):
        self.client.force_login(self.superuser)

        with (
            patch(
                "apps.access.admin.DjiGateway.get_media_playback_url",
                return_value="https://playback.example/bound-video.m3u8",
            ) as get_media_playback_url,
            patch(
                "apps.access.admin.DjiGateway.get_media_preview_url",
                return_value="https://preview.example/unbound-photo.jpg",
            ) as get_media_preview_url,
        ):
            video_response = self.client.get(f"/admin/media_file/mediafile/{self.bound_media.id}/preview/")
            photo_response = self.client.get(f"/admin/media_file/mediafile/{self.unbound_media.id}/preview/")

        get_media_playback_url.assert_called_once_with("DJI-FILE-001")
        get_media_preview_url.assert_called_once_with("DJI-FILE-002")

        self.assertEqual(video_response.status_code, 200)
        self.assertEqual(
            video_response.json(),
            {
                "media_type": "video",
                "file_name": "bound-video.mp4",
                "url": "https://playback.example/bound-video.m3u8",
            },
        )
        self.assertEqual(photo_response.status_code, 200)
        self.assertEqual(
            photo_response.json(),
            {
                "media_type": "photo",
                "file_name": "unbound-photo.jpg",
                "url": "https://preview.example/unbound-photo.jpg",
            },
        )
