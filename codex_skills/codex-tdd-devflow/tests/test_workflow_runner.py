import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "workflow_runner.py"
MODULE_SPEC = importlib.util.spec_from_file_location("codex_tdd_devflow_workflow_runner", MODULE_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError(f"cannot load workflow runner from {MODULE_PATH}")
workflow_runner = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = workflow_runner
MODULE_SPEC.loader.exec_module(workflow_runner)


class WorkflowRunnerSemanticFlowTests(unittest.TestCase):
    def _make_paths(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        scaffold = root / "codex_devflow_scaffold"
        paths = workflow_runner.Paths(root=root, scaffold=scaffold)
        workflow_runner.ensure_scaffold(paths, force=True)
        return paths

    def test_run_auto_keeps_stage3_gate_artifact_read_only(self):
        paths = self._make_paths()
        artifact = {
            "stage": 3,
            "business_event_candidates": [
                {
                    "event_id": "EVT-001",
                    "title": "Session event",
                    "api_refs": ["POST /api/v1/routes"],
                    "expected_business_codes": ["SUCCESS"],
                }
            ],
            "business_events": [
                {
                    "event_id": "EVT-001",
                    "title": "Session event",
                    "api_refs": ["POST /api/v1/routes"],
                    "expected_business_codes": ["SUCCESS"],
                }
            ],
            "focus_api_keys": ["POST /api/v1/routes"],
            "progress_snapshot": {},
            "semantic_context": {},
            "semantic_review": {"status": "pending"},
            "editor_notes": [],
        }
        workflow_runner.write_json(paths.stage_artifact(3), artifact)
        workflow_runner.set_pending(
            paths,
            3,
            {
                "reason": "stage3_semantic_event_review_required",
                "allowed_actions": ["approve", "reject", "goto_stage"],
                "next_stage_on_approve": 4,
                "fallback_stage": 2,
            },
        )
        state = workflow_runner.load_state(paths)
        state["status"] = "waiting_decision"
        state["current_stage"] = 3
        workflow_runner.save_state(paths, state)

        before = workflow_runner.read_json(paths.stage_artifact(3), {})
        result = workflow_runner.run_auto(paths)
        after = workflow_runner.read_json(paths.stage_artifact(3), {})

        self.assertEqual(result, "waiting_decision")
        self.assertEqual(before, after)

    def test_run_auto_keeps_stage4_gate_artifact_read_only(self):
        paths = self._make_paths()
        artifact = {
            "stage": 4,
            "business_events": [
                {
                    "event_id": "EVT-001",
                    "title": "Session event",
                    "api_refs": ["POST /api/v1/routes"],
                    "expected_business_codes": ["SUCCESS"],
                }
            ],
            "candidate_cases": [
                {
                    "case_id": "CASE-001",
                    "event_id": "EVT-001",
                    "api_refs": ["POST /api/v1/routes"],
                    "expected_business_code": "SUCCESS",
                    "preconditions": ["have fixture"],
                    "steps": ["call api"],
                }
            ],
            "generated_cases": [],
            "generated_test_files": [],
            "coverage_by_event": [],
            "traceability_check": {"status": "ok", "reason": "stage4_case_traceability_ok"},
            "semantic_review": {"status": "pending"},
            "test_generation": {"status": "missing"},
        }
        workflow_runner.write_json(paths.stage_artifact(4), artifact)
        workflow_runner.set_pending(
            paths,
            4,
            {
                "reason": "stage4_semantic_review_required",
                "allowed_actions": ["approve", "reject", "goto_stage"],
                "next_stage_on_approve": 5,
                "fallback_stage": 3,
            },
        )
        state = workflow_runner.load_state(paths)
        state["status"] = "waiting_decision"
        state["current_stage"] = 4
        workflow_runner.save_state(paths, state)

        before = workflow_runner.read_json(paths.stage_artifact(4), {})
        result = workflow_runner.run_auto(paths)
        after = workflow_runner.read_json(paths.stage_artifact(4), {})

        self.assertEqual(result, "waiting_decision")
        self.assertEqual(before, after)

    def test_stage3_blocks_when_session_event_candidates_are_missing(self):
        paths = self._make_paths()
        workflow_runner.write_json(
            paths.stage_artifact(0),
            {
                "api_candidates": [
                    {
                        "entity": "route",
                        "api_key": "POST /api/v1/routes",
                        "expected_business_codes": ["SUCCESS", "INVALID_PARAMS"],
                    }
                ],
                "required_case_codes": ["SUCCESS", "INVALID_PARAMS"],
            },
        )
        workflow_runner.write_json(paths.stage_artifact(2), {"scanned_apis": ["POST /api/v1/routes"]})

        result = workflow_runner.stage3(paths, workflow_runner.DEFAULT_WORKFLOW_SPEC)
        contract = workflow_runner.evaluate_stage_contract(paths, 3, result.artifact)

        self.assertEqual(result.artifact["business_event_candidates"], [])
        self.assertEqual(result.artifact["business_events"], [])
        self.assertEqual(contract["reason"], "stage3_not_ready_for_stage4_no_business_events")
        self.assertTrue(contract["blocking"])
        self.assertIn("session_event_candidates.jsonl", result.artifact["semantic_review"]["reason"])

    def test_stage4_blocks_when_session_case_candidates_are_missing(self):
        paths = self._make_paths()
        workflow_runner.write_json(
            paths.stage_artifact(0),
            {
                "api_candidates": [
                    {
                        "entity": "route",
                        "api_key": "POST /api/v1/routes",
                        "expected_business_codes": ["SUCCESS", "INVALID_PARAMS"],
                    }
                ],
                "required_case_codes": ["SUCCESS", "INVALID_PARAMS"],
            },
        )
        workflow_runner.write_json(
            paths.stage_artifact(3),
            {
                "business_events": [
                    {
                        "event_id": "EVT-001",
                        "title": "Session event",
                        "description": "Session-defined event",
                        "entity": "route",
                        "api_refs": ["POST /api/v1/routes"],
                        "expected_business_codes": ["SUCCESS"],
                    }
                ]
            },
        )

        result = workflow_runner.stage4(paths, workflow_runner.DEFAULT_WORKFLOW_SPEC)
        contract = workflow_runner.evaluate_stage_contract(paths, 4, result.artifact)

        self.assertEqual(result.artifact["candidate_cases"], [])
        self.assertEqual(result.artifact["generated_cases"], [])
        self.assertEqual(contract["reason"], "stage4_not_ready_for_stage5_no_candidate_cases")
        self.assertTrue(contract["blocking"])
        self.assertIn("Runner 不再补齐或生成默认 case", result.artifact["session_codegen_note"])


class WorkflowRunnerStage5Tests(unittest.TestCase):
    def _make_paths(self, project_test_roots=None):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        scaffold = root / "codex_devflow_scaffold"
        (scaffold / "config").mkdir(parents=True, exist_ok=True)
        (scaffold / "artifacts" / "stage4").mkdir(parents=True, exist_ok=True)

        static_controls = json.loads(json.dumps(workflow_runner.DEFAULT_STATIC_CONTROLS))
        if project_test_roots is not None:
            static_controls["project_layout"]["project_test_roots"] = project_test_roots
        workflow_runner.write_json(scaffold / "config" / workflow_runner.STATIC_CONTROLS_FILENAME, static_controls)
        workflow_runner.write_json(scaffold / "artifacts" / "stage4" / "latest.json", {"generated_cases": []})
        return workflow_runner.Paths(root=root, scaffold=scaffold)

    def test_detect_duplicate_regression_when_bare_django_test_matches_project_root(self):
        paths = self._make_paths(project_test_roots=["apps"])

        duplicate = workflow_runner.detect_duplicate_stage5_regression_run(
            paths,
            [".venv/bin/django-admin", "test", "apps", "-v", "2", "--settings", "config.settings"],
            [".venv/bin/django-admin", "test", "-v", "2", "--settings", "config.settings"],
        )

        self.assertTrue(duplicate["skip"])
        self.assertEqual(duplicate["reason"], "same_django_test_scope")
        self.assertEqual(duplicate["project_internal_scope"], ["apps"])
        self.assertEqual(duplicate["regression_scope"], ["apps"])

    def test_detect_duplicate_regression_keeps_full_suite_when_project_test_roots_are_wider(self):
        paths = self._make_paths(project_test_roots=["apps", "integration_tests"])

        duplicate = workflow_runner.detect_duplicate_stage5_regression_run(
            paths,
            [".venv/bin/django-admin", "test", "apps", "-v", "2", "--settings", "config.settings"],
            [".venv/bin/django-admin", "test", "-v", "2", "--settings", "config.settings"],
        )

        self.assertFalse(duplicate["skip"])
        self.assertEqual(duplicate["reason"], "different_django_test_scope")
        self.assertEqual(duplicate["project_internal_scope"], ["apps"])
        self.assertEqual(duplicate["regression_scope"], ["apps", "integration_tests"])

    def test_stage5_skips_duplicate_regression_run_and_reuses_project_internal_result(self):
        paths = self._make_paths(project_test_roots=["apps"])
        workflow_runner.write_json(
            paths.stage_artifact(4),
            {
                "generated_cases": [
                    {
                        "case_id": "CASE-AUTO-I001-001",
                        "test_name": "test_auto__case_auto_i001_001",
                    }
                ]
            },
        )
        spec = {
            "test_command": [".venv/bin/django-admin", "test", "-v", "2", "--pythonpath", ".", "--settings", "config.settings"],
            "generated_test_command": [
                ".venv/bin/django-admin",
                "test",
                "codex_devflow_scaffold.tests.generated.test_generated_specs",
                "-v",
                "2",
                "--pythonpath",
                ".",
                "--settings",
                "config.settings",
            ],
            "project_internal_test_command": [
                ".venv/bin/django-admin",
                "test",
                "apps",
                "-v",
                "2",
                "--pythonpath",
                ".",
                "--settings",
                "config.settings",
            ],
            "stage5_failure_threshold": 2,
        }

        with (
            mock.patch.object(
                workflow_runner,
                "run_generated_case_suite",
                return_value={
                    "return_code": 0,
                    "tests_run": 1,
                    "failed_cases": [],
                    "failed_tests": [],
                    "skipped_tests": [],
                    "missing_tests": [],
                    "mismatch": False,
                },
            ),
            mock.patch.object(
                workflow_runner,
                "run_project_internal_suite",
                return_value={
                    "return_code": 0,
                    "tests_run": 139,
                    "stdout_tail": "Ran 139 tests in 10.000s",
                    "stderr_tail": "",
                },
            ),
            mock.patch.object(
                workflow_runner,
                "evaluate_project_test_sink",
                return_value={"status": "ready", "unmatched_case_ids": []},
            ),
            mock.patch.object(workflow_runner.subprocess, "run") as regression_run,
        ):
            result = workflow_runner.stage5(paths, spec, {})

        self.assertFalse(regression_run.called)
        self.assertEqual(result.next_stage, 6)
        self.assertEqual(result.artifact["project_internal_suite"]["tests_run"], 139)
        self.assertTrue(result.artifact["regression_summary"]["skipped_as_duplicate"])
        self.assertEqual(result.artifact["regression_summary"]["regression_return_code"], 0)
        self.assertEqual(
            result.artifact["regression_summary"]["duplicate_check"]["reason"],
            "same_django_test_scope",
        )


class WorkflowRunnerStage6DocSyncTests(unittest.TestCase):
    def _make_paths(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        scaffold = root / "codex_devflow_scaffold"
        (scaffold / "config").mkdir(parents=True, exist_ok=True)
        workflow_runner.write_json(
            scaffold / "config" / workflow_runner.STATIC_CONTROLS_FILENAME,
            workflow_runner.DEFAULT_STATIC_CONTROLS,
        )
        return workflow_runner.Paths(root=root, scaffold=scaffold)

    def test_merge_existing_doc_only_cleans_stage6_generated_blocks(self):
        paths = self._make_paths()
        original = "\n".join(
            [
                "# 示例实体实现说明",
                "",
                "- generated_at: 2026-03-08T07:52:00Z",
                "- entity: sample_entity",
                "",
                "## 文档格式说明",
                "",
                "### 更新本文档的指南（大模型用）",
                "",
                "```",
                "## N. {模块名}",
                "",
                "### N.X API / 功能名称",
                "- 功能：{功能描述}",
                "- 路径：{API路径}",
                "- 方法：{HTTP方法}",
                "- 权限：{所需权限}",
                "- 请求体：{请求格式}",
                "- 业务码：{返回的业务码}",
                "```",
                "",
                "## 功能记录",
                "",
                "### 1. 示例已有条目",
                "- 功能：人工维护内容",
                "- 备注：应当保留",
                "",
                "## 审计动作",
                "",
                "- MANUAL_AUDIT_ENTRY",
                "",
                "<!-- stage6_doc_sync::sample_entity::impl_desc.md::start -->",
                "旧的 stage6 尾块",
                "<!-- stage6_doc_sync::sample_entity::impl_desc.md::end -->",
            ]
        )
        merged = workflow_runner._merge_entity_doc_content(paths, original, "impl_desc.md", {})

        self.assertIn("### 1. 示例已有条目", merged)
        self.assertIn("- MANUAL_AUDIT_ENTRY", merged)
        self.assertIn("### 更新本文档的指南（大模型用）", merged)
        self.assertNotIn("stage6_doc_sync", merged)
        self.assertNotIn("旧的 stage6 尾块", merged)
        self.assertIn("- 备注：应当保留", merged)

    def test_merge_schema_only_removes_stage6_markers_and_keeps_manual_notes(self):
        paths = self._make_paths()
        original = "\n".join(
            [
                "// updated_at: 2026-03-08",
                "// entity: sample_entity",
                "",
                "Table sample_entities {",
                "  id bigint [pk, increment]",
                "  title varchar(64)",
                "}",
                "",
                "// 人工说明：这段说明应该保留",
                "",
                "// stage6_doc_sync::sample_entity::schema.dbml::start",
                "// generated sync block",
                "// stage6_doc_sync::sample_entity::schema.dbml::end",
            ]
        )
        merged = workflow_runner._merge_entity_doc_content(paths, original, "schema.dbml", {})

        self.assertIn("// 人工说明：这段说明应该保留", merged)
        self.assertNotIn("stage6_doc_sync", merged)
        self.assertNotIn("generated sync block", merged)

    def test_auto_sync_skips_missing_docs_instead_of_generating_templates(self):
        paths = self._make_paths()

        report = workflow_runner.auto_sync_entity_docs(
            paths,
            touched_entities=["sample_entity"],
            changed_paths=["apps/sample_entity/views.py"],
        )

        self.assertTrue(report["attempted"])
        self.assertEqual(report["created_docs"], [])
        self.assertEqual(report["updated_docs"], [])
        self.assertEqual(
            sorted(report["skipped_docs"]),
            sorted(
                [
                    "业务侧实现/sample_entity_data_dictionary.md",
                    "业务侧实现/sample_entity_impl_desc.md",
                    "业务侧实现/sample_entity_logical_model.md",
                    "业务侧实现/sample_entity_schema.dbml",
                ]
            ),
        )


class WorkflowRunnerRuntimeGuardTests(unittest.TestCase):
    def _make_paths(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        scaffold = root / "codex_devflow_scaffold"
        paths = workflow_runner.Paths(root=root, scaffold=scaffold)
        workflow_runner.ensure_scaffold(paths, force=True)
        return paths

    def test_build_status_hides_stage_artifact_while_stage_is_running(self):
        paths = self._make_paths()
        workflow_runner.write_json(
            paths.stage_artifact(5),
            {
                "stage": 5,
                "executed_cases": 9,
                "failed_cases": [],
                "project_internal_suite": {"return_code": 0},
                "project_test_sink": {"status": "ready", "unmatched_case_ids": []},
                "regression_summary": {"return_code": 0},
                "stage_contract": {"status": "ready", "ready_for_next_stage": True},
            },
        )
        state = workflow_runner.load_state(paths)
        state["status"] = "running"
        state["current_stage"] = 5
        state["running_stage"] = 5
        state["running_pid"] = os.getpid()
        state["running_started_at"] = "2026-03-10T00:00:00Z"
        state["running_trigger_source"] = "run_auto"
        state["last_completed_stage"] = 4
        workflow_runner.save_state(paths, state)

        payload = workflow_runner.build_status_brief_payload(paths)

        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["internal_stage"], 5)
        self.assertEqual(payload["stage_brief"]["status"], "running")
        self.assertEqual(payload["stage_brief"]["artifact_snapshot"], "suppressed_while_stage_running")
        self.assertNotIn("return_code", payload["stage_brief"])

    def test_build_status_recovers_stale_running_when_pid_is_gone(self):
        paths = self._make_paths()
        state = workflow_runner.load_state(paths)
        state["status"] = "running"
        state["current_stage"] = 5
        state["running_stage"] = 5
        state["running_pid"] = 999999
        state["running_started_at"] = "2026-03-10T00:00:00Z"
        state["running_trigger_source"] = "run_auto"
        workflow_runner.save_state(paths, state)

        with mock.patch.object(workflow_runner, "_pid_is_alive", return_value=False):
            payload = workflow_runner.build_status_brief_payload(paths)

        reloaded = workflow_runner.load_state(paths)
        self.assertEqual(payload["status"], "idle")
        self.assertEqual(reloaded["status"], "idle")
        self.assertIsNone(reloaded["running_stage"])
        self.assertIsNone(reloaded["running_pid"])

    def test_resume_approve_revalidates_stage6_doc_sync_before_advancing(self):
        paths = self._make_paths()
        state = workflow_runner.load_state(paths)
        state["status"] = "waiting_decision"
        state["current_stage"] = 6
        workflow_runner.save_state(paths, state)
        pending = workflow_runner.set_pending(
            paths,
            6,
            {
                "reason": "stage6_final_gate",
                "allowed_actions": ["approve", "reject", "goto_stage"],
                "next_stage_on_approve": 0,
                "fallback_stage": 6,
            },
        )
        workflow_runner.write_json(
            paths.decision,
            {
                "schema_version": 1,
                "decision_id": pending["decision_id"],
                "action": "approve",
                "reason": "test",
                "timestamp": workflow_runner.now_iso(),
            },
        )
        workflow_runner.write_json(
            paths.stage_artifact(6),
            {
                "stage": 6,
                "registry_updates": {"api_registry_count": 1, "case_registry_count": 1},
                "docs_updates": [],
                "entity_review": {
                    "final_entities": ["route"],
                    "focus_api_keys": ["POST /api/v1/routes/{id}/disable"],
                },
                "business_doc_sync": {"passed": False, "semantic_unsynced_entities": {"route": ["docs_not_updated_this_round"]}},
                "doc_sync_autofix": {"attempted": True, "updated_docs": []},
                "commit_review": {"needs_user_review": False, "suggested_git_add_files": []},
                "settlement_commit_message": "stage6 blocked",
                "stage_contract": {"status": "ready", "ready_for_next_stage": True},
            },
        )

        with self.assertRaisesRegex(workflow_runner.WorkflowError, "stage6 当前产物未通过复核"):
            workflow_runner.resume(paths, auto_continue=False)

        reloaded = workflow_runner.load_state(paths)
        self.assertEqual(reloaded["status"], "waiting_decision")
        self.assertEqual(reloaded["current_stage"], 6)

    def test_run_one_stage_refuses_to_enter_stage6_when_stage5_is_not_ready(self):
        paths = self._make_paths()
        state = workflow_runner.load_state(paths)
        state["current_stage"] = 6
        workflow_runner.save_state(paths, state)
        workflow_runner.write_json(
            paths.stage_artifact(5),
            {
                "stage": 5,
                "executed_cases": 1,
                "failed_cases": ["CASE-001"],
                "project_internal_suite": {"return_code": 0},
                "project_test_sink": {"status": "ready", "unmatched_case_ids": []},
                "regression_summary": {"return_code": 1, "consecutive_failures": 1},
                "stage_contract": {"status": "ready", "ready_for_next_stage": True},
            },
        )

        with (
            mock.patch.object(workflow_runner, "ensure_runtime_config_ready"),
            mock.patch.object(workflow_runner, "ensure_semantic_directories_for_stage"),
            mock.patch.object(workflow_runner, "execute_stage") as execute_stage,
            self.assertRaisesRegex(workflow_runner.WorkflowError, "前置 stage5 产物未通过复核"),
        ):
            workflow_runner.run_one_stage(paths, workflow_runner.DEFAULT_WORKFLOW_SPEC, state, 6)

        self.assertFalse(execute_stage.called)

    def test_stage6_contract_blocks_when_focus_api_exists_but_no_entity_was_selected(self):
        paths = self._make_paths()
        contract = workflow_runner.evaluate_stage_contract(
            paths,
            6,
            {
                "stage": 6,
                "registry_updates": {"api_registry_count": 1, "case_registry_count": 1},
                "docs_updates": [],
                "entity_review": {"final_entities": [], "focus_api_keys": ["POST /api/v1/routes/{id}/disable"]},
                "business_doc_sync": {"passed": True, "semantic_unsynced_entities": {}},
                "doc_sync_autofix": {"attempted": True, "updated_docs": []},
                "commit_review": {"review_status": "approved_skip_commit"},
                "settlement_commit_message": "stage6 settlement",
                "stage_contract": {"status": "ready", "ready_for_next_stage": True},
            },
        )

        self.assertEqual(contract["reason"], "stage6_not_ready_for_stage0_no_touched_entities_for_doc_sync")
        self.assertFalse(contract["ready_for_next_stage"])


if __name__ == "__main__":
    unittest.main()
