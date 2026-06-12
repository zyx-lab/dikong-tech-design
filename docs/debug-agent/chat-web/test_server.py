import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server


class ChatWebServerTests(unittest.TestCase):
    def test_load_config_reads_hermes_api_server_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            project_root.mkdir()
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "API_SERVER_KEY=test-key",
                        "API_SERVER_HOST=127.0.0.1",
                        "API_SERVER_PORT=9999",
                        "CHAT_WEB_HOST=0.0.0.0",
                        "CHAT_WEB_PORT=7861",
                        f"DEBUG_AGENT_PROJECT_ROOT={project_root}",
                    ]
                )
                + "\n"
            )

            config = server.load_config(env_path, allowed_project_root=project_root)

        self.assertEqual(config.api_key, "test-key")
        self.assertEqual(config.hermes_url, "http://127.0.0.1:9999/v1/chat/completions")
        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 7861)
        self.assertEqual(config.project_root, project_root.resolve())

    def test_load_config_rejects_project_root_outside_dikong_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside"
            outside.mkdir()
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "API_SERVER_KEY=test-key",
                        f"DEBUG_AGENT_PROJECT_ROOT={outside}",
                    ]
                )
                + "\n"
            )

            with self.assertRaises(ValueError):
                server.load_config(env_path)

    def test_session_store_creates_and_persists_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            store = server.SessionStore(path)

            session_id = store.create_session("route upload")
            store.append_message(session_id, "user", "POST /api/v2/inspection/routes 返回 400")
            store.append_message(session_id, "assistant", "需要 response body")

            reloaded = server.SessionStore(path)

        self.assertEqual(reloaded.list_sessions(), [(session_id, "route upload")])
        self.assertEqual(
            reloaded.get_messages(session_id),
            [
                {"role": "user", "content": "POST /api/v2/inspection/routes 返回 400"},
                {"role": "assistant", "content": "需要 response body"},
            ],
        )

    def test_session_store_renames_session_and_persists_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            store = server.SessionStore(path)
            session_id = store.create_session("route upload")

            renamed = store.rename_session(session_id, "  机场航线上传  \n第二行忽略")
            reloaded = server.SessionStore(path)

        self.assertTrue(renamed)
        self.assertEqual(reloaded.list_sessions(), [(session_id, "机场航线上传")])

    def test_session_store_ignores_empty_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            store = server.SessionStore(path)
            session_id = store.create_session("route upload")

            renamed = store.rename_session(session_id, "   \n  ")

        self.assertFalse(renamed)
        self.assertEqual(store.list_sessions(), [(session_id, "route upload")])

    def test_session_store_ignores_unknown_session_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = server.SessionStore(Path(tmp) / "sessions.json")

            renamed = store.rename_session("missing", "name")

        self.assertFalse(renamed)
        self.assertEqual(store.list_sessions(), [])

    def test_session_store_default_title_is_specific(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = server.SessionStore(Path(tmp) / "sessions.json")

            session_id = store.create_session()
            [(listed_session_id, title)] = store.list_sessions()

        self.assertEqual(listed_session_id, session_id)
        self.assertNotEqual(title, "新会话")
        self.assertRegex(title, r"^调试 \d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_new_session_uses_specific_default_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = server.SessionStore(Path(tmp) / "sessions.json")

            _, session_id, choices = server.new_session(store)

        self.assertEqual(choices[0][0], session_id)
        self.assertNotEqual(choices[0][1], "新会话")
        self.assertRegex(choices[0][1], r"^调试 \d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_legacy_new_session_title_displays_as_specific_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            path.write_text(
                json.dumps(
                    {
                        "sessions": {
                            "session-1": {
                                "title": "新会话",
                                "messages": [],
                                "created_at": "2026-06-12T06:30:45+00:00",
                                "updated_at": "2026-06-12T06:30:45+00:00",
                            }
                        }
                    }
                )
            )
            store = server.SessionStore(path)

            sessions = store.list_sessions()

        self.assertEqual(sessions, [("session-1", "调试 06-12 14:30:45")])

    def test_build_payload_adds_system_prompt_and_project_boundary(self):
        payload = server.build_hermes_payload(
            [
                {"role": "user", "content": "POST /api/v2/foo 返回 403"},
                {"role": "assistant", "content": "需要 traceId"},
            ],
            project_root=Path("/home/charles/dikong-tech-design"),
            project_context="Context from project files only.",
        )

        self.assertEqual(payload["model"], "hermes-agent")
        self.assertEqual(payload["messages"][0]["role"], "system")
        system_prompt = payload["messages"][0]["content"]
        self.assertIn("Dikong project debug assistant", system_prompt)
        self.assertIn("Project root: /home/charles/dikong-tech-design", system_prompt)
        self.assertIn("Only answer questions about this repository", system_prompt)
        self.assertIn("Do not modify files", system_prompt)
        self.assertIn("Do not provide fallback implementation advice", system_prompt)
        self.assertIn("dikong_code_search", system_prompt)
        self.assertIn("dikong_code_read", system_prompt)
        self.assertIn("dikong_graph_query", system_prompt)
        self.assertIn("scope='source'", system_prompt)
        self.assertIn("dikong_log_search", system_prompt)
        self.assertIn("dikong_log_read", system_prompt)
        self.assertIn("dikong_file_list", system_prompt)
        self.assertIn("dikong_file_read", system_prompt)
        self.assertIn("dikong_bash", system_prompt)
        self.assertIn("dikong_docker_logs", system_prompt)
        self.assertIn("dikong_http_request", system_prompt)
        self.assertIn("dikong_authenticated_http_request", system_prompt)
        self.assertIn("dikong_deployment_scan", system_prompt)
        self.assertIn("http://127.0.0.1:8000/api/v2/docs/", system_prompt)
        self.assertIn("/home/charles/DJI-Cloud-API-Demo", system_prompt)
        self.assertIn("DJI Cloud API Demo", system_prompt)
        self.assertIn("project_root", system_prompt)
        self.assertIn("not alternatives", system_prompt)
        self.assertIn("Search and compare them together", system_prompt)
        self.assertIn("root-qualified paths", system_prompt)
        self.assertIn("one-use", system_prompt)
        self.assertIn("POST/PUT/PATCH/DELETE", system_prompt)
        self.assertIn("read-only diagnostic", system_prompt)
        self.assertIn("out of scope", system_prompt)
        self.assertIn("frontend-api-debug", system_prompt)
        self.assertIn("Context from project files only.", system_prompt)
        self.assertEqual(payload["messages"][-1]["content"], "需要 traceId")

    def test_chat_page_copy_does_not_show_sensitive_input_restrictions(self):
        self.assertNotIn("局域网前端联调机器人", server.CHAT_INTRO_TEXT)
        self.assertNotIn("不要粘贴", server.CHAT_WARNING_TEXT)
        self.assertNotIn("accessToken", server.CHAT_WARNING_TEXT)
        self.assertNotIn("refreshToken", server.CHAT_WARNING_TEXT)
        self.assertNotIn("DJI 密钥", server.CHAT_WARNING_TEXT)
        self.assertNotIn("完整敏感日志", server.CHAT_WARNING_TEXT)
        self.assertIn("账号密码", server.CHAT_WARNING_TEXT)
        self.assertIn("认证调试", server.CHAT_WARNING_TEXT)

    def test_collect_project_context_reads_only_allowed_project_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "dikong-tech-design"
            docs_dir = project_root / "docs"
            docs_dir.mkdir(parents=True)
            (docs_dir / "api-v2-frontend-guide.md").write_text(
                "Route upload uses POST /api/v2/inspection/routes.\n"
            )
            (project_root / ".env").write_text("SECRET_TOKEN=do-not-read\n")

            context = server.collect_project_context(
                "route upload /api/v2/inspection/routes",
                project_root,
                max_bytes=2000,
            )

        self.assertIn("docs/api-v2-frontend-guide.md", context)
        self.assertIn("POST /api/v2/inspection/routes", context)
        self.assertNotIn("SECRET_TOKEN", context)

    def test_collect_project_context_includes_understand_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "dikong-tech-design"
            graph_dir = project_root / ".understand-anything"
            graph_dir.mkdir(parents=True)
            (graph_dir / "knowledge-graph.json").write_text(
                json.dumps(
                    {
                        "project": {"name": "Dikong API v2", "summary": "Django DRF backend"},
                        "layers": [
                            {"name": "API v2 公共层", "description": "OpenAPI and routes"},
                        ],
                        "tour": [
                            {"title": "项目概览", "description": "Only /api/v2 business APIs"},
                        ],
                        "nodes": [],
                        "edges": [],
                    }
                )
            )

            context = server.collect_project_context("解释项目", project_root, max_bytes=2000)

        self.assertIn("Dikong API v2", context)
        self.assertIn("API v2 公共层", context)
        self.assertIn("Only /api/v2 business APIs", context)

    def test_dropdown_choices_use_title_labels_and_session_id_values(self):
        choices = server.dropdown_choices([("session-1", "Route upload"), ("session-2", "403 debug")])

        self.assertEqual(choices, [("Route upload", "session-1"), ("403 debug", "session-2")])

    def test_streamlit_session_helpers_preserve_ids_and_default_to_latest(self):
        sessions = [("session-1", "Route upload"), ("session-2", "403 debug")]

        self.assertEqual(
            server.streamlit_session_label_lookup(sessions),
            {"session-1": "Route upload", "session-2": "403 debug"},
        )
        self.assertEqual(server.resolve_active_session_id("session-2", sessions), "session-2")
        self.assertEqual(server.resolve_active_session_id("missing", sessions), "session-1")
        self.assertIsNone(server.resolve_active_session_id(None, []))

    def test_send_message_appends_user_and_assistant_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = server.ServerConfig(
                api_key="test-key",
                hermes_url="http://127.0.0.1:8642/v1/chat/completions",
                host="127.0.0.1",
                port=7860,
                sessions_path=Path(tmp) / "sessions.json",
            )
            store = server.SessionStore(config.sessions_path)
            session_id = store.create_session("debug")

            with mock.patch("server.post_json", return_value={"choices": [{"message": {"content": "answer"}}]}):
                history, current_session, choices = server.send_message(
                    "POST /api/v2/foo 返回 403",
                    session_id,
                    config,
                    store,
                )

        self.assertEqual(current_session, session_id)
        self.assertEqual(choices, [(session_id, "debug")])
        self.assertEqual(
            history,
            [
                {"role": "user", "content": "POST /api/v2/foo 返回 403"},
                {"role": "assistant", "content": "answer"},
            ],
        )

    def test_send_message_creates_session_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = server.ServerConfig(
                api_key="test-key",
                hermes_url="http://127.0.0.1:8642/v1/chat/completions",
                host="127.0.0.1",
                port=7860,
                sessions_path=Path(tmp) / "sessions.json",
            )
            store = server.SessionStore(config.sessions_path)

            with mock.patch("server.post_json", return_value={"choices": [{"message": {"content": "answer"}}]}):
                history, current_session, choices = server.send_message("hello", "", config, store)

        self.assertTrue(current_session)
        self.assertEqual(choices, [(current_session, "hello")])
        self.assertEqual(
            history,
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "answer"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
