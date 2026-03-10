#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import contextlib
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STAGES = tuple(range(9))
LOGICAL_STAGES = tuple(range(8))
LOGICAL_TO_INTERNAL_STAGE_START = {
    0: 0,
    1: 1,
    2: 3,
    3: 4,
    4: 5,
    5: 6,
    6: 7,
    7: 8,
}
INTERNAL_TO_LOGICAL_STAGE = {
    0: 0,
    1: 1,
    2: 1,
    3: 2,
    4: 3,
    5: 4,
    6: 5,
    7: 6,
    8: 7,
}
LOGICAL_STAGE_NAMES = {
    0: "提名API",
    1: "实现API",
    2: "业务事件",
    3: "测试资产",
    4: "回归",
    5: "结算",
    6: "干预",
    7: "回退",
}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
SKILL_FILE = Path(__file__).resolve()
SKILL_NAME = SKILL_FILE.parent.parent.name
REPO_ROOT_ENV_VARS = ("CODEX_DEVFLOW_ROOT", "CODEX_WORKSPACE_ROOT")
DEFAULT_REPO_ROOT_MARKERS = ("manage.py", "pyproject.toml", ".git")
REQUIRED_PLACEHOLDER_PREFIX = "__CODEX_DEVFLOW_REQUIRED_"
SUPPORTED_INIT_PRESETS = ("generic", "django", "fastapi")
DEFAULT_SEMANTIC_DIR_NAMES = {
    "overview": "项目总体概览",
    "business": "业务侧实现",
    "permission": "权限管理侧实现",
}
REQUIRED_SEMANTIC_DIR_KEYS = ("overview", "business")
OPTIONAL_SEMANTIC_DIR_KEYS = ("permission",)
DEFAULT_PROJECT_ENTRY_FILES = ("manage.py",)
DEFAULT_PROJECT_CODE_DIRECTORIES = ("apps/", "config/")
DEFAULT_ENTITY_CODE_ROOTS = ("apps",)
DEFAULT_PROJECT_TEST_ROOTS = ("apps",)
DEFAULT_PROJECT_TEST_FILE_GLOBS = ("tests.py", "test_*.py")
DEFAULT_PERMISSION_CODE_DIRECTORIES = ("apps/access/",)
GENERATED_TEST_FILENAME = "test_generated_specs.py"
GENERATED_EVENT_NOTES_FILENAME = "business_events_zh.md"
SESSION_CASE_FILENAME = "session_case_candidates.jsonl"
SESSION_EVENT_FILENAME = "session_event_candidates.jsonl"
SESSION_API_FILENAME = "session_api_candidates.jsonl"
SESSION_ENTITY_REVIEW_FILENAME = "session_entity_review.json"
STATIC_CONTROLS_FILENAME = "static_controls.json"
ITERATION_CONTROLS_FILENAME = "iteration_controls.json"
PROJECT_RULES_FILENAME = "project_rules.json"
SEMANTIC_BLOCKED_STAGES = {1, 3, 4, 7, 8}
ENTITY_DOC_SUFFIXES = (
    "data_dictionary.md",
    "impl_desc.md",
    "logical_model.md",
    "schema.dbml",
)
DEFAULT_COMMIT_REVIEW_CODE_PATTERNS = ("apps/**", "config/**")
DEFAULT_COMMIT_REVIEW_BLACKLIST_PREFIXES = (
    "codex_devflow_scaffold/",
    "codex_skills/",
    ".codex/",
)
IGNORE_ENTITY_SEGMENTS = {
    "api",
    "internal",
    "auth",
    "docs",
    "health",
    "session-status",
    "session_status",
    "permissions",
    "permission",
    "groups",
    "users",
    "staff-types",
    "staff_types",
    "scopes",
    "matrix",
}


def required_placeholder(name: str) -> str:
    token = re.sub(r"[^A-Z0-9_]+", "_", str(name).strip().upper()).strip("_")
    return f"{REQUIRED_PLACEHOLDER_PREFIX}{token}__"


def is_required_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    token = value.strip().rstrip("/").rstrip("\\")
    return token.startswith(REQUIRED_PLACEHOLDER_PREFIX)


def _resolve_root_candidate(raw_root: str) -> Path:
    candidate = Path(raw_root).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve()


def _detect_repo_root(explicit_root: str | None = None) -> Path:
    raw_candidates: list[str] = []
    if explicit_root:
        raw_candidates.append(explicit_root)
    for env_name in REPO_ROOT_ENV_VARS:
        env_value = os.environ.get(env_name, "").strip()
        if env_value:
            raw_candidates.append(env_value)

    for raw in raw_candidates:
        candidate = _resolve_root_candidate(raw)
        if candidate.exists() and candidate.is_dir():
            return candidate
        raise RuntimeError(f"workspace root does not exist: {candidate}")

    current = SKILL_FILE
    for parent in current.parents:
        skill_candidate = parent / "codex_skills" / SKILL_NAME / "scripts" / current.name
        try:
            if skill_candidate.resolve() == current:
                return parent
        except OSError:
            continue

    for parent in current.parents:
        if any((parent / marker).exists() for marker in DEFAULT_REPO_ROOT_MARKERS):
            return parent

    cwd = Path.cwd().resolve()
    cwd_skill = cwd / "codex_skills" / SKILL_NAME / "scripts" / current.name
    if cwd_skill.exists():
        return cwd

    raise RuntimeError(
        "cannot detect repository root; use --workspace /path/to/repo or set CODEX_DEVFLOW_ROOT"
    )


def build_default_commit_review_whitelist_patterns(
    business_dirname: str,
    *,
    code_directories: list[str] | None = None,
    entry_files: list[str] | None = None,
) -> list[str]:
    business_dir = business_dirname.replace("\\", "/").strip("/") or DEFAULT_SEMANTIC_DIR_NAMES["business"]
    patterns = [
        f"{business_dir}/*_data_dictionary.md",
        f"{business_dir}/*_impl_desc.md",
        f"{business_dir}/*_logical_model.md",
        f"{business_dir}/*_schema.dbml",
    ]
    normalized_entry_files = [
        item.replace("\\", "/").strip().strip("/")
        for item in (entry_files or list(DEFAULT_PROJECT_ENTRY_FILES))
        if str(item).strip()
    ]
    normalized_code_dirs = [
        item.replace("\\", "/").strip().strip("/") + "/**"
        for item in (code_directories or list(DEFAULT_PROJECT_CODE_DIRECTORIES))
        if str(item).strip()
    ]
    patterns.extend(normalized_code_dirs or list(DEFAULT_COMMIT_REVIEW_CODE_PATTERNS))
    patterns.extend(normalized_entry_files)
    return patterns


DEFAULT_WORKFLOW_SPEC: dict[str, Any] = {
    "schema_version": 1,
    "name": "codex-tdd-devflow",
    "approval_mode": "manual",
    "stage_count": 9,
    "max_new_api_per_iteration": 1,
    "stage5_failure_threshold": 2,
    "business_api": {
        "schema_url": "http://127.0.0.1:8001/api/v1/docs/schema/",
        "root_url": "http://127.0.0.1:8001/api/v1/",
        "timeout_seconds": 5,
        "ignore_api_patterns": [],
        "local_scan_enabled": True,
        "local_scan_urlconf": "config.business_api_urlconf",
    },
    "business_code_fields": ["business_code", "business_detail_code"],
    "stage4_business_code_strict": False,
    "stage4_semantic_test_required": True,
    "stage4_case_design_mode": "session",
    "test_command": [".venv/bin/python", "manage.py", "test"],
    "generated_test_command": ["{python}", "manage.py", "test", "{generated_test_label}", "-v", "2"],
    "project_internal_test_command": ["{python}", "manage.py", "test", "{project_internal_test_target}", "-v", "2"],
    "gate_policy": {
        "0": {
            "reason": "stage0_review_required",
            "allowed_actions": ["approve", "reject", "goto_stage"],
            "next_stage_on_approve": 1,
            "fallback_stage": 0,
        },
        "3": {
            "reason": "stage3_semantic_event_review_required",
            "allowed_actions": ["approve", "reject", "goto_stage"],
            "next_stage_on_approve": 4,
            "fallback_stage": 2,
        },
        "4": {
            "reason": "stage4_semantic_review_required",
            "allowed_actions": ["approve", "reject", "goto_stage"],
            "next_stage_on_approve": 5,
            "fallback_stage": 3,
        },
        "6": {
            "reason": "stage6_final_gate",
            "allowed_actions": ["approve", "reject", "goto_stage"],
            "next_stage_on_approve": 0,
            "fallback_stage": 1,
        },
        "7": {
            "reason": "stage7_manual_intervention",
            "allowed_actions": ["approve", "reject", "goto_stage", "run_stage8"],
            "next_stage_on_approve": 1,
            "fallback_stage": 8,
        },
        "8": {
            "reason": "stage8_rollback_decision",
            "allowed_actions": ["approve", "reject", "goto_stage"],
            "next_stage_on_approve": 0,
            "fallback_stage": 0,
        },
    },
}

INIT_WORKFLOW_SPEC: dict[str, Any] = {
    "schema_version": 1,
    "name": "codex-tdd-devflow",
    "_template": {
        "kind": "onboarding_minimal",
        "notes": [
            "先替换 __CODEX_DEVFLOW_REQUIRED_*__ 占位值，再执行 run-auto。",
            "至少配置一种 API 发现方式：schema_url、root_url、或 local_scan_command。",
            "当前最佳支持仍然是 Python Web API 项目；测试命令请按项目实际填写。",
        ],
    },
    "approval_mode": "manual",
    "stage_count": 9,
    "max_new_api_per_iteration": 1,
    "stage5_failure_threshold": 2,
    "business_api": {
        "schema_url": "",
        "root_url": "",
        "timeout_seconds": 5,
        "ignore_api_patterns": [],
        "local_scan_enabled": False,
        "local_scan_command": [required_placeholder("api_discovery_command")],
        "local_scan_urlconf": "",
    },
    "business_code_fields": ["business_code", "business_detail_code"],
    "stage4_business_code_strict": False,
    "stage4_semantic_test_required": True,
    "stage4_case_design_mode": "session",
    "test_command": [required_placeholder("test_command")],
    "generated_test_command": [required_placeholder("generated_test_command")],
    "project_internal_test_command": [required_placeholder("project_internal_test_command")],
    "gate_policy": DEFAULT_WORKFLOW_SPEC["gate_policy"],
}

DEFAULT_STATIC_CONTROLS: dict[str, Any] = {
    "schema_version": 1,
    "semantic": {
        "directories": dict(DEFAULT_SEMANTIC_DIR_NAMES),
    },
    "project_layout": {
        "entry_files": list(DEFAULT_PROJECT_ENTRY_FILES),
        "code_directories": list(DEFAULT_PROJECT_CODE_DIRECTORIES),
        "entity_code_roots": list(DEFAULT_ENTITY_CODE_ROOTS),
        "project_test_roots": list(DEFAULT_PROJECT_TEST_ROOTS),
        "project_test_file_globs": list(DEFAULT_PROJECT_TEST_FILE_GLOBS),
        "permission_code_directories": list(DEFAULT_PERMISSION_CODE_DIRECTORIES),
    },
    "commit_controls": {
        "git_add_whitelist_patterns": build_default_commit_review_whitelist_patterns(
            DEFAULT_SEMANTIC_DIR_NAMES["business"]
        ),
        "git_add_blacklist_prefixes": list(DEFAULT_COMMIT_REVIEW_BLACKLIST_PREFIXES),
    },
}

INIT_STATIC_CONTROLS: dict[str, Any] = {
    "schema_version": 1,
    "_template": {
        "kind": "onboarding_minimal",
        "notes": [
            "这个文件描述项目目录结构。",
            "显式空数组表示当前项目没有这类目录；不会再偷偷回退到 Django 默认值。",
            "permission 目录是可选的；overview 和 business 是必填语义目录。",
        ],
    },
    "semantic": {
        "directories": {
            "overview": required_placeholder("overview_semantic_dir"),
            "business": required_placeholder("business_semantic_dir"),
            "permission": "",
        },
    },
    "project_layout": {
        "entry_files": [required_placeholder("project_entry_file")],
        "code_directories": [required_placeholder("project_code_dir")],
        "entity_code_roots": [required_placeholder("entity_code_root")],
        "project_test_roots": [required_placeholder("project_test_root")],
        "project_test_file_globs": ["test_*.py", "*_test.py", "tests.py"],
        "permission_code_directories": [],
    },
    "commit_controls": {
        "git_add_whitelist_patterns": [],
        "git_add_blacklist_prefixes": list(DEFAULT_COMMIT_REVIEW_BLACKLIST_PREFIXES),
    },
}

DEFAULT_ITERATION_CONTROLS: dict[str, Any] = {
    "schema_version": 1,
    "iteration_overrides": {},
}

INIT_ITERATION_CONTROLS: dict[str, Any] = {
    "schema_version": 1,
    "_template": {
        "kind": "advanced_optional",
        "notes": [
            "这个文件是可选高级配置。",
            "只有当你想按轮次追加 commit 白名单/黑名单时才需要填写。",
            "如果当前项目不需要分轮次差异，保持 iteration_overrides 为空即可。",
        ],
    },
    "iteration_overrides": {},
}

DEFAULT_PROJECT_RULES: dict[str, Any] = {
    "schema_version": 1,
    "rules": [],
}

INIT_PROJECT_RULES: dict[str, Any] = {
    "schema_version": 1,
    "_template": {
        "kind": "project_notes",
        "notes": [
            "这个文件只写团队补充说明，不改变 stage 功能。",
            "适合写测试沉淀要求、fixture 复用偏好、文档风格要求。",
            "不适合写 stage 顺序、门禁推进逻辑、实体识别算法。",
        ],
        "examples": [
            "将 skill 流程中产生的测试 case 沉淀到项目内",
            {
                "text": "Stage3 生成测试优先复用项目已有 fixture",
                "stages": [3],
            },
        ],
    },
    "rules": [],
}

DEFAULT_STATE: dict[str, Any] = {
    "schema_version": 1,
    "status": "idle",
    "current_stage": 0,
    "running_iteration": 1,
    "running_stage": None,
    "running_started_at": "",
    "running_pid": None,
    "running_trigger_source": "",
    "stage0_replan_required": False,
    "stage0_entered_at": "",
    "stage0_last_replan_at": "",
    "stage5_consecutive_failures": 0,
    "last_completed_stage": None,
    "last_event_id": "",
    "updated_at": "",
}

DEFAULT_SEMANTIC_MODEL: dict[str, Any] = {
    "schema_version": 1,
    "business_goal": "低空业务 API 增量迭代",
    "roles": ["business_admin", "dispatcher", "pilot_operator", "auditor"],
    "resources": ["drone", "drone_assignment"],
    "actions": ["view", "create", "update", "change_status", "assign", "cancel_assignment"],
    "state_machines": [
        {
            "resource": "drone",
            "states": ["ENABLED", "DISABLED", "MAINTENANCE", "RETIRED"],
            "terminal_states": ["RETIRED"],
        }
    ],
    "constraints": ["RETIRED 状态不可逆", "分配关系仅允许一条 ACTIVE 记录"],
    "permission_boundary": {
        "model": "staff_type -> group -> permission(scope)",
        "scope_types": ["ALL", "OWN", "ASSIGNED"],
    },
}

INIT_SEMANTIC_MODEL: dict[str, Any] = {
    "schema_version": 1,
    "_template": {
        "kind": "onboarding_minimal",
        "notes": [
            "只写当前项目已经明确的业务语义，不要为了填模板编造示例业务。",
            "如果业务语义主要沉淀在 overview/business 文档，这里可以先保持最小骨架。",
        ],
    },
    "business_goal": "",
    "roles": [],
    "resources": [],
    "actions": [],
    "state_machines": [],
    "constraints": [],
    "permission_boundary": {},
}


def build_init_static_controls_template(preset: str) -> dict[str, Any]:
    payload = copy.deepcopy(INIT_STATIC_CONTROLS)
    template = payload.setdefault("_template", {})
    if isinstance(template, dict):
        template["preset"] = preset
        notes = template.setdefault("notes", [])
        if isinstance(notes, list):
            notes.append("JSON 不支持真正注释；请阅读 _template / _comment / _examples 字段。")

    payload["semantic"]["_comment"] = "业务语义目录；都相对仓库根目录填写。"
    payload["semantic"]["directories"]["_comment"] = "overview/business 必填；permission 可留空字符串。"
    payload["project_layout"]["_comment"] = "代码与测试目录；都相对仓库根目录填写。显式空数组表示没有该类目录。"
    payload["project_layout"]["_examples"] = {
        "django": {
            "entry_files": ["manage.py"],
            "code_directories": ["apps", "config"],
            "entity_code_roots": ["apps"],
            "project_test_roots": ["apps"],
            "permission_code_directories": ["apps/access"],
        },
        "fastapi": {
            "entry_files": ["pyproject.toml"],
            "code_directories": ["app", "src"],
            "entity_code_roots": ["app"],
            "project_test_roots": ["tests"],
            "permission_code_directories": [],
        },
    }
    payload["commit_controls"]["_comment"] = "Stage5/Stage6 生成提交建议时允许纳入的文件范围。通常只需要改 whitelist。"

    if preset == "django":
        payload["project_layout"]["entry_files"] = ["manage.py"]
        payload["project_layout"]["code_directories"] = ["apps", "config"]
        payload["project_layout"]["entity_code_roots"] = ["apps"]
        payload["project_layout"]["project_test_roots"] = ["apps"]
        payload["project_layout"]["project_test_file_globs"] = ["tests.py", "test_*.py"]
        payload["project_layout"]["permission_code_directories"] = ["apps/access"]
        payload["commit_controls"]["git_add_whitelist_patterns"] = build_default_commit_review_whitelist_patterns(
            DEFAULT_SEMANTIC_DIR_NAMES["business"],
            code_directories=["apps", "config"],
            entry_files=["manage.py"],
        )
        if isinstance(template, dict):
            notes = template.get("notes", [])
            if isinstance(notes, list):
                notes.append("django preset 已预填 manage.py/apps/config 等常见布局。语义目录名仍需按项目确认。")
    elif preset == "fastapi":
        payload["project_layout"]["entry_files"] = ["pyproject.toml"]
        payload["project_layout"]["code_directories"] = ["app", "src"]
        payload["project_layout"]["entity_code_roots"] = ["app"]
        payload["project_layout"]["project_test_roots"] = ["tests"]
        payload["project_layout"]["project_test_file_globs"] = ["test_*.py"]
        payload["project_layout"]["permission_code_directories"] = []
        payload["commit_controls"]["git_add_whitelist_patterns"] = build_default_commit_review_whitelist_patterns(
            DEFAULT_SEMANTIC_DIR_NAMES["business"],
            code_directories=["app", "src"],
            entry_files=["pyproject.toml"],
        )
        if isinstance(template, dict):
            notes = template.get("notes", [])
            if isinstance(notes, list):
                notes.append("fastapi preset 只预填常见 app/tests 布局；API 导出和测试命令通常仍需按项目调整。")
    return payload


def build_init_workflow_spec_template(preset: str) -> dict[str, Any]:
    payload = copy.deepcopy(INIT_WORKFLOW_SPEC)
    template = payload.setdefault("_template", {})
    if isinstance(template, dict):
        template["preset"] = preset
        notes = template.setdefault("notes", [])
        if isinstance(notes, list):
            notes.append("JSON 不支持真正注释；请阅读 _template / _comment / _examples 字段。")

    payload["business_api"]["_comment"] = "API 发现配置。三选一即可：schema_url、root_url、或 local_scan_command。"
    payload["business_api"]["_comment_ignore_api_patterns"] = (
        "可选：需要从业务 API 发现中排除的 METHOD path 模式列表，支持 fnmatch。"
    )
    payload["business_api"]["_examples"] = {
        "django_local_scan": ["{python}", "manage.py", "spectacular", "--format", "openapi-json", "--urlconf", "config.business_api_urlconf"],
        "custom_export_script": ["python3", "tools/export_openapi.py"],
    }
    payload["_comment_business_code_fields"] = "如果项目响应里不用 business_code / business_detail_code，可改成你自己的业务码字段名。"
    payload["_comment_stage4"] = "Stage4 是否强制校验业务码、是否要求语义测试、case 设计模式。"
    payload["_comment_test_command"] = "全量回归命令。要求能在仓库根目录执行。"
    payload["_comment_generated_test_command"] = "只跑 generated tests 的命令。{python} 和 {generated_test_label} 会在运行时替换。"
    payload["_comment_project_internal_test_command"] = "项目内测试命令。{python} 和 {project_internal_test_target} 会在运行时替换。"
    payload["_comment_gate_policy"] = "一般不需要修改。只有你明确想调整门禁阶段时再动。"

    if preset == "django":
        payload["business_api"]["local_scan_enabled"] = True
        payload["business_api"]["local_scan_command"] = []
        payload["business_api"]["local_scan_urlconf"] = "config.business_api_urlconf"
        payload["test_command"] = [".venv/bin/python", "manage.py", "test"]
        payload["generated_test_command"] = ["{python}", "manage.py", "test", "{generated_test_label}", "-v", "2"]
        payload["project_internal_test_command"] = ["{python}", "manage.py", "test", "{project_internal_test_target}", "-v", "2"]
        if isinstance(template, dict):
            notes = template.get("notes", [])
            if isinstance(notes, list):
                notes.append("django preset 已预填 manage.py test 与 drf-spectacular 本地扫描默认值。")
    elif preset == "fastapi":
        payload["business_api"]["local_scan_enabled"] = False
        payload["business_api"]["local_scan_command"] = [required_placeholder("fastapi_openapi_export_command")]
        payload["business_api"]["local_scan_urlconf"] = ""
        payload["test_command"] = [required_placeholder("test_command")]
        payload["generated_test_command"] = [required_placeholder("generated_test_command")]
        payload["project_internal_test_command"] = [required_placeholder("project_internal_test_command")]
        if isinstance(template, dict):
            notes = template.get("notes", [])
            if isinstance(notes, list):
                notes.append("fastapi preset 不会预填测试命令，避免把 pytest/自定义脚本误写成通用事实。")
    return payload


def build_init_iteration_controls_template() -> dict[str, Any]:
    return copy.deepcopy(INIT_ITERATION_CONTROLS)


def build_init_project_rules_template() -> dict[str, Any]:
    return copy.deepcopy(INIT_PROJECT_RULES)

DEFAULT_API_REGISTRY: dict[str, Any] = {
    "schema_version": 1,
    "bootstrap_done": False,
    "items": [],
}

DEFAULT_CASE_REGISTRY: dict[str, Any] = {
    "schema_version": 1,
    "items": [],
}

DEFAULT_PENDING: dict[str, Any] = {
    "schema_version": 1,
    "active": False,
}

DEFAULT_DECISION: dict[str, Any] = {
    "schema_version": 1,
}

DEFAULT_ENTITY_REVIEW: dict[str, Any] = {
    "schema_version": 1,
    "mode": "append",
    "entities": [],
    "reason": "",
}

DEFAULT_DOCS = {
    "api_change_summary.md": "# API Change Summary\n\n- 本轮尚未产出实际 API 变更。\n",
    "api_catalog.md": "# API Catalog\n\n本文件由 Stage 6 维护。\n",
    "data_model_delta.md": "# Data Model Delta\n\n本文件由 Stage 6 维护。\n",
    "traceability_matrix.md": "# Traceability Matrix\n\n本文件由 Stage 6 维护。\n",
    "test_example_records.md": "# Test Example Records\n\n本文件由 Stage 6 维护。\n",
}
DEFAULT_GENERATED_TEST = (
    '"""Auto-generated placeholder test file for codex-tdd-devflow."""\n\n'
    "def test_placeholder_generated_case():\n"
    "    assert True\n"
)

STAGE_REQUIRED_FIELDS = {
    0: ["api_candidates", "biz_status_codes", "required_case_codes", "risk_assessment", "semantic_gap_report", "stage_contract"],
    1: ["implementation_tasks", "changed_files", "notes", "stage_contract"],
    2: ["scanned_apis", "existing_api_keys", "new_api_keys", "drift_items", "stage_contract"],
    3: [
        "business_event_candidates",
        "business_events",
        "focus_api_keys",
        "progress_snapshot",
        "semantic_context",
        "semantic_review",
        "editor_notes",
        "stage_contract",
    ],
    4: [
        "candidate_cases",
        "generated_cases",
        "generated_test_files",
        "coverage_by_event",
        "traceability_check",
        "semantic_review",
        "test_generation",
        "stage_contract",
    ],
    5: ["executed_cases", "failed_cases", "project_internal_suite", "project_test_sink", "regression_summary", "stage_contract"],
    6: [
        "registry_updates",
        "docs_updates",
        "entity_review",
        "business_doc_sync",
        "doc_sync_autofix",
        "commit_review",
        "settlement_commit_message",
        "stage_contract",
    ],
    7: ["intervention_summary", "recommendations", "stage_contract"],
    8: ["decision", "reasoning", "actions", "stage_contract"],
}

class WorkflowError(RuntimeError):
    pass


@dataclass
class StageResult:
    stage: int
    artifact: dict[str, Any]
    next_stage: int
    gate: bool = False


@dataclass
class Paths:
    root: Path
    scaffold: Path

    @property
    def workflow_spec(self) -> Path:
        return self.scaffold / "workflow_spec.json"

    @property
    def state(self) -> Path:
        return self.scaffold / "state.json"

    @property
    def semantic_model(self) -> Path:
        return self.scaffold / "semantic" / "business_semantic_model.json"

    @property
    def semantic_snapshot(self) -> Path:
        return self.scaffold / "semantic" / "semantic_snapshot.json"

    @property
    def api_registry(self) -> Path:
        return self.scaffold / "registry" / "api_registry.json"

    @property
    def case_registry(self) -> Path:
        return self.scaffold / "registry" / "case_registry.json"

    @property
    def pending(self) -> Path:
        return self.scaffold / "decisions" / "pending.json"

    @property
    def decision(self) -> Path:
        return self.scaffold / "decisions" / "decision.json"

    @property
    def events(self) -> Path:
        return self.scaffold / "logs" / "events.ndjson"

    @property
    def case_descriptions(self) -> Path:
        return self.scaffold / "cases" / "case_descriptions.jsonl"

    @property
    def session_case_candidates(self) -> Path:
        return self.scaffold / "cases" / SESSION_CASE_FILENAME

    @property
    def session_event_candidates(self) -> Path:
        return self.scaffold / "cases" / SESSION_EVENT_FILENAME

    @property
    def session_api_candidates(self) -> Path:
        return self.scaffold / "cases" / SESSION_API_FILENAME

    @property
    def session_entity_review(self) -> Path:
        return self.scaffold / "cases" / SESSION_ENTITY_REVIEW_FILENAME

    @property
    def config_dir(self) -> Path:
        return self.scaffold / "config"

    @property
    def static_controls(self) -> Path:
        return self.config_dir / STATIC_CONTROLS_FILENAME

    @property
    def iteration_controls(self) -> Path:
        return self.config_dir / ITERATION_CONTROLS_FILENAME

    @property
    def project_rules(self) -> Path:
        return self.config_dir / PROJECT_RULES_FILENAME

    @property
    def workflow_lock(self) -> Path:
        return self.scaffold / "runtime" / "workflow.lock"

    def stage_input(self, stage: int) -> Path:
        return self.scaffold / "inputs" / f"stage{stage}.json"

    def stage_artifact(self, stage: int) -> Path:
        return self.scaffold / "artifacts" / f"stage{stage}" / "latest.json"

    def prompt(self, stage: int) -> Path:
        return self.scaffold / "prompts" / f"stage{stage}.md"

    def stage_schema(self, stage: int) -> Path:
        return self.scaffold / "schemas" / f"stage{stage}_output.schema.json"


# ----------------------------
# generic helpers
# ----------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso_to_epoch(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def epoch_to_iso(value: float | None) -> str:
    if value is None:
        return ""
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def file_mtime_epoch(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def write_json(path: Path, payload: Any) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    _atomic_write_bytes(path, (data + "\n").encode("utf-8"))


def read_jsonl(path: Path) -> list[Any]:
    if not path.exists():
        return []
    rows: list[Any] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rows.append(json.loads(stripped))
        except json.JSONDecodeError as exc:
            raise WorkflowError(f"{path.as_posix()} 第 {idx} 行 JSON 非法: {exc}") from exc
    return rows


def write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def ensure_json(path: Path, payload: Any, force: bool) -> None:
    if force or not path.exists():
        write_json(path, payload)


def ensure_text(path: Path, text: str, force: bool) -> None:
    if force or not path.exists():
        write_text(path, text)


def _normalize_string_list(raw: Any) -> list[str]:
    tokens: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            token = str(item).strip()
            if token:
                tokens.append(token)
    return dedupe_keep_order(tokens)


def load_static_controls(paths: Paths) -> dict[str, Any]:
    payload = read_json(paths.static_controls, DEFAULT_STATIC_CONTROLS)
    if not isinstance(payload, dict):
        return DEFAULT_STATIC_CONTROLS
    return payload


def _normalize_relative_dir_token(raw: str, trailing_slash: bool) -> str:
    token = raw.replace("\\", "/").strip().strip("/")
    if not token:
        return ""
    return f"{token}/" if trailing_slash else token


def resolve_semantic_dir_names(paths: Paths) -> dict[str, str]:
    controls = load_static_controls(paths)
    semantic = controls.get("semantic", {}) if isinstance(controls, dict) else {}
    configured = semantic.get("directories", {}) if isinstance(semantic, dict) else {}

    resolved: dict[str, str] = {}
    for key, default in DEFAULT_SEMANTIC_DIR_NAMES.items():
        configured_explicitly = isinstance(configured, dict) and key in configured
        candidate = configured.get(key, default) if configured_explicitly else default
        token = _normalize_relative_dir_token(str(candidate), trailing_slash=False)
        if configured_explicitly:
            resolved[key] = token
        else:
            resolved[key] = token or default
    return resolved


def resolve_project_layout(paths: Paths) -> dict[str, list[str]]:
    controls = load_static_controls(paths)
    raw_layout = controls.get("project_layout", {}) if isinstance(controls, dict) else {}
    if not isinstance(raw_layout, dict):
        raw_layout = {}

    def _resolve_layout_list(key: str, *, trailing_slash: bool, default_values: tuple[str, ...]) -> list[str]:
        configured_explicitly = key in raw_layout
        raw_value = raw_layout.get(key) if configured_explicitly else list(default_values)
        values = [
            token
            for token in (
                _normalize_relative_dir_token(item, trailing_slash=trailing_slash)
                for item in _normalize_string_list(raw_value)
            )
            if token
        ]
        if not configured_explicitly:
            return dedupe_keep_order(values)
        return dedupe_keep_order(values)

    entry_files = _resolve_layout_list(
        "entry_files",
        trailing_slash=False,
        default_values=DEFAULT_PROJECT_ENTRY_FILES,
    )
    code_directories = _resolve_layout_list(
        "code_directories",
        trailing_slash=True,
        default_values=DEFAULT_PROJECT_CODE_DIRECTORIES,
    )
    entity_code_roots = _resolve_layout_list(
        "entity_code_roots",
        trailing_slash=False,
        default_values=DEFAULT_ENTITY_CODE_ROOTS,
    )
    project_test_roots = _resolve_layout_list(
        "project_test_roots",
        trailing_slash=False,
        default_values=DEFAULT_PROJECT_TEST_ROOTS,
    )
    if "project_test_file_globs" in raw_layout:
        project_test_file_globs = _normalize_string_list(raw_layout.get("project_test_file_globs"))
    else:
        project_test_file_globs = list(DEFAULT_PROJECT_TEST_FILE_GLOBS)
    permission_code_directories = _resolve_layout_list(
        "permission_code_directories",
        trailing_slash=True,
        default_values=DEFAULT_PERMISSION_CODE_DIRECTORIES,
    )

    return {
        "entry_files": dedupe_keep_order(entry_files),
        "code_directories": dedupe_keep_order(code_directories),
        "entity_code_roots": dedupe_keep_order(entity_code_roots),
        "project_test_roots": dedupe_keep_order(project_test_roots),
        "project_test_file_globs": dedupe_keep_order(project_test_file_globs),
        "permission_code_directories": dedupe_keep_order(permission_code_directories),
    }


def _path_is_entry_file(path: str, entry_files: list[str]) -> bool:
    normalized = path.replace("\\", "/").strip().strip("/")
    return normalized in set(entry_files)


def _path_has_prefix(path: str, prefixes: list[str]) -> bool:
    normalized = path.replace("\\", "/").strip()
    return any(normalized.startswith(prefix) for prefix in prefixes)


def is_project_code_path(paths: Paths, path: str) -> bool:
    layout = resolve_project_layout(paths)
    return _path_is_entry_file(path, layout["entry_files"]) or _path_has_prefix(path, layout["code_directories"])


def _normalize_commit_controls(
    raw: Any,
    business_dir: str | None = None,
    *,
    code_directories: list[str] | None = None,
    entry_files: list[str] | None = None,
) -> dict[str, list[str]]:
    resolved_business_dir = business_dir or DEFAULT_SEMANTIC_DIR_NAMES["business"]
    controls = {
        "git_add_whitelist_patterns": build_default_commit_review_whitelist_patterns(
            resolved_business_dir,
            code_directories=code_directories,
            entry_files=entry_files,
        ),
        "git_add_blacklist_prefixes": list(DEFAULT_COMMIT_REVIEW_BLACKLIST_PREFIXES),
    }
    if not isinstance(raw, dict):
        return controls

    whitelist = _normalize_string_list(raw.get("git_add_whitelist_patterns"))
    blacklist = _normalize_string_list(raw.get("git_add_blacklist_prefixes"))
    if whitelist:
        controls["git_add_whitelist_patterns"] = whitelist
    if blacklist:
        controls["git_add_blacklist_prefixes"] = blacklist
    return controls


def ensure_skill_controls(paths: Paths, force: bool = False, preset: str = "generic") -> None:
    ensure_json(paths.static_controls, build_init_static_controls_template(preset), force)
    ensure_json(paths.iteration_controls, build_init_iteration_controls_template(), force)
    ensure_json(paths.project_rules, build_init_project_rules_template(), force)


def _normalize_logical_stage_scope(raw: Any) -> list[int]:
    values: list[int] = []
    candidates = raw if isinstance(raw, list) else [raw]
    for item in candidates:
        try:
            stage = int(item)
        except (TypeError, ValueError):
            continue
        if stage in LOGICAL_STAGES:
            values.append(stage)
    return dedupe_keep_order(values)


def _normalize_project_rule_item(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        return {"text": text, "stages": []}
    if not isinstance(raw, dict):
        return None

    text = str(raw.get("text", raw.get("rule", ""))).strip()
    if not text:
        return None
    return {
        "text": text,
        "stages": _normalize_logical_stage_scope(raw.get("stages", raw.get("logical_stages", []))),
    }


def load_project_rules(paths: Paths) -> dict[str, Any]:
    payload = read_json(paths.project_rules, DEFAULT_PROJECT_RULES)
    if not isinstance(payload, dict):
        payload = DEFAULT_PROJECT_RULES

    normalized_rules: list[dict[str, Any]] = []
    raw_rules = payload.get("rules", [])
    if isinstance(raw_rules, list):
        for item in raw_rules:
            normalized = _normalize_project_rule_item(item)
            if normalized:
                normalized_rules.append(normalized)

    return {
        "schema_version": 1,
        "rules": normalized_rules,
    }


def active_project_rules(paths: Paths, internal_stage: int | None = None) -> list[dict[str, Any]]:
    logical_stage = logical_stage_for_internal(internal_stage) if internal_stage in STAGES else None
    payload = load_project_rules(paths)
    rules = payload.get("rules", [])
    if not isinstance(rules, list):
        return []

    active: list[dict[str, Any]] = []
    for item in rules:
        if not isinstance(item, dict):
            continue
        stages = item.get("stages", [])
        if logical_stage is None or not stages or logical_stage in stages:
            active.append({"text": str(item.get("text", "")).strip(), "stages": list(stages) if isinstance(stages, list) else []})
    return active


def _configured_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not is_required_placeholder(value)


def _configured_string_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    tokens = [str(item).strip() for item in value if str(item).strip()]
    return bool(tokens) and not any(is_required_placeholder(token) for token in tokens)


def _append_readiness_error(errors: list[str], path: str, message: str) -> None:
    errors.append(f"{path} {message}")


def build_session_semantic_audit_requirement() -> dict[str, Any]:
    return {
        "required": True,
        "owner": "current_codex_session",
        "status": "required",
        "purpose": "读取 overview/business 与当前代码，确认语义资料足以支撑 Stage0 API 提名、entity 判断和 bootstrap。",
        "pass_criteria": [
            "能说明 overview/business 主要在讲什么业务对象与流程",
            "能把本轮候选 API 放回真实业务语义，而不是只看 path 或 method",
            "能确认当前代码实现与语义资料之间没有明显脱节到无法继续 Stage0",
        ],
        "note": "runner 的 semantic_review 只做语义源就绪检查，不代替语义理解。",
    }


def build_config_readiness_brief(readiness: dict[str, Any]) -> dict[str, Any]:
    blocking_items = readiness.get("blocking_items", []) if isinstance(readiness, dict) else []
    warning_items = readiness.get("warning_items", []) if isinstance(readiness, dict) else []
    semantic_review = readiness.get("semantic_review", {}) if isinstance(readiness, dict) else {}
    session_semantic_audit = readiness.get("session_semantic_audit", {}) if isinstance(readiness, dict) else {}

    blocking_list = [str(item) for item in blocking_items if str(item).strip()] if isinstance(blocking_items, list) else []
    warning_list = [str(item) for item in warning_items if str(item).strip()] if isinstance(warning_items, list) else []
    semantic_status = str(semantic_review.get("status", "")).strip() if isinstance(semantic_review, dict) else ""
    status = str(readiness.get("status", "")).strip() if isinstance(readiness, dict) else ""

    if status == "ready":
        summary = "结构 ready；当前 session 仍需完成语义审核"
    elif blocking_list:
        summary = f"存在 {len(blocking_list)} 个阻断项，暂不能进入主流程"
    else:
        summary = "配置仍需补充"

    payload = {
        "status": status,
        "summary": summary,
        "semantic_review_status": semantic_status,
        "session_semantic_audit_required": bool(session_semantic_audit.get("required", False))
        if isinstance(session_semantic_audit, dict)
        else True,
        "blocking_item_count": len(blocking_list),
        "warning_item_count": len(warning_list),
        "next_step_hint": str(readiness.get("next_step_hint", "")).strip() if isinstance(readiness, dict) else "",
        "detail_hint": "如需完整配置/语义源校验结果，执行 check-config；如需完整当前载荷，使用 --json。",
    }
    if blocking_list:
        payload["blocking_items_preview"] = blocking_list[:3]
    if warning_list:
        payload["warning_items_preview"] = warning_list[:2]
    return payload


def build_onboarding_readiness(paths: Paths, spec: dict[str, Any] | None = None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    static_controls = load_static_controls(paths)
    workflow_spec = spec if isinstance(spec, dict) else read_json(paths.workflow_spec, INIT_WORKFLOW_SPEC)
    semantic_model = read_json(paths.semantic_model, INIT_SEMANTIC_MODEL)

    static_controls_path = rel_path(paths, paths.static_controls)
    workflow_spec_path = rel_path(paths, paths.workflow_spec)
    semantic_model_path = rel_path(paths, paths.semantic_model)

    directories = resolve_semantic_dir_names(paths)
    for key in REQUIRED_SEMANTIC_DIR_KEYS:
        if not _configured_text(directories.get(key, "")):
            _append_readiness_error(
                errors,
                static_controls_path,
                f"-> semantic.directories.{key} 未配置或仍是模板占位值",
            )

    project_layout = resolve_project_layout(paths)
    for key in ("entry_files", "code_directories", "entity_code_roots", "project_test_roots"):
        if not _configured_string_list(project_layout.get(key, [])):
            _append_readiness_error(
                errors,
                static_controls_path,
                f"-> project_layout.{key} 未配置或仍是模板占位值",
            )
    if not _configured_string_list(project_layout.get("project_test_file_globs", [])):
        _append_readiness_error(errors, static_controls_path, "-> project_layout.project_test_file_globs 未配置")

    business_api = dict(DEFAULT_WORKFLOW_SPEC.get("business_api", {}))
    if isinstance(workflow_spec, dict) and isinstance(workflow_spec.get("business_api"), dict):
        business_api.update(workflow_spec.get("business_api", {}))
    schema_ready = _configured_text(business_api.get("schema_url", ""))
    root_ready = _configured_text(business_api.get("root_url", ""))
    local_enabled = bool(business_api.get("local_scan_enabled", False))
    local_command_ready = _configured_string_list(business_api.get("local_scan_command", []))
    local_urlconf_ready = _configured_text(business_api.get("local_scan_urlconf", ""))
    if not (schema_ready or root_ready or (local_enabled and (local_command_ready or local_urlconf_ready))):
        _append_readiness_error(
            errors,
            workflow_spec_path,
            "-> business_api 需要至少配置一种 API 发现方式（schema_url、root_url、或 local_scan_command）",
        )

    for key in ("test_command", "generated_test_command", "project_internal_test_command"):
        fallback = DEFAULT_WORKFLOW_SPEC.get(key, [])
        configured = workflow_spec.get(key, fallback) if isinstance(workflow_spec, dict) else fallback
        if not _configured_string_list(configured):
            _append_readiness_error(
                errors,
                workflow_spec_path,
                f"-> {key} 未配置或仍是模板占位值",
            )

    if isinstance(semantic_model, dict):
        has_semantic_signal = any(
            [
                _configured_text(semantic_model.get("business_goal", "")),
                bool(semantic_model.get("roles", [])),
                bool(semantic_model.get("resources", [])),
                bool(semantic_model.get("actions", [])),
                bool(semantic_model.get("constraints", [])),
                bool(semantic_model.get("state_machines", [])),
                bool(semantic_model.get("permission_boundary", {})),
            ]
        )
        if not has_semantic_signal:
            warnings.append(f"{semantic_model_path} 当前仍是最小语义骨架，建议结合 overview/business 文档补充显式语义。")

    semantic_review = build_semantic_review(paths)
    semantic_blocking = semantic_review.get("blocking_items", [])
    semantic_warnings = semantic_review.get("warning_items", [])
    if isinstance(semantic_blocking, list):
        errors.extend([f"semantic_review -> {str(item)}" for item in semantic_blocking if str(item).strip()])
    if isinstance(semantic_warnings, list):
        warnings.extend([f"semantic_review -> {str(item)}" for item in semantic_warnings if str(item).strip()])

    ready = not errors
    return {
        "status": "ready" if ready else "needs_setup",
        "scope": "runtime_config_and_semantic_source_readiness",
        "owner": "workflow_runner",
        "interpretation": (
            "ready 只表示运行时配置完整，且 overview/business 等语义源在结构上可读；"
            "不表示当前 Codex session 已完成业务语义理解，也不表示可以跳过 INTRO 里的语义审核。"
        ),
        "next_step_hint": (
            "先由当前 Codex session 阅读 overview/business 与当前代码完成语义审核，再决定是否进入 run-auto。"
            if ready
            else "先修正 blocking_items，再重新检查 config_readiness。"
        ),
        "blocking_items": errors,
        "warning_items": warnings,
        "semantic_review": semantic_review,
        "session_semantic_audit": build_session_semantic_audit_requirement(),
        "checked_at": now_iso(),
    }


def ensure_runtime_config_ready(paths: Paths, spec: dict[str, Any] | None = None) -> None:
    readiness = build_onboarding_readiness(paths, spec)
    if readiness.get("status") == "ready":
        return
    blocking = readiness.get("blocking_items", [])
    preview = "; ".join(str(item) for item in blocking[:6])
    if len(blocking) > 6:
        preview += f"; 另外还有 {len(blocking) - 6} 项"
    raise WorkflowError(
        "运行前配置/语义审核未完成: "
        + preview
        + f"。请先检查 {rel_path(paths, paths.static_controls)}、{rel_path(paths, paths.workflow_spec)}，"
        + f"必要时参考 codex_skills/{SKILL_NAME}/references/onboarding.md。"
    )


def _read_existing_texts(root: Path, relative_paths: list[str], limit: int = 20000) -> dict[str, str]:
    payload: dict[str, str] = {}
    for rel in relative_paths:
        target = root / rel
        if not target.exists() or not target.is_file():
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        payload[rel] = text[:limit]
    return payload


SEMANTIC_SOURCE_SUFFIXES = (".md", ".dbml", ".dbdiagram", ".txt", ".json", ".yaml", ".yml")


def _semantic_source_files(folder: Path, root: Path, limit: int = 80) -> list[str]:
    if not folder.exists() or not folder.is_dir():
        return []
    rows: list[str] = []
    try:
        candidates = sorted(path for path in folder.rglob("*") if path.is_file())
    except OSError:
        return []
    for item in candidates:
        if item.suffix.lower() not in SEMANTIC_SOURCE_SUFFIXES:
            continue
        try:
            rel = item.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = item.as_posix()
        rows.append(rel)
        if len(rows) >= limit:
            break
    return rows


def build_semantic_review(paths: Paths) -> dict[str, Any]:
    semantic_dirs = semantic_directories(paths)
    overview_dir = semantic_dirs.get("overview")
    business_dir = semantic_dirs.get("business")
    permission_dir = semantic_dirs.get("permission")

    blocking_items: list[str] = []
    warning_items: list[str] = []

    overview_exists = bool(overview_dir and overview_dir.exists() and overview_dir.is_dir())
    business_exists = bool(business_dir and business_dir.exists() and business_dir.is_dir())
    permission_exists = bool(permission_dir and permission_dir.exists() and permission_dir.is_dir())

    overview_files = _semantic_source_files(overview_dir, paths.root) if overview_exists and overview_dir else []
    business_files = _semantic_source_files(business_dir, paths.root) if business_exists and business_dir else []
    permission_files = _semantic_source_files(permission_dir, paths.root) if permission_exists and permission_dir else []

    if overview_dir and not overview_exists:
        blocking_items.append("overview 语义目录不存在或不可读")
    elif overview_dir and not overview_files:
        blocking_items.append("overview 语义目录存在，但未发现可读语义文件")
    if business_dir and not business_exists:
        blocking_items.append("business 语义目录不存在或不可读")
    elif business_dir and not business_files:
        blocking_items.append("business 语义目录存在，但未发现可读语义文件")

    entities = sorted(business_entities(paths)) if business_exists else []
    entity_doc_review: list[dict[str, Any]] = []
    for entity in entities:
        present_suffixes: list[str] = []
        missing_suffixes: list[str] = []
        for suffix, path in zip(ENTITY_DOC_SUFFIXES, entity_doc_paths(paths, entity)):
            if path.exists():
                present_suffixes.append(suffix)
            else:
                missing_suffixes.append(suffix)
        entity_doc_review.append(
            {
                "entity": entity,
                "doc_count": len(present_suffixes),
                "present_suffixes": present_suffixes,
                "missing_suffixes": missing_suffixes,
                "quartet_ready": not missing_suffixes,
            }
        )
        if missing_suffixes:
            warning_items.append(f"业务实体 {entity} 缺少四件套: {', '.join(missing_suffixes)}")

    if business_files and not entities:
        warning_items.append("business 语义目录已有文件，但尚未识别出符合四件套命名规范的实体文档")
    if permission_dir and not permission_exists:
        warning_items.append("permission 语义目录已配置，但目录不存在或不可读")
    elif permission_dir and not permission_files:
        warning_items.append("permission 语义目录已配置，但当前未发现可读语义文件")

    status = "ready" if not blocking_items else "blocked"
    return {
        "status": status,
        "review_scope": "semantic_source_readiness",
        "review_owner": "workflow_runner",
        "review_limitations": [
            "只校验 overview/business/permission 语义源是否存在、可读，以及 business 四件套覆盖情况。",
            "不代替当前 Codex session 对业务语义、实体边界、API 意图的真实理解。",
        ],
        "overview_dir": rel_path(paths, overview_dir) if overview_dir else "",
        "business_dir": rel_path(paths, business_dir) if business_dir else "",
        "permission_dir": rel_path(paths, permission_dir) if permission_dir else "",
        "overview_file_count": len(overview_files),
        "business_file_count": len(business_files),
        "permission_file_count": len(permission_files),
        "overview_files_preview": overview_files[:12],
        "business_files_preview": business_files[:12],
        "permission_files_preview": permission_files[:12],
        "business_entity_count": len(entities),
        "entity_doc_review": entity_doc_review[:20],
        "blocking_items": blocking_items,
        "warning_items": warning_items,
        "checked_at": now_iso(),
    }


def detect_project_preset(paths: Paths) -> dict[str, Any]:
    root = paths.root
    scores = {preset: 0 for preset in SUPPORTED_INIT_PRESETS}
    evidence: dict[str, list[str]] = {preset: [] for preset in SUPPORTED_INIT_PRESETS}

    def hit(preset: str, score: int, reason: str) -> None:
        scores[preset] += score
        evidence[preset].append(reason)

    if (root / "manage.py").exists():
        hit("django", 4, "发现 manage.py")
    if (root / "config" / "settings.py").exists():
        hit("django", 4, "发现 config/settings.py")
    if (root / "apps").exists() and (root / "apps").is_dir():
        hit("django", 2, "发现 apps/ 目录")

    candidate_texts = _read_existing_texts(
        root,
        [
            "requirements.txt",
            "pyproject.toml",
            "config/settings.py",
            "manage.py",
            "app/main.py",
            "main.py",
            "src/main.py",
            "src/app/main.py",
        ],
    )
    combined = "\n".join(candidate_texts.values()).lower()
    if "django" in combined:
        hit("django", 3, "依赖或配置中出现 django")
    if "djangorestframework" in combined or "rest_framework" in combined:
        hit("django", 2, "依赖或配置中出现 DRF")
    if "drf_spectacular" in combined:
        hit("django", 2, "依赖或配置中出现 drf_spectacular")
    if "fastapi" in combined:
        hit("fastapi", 4, "依赖或代码中出现 fastapi")
    if "uvicorn" in combined:
        hit("fastapi", 1, "依赖中出现 uvicorn")
    if "from fastapi import fastapi" in combined or "fastapi(" in combined:
        hit("fastapi", 4, "发现 FastAPI 应用入口")
    if (root / "app").exists() and (root / "app").is_dir():
        hit("fastapi", 1, "发现 app/ 目录")
    if (root / "tests").exists() and (root / "tests").is_dir():
        hit("fastapi", 1, "发现 tests/ 目录")
    if (root / "pyproject.toml").exists():
        hit("generic", 1, "发现 pyproject.toml")

    ranked = sorted(
        ((preset, score) for preset, score in scores.items() if preset != "generic"),
        key=lambda item: item[1],
        reverse=True,
    )
    top_preset, top_score = ranked[0] if ranked else ("generic", 0)
    second_score = ranked[1][1] if len(ranked) > 1 else 0

    if top_score <= 0:
        detected = "generic"
        confidence = "low"
    elif top_score >= 7 and top_score - second_score >= 3:
        detected = top_preset
        confidence = "high"
    elif top_score >= 4:
        detected = top_preset
        confidence = "medium"
    else:
        detected = "generic"
        confidence = "low"

    framework_label = {
        "django": "Django / DRF",
        "fastapi": "FastAPI",
        "generic": "未明确识别，建议走 generic",
    }[detected]
    init_command = f"python3 codex_skills/{SKILL_NAME}/scripts/workflow_runner.py init --preset {detected}"
    return {
        "detected_preset": detected,
        "framework_label": framework_label,
        "confidence": confidence,
        "scores": scores,
        "evidence": {key: dedupe_keep_order(value) for key, value in evidence.items() if value},
        "recommended_init_command": init_command,
        "intro_prompt": f"识别到当前项目更像 {framework_label}。如果你确认，就执行 `{init_command}` 初始化配置模板。",
        "checked_at": now_iso(),
    }


def build_config_check_payload(paths: Paths) -> dict[str, Any]:
    if not paths.scaffold.exists():
        return {
            "status": "missing_scaffold",
            "scaffold_dir": paths.scaffold.as_posix(),
            "suggested_next_step": f"先执行 python3 codex_skills/{SKILL_NAME}/scripts/workflow_runner.py init",
            "checked_at": now_iso(),
        }
    readiness = build_onboarding_readiness(paths)
    payload = {
        "status": readiness.get("status"),
        "scaffold_dir": rel_path(paths, paths.scaffold),
        "blocking_items": readiness.get("blocking_items", []),
        "warning_items": readiness.get("warning_items", []),
        "semantic_review": readiness.get("semantic_review", {}),
        "readiness_scope": readiness.get("scope"),
        "readiness_interpretation": readiness.get("interpretation"),
        "session_semantic_audit": readiness.get("session_semantic_audit", build_session_semantic_audit_requirement()),
        "checked_at": readiness.get("checked_at", now_iso()),
    }
    if readiness.get("status") == "ready":
        payload["suggested_next_step"] = (
            f"{str(readiness.get('next_step_hint', '')).strip()} "
            f"确认通过后再执行 python3 codex_skills/{SKILL_NAME}/scripts/workflow_runner.py run-auto"
        ).strip()
    else:
        payload["suggested_next_step"] = f"修正配置后重新执行 python3 codex_skills/{SKILL_NAME}/scripts/workflow_runner.py check-config"
    return payload


def build_project_rules_payload(paths: Paths, internal_stage: int | None = None) -> dict[str, Any]:
    all_rules = load_project_rules(paths).get("rules", [])
    active_rules = active_project_rules(paths, internal_stage)
    logical_stage = logical_stage_for_internal(internal_stage) if internal_stage in STAGES else None
    return {
        "source": rel_path(paths, paths.project_rules),
        "logical_stage": logical_stage,
        "active_rules": [str(item.get("text", "")).strip() for item in active_rules if str(item.get("text", "")).strip()],
        "active_rule_count": len(active_rules),
        "total_rule_count": len(all_rules) if isinstance(all_rules, list) else 0,
    }


def build_stage_input_project_rules_payload(paths: Paths, stage: int) -> dict[str, Any]:
    logical_stage = logical_stage_for_internal(stage) if stage in STAGES else None
    active_rules = [
        str(item.get("text", "")).strip()
        for item in active_project_rules(paths, stage)
        if isinstance(item, dict) and str(item.get("text", "")).strip()
    ]
    return {
        "source": rel_path(paths, paths.project_rules),
        "logical_stage": logical_stage,
        "active_rules": active_rules,
        "active_rule_count": len(active_rules),
        "active_hash": stable_payload_hash(active_rules),
    }


def build_stage_input_config_refs(paths: Paths, stage: int) -> dict[str, Any]:
    project_rules = build_stage_input_project_rules_payload(paths, stage)
    return {
        "workflow_spec": {"path": paths.workflow_spec.as_posix(), "hash": sha256_file(paths.workflow_spec)},
        "static_controls": {"path": paths.static_controls.as_posix(), "hash": sha256_file(paths.static_controls)},
        "iteration_controls": {"path": paths.iteration_controls.as_posix(), "hash": sha256_file(paths.iteration_controls)},
        "semantic_model": {"path": paths.semantic_model.as_posix(), "hash": sha256_file(paths.semantic_model)},
        "project_rules": {"path": paths.project_rules.as_posix(), "active_hash": str(project_rules.get("active_hash", ""))},
    }


def build_stage_input_payload(
    paths: Paths,
    stage: int,
    *,
    generated_at: str | None = None,
    config_synced_at: str | None = None,
) -> dict[str, Any]:
    project_rules = build_stage_input_project_rules_payload(paths, stage)
    payload = {
        "stage": stage,
        "workflow_spec": paths.workflow_spec.as_posix(),
        "semantic_model": paths.semantic_model.as_posix(),
        "config_refs": build_stage_input_config_refs(paths, stage),
        "project_rules": project_rules,
        "generated_at": generated_at or now_iso(),
    }
    if config_synced_at:
        payload["config_synced_at"] = config_synced_at
    return payload


def _stage_input_compare_view(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    return {
        "stage": payload.get("stage"),
        "workflow_spec": payload.get("workflow_spec"),
        "semantic_model": payload.get("semantic_model"),
        "config_refs": payload.get("config_refs"),
        "project_rules": payload.get("project_rules"),
    }


def sync_stage_input_snapshots(paths: Paths, force: bool = False) -> int:
    updated = 0
    for stage in STAGES:
        target = paths.stage_input(stage)
        existing = read_json(target, None)
        if force or not isinstance(existing, dict):
            write_json(target, build_stage_input_payload(paths, stage))
            updated += 1
            continue

        generated_at = str(existing.get("generated_at", "")).strip() or now_iso()
        desired = build_stage_input_payload(paths, stage, generated_at=generated_at)
        if _stage_input_compare_view(existing) == _stage_input_compare_view(desired):
            continue

        write_json(
            target,
            build_stage_input_payload(paths, stage, generated_at=generated_at, config_synced_at=now_iso()),
        )
        updated += 1
    return updated


def resolve_commit_controls(paths: Paths, state: dict[str, Any] | None = None) -> dict[str, Any]:
    static_controls = load_static_controls(paths)
    static_commit_raw = static_controls.get("commit_controls", {})
    semantic_dir_names = resolve_semantic_dir_names(paths)
    project_layout = resolve_project_layout(paths)
    resolved = _normalize_commit_controls(
        static_commit_raw,
        business_dir=semantic_dir_names.get("business", DEFAULT_SEMANTIC_DIR_NAMES["business"]),
        code_directories=project_layout.get("code_directories", []),
        entry_files=project_layout.get("entry_files", []),
    )
    try:
        scaffold_prefix = paths.scaffold.resolve().relative_to(paths.root.resolve()).as_posix().rstrip("/") + "/"
    except ValueError:
        scaffold_prefix = ""
    if scaffold_prefix:
        resolved["git_add_blacklist_prefixes"] = dedupe_keep_order(
            resolved["git_add_blacklist_prefixes"] + [scaffold_prefix]
        )

    iteration_controls = read_json(paths.iteration_controls, DEFAULT_ITERATION_CONTROLS)
    if not isinstance(iteration_controls, dict):
        iteration_controls = DEFAULT_ITERATION_CONTROLS

    if state is None:
        loaded = read_json(paths.state, {})
        state = loaded if isinstance(loaded, dict) else {}

    iteration = None
    if isinstance(state, dict):
        raw_iteration = state.get("running_iteration")
        try:
            iteration = int(raw_iteration)
        except (TypeError, ValueError):
            iteration = None

    applied_iteration_override = False
    iteration_overrides = iteration_controls.get("iteration_overrides", {})
    if isinstance(iteration_overrides, dict) and iteration is not None:
        override = iteration_overrides.get(str(iteration))
        if isinstance(override, dict):
            commit_override_raw = override.get("commit_controls")
            if not isinstance(commit_override_raw, dict):
                # 兼容旧格式：直接在 iteration_overrides.<n> 下写白名单字段。
                commit_override_raw = {
                    "git_add_whitelist_patterns": override.get("git_add_whitelist_patterns", []),
                    "git_add_blacklist_prefixes": override.get("git_add_blacklist_prefixes", []),
                }
            override_whitelist = _normalize_string_list(commit_override_raw.get("git_add_whitelist_patterns"))
            override_blacklist = _normalize_string_list(commit_override_raw.get("git_add_blacklist_prefixes"))
            if override_whitelist:
                resolved["git_add_whitelist_patterns"] = dedupe_keep_order(
                    resolved["git_add_whitelist_patterns"] + override_whitelist
                )
            if override_blacklist:
                resolved["git_add_blacklist_prefixes"] = dedupe_keep_order(
                    resolved["git_add_blacklist_prefixes"] + override_blacklist
                )
            applied_iteration_override = bool(override_whitelist or override_blacklist)

    resolved["meta"] = {
        "source": {
            "static": paths.static_controls.as_posix(),
            "iteration": paths.iteration_controls.as_posix(),
        },
        "merge_strategy": "union",
        "iteration": iteration,
        "iteration_override_applied": applied_iteration_override,
    }
    return resolved


def append_event(paths: Paths, event_type: str, payload: dict[str, Any]) -> str:
    event = {
        "event_id": str(uuid.uuid4()),
        "timestamp": now_iso(),
        "type": event_type,
        "payload": payload,
    }
    paths.events.parent.mkdir(parents=True, exist_ok=True)
    with paths.events.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event["event_id"]


def sha256_file(path: Path) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(8192)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def stable_payload_hash(payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def run_git(paths: Paths, args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(paths.root),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout.rstrip("\n"), proc.stderr.rstrip("\n")


def rel_path(paths: Paths, target: Path) -> str:
    try:
        return target.resolve().relative_to(paths.root.resolve()).as_posix()
    except ValueError:
        return target.resolve().as_posix()


def logical_stage_for_internal(stage: Any) -> int:
    try:
        normalized = int(stage)
    except (TypeError, ValueError):
        return 0
    return INTERNAL_TO_LOGICAL_STAGE.get(normalized, 0)


def logical_stage_name(stage: Any) -> str:
    return LOGICAL_STAGE_NAMES.get(logical_stage_for_internal(stage), "未知阶段")


def internal_stage_for_logical(logical_stage: int, current_internal_stage: int | None = None) -> int:
    if logical_stage not in LOGICAL_STAGES:
        raise WorkflowError(f"逻辑阶段必须在 0-7，当前收到: {logical_stage}")
    if current_internal_stage is not None and logical_stage_for_internal(current_internal_stage) == logical_stage:
        return current_internal_stage
    return LOGICAL_TO_INTERNAL_STAGE_START[logical_stage]


def display_action_name(action: str) -> str:
    token = str(action).strip()
    if token == "run_stage8":
        return "enter_rollback"
    return token


def display_action_names(actions: Any) -> list[str]:
    if not isinstance(actions, list):
        return []
    normalized: list[str] = []
    for item in actions:
        token = display_action_name(str(item))
        if token:
            normalized.append(token)
    return dedupe_keep_order(normalized)


def generated_test_file_path(paths: Paths) -> Path:
    return paths.scaffold / "tests" / "generated" / GENERATED_TEST_FILENAME


def generated_event_notes_path(paths: Paths) -> Path:
    return paths.scaffold / "cases" / GENERATED_EVENT_NOTES_FILENAME


def stage4_business_code_fields(spec: dict[str, Any]) -> list[str]:
    raw = spec.get("business_code_fields", [])
    fields: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            token = str(item).strip()
            if token:
                fields.append(token)
    if not fields:
        fields = ["business_code", "business_detail_code"]
    return dedupe_keep_order(fields)


def stage4_business_code_strict(spec: dict[str, Any]) -> bool:
    return bool(spec.get("stage4_business_code_strict", False))


def stage4_semantic_test_required(spec: dict[str, Any]) -> bool:
    return bool(spec.get("stage4_semantic_test_required", True))


def stage4_case_design_mode(spec: dict[str, Any]) -> str:
    return "session"


def semantic_directories(paths: Paths) -> dict[str, Path]:
    resolved: dict[str, Path] = {}
    for key, dirname in resolve_semantic_dir_names(paths).items():
        token = dirname.replace("\\", "/").strip().strip("/")
        if not token:
            continue
        resolved[key] = paths.root / token
    return resolved


def missing_semantic_directories(paths: Paths) -> list[str]:
    missing: list[str] = []
    semantic_dirs = semantic_directories(paths)
    for key in REQUIRED_SEMANTIC_DIR_KEYS:
        folder = semantic_dirs.get(key)
        if folder is None:
            missing.append(f"<unconfigured:{key}>")
            continue
        if not folder.exists() or not folder.is_dir():
            missing.append(rel_path(paths, folder))
    return sorted(missing)


def ensure_semantic_directories_for_stage(paths: Paths, stage: int) -> None:
    if stage not in SEMANTIC_BLOCKED_STAGES:
        return
    missing = missing_semantic_directories(paths)
    if missing:
        raise WorkflowError(f"stage{stage} 阻断：缺少业务语义目录 {missing}")


def decode_git_path_token(raw_path: str) -> str:
    token = raw_path.strip()
    if not token:
        return ""

    if token.startswith('"') and token.endswith('"') and len(token) >= 2:
        escaped = token[1:-1]
        try:
            # git status --porcelain may emit C-style escaped bytes when quotePath=true.
            decoded = escaped.encode("utf-8").decode("unicode_escape")
            try:
                token = decoded.encode("latin1").decode("utf-8")
            except UnicodeDecodeError:
                token = decoded
        except UnicodeDecodeError:
            token = escaped

    return token.replace("\\", "/")


def git_changed_paths(paths: Paths) -> list[str]:
    rc, stdout, _ = run_git(paths, ["status", "--porcelain"])
    if rc != 0:
        return []

    changed: list[str] = []
    for raw in stdout.splitlines():
        line = raw.rstrip()
        if not line:
            continue

        candidate = ""
        if len(line) >= 4 and line[2] == " ":
            candidate = line[3:]
        else:
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                candidate = parts[1]

        if " -> " in candidate:
            candidate = candidate.split(" -> ", 1)[1]

        normalized = decode_git_path_token(candidate)
        if normalized:
            changed.append(normalized)

    return changed


def dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def normalize_api_key(api_key: str) -> str:
    token = " ".join(api_key.strip().split())
    if not token:
        return ""
    parts = token.split(" ", 1)
    if len(parts) == 1:
        return parts[0].upper()
    return f"{parts[0].upper()} {parts[1]}"


def normalize_api_pattern(pattern: str) -> str:
    token = " ".join(pattern.strip().split())
    if not token:
        return ""
    parts = token.split(" ", 1)
    if len(parts) == 2:
        return f"{parts[0].upper()} {parts[1]}"
    return token


def configured_ignore_api_patterns(spec: dict[str, Any]) -> list[str]:
    business_api = spec.get("business_api", {}) if isinstance(spec, dict) else {}
    raw_patterns = business_api.get("ignore_api_patterns", []) if isinstance(business_api, dict) else []
    if not isinstance(raw_patterns, list):
        return []
    patterns: list[str] = []
    for item in raw_patterns:
        if not isinstance(item, str):
            continue
        normalized = normalize_api_pattern(item)
        if normalized:
            patterns.append(normalized)
    return dedupe_keep_order(patterns)


def api_key_matches_pattern(api_key: str, pattern: str) -> bool:
    normalized_key = normalize_api_key(api_key)
    normalized_pattern = normalize_api_pattern(pattern)
    if not normalized_key or not normalized_pattern:
        return False

    parts = normalized_key.split(" ", 1)
    path = parts[1] if len(parts) == 2 else normalized_key
    if " " not in normalized_pattern:
        return fnmatch.fnmatch(path, normalized_pattern)
    return fnmatch.fnmatch(normalized_key, normalized_pattern)


def filter_api_keys(api_keys: list[str], ignore_patterns: list[str]) -> list[str]:
    normalized_patterns: list[str] = []
    for pattern in ignore_patterns:
        normalized_pattern = normalize_api_pattern(pattern)
        if normalized_pattern:
            normalized_patterns.append(normalized_pattern)
    kept: list[str] = []
    for item in api_keys:
        normalized = normalize_api_key(str(item))
        if not normalized:
            continue
        if any(api_key_matches_pattern(normalized, pattern) for pattern in normalized_patterns):
            continue
        kept.append(normalized)
    return dedupe_keep_order(kept)


def _prune_ignored_api_registry_items(
    api_registry: dict[str, Any],
    ignore_patterns: list[str],
) -> tuple[list[str], bool]:
    items = api_registry.get("items", []) if isinstance(api_registry, dict) else []
    if not isinstance(items, list):
        return [], False

    changed = False
    removed: list[str] = []
    kept_items: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            kept_items.append(item)
            continue
        raw_key = item.get("api_key")
        if not isinstance(raw_key, str):
            kept_items.append(item)
            continue

        normalized_key = normalize_api_key(raw_key)
        if normalized_key and any(api_key_matches_pattern(normalized_key, pattern) for pattern in ignore_patterns):
            removed.append(normalized_key)
            changed = True
            continue

        current_item = item
        if normalized_key and raw_key != normalized_key:
            current_item = dict(item)
            current_item["api_key"] = normalized_key
            changed = True
        kept_items.append(current_item)

    if changed:
        api_registry["items"] = kept_items
    return sorted(set(removed)), changed


def normalize_entity_name(raw: str) -> str:
    token = raw.strip().lower().replace("-", "_")
    token = re.sub(r"[^a-z0-9_]+", "", token).strip("_")
    if not token:
        return ""
    if re.fullmatch(r"v\d+", token):
        return ""
    if token.endswith("ies") and len(token) > 3:
        token = token[:-3] + "y"
    elif token.endswith("s") and len(token) > 1 and not token.endswith("ss"):
        token = token[:-1]
    return token


def business_entities(paths: Paths) -> set[str]:
    business_dir = semantic_directories(paths)["business"]
    if not business_dir.exists() or not business_dir.is_dir():
        return set()

    entities: set[str] = set()
    for suffix in ENTITY_DOC_SUFFIXES:
        marker = f"_{suffix}"
        for item in business_dir.glob(f"*{marker}"):
            name = item.name[: -len(marker)]
            entity = normalize_entity_name(name)
            if entity:
                entities.add(entity)
    return entities


def semantic_model_entities(semantic_model: dict[str, Any]) -> list[str]:
    resources = semantic_model.get("resources", []) if isinstance(semantic_model, dict) else []
    return dedupe_keep_order(
        [normalize_entity_name(str(item)) for item in resources if normalize_entity_name(str(item))]
    )


def known_business_entities(paths: Paths, semantic_model: dict[str, Any]) -> list[str]:
    return dedupe_keep_order(sorted(business_entities(paths)) + semantic_model_entities(semantic_model))


def unambiguous_business_entity(paths: Paths, semantic_model: dict[str, Any]) -> str:
    entities = known_business_entities(paths, semantic_model)
    return entities[0] if len(entities) == 1 else ""


def entity_doc_paths(paths: Paths, entity: str) -> list[Path]:
    business_dir = semantic_directories(paths)["business"]
    return [business_dir / f"{entity}_{suffix}" for suffix in ENTITY_DOC_SUFFIXES]


def resolve_entities(candidates: list[str], available_entities: set[str]) -> list[str]:
    if not candidates:
        return []
    if not available_entities:
        return dedupe_keep_order(candidates)

    resolved: list[str] = []
    for candidate in candidates:
        if candidate in available_entities:
            resolved.append(candidate)
            continue
        for entity in sorted(available_entities):
            if candidate.startswith(f"{entity}_") or candidate == f"{entity}s":
                resolved.append(entity)
                break
    return dedupe_keep_order(resolved)


def pluralize_entity_slug(entity: str) -> str:
    token = entity.strip().lower().replace("_", "-")
    if not token:
        return "todos"
    if token.endswith("y") and len(token) > 1 and token[-2] not in "aeiou":
        return f"{token[:-1]}ies"
    if token.endswith("s"):
        return token
    return f"{token}s"


def normalize_business_code_list(raw: Any, allowed_codes: list[str] | None = None) -> list[str]:
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = raw
    else:
        values = []

    normalized = dedupe_keep_order(
        [
            str(item).strip().upper().replace("-", "_")
            for item in values
            if str(item).strip()
        ]
    )
    if not allowed_codes:
        return normalized

    allowed = {
        str(item).strip().upper().replace("-", "_")
        for item in allowed_codes
        if str(item).strip()
    }
    return [item for item in normalized if item in allowed]

def _normalize_stage0_session_api_candidate(
    paths: Paths,
    raw: dict[str, Any],
    idx: int,
    semantic_model: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    api_key = normalize_api_key(str(raw.get("api_key", "")))
    if not api_key or " " not in api_key:
        return None

    method, path = api_key.split(" ", 1)
    if method.lower() not in HTTP_METHODS:
        return None
    if not path.startswith("/"):
        return None

    entity = normalize_entity_name(str(raw.get("entity", "")))
    if not entity:
        entity = unambiguous_business_entity(paths, semantic_model)
    expected_business_codes = normalize_business_code_list(
        raw.get("expected_business_codes", raw.get("semantic_codes", []))
    )
    semantic_title = str(raw.get("semantic_title", raw.get("title", ""))).strip()
    semantic_description = str(raw.get("semantic_description", "")).strip()

    reason = str(raw.get("reason", "")).strip()
    if not reason:
        reason = "由当前 Codex session 基于业务描述与项目文档语义提名，待人工 review"

    risk = str(raw.get("risk", "medium")).strip().lower()
    if risk not in {"low", "medium", "high"}:
        risk = "medium"

    return {
        "entity": entity,
        "api_key": api_key,
        "expected_business_codes": expected_business_codes,
        "semantic_title": semantic_title,
        "semantic_description": semantic_description,
        "reason": reason,
        "risk": risk,
        "source": "session_semantic",
        "index": idx,
    }


def load_stage0_session_api_candidates(
    paths: Paths,
    semantic_model: dict[str, Any],
    existing_api_keys: set[str],
    observed_api_keys: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    rows = read_jsonl(paths.session_api_candidates)
    normalized: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_api_keys: set[str] = set()

    for idx, item in enumerate(rows, start=1):
        if not isinstance(item, dict):
            warnings.append(f"line_{idx}: not_object")
            continue

        candidate = _normalize_stage0_session_api_candidate(paths, item, idx, semantic_model)
        if not isinstance(candidate, dict):
            warnings.append(f"line_{idx}: invalid_api_key")
            continue

        api_key = str(candidate.get("api_key", "")).strip()
        if not api_key:
            warnings.append(f"line_{idx}: empty_api_key")
            continue
        if api_key in seen_api_keys:
            warnings.append(f"line_{idx}: duplicated_api_key")
            continue
        if api_key in existing_api_keys:
            warnings.append(f"line_{idx}: already_registered")
            continue
        if api_key in observed_api_keys:
            warnings.append(f"line_{idx}: already_observed_in_codebase")
            continue

        seen_api_keys.add(api_key)
        normalized.append(candidate)

    return normalized, warnings


def infer_touched_entities(
    paths: Paths,
    stage0_artifact: dict[str, Any],
    stage1_artifact: dict[str, Any],
    stage2_artifact: dict[str, Any],
) -> list[str]:
    available = business_entities(paths)
    semantic_model = read_json(paths.semantic_model, {})
    semantic_entities = semantic_model_entities(semantic_model if isinstance(semantic_model, dict) else {})
    if semantic_entities:
        available = set(available) | set(semantic_entities)
    touched: list[str] = []

    for artifact in (stage1_artifact, stage0_artifact):
        entities = artifact.get("touched_entities", [])
        if not isinstance(entities, list):
            continue
        for item in entities:
            normalized = normalize_entity_name(str(item))
            if normalized:
                touched.append(normalized)

    stage0_candidates = stage0_artifact.get("api_candidates", [])
    if isinstance(stage0_candidates, list):
        for item in stage0_candidates:
            if not isinstance(item, dict):
                continue
            normalized = normalize_entity_name(str(item.get("entity", "")))
            if normalized:
                touched.append(normalized)

    if not touched and available:
        for entity in semantic_entities:
            mapped = resolve_entities([entity], available)
            if mapped:
                touched.extend(mapped)
                break

    return dedupe_keep_order(touched)


def load_stage6_entity_review(paths: Paths) -> dict[str, Any]:
    raw = read_json(paths.session_entity_review, DEFAULT_ENTITY_REVIEW)
    warnings: list[str] = []

    mode = "append"
    entities_raw: list[Any] = []
    reason = ""
    if isinstance(raw, dict):
        candidate_mode = str(raw.get("mode", "append")).strip().lower()
        if candidate_mode in {"append", "replace"}:
            mode = candidate_mode
        else:
            warnings.append("invalid_mode")
        source_entities = raw.get("entities", [])
        if isinstance(source_entities, list):
            entities_raw = source_entities
        else:
            warnings.append("entities_not_list")
        reason = str(raw.get("reason", "")).strip()
    else:
        warnings.append("review_payload_not_object")

    entities: list[str] = []
    for item in entities_raw:
        normalized = normalize_entity_name(str(item))
        if normalized:
            entities.append(normalized)
    entities = dedupe_keep_order(entities)

    return {
        "file": rel_path(paths, paths.session_entity_review),
        "mode": mode,
        "entities": entities,
        "reason": reason or "N/A",
        "warnings": warnings,
        "has_override": bool(entities),
    }


def apply_stage6_entity_review(inferred_entities: list[str], review: dict[str, Any]) -> list[str]:
    inferred = dedupe_keep_order([normalize_entity_name(item) for item in inferred_entities if normalize_entity_name(item)])
    override_entities = review.get("entities", []) if isinstance(review, dict) else []
    if not isinstance(override_entities, list):
        override_entities = []

    mode = str(review.get("mode", "append")).strip().lower() if isinstance(review, dict) else "append"
    if mode == "replace":
        if override_entities:
            return dedupe_keep_order([str(item) for item in override_entities if str(item).strip()])
        return inferred
    return dedupe_keep_order(inferred + [str(item) for item in override_entities if str(item).strip()])


def build_stage6_entity_review_summary(
    paths: Paths,
    inferred_entities: list[str],
    final_entities: list[str],
    review: dict[str, Any],
    focus_api_keys: list[str],
) -> dict[str, Any]:
    review_mode = str(review.get("mode", "append")) if isinstance(review, dict) else "append"
    review_entities = review.get("entities", []) if isinstance(review, dict) and isinstance(review.get("entities"), list) else []
    review_warnings = review.get("warnings", []) if isinstance(review, dict) and isinstance(review.get("warnings"), list) else []
    review_file = str(review.get("file", rel_path(paths, paths.session_entity_review))) if isinstance(review, dict) else rel_path(paths, paths.session_entity_review)
    review_reason = str(review.get("reason", "N/A")) if isinstance(review, dict) else "N/A"
    return {
        "inference_mode": "session_semantic_primary",
        "inference_sources": [
            "stage0/stage1 touched_entities",
            "stage0 api_candidates.entity",
            "semantic_model.resources fallback",
        ],
        "focus_api_keys": focus_api_keys,
        "inferred_entities": inferred_entities,
        "review_file": review_file,
        "review_mode": review_mode,
        "review_entities": review_entities,
        "review_reason": review_reason,
        "review_warnings": review_warnings,
        "final_entities": final_entities,
        "needs_user_review": True,
        "review_status": "pending_user_review",
    }


def _path_modified_after_epoch(path: Path, epoch: float | None) -> bool:
    if epoch is None:
        return False
    mtime = file_mtime_epoch(path)
    if mtime is None:
        return False
    return mtime >= epoch


def build_entity_api_ref_index(*artifacts: dict[str, Any]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}

    def add(entity_raw: Any, refs_raw: Any) -> None:
        entity = normalize_entity_name(str(entity_raw))
        if not entity:
            return
        refs = dedupe_keep_order(
            [normalize_api_key(str(item)) for item in (refs_raw if isinstance(refs_raw, list) else []) if normalize_api_key(str(item))]
        )
        if not refs:
            return
        existing = index.setdefault(entity, [])
        index[entity] = dedupe_keep_order(existing + refs)

    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue

        candidates = artifact.get("api_candidates", [])
        if isinstance(candidates, list):
            for item in candidates:
                if isinstance(item, dict):
                    add(item.get("entity", ""), [item.get("api_key", "")])

        focus_api_items = artifact.get("focus_api_items", [])
        if isinstance(focus_api_items, list):
            for item in focus_api_items:
                if isinstance(item, dict):
                    add(item.get("entity", ""), [item.get("api_key", "")])

        business_events = artifact.get("business_events", [])
        if isinstance(business_events, list):
            for event in business_events:
                if isinstance(event, dict):
                    add(event.get("entity", ""), event.get("api_refs", []))

        for collection_name in ("candidate_cases", "generated_cases", "executed_cases"):
            rows = artifact.get(collection_name, [])
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict):
                    add(row.get("entity", ""), row.get("api_refs", []))

    return index


def _strip_stage6_trace_blocks(text: str, suffix: str) -> str:
    if not text:
        return ""
    if suffix.endswith(".md"):
        cleaned = re.sub(r"<!--\s*stage6_round_context::.*?-->\s*", "", text, flags=re.I)
        cleaned = re.sub(r"##\s*Stage6\s*同步上下文（可追溯）\s*```json.*?```", "", cleaned, flags=re.S)
        cleaned = re.sub(r"<!--\s*stage6_doc_sync::.*?::(start|end)\s*-->\s*", "", cleaned, flags=re.I)
        return cleaned.strip()
    if suffix == "schema.dbml":
        kept_lines: list[str] = []
        for line in text.splitlines():
            lowered = line.strip().lower()
            if lowered.startswith("// stage6_doc_sync::"):
                continue
            if lowered.startswith("// stage6_round_context::"):
                continue
            if lowered.startswith("// trace_context:"):
                continue
            if "non-destructive stage6 append" in lowered:
                continue
            kept_lines.append(line)
        return "\n".join(kept_lines).strip()
    return text.strip()


def evaluate_entity_doc_sync(
    paths: Paths,
    touched_entities: list[str],
    changed_paths: list[str],
    focus_api_keys: list[str] | None = None,
    round_started_at: str = "",
) -> dict[str, Any]:
    stage0_artifact = read_json(paths.stage_artifact(0), {})
    stage3_artifact = read_json(paths.stage_artifact(3), {})
    stage4_artifact = read_json(paths.stage_artifact(4), {})
    entity_api_refs = build_entity_api_ref_index(
        stage0_artifact if isinstance(stage0_artifact, dict) else {},
        stage3_artifact if isinstance(stage3_artifact, dict) else {},
        stage4_artifact if isinstance(stage4_artifact, dict) else {},
    )
    project_layout = resolve_project_layout(paths)
    entry_files = project_layout["entry_files"]
    code_prefixes = project_layout["code_directories"]
    changed_set = {item.replace("\\", "/") for item in changed_paths}
    code_changed = any(
        _path_is_entry_file(path, entry_files) or _path_has_prefix(path, code_prefixes)
        for path in changed_set
    )
    round_started_epoch = parse_iso_to_epoch(round_started_at)
    code_changed_this_round = code_changed
    if round_started_epoch is not None:
        code_changed_this_round = False
        for path in changed_set:
            if not (_path_is_entry_file(path, entry_files) or _path_has_prefix(path, code_prefixes)):
                continue
            abs_path = paths.root / path
            if _path_modified_after_epoch(abs_path, round_started_epoch):
                code_changed_this_round = True
                break

    missing_docs: dict[str, list[str]] = {}
    unsynced_entities: list[str] = []
    low_quality_docs: dict[str, list[str]] = {}
    semantic_unsynced_entities: dict[str, list[str]] = {}
    normalized_focus_api_keys = dedupe_keep_order(
        [normalize_api_key(item) for item in (focus_api_keys or []) if normalize_api_key(item)]
    )

    def is_low_quality_doc(text: str) -> bool:
        lowered = text.lower()
        markers = [
            "generated_by: stage6_auto_sync",
            "stage6 自动同步记录",
            "请补充",
            "需人工补充",
            "todo:",
            "[ ] todo",
            "- todo",
            "todo placeholder",
        ]
        return any(marker in lowered for marker in markers)

    for entity in touched_entities:
        docs = entity_doc_paths(paths, entity)
        rel_docs = [rel_path(paths, doc) for doc in docs]
        missing = [rel for rel, doc in zip(rel_docs, docs) if not doc.exists()]
        if missing:
            missing_docs[entity] = missing
            continue
        low_quality = []
        semantic_text_blocks: list[str] = []
        for rel, doc in zip(rel_docs, docs):
            text = _safe_read_text(doc)
            suffix = doc.name.split(f"{entity}_", 1)[-1] if f"{entity}_" in doc.name else doc.suffix.lstrip(".")
            semantic_text = _strip_stage6_trace_blocks(text, suffix)
            if semantic_text:
                semantic_text_blocks.append(semantic_text.lower())
            if text and is_low_quality_doc(text):
                low_quality.append(rel)
        if low_quality:
            low_quality_docs[entity] = low_quality
        entity_related_changes = _entity_related_code_paths(paths, entity, changed_paths)
        entity_code_changed_this_round = code_changed_this_round
        if entity_related_changes:
            entity_code_changed_this_round = False
            for rel in entity_related_changes:
                if not (_path_is_entry_file(rel, entry_files) or _path_has_prefix(rel, code_prefixes)):
                    continue
                abs_path = paths.root / rel
                if round_started_epoch is None:
                    entity_code_changed_this_round = True
                    break
                if _path_modified_after_epoch(abs_path, round_started_epoch):
                    entity_code_changed_this_round = True
                    break
        docs_updated_this_round = any(_path_modified_after_epoch(doc, round_started_epoch) for doc in docs) if round_started_epoch is not None else any(rel in changed_set for rel in rel_docs)
        if entity_code_changed_this_round and not docs_updated_this_round:
            unsynced_entities.append(entity)
        entity_expected_api_keys = list(entity_api_refs.get(entity, []))
        if not entity_expected_api_keys and len(touched_entities) == 1 and entity == normalize_entity_name(str(touched_entities[0])):
            entity_expected_api_keys = list(normalized_focus_api_keys)
        if entity_expected_api_keys:
            combined_semantic_text = "\n".join(semantic_text_blocks)
            semantic_issues: list[str] = []
            if not docs_updated_this_round:
                semantic_issues.append("docs_not_updated_this_round")
            missing_api_refs = [api for api in entity_expected_api_keys if api.lower() not in combined_semantic_text]
            if missing_api_refs:
                semantic_issues.extend(missing_api_refs)
            if semantic_issues:
                semantic_unsynced_entities[entity] = dedupe_keep_order(semantic_issues)

    return {
        "touched_entities": touched_entities,
        "code_changed": code_changed,
        "code_changed_this_round": code_changed_this_round,
        "round_started_at": round_started_at or "N/A",
        "missing_docs": missing_docs,
        "unsynced_entities": unsynced_entities,
        "low_quality_docs": low_quality_docs,
        "semantic_unsynced_entities": semantic_unsynced_entities,
        "passed": not missing_docs and not unsynced_entities and not low_quality_docs and not semantic_unsynced_entities,
    }


def _entity_related_code_paths(paths: Paths, entity: str, changed_paths: list[str]) -> list[str]:
    token = entity.strip().lower()
    if not token:
        return []
    project_layout = resolve_project_layout(paths)
    entry_files = project_layout["entry_files"]
    code_prefixes = project_layout["code_directories"]
    plural = pluralize_entity_slug(token).replace("-", "_")
    camel = "".join(part.capitalize() for part in token.split("_") if part)
    related: list[str] = []
    for path in changed_paths:
        normalized = path.replace("\\", "/")
        lower = normalized.lower()
        if not (_path_is_entry_file(normalized, entry_files) or _path_has_prefix(normalized, code_prefixes)):
            continue
        hit = token in lower or token.replace("_", "-") in lower or plural in lower
        if not hit:
            abs_path = paths.root / normalized
            if abs_path.exists() and abs_path.is_file() and abs_path.suffix in {".py", ".md", ".dbml", ".txt"}:
                text = _safe_read_text(abs_path, max_bytes=200_000)
                low = text.lower()
                if token in low or camel in text or f"{camel}Status" in text:
                    hit = True
        if hit:
            related.append(normalized)
    return dedupe_keep_order(related)


def _entity_canonical_code_paths(paths: Paths, entity: str) -> list[str]:
    token = normalize_entity_name(entity)
    if not token:
        return []
    project_layout = resolve_project_layout(paths)
    entity_code_roots = project_layout["entity_code_roots"]
    candidates: list[str] = []
    app_tokens = dedupe_keep_order([token, pluralize_entity_slug(token).replace("-", "_")])
    filenames = ("models.py", "serializers.py", "views.py", "urls.py", "tests.py")
    for root_name in entity_code_roots:
        root_dir = paths.root / root_name
        if not root_dir.exists() or not root_dir.is_dir():
            continue
        for app_token in app_tokens:
            app_dir = root_dir / app_token
            if not app_dir.exists() or not app_dir.is_dir():
                continue
            for filename in filenames:
                path = app_dir / filename
                if path.exists() and path.is_file():
                    candidates.append(rel_path(paths, path))
    return dedupe_keep_order(candidates)


def _safe_read_text(path: Path, max_bytes: int = 400_000) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > max_bytes:
        data = data[:max_bytes]
    return data.decode("utf-8", errors="ignore")


def _entity_camel_name(entity: str) -> str:
    parts = [part for part in entity.split("_") if part]
    return "".join(part.capitalize() for part in parts) or entity.capitalize()


def _extract_python_class_block(text: str, class_name: str) -> str:
    if not text.strip():
        return ""
    lines = text.splitlines()
    start = -1
    class_indent = 0
    class_pattern = re.compile(rf"^\s*class\s+{re.escape(class_name)}\s*(\(|:)")
    for idx, line in enumerate(lines):
        if class_pattern.search(line):
            start = idx
            class_indent = len(line) - len(line.lstrip(" "))
            break
    if start < 0:
        return ""
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        line = lines[idx]
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= class_indent and stripped.startswith("class "):
            end = idx
            break
    return "\n".join(lines[start:end]).strip()


def _parse_model_fields_from_block(class_block: str) -> list[dict[str, Any]]:
    if not class_block:
        return []
    lines = class_block.splitlines()
    fields: list[dict[str, Any]] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        match = re.match(r"^\s+(\w+)\s*=\s*models\.(\w+)\(", line)
        if not match:
            idx += 1
            continue

        field_name = match.group(1)
        field_type = match.group(2)
        chunk_lines = [line]
        paren_balance = line.count("(") - line.count(")")
        idx += 1
        while idx < len(lines) and paren_balance > 0:
            chunk_lines.append(lines[idx])
            paren_balance += lines[idx].count("(") - lines[idx].count(")")
            idx += 1
        chunk = "\n".join(chunk_lines)

        verbose_match = re.search(r"models\.\w+\(\s*['\"]([^'\"]+)['\"]", chunk)
        max_length_match = re.search(r"max_length\s*=\s*(\d+)", chunk)
        max_digits_match = re.search(r"max_digits\s*=\s*(\d+)", chunk)
        decimal_places_match = re.search(r"decimal_places\s*=\s*(\d+)", chunk)
        default_match = re.search(r"default\s*=\s*([^,\n)]+)", chunk)
        related_model_match = re.search(r"models\.ForeignKey\(\s*([^,\n)]+)", chunk)
        choices_class_match = re.search(r"choices\s*=\s*(\w+)\.choices", chunk)

        constraints: list[str] = []
        if re.search(r"\bunique\s*=\s*True\b", chunk):
            constraints.append("unique")
        if re.search(r"\bnull\s*=\s*True\b", chunk):
            constraints.append("nullable")
        if re.search(r"\bblank\s*=\s*True\b", chunk):
            constraints.append("blank")
        if max_length_match:
            constraints.append(f"max_length={max_length_match.group(1)}")
        if default_match:
            constraints.append(f"default={default_match.group(1).strip()}")

        fields.append(
            {
                "name": field_name,
                "type": field_type,
                "verbose": verbose_match.group(1).strip() if verbose_match else "",
                "max_length": int(max_length_match.group(1)) if max_length_match else None,
                "max_digits": int(max_digits_match.group(1)) if max_digits_match else None,
                "decimal_places": int(decimal_places_match.group(1)) if decimal_places_match else None,
                "default": default_match.group(1).strip() if default_match else "",
                "constraints": constraints,
                "is_relation": field_type.lower() == "foreignkey",
                "related_model": related_model_match.group(1).strip() if related_model_match else "",
                "choices_class": choices_class_match.group(1).strip() if choices_class_match else "",
            }
        )

    return fields


def _parse_textchoices_values(text: str, class_name: str) -> list[dict[str, str]]:
    block = _extract_python_class_block(text, class_name)
    if not block:
        return []
    rows: list[dict[str, str]] = []
    for line in block.splitlines():
        match = re.match(r"^\s+([A-Z0-9_]+)\s*=\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]", line)
        if not match:
            continue
        rows.append({"key": match.group(1), "value": match.group(2), "label": match.group(3)})
    return rows


def _parse_db_table_from_model_block(class_block: str) -> str:
    if not class_block:
        return ""
    match = re.search(r"db_table\s*=\s*['\"]([^'\"]+)['\"]", class_block)
    if match:
        return match.group(1).strip()
    return ""


def _parse_permissions_from_model_block(class_block: str) -> list[dict[str, str]]:
    if not class_block:
        return []
    perms_match = re.search(r"permissions\s*=\s*\[(.*?)\]", class_block, flags=re.S)
    if not perms_match:
        return []
    perms_block = perms_match.group(1)
    rows = []
    for codename, label in re.findall(r"\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)", perms_block):
        rows.append({"codename": codename.strip(), "label": label.strip()})
    return rows


def _parse_serializer_fields_from_text(text: str, entity: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    camel = _entity_camel_name(entity)
    class_pattern = re.compile(r"^\s*class\s+(\w*Serializer)\s*\(", flags=re.M)
    for class_name in class_pattern.findall(text):
        if camel not in class_name:
            continue
        block = _extract_python_class_block(text, class_name)
        if not block:
            continue
        fields_match = re.search(r"fields\s*=\s*\[(.*?)\]", block, flags=re.S)
        if not fields_match:
            continue
        raw = fields_match.group(1)
        fields = [item.strip() for item in re.findall(r"['\"]([^'\"]+)['\"]", raw)]
        result[class_name] = fields
    return result


def _filter_entity_stage_rows(rows: Any, entity: str) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return selected
    entity_token = normalize_entity_name(entity)
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_entity = normalize_entity_name(str(row.get("entity", "")))
        if row_entity == entity_token:
            selected.append(dict(row))
    return selected


def _entity_case_execution_summary(
    entity_cases: list[dict[str, Any]],
    stage5_artifact: dict[str, Any],
) -> dict[str, Any]:
    case_ids = [
        str(item.get("case_id", "")).strip()
        for item in entity_cases
        if isinstance(item, dict) and str(item.get("case_id", "")).strip()
    ]
    case_id_set = set(case_ids)
    failed_cases = [
        str(item).strip()
        for item in stage5_artifact.get("failed_cases", [])
        if isinstance(item, str) and str(item).strip() in case_id_set
    ] if isinstance(stage5_artifact, dict) else []

    project_sink = stage5_artifact.get("project_test_sink", {}) if isinstance(stage5_artifact, dict) else {}
    sink_mapping = project_sink.get("mapping", []) if isinstance(project_sink, dict) else []
    matched_case_ids: list[str] = []
    unmatched_case_ids: list[str] = []
    matched_tests_by_case: dict[str, list[str]] = {}
    if isinstance(sink_mapping, list):
        for item in sink_mapping:
            if not isinstance(item, dict):
                continue
            case_id = str(item.get("case_id", "")).strip()
            if case_id not in case_id_set:
                continue
            matched_tests = item.get("matched_tests", [])
            test_labels = [
                f"{str(test.get('test_file', '')).strip()}::{str(test.get('test_name', '')).strip()}"
                for test in matched_tests
                if isinstance(test, dict) and str(test.get("test_name", "")).strip()
            ]
            if test_labels:
                matched_case_ids.append(case_id)
                matched_tests_by_case[case_id] = test_labels
            else:
                unmatched_case_ids.append(case_id)

    executed_count = int(stage5_artifact.get("executed_cases", 0)) if isinstance(stage5_artifact, dict) else 0
    regression = stage5_artifact.get("regression_summary", {}) if isinstance(stage5_artifact, dict) else {}
    generated_suite = regression.get("generated_suite", {}) if isinstance(regression, dict) else {}
    return {
        "case_count": len(case_ids),
        "executed_cases": executed_count,
        "failed_cases": dedupe_keep_order(failed_cases),
        "matched_case_ids": dedupe_keep_order(matched_case_ids),
        "unmatched_case_ids": dedupe_keep_order(unmatched_case_ids),
        "matched_tests_by_case": matched_tests_by_case,
        "generated_suite": generated_suite if isinstance(generated_suite, dict) else {},
    }


def _entity_semantic_artifact_context(
    paths: Paths,
    entity: str,
) -> dict[str, Any]:
    stage0_artifact = read_json(paths.stage_artifact(0), {})
    stage1_artifact = read_json(paths.stage_artifact(1), {})
    stage3_artifact = read_json(paths.stage_artifact(3), {})
    stage4_artifact = read_json(paths.stage_artifact(4), {})
    stage5_artifact = read_json(paths.stage_artifact(5), {})
    semantic_model = read_json(paths.semantic_model, DEFAULT_SEMANTIC_MODEL)
    semantic_context = stage3_semantic_context_for_entity(
        entity,
        semantic_model if isinstance(semantic_model, dict) else {},
    )

    stage0_candidates = _filter_entity_stage_rows(
        stage0_artifact.get("api_candidates", []) if isinstance(stage0_artifact, dict) else [],
        entity,
    )
    business_events = _filter_entity_stage_rows(
        stage4_artifact.get("business_events", []) if isinstance(stage4_artifact, dict) else [],
        entity,
    )
    generated_cases = _filter_entity_stage_rows(
        stage4_artifact.get("generated_cases", []) if isinstance(stage4_artifact, dict) else [],
        entity,
    )
    if not business_events:
        business_events = _filter_entity_stage_rows(
            stage3_artifact.get("business_events", []) if isinstance(stage3_artifact, dict) else [],
            entity,
        )

    api_refs = dedupe_keep_order(
        [
            normalize_api_key(str(item.get("api_key", "")))
            for item in stage0_candidates
            if normalize_api_key(str(item.get("api_key", "")))
        ]
        + [
            normalize_api_key(str(api))
            for event in business_events
            if isinstance(event, dict)
            for api in (event.get("api_refs", []) if isinstance(event.get("api_refs"), list) else [])
            if normalize_api_key(str(api))
        ]
        + [
            normalize_api_key(str(api))
            for case in generated_cases
            if isinstance(case, dict)
            for api in (case.get("api_refs", []) if isinstance(case.get("api_refs"), list) else [])
            if normalize_api_key(str(api))
        ]
    )

    stage0_reasons = dedupe_keep_order(
        [str(item.get("reason", "")).strip() for item in stage0_candidates if str(item.get("reason", "")).strip()]
    )
    stage0_expected_codes = dedupe_keep_order(
        [
            code
            for item in stage0_candidates
            if isinstance(item, dict)
            for code in normalize_business_code_list(item.get("expected_business_codes", []))
        ]
    )
    required_codes = stage3_required_case_codes(stage0_artifact if isinstance(stage0_artifact, dict) else {})
    covered_codes = dedupe_keep_order(
        [
            str(code).strip()
            for event in business_events
            if isinstance(event, dict)
            for code in normalize_business_code_list(event.get("expected_business_codes", []))
        ]
    )
    missing_codes = [code for code in required_codes if code not in set(covered_codes)]

    changed_files = stage1_artifact.get("changed_files", []) if isinstance(stage1_artifact, dict) else []
    implementation_tasks = stage1_artifact.get("implementation_tasks", []) if isinstance(stage1_artifact, dict) else []
    entity_token = normalize_entity_name(entity)
    entity_related_changed_files = [
        str(path).strip()
        for path in changed_files
        if isinstance(path, str) and entity_token and entity_token in str(path).lower().replace("-", "_")
    ]
    entity_related_tasks = [
        str(task).strip()
        for task in implementation_tasks
        if isinstance(task, str) and entity_token and entity_token in str(task).lower().replace("-", "_")
    ]

    execution = _entity_case_execution_summary(generated_cases, stage5_artifact if isinstance(stage5_artifact, dict) else {})
    semantic_actions = semantic_model.get("actions", []) if isinstance(semantic_model, dict) else []
    semantic_roles = semantic_model.get("roles", []) if isinstance(semantic_model, dict) else []
    business_goal = str(semantic_model.get("business_goal", "")).strip() if isinstance(semantic_model, dict) else ""

    return {
        "business_goal": business_goal,
        "roles": [str(item).strip() for item in semantic_roles if isinstance(item, str) and str(item).strip()],
        "actions": [str(item).strip() for item in semantic_actions if isinstance(item, str) and str(item).strip()],
        "state_machine": semantic_context.get("state_machine", {}) if isinstance(semantic_context, dict) else {},
        "constraints": semantic_context.get("constraints", []) if isinstance(semantic_context, dict) else [],
        "permission_boundary": semantic_context.get("permission_boundary", {}) if isinstance(semantic_context, dict) else {},
        "stage0_candidates": stage0_candidates,
        "stage0_reasons": stage0_reasons,
        "stage0_expected_codes": stage0_expected_codes,
        "business_events": business_events,
        "generated_cases": generated_cases,
        "api_refs": api_refs,
        "required_codes": required_codes,
        "covered_codes": covered_codes,
        "missing_codes": missing_codes,
        "changed_files": dedupe_keep_order(entity_related_changed_files),
        "implementation_tasks": dedupe_keep_order(entity_related_tasks),
        "execution": execution,
    }


def _collect_entity_code_supplement(
    paths: Paths,
    entity: str,
    changed_paths: list[str],
) -> dict[str, Any]:
    related_paths = dedupe_keep_order(
        _entity_related_code_paths(paths, entity, changed_paths) + _entity_canonical_code_paths(paths, entity)
    )
    related_text: dict[str, str] = {}
    for rel in related_paths:
        abs_path = paths.root / rel
        if abs_path.exists() and abs_path.is_file():
            related_text[rel] = _safe_read_text(abs_path)

    merged_text = "\n\n".join(related_text.values())
    model_class_name = _entity_camel_name(entity)
    model_status_name = f"{model_class_name}Status"

    model_block = _extract_python_class_block(merged_text, model_class_name)
    model_fields = _parse_model_fields_from_block(model_block)
    if not any(row.get("name") == "id" for row in model_fields):
        model_fields = [{"name": "id", "type": "BigAutoField", "verbose": "ID", "constraints": ["pk"], "max_length": None, "default": "", "is_relation": False, "related_model": ""}] + model_fields

    status_choices = _parse_textchoices_values(merged_text, model_status_name)
    permissions = _parse_permissions_from_model_block(model_block)
    db_table = _parse_db_table_from_model_block(model_block) or pluralize_entity_slug(entity).replace("-", "_")
    serializer_fields = _parse_serializer_fields_from_text(merged_text, entity)

    unique_fields = [row["name"] for row in model_fields if "unique" in row.get("constraints", [])]
    default_status = ""
    for row in model_fields:
        if row.get("name") == "status" and row.get("default"):
            default_status = str(row.get("default", ""))
            break

    return {
        "model_fields": model_fields,
        "status_choices": status_choices,
        "permissions": permissions,
        "db_table": db_table,
        "serializer_fields": serializer_fields,
        "related_paths": related_paths,
        "unique_fields": unique_fields,
        "default_status": default_status,
    }


def _collect_entity_doc_context(
    paths: Paths,
    entity: str,
    changed_paths: list[str],
) -> dict[str, Any]:
    semantic_ctx = _entity_semantic_artifact_context(paths, entity)
    code_ctx = _collect_entity_code_supplement(paths, entity, changed_paths)
    source_artifacts = [
        rel_path(paths, paths.stage_artifact(0)),
        rel_path(paths, paths.stage_artifact(1)),
        rel_path(paths, paths.stage_artifact(3)),
        rel_path(paths, paths.stage_artifact(4)),
        rel_path(paths, paths.stage_artifact(5)),
    ]
    stage0_reasons = semantic_ctx.get("stage0_reasons", [])
    return {
        "entity": entity,
        "entity_title": entity.replace("_", " "),
        "generated_at": now_iso(),
        "source_artifacts": source_artifacts,
        "stage0_reason": stage0_reasons[0] if isinstance(stage0_reasons, list) and stage0_reasons else "N/A",
        **semantic_ctx,
        **code_ctx,
    }


def _join_items(items: list[str], limit: int | None = None) -> str:
    tokens = [str(item).strip() for item in items if str(item).strip()]
    if limit is not None:
        tokens = tokens[:limit]
    return ", ".join(tokens) if tokens else "N/A"


def _entity_event_rows(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in ctx.get("business_events", []):
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id", "")).strip() or f"EVT-{len(rows) + 1:03d}"
        if event_id in seen:
            continue
        seen.add(event_id)
        api_refs = dedupe_keep_order(
            [normalize_api_key(str(item)) for item in event.get("api_refs", []) if normalize_api_key(str(item))]
        )
        rows.append(
            {
                "event_id": event_id,
                "title": str(event.get("title", "")).strip() or "未命名业务事件",
                "description": str(event.get("description", "")).strip(),
                "api_refs": api_refs,
                "expected_business_codes": normalize_business_code_list(event.get("expected_business_codes", [])),
            }
        )
    return rows


def _entity_case_rows(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in ctx.get("generated_cases", []):
        if not isinstance(case, dict):
            continue
        rows.append(
            {
                "case_id": str(case.get("case_id", "")).strip(),
                "title": str(case.get("title", "")).strip(),
                "event_id": str(case.get("event_id", "")).strip(),
                "expected_business_code": str(case.get("expected_business_code", "")).strip(),
                "api_refs": [
                    normalize_api_key(str(item))
                    for item in case.get("api_refs", [])
                    if normalize_api_key(str(item))
                ],
            }
        )
    return rows


def _entity_permission_lines(ctx: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    permission_boundary = ctx.get("permission_boundary", {})
    if isinstance(permission_boundary, dict):
        model = str(permission_boundary.get("model", "")).strip()
        scope_types = [
            str(item).strip()
            for item in permission_boundary.get("scope_types", [])
            if isinstance(item, str) and str(item).strip()
        ]
        if model:
            lines.append(f"权限模型: {model}")
        if scope_types:
            lines.append(f"Scope 类型: {_join_items(scope_types)}")

    permissions = ctx.get("permissions", [])
    if isinstance(permissions, list) and permissions:
        labels = [
            f"{str(item.get('codename', '')).strip()} ({str(item.get('label', '')).strip()})"
            for item in permissions
            if isinstance(item, dict) and str(item.get("codename", "")).strip()
        ]
        if labels:
            lines.append(f"代码权限码: {_join_items(labels, limit=6)}")
    return lines or ["N/A"]


def _entity_state_machine_lines(ctx: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    state_machine = ctx.get("state_machine", {})
    if isinstance(state_machine, dict):
        states = [str(item).strip() for item in state_machine.get("states", []) if str(item).strip()]
        terminal_states = [str(item).strip() for item in state_machine.get("terminal_states", []) if str(item).strip()]
        if states:
            lines.append(f"状态集合: {_join_items(states)}")
        if terminal_states:
            lines.append(f"终态: {_join_items(terminal_states)}")

    status_choices = ctx.get("status_choices", [])
    if not lines and isinstance(status_choices, list) and status_choices:
        choice_lines = [
            f"{str(item.get('value', '')).strip()} ({str(item.get('label', '')).strip()})"
            for item in status_choices
            if isinstance(item, dict) and str(item.get("value", "")).strip()
        ]
        if choice_lines:
            lines.append(f"状态枚举: {_join_items(choice_lines)}")
    return lines or ["N/A"]


def _entity_field_lines(ctx: dict[str, Any], limit: int = 8) -> list[str]:
    fields = ctx.get("model_fields", [])
    if not isinstance(fields, list) or not fields:
        return ["N/A"]
    lines: list[str] = []
    for field in fields[:limit]:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name", "")).strip()
        field_type = str(field.get("type", "")).strip() or "Unknown"
        if not name:
            continue
        constraints = [str(item).strip() for item in field.get("constraints", []) if str(item).strip()]
        verbose = str(field.get("verbose", "")).strip()
        parts = [f"type={field_type}"]
        if constraints:
            parts.append(f"constraints={_join_items(constraints)}")
        if verbose:
            parts.append(f"verbose={verbose}")
        lines.append(f"{name}: {'; '.join(parts)}")
    return lines or ["N/A"]


def _entity_serializer_lines(ctx: dict[str, Any], limit: int = 4) -> list[str]:
    serializers = ctx.get("serializer_fields", {})
    if not isinstance(serializers, dict) or not serializers:
        return ["N/A"]
    lines: list[str] = []
    for serializer, fields in list(serializers.items())[:limit]:
        field_list = [str(item).strip() for item in fields if str(item).strip()] if isinstance(fields, list) else []
        lines.append(f"{serializer}: {_join_items(field_list, limit=12)}")
    return lines or ["N/A"]


def _entity_execution_lines(ctx: dict[str, Any]) -> list[str]:
    execution = ctx.get("execution", {})
    if not isinstance(execution, dict):
        return ["N/A"]
    matched_case_ids = execution.get("matched_case_ids", [])
    unmatched_case_ids = execution.get("unmatched_case_ids", [])
    failed_cases = execution.get("failed_cases", [])
    lines = [
        f"生成用例数: {int(execution.get('case_count', 0))}",
        f"已执行用例数: {int(execution.get('executed_cases', 0))}",
        f"已沉淀到项目测试: {len(matched_case_ids) if isinstance(matched_case_ids, list) else 0}",
        (
            f"待沉淀 case: {_join_items(unmatched_case_ids, limit=6)}"
            if isinstance(unmatched_case_ids, list) and unmatched_case_ids
            else "待沉淀 case: N/A"
        ),
        (
            f"失败 case: {_join_items(failed_cases, limit=6)}"
            if isinstance(failed_cases, list) and failed_cases
            else "失败 case: N/A"
        ),
    ]
    return lines


def _clean_existing_entity_doc(original: str, suffix: str) -> str:
    cleaned = _strip_stage6_generated_content(original, suffix)
    if not cleaned:
        return ""
    return cleaned if cleaned.endswith("\n") else cleaned + "\n"


def _strip_stage6_generated_content(text: str, suffix: str) -> str:
    if not text:
        return ""
    if suffix.endswith(".md"):
        cleaned = re.sub(
            r"<!--\s*stage6_doc_sync::.*?::start\s*-->.*?<!--\s*stage6_doc_sync::.*?::end\s*-->\s*",
            "",
            text,
            flags=re.S,
        )
        cleaned = re.sub(r"<!--\s*stage6_round_context::.*?-->\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"##\s*Stage6\s*同步上下文（可追溯）\s*```json.*?```", "", cleaned, flags=re.S)
        return cleaned.strip()
    if suffix == "schema.dbml":
        kept_lines: list[str] = []
        skipping_sync_block = False
        for line in text.splitlines():
            lowered = line.strip().lower()
            if lowered.startswith("// stage6_doc_sync::") and lowered.endswith("::start"):
                skipping_sync_block = True
                continue
            if lowered.startswith("// stage6_doc_sync::") and lowered.endswith("::end"):
                skipping_sync_block = False
                continue
            if skipping_sync_block:
                continue
            if lowered.startswith("// stage6_round_context::"):
                continue
            if lowered.startswith("// trace_context:"):
                continue
            if "non-destructive stage6 append" in lowered:
                continue
            kept_lines.append(line)
        return "\n".join(kept_lines).strip()
    return text.strip()


def _merge_entity_doc_content(paths: Paths, original: str, suffix: str, ctx: dict[str, Any]) -> str:
    del paths, ctx
    return _clean_existing_entity_doc(original, suffix)


def auto_sync_entity_docs(
    paths: Paths,
    touched_entities: list[str],
    changed_paths: list[str],
) -> dict[str, Any]:
    report = {
        "attempted": False,
        "attempted_entities": [],
        "created_docs": [],
        "updated_docs": [],
        "skipped_docs": [],
    }
    if not touched_entities:
        return report

    report["attempted"] = True
    report["attempted_entities"] = touched_entities

    for entity in touched_entities:
        docs = entity_doc_paths(paths, entity)
        for doc in docs:
            rel = rel_path(paths, doc)
            suffix = doc.name.split(f"{entity}_", 1)[-1]
            if not doc.exists():
                report["skipped_docs"].append(rel)
                continue
            original = doc.read_text(encoding="utf-8")
            merged = _merge_entity_doc_content(paths, original, suffix, {})
            next_content = merged if merged != original else original
            if next_content == original:
                report["skipped_docs"].append(rel)
                continue
            write_text(doc, next_content)
            report["updated_docs"].append(rel)

    return report


def _is_stage6_commit_whitelist_path(path: str, whitelist_patterns: list[str], blacklist_prefixes: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    if any(normalized.startswith(prefix) for prefix in blacklist_prefixes):
        return False
    for pattern in whitelist_patterns:
        if fnmatch.fnmatch(normalized, pattern):
            return True
    return False


def build_stage6_commit_review(
    paths: Paths,
    state: dict[str, Any],
    stage0_artifact: dict[str, Any],
    touched_entities: list[str],
    changed_paths: list[str],
) -> dict[str, Any]:
    commit_controls = resolve_commit_controls(paths, state)
    whitelist_patterns = commit_controls.get("git_add_whitelist_patterns", [])
    blacklist_prefixes = commit_controls.get("git_add_blacklist_prefixes", [])
    if not isinstance(whitelist_patterns, list):
        whitelist_patterns = build_default_commit_review_whitelist_patterns(resolve_semantic_dir_names(paths)["business"])
    if not isinstance(blacklist_prefixes, list):
        blacklist_prefixes = list(DEFAULT_COMMIT_REVIEW_BLACKLIST_PREFIXES)

    focus_api_keys = stage0_candidate_api_keys_from_artifact(stage0_artifact)
    include_files = sorted(
        {
            path.replace("\\", "/")
            for path in changed_paths
            if _is_stage6_commit_whitelist_path(path, whitelist_patterns=whitelist_patterns, blacklist_prefixes=blacklist_prefixes)
        }
    )
    exclude_files = sorted({path.replace("\\", "/") for path in changed_paths if path.replace("\\", "/") not in include_files})
    primary_api = focus_api_keys[0] if focus_api_keys else ""
    if primary_api:
        suggested_subject = f"feat(api): {primary_api}"
    elif touched_entities:
        suggested_subject = f"feat({touched_entities[0]}): stage6 checkpoint"
    else:
        suggested_subject = "chore(stage6): checkpoint"
    return {
        "needs_user_review": True,
        "review_status": "pending_user_review",
        "focus_api_keys": focus_api_keys,
        "touched_entities": touched_entities,
        "git_add_whitelist_patterns": whitelist_patterns,
        "git_add_blacklist_prefixes": blacklist_prefixes,
        "whitelist_source": commit_controls.get("meta", {}),
        "suggested_commit_message": suggested_subject,
        "suggested_git_add_files": include_files,
        "excluded_changed_files": exclude_files,
        "ready_for_review": bool(include_files),
        "note": (
            "Stage6 仅生成待提交清单；必须先由用户审核 commit message 与文件范围，"
            "用户明确批准后才可执行 git add/git commit。"
        ),
    }


def detect_permission_changes(paths: Paths, changed_paths: list[str]) -> tuple[bool, list[str]]:
    semantic_dirs = resolve_semantic_dir_names(paths)
    permission_dir = semantic_dirs.get("permission", "").replace("\\", "/").strip("/")
    permission_prefix = f"{permission_dir}/" if permission_dir else ""
    project_layout = resolve_project_layout(paths)
    permission_code_prefixes = project_layout["permission_code_directories"]
    impacted = []
    for path in changed_paths:
        normalized = path.replace("\\", "/")
        lower = normalized.lower()
        if (
            (bool(permission_prefix) and normalized.startswith(permission_prefix))
            or _path_has_prefix(normalized, permission_code_prefixes)
            or "permission" in lower
            or "scope" in lower
            or "authz" in lower
        ):
            impacted.append(normalized)
    impacted = sorted(set(impacted))
    return bool(impacted), impacted


def action_from_decide_args(args: argparse.Namespace) -> tuple[str, int | None]:
    if getattr(args, "approve", False):
        return "approve", None
    if getattr(args, "reject", False):
        return "reject", None
    if getattr(args, "run_stage8", False) or getattr(args, "enter_rollback", False):
        return "run_stage8", None
    goto_stage = getattr(args, "goto_stage", None)
    if goto_stage is not None:
        return "goto_stage", int(goto_stage)
    raise WorkflowError("decide 命令必须指定一个动作")


def load_state(paths: Paths) -> dict[str, Any]:
    state = read_json(paths.state)
    if not isinstance(state, dict):
        raise WorkflowError("state.json 不存在或格式错误，请先执行 init")
    normalized = copy.deepcopy(DEFAULT_STATE)
    normalized.update(state)
    return normalized


def save_state(paths: Paths, state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    write_json(paths.state, state)


@contextlib.contextmanager
def workflow_file_lock(paths: Paths, exclusive: bool):
    lock_path = paths.workflow_lock
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    with lock_path.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), mode)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _safe_pid(value: Any) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _pid_is_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _running_stage_from_state(state: dict[str, Any]) -> int:
    running_stage = state.get("running_stage")
    if running_stage in STAGES:
        return int(running_stage)
    current_stage = state.get("current_stage")
    if current_stage in STAGES:
        return int(current_stage)
    return 0


def _clear_running_state(state: dict[str, Any]) -> None:
    state["running_stage"] = None
    state["running_started_at"] = ""
    state["running_pid"] = None
    state["running_trigger_source"] = ""


def _mark_state_running(state: dict[str, Any], stage: int, trigger_source: str) -> None:
    state["status"] = "running"
    state["current_stage"] = stage
    state["running_stage"] = stage
    state["running_started_at"] = now_iso()
    state["running_pid"] = os.getpid()
    state["running_trigger_source"] = trigger_source


def recover_stale_running_state(paths: Paths) -> dict[str, Any]:
    with workflow_file_lock(paths, exclusive=True):
        state = load_state(paths)
        if state.get("status") != "running":
            return {"recovered": False, "reason": "not_running", "state": state}

        pid = _safe_pid(state.get("running_pid"))
        if _pid_is_alive(pid):
            return {"recovered": False, "reason": "pid_alive", "state": state}

        stage = _running_stage_from_state(state)
        started_at = str(state.get("running_started_at", "")).strip()
        trigger_source = str(state.get("running_trigger_source", "")).strip()
        state["status"] = "idle"
        state["current_stage"] = stage
        _clear_running_state(state)
        event_id = append_event(
            paths,
            "stale_running_recovered",
            {
                "stage": stage,
                "running_started_at": started_at,
                "running_pid": pid,
                "trigger_source": trigger_source or "unknown",
            },
        )
        state["last_event_id"] = event_id
        save_state(paths, state)
        return {"recovered": True, "reason": "pid_not_alive", "state": state}


def approval_mode(spec: dict[str, Any]) -> str:
    mode = str(spec.get("approval_mode", "manual")).strip().lower()
    if mode not in {"manual", "auto"}:
        return "manual"
    return mode


def ensure_auto_continue_allowed(spec: dict[str, Any], allowed_by_flag: bool, command_name: str) -> None:
    if approval_mode(spec) == "manual" and not allowed_by_flag:
        raise WorkflowError(
            f"{command_name} 使用 --auto 被阻断：当前 approval_mode=manual。"
            "请先人工审核 gate 摘要；若确认自动续跑，追加 --allow-auto-continue。"
        )


def _is_bootstrap_mode(paths: Paths) -> bool:
    stage0_artifact = read_json(paths.stage_artifact(0), {})
    if isinstance(stage0_artifact, dict):
        return bool(stage0_artifact.get("bootstrap_mode", False))
    return False


def _normalize_path_list(raw: Any) -> list[str]:
    values: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            token = str(item).strip().replace("\\", "/")
            if token:
                values.append(token)
    return dedupe_keep_order(values)


def apply_stage6_commit_after_user_approval(
    paths: Paths,
    pending: dict[str, Any],
) -> dict[str, Any]:
    pending_stage = _safe_int(pending.get("stage"), -1)
    if pending_stage != 6:
        return {"applied": False, "reason": "not_stage6"}
    if _is_bootstrap_mode(paths):
        return {"applied": False, "reason": "bootstrap_mode_skip"}

    stage6_artifact = read_json(paths.stage_artifact(6), {})
    if not isinstance(stage6_artifact, dict):
        return {"applied": False, "reason": "stage6_artifact_invalid"}
    commit_review = stage6_artifact.get("commit_review", {})
    if not isinstance(commit_review, dict):
        return {"applied": False, "reason": "commit_review_missing"}

    include_files = _normalize_path_list(commit_review.get("suggested_git_add_files", []))
    commit_message = str(
        commit_review.get("suggested_commit_message")
        or stage6_artifact.get("settlement_commit_message")
        or "chore(stage6): checkpoint commit"
    ).strip()

    reviewed_at = now_iso()
    commit_review["reviewed_by_action"] = "approve"
    commit_review["reviewed_at"] = reviewed_at

    if not include_files:
        commit_review["review_status"] = "approved_no_files"
        commit_review["commit_executed"] = False
        stage6_artifact["commit_review"] = commit_review
        write_json(paths.stage_artifact(6), stage6_artifact)
        append_event(
            paths,
            "stage6_commit_review_approved",
            {
                "result": "no_files",
                "trigger_source": "resume",
            },
        )
        return {"applied": False, "reason": "no_whitelist_files"}

    rc, staged_before, stderr = run_git(paths, ["-c", "core.quotePath=false", "diff", "--cached", "--name-only"])
    if rc != 0:
        raise WorkflowError(f"读取暂存区失败，无法执行 Stage6 提交: {stderr or staged_before}")
    if staged_before.strip():
        raise WorkflowError(
            "检测到已有已暂存变更。为避免误提交，已阻断 Stage6 自动提交；"
            "请先清理暂存区后再 approve。"
        )

    rc, stdout, stderr = run_git(paths, ["add", *include_files])
    if rc != 0:
        raise WorkflowError(f"Stage6 执行 git add 失败: {stderr or stdout}")

    rc, staged_after, stderr = run_git(paths, ["-c", "core.quotePath=false", "diff", "--cached", "--name-only"])
    if rc != 0:
        raise WorkflowError(f"读取 git add 后暂存区失败: {stderr or staged_after}")
    staged_files = _normalize_path_list(staged_after.splitlines())
    if not staged_files:
        commit_review["review_status"] = "approved_no_changes"
        commit_review["commit_executed"] = False
        stage6_artifact["commit_review"] = commit_review
        write_json(paths.stage_artifact(6), stage6_artifact)
        append_event(
            paths,
            "stage6_commit_review_approved",
            {
                "result": "no_changes",
                "trigger_source": "resume",
            },
        )
        return {"applied": False, "reason": "no_staged_changes"}

    rc, stdout, stderr = run_git(paths, ["commit", "-m", commit_message, "--", *include_files])
    if rc != 0:
        raise WorkflowError(f"Stage6 执行 git commit 失败: {stderr or stdout}")
    rc, commit_sha, sha_err = run_git(paths, ["rev-parse", "HEAD"])
    sha = commit_sha.strip() if rc == 0 else ""

    commit_review["review_status"] = "approved_and_committed"
    commit_review["commit_executed"] = True
    commit_review["commit_message"] = commit_message
    commit_review["commit_sha"] = sha
    commit_review["committed_files"] = staged_files
    stage6_artifact["commit_review"] = commit_review
    write_json(paths.stage_artifact(6), stage6_artifact)
    append_event(
        paths,
        "stage6_commit_review_approved",
        {
            "result": "committed",
            "commit_sha": sha,
            "commit_message": commit_message,
            "committed_files": staged_files,
            "trigger_source": "resume",
            "rev_parse_warning": sha_err if rc != 0 else "",
        },
    )
    return {
        "applied": True,
        "commit_sha": sha,
        "commit_message": commit_message,
        "committed_files": staged_files,
    }


def enforce_stage6_commit_decision(paths: Paths, pending: dict[str, Any], decision: dict[str, Any], action: str) -> dict[str, Any]:
    pending_stage = _safe_int(pending.get("stage"), -1)
    if pending_stage != 6 or action != "approve":
        return {"enforced": False, "reason": "not_stage6_approve"}

    stage6_artifact = read_json(paths.stage_artifact(6), {})
    if not isinstance(stage6_artifact, dict):
        return {"enforced": False, "reason": "stage6_artifact_invalid"}
    commit_review = stage6_artifact.get("commit_review", {})
    if not isinstance(commit_review, dict):
        return {"enforced": False, "reason": "commit_review_missing"}

    include_files = _normalize_path_list(commit_review.get("suggested_git_add_files", []))
    needs_review = bool(commit_review.get("needs_user_review", False)) or bool(include_files)
    if not needs_review:
        return {"enforced": False, "reason": "commit_review_not_required"}

    commit_decision = str(decision.get("stage6_commit_decision", "")).strip().lower()
    if commit_decision not in {"confirm", "skip"}:
        raise WorkflowError(
            "Stage6 approve 前必须明确提交决策："
            "使用 --confirm-stage6-commit 执行提交，或 --skip-stage6-commit 跳过本轮提交。"
        )

    reviewed_at = now_iso()
    commit_review["reviewed_by_action"] = "approve"
    commit_review["reviewed_at"] = reviewed_at

    if commit_decision == "confirm":
        result = apply_stage6_commit_after_user_approval(paths, pending)
        if result.get("applied"):
            return {"enforced": True, "decision": "confirm", "result": result}
        commit_review["review_status"] = "approved_no_commit"
        commit_review["commit_executed"] = False
        commit_review["skip_reason"] = str(result.get("reason", "no_commit"))
        stage6_artifact["commit_review"] = commit_review
        write_json(paths.stage_artifact(6), stage6_artifact)
        return {"enforced": True, "decision": "confirm", "result": result}

    commit_review["review_status"] = "approved_skip_commit"
    commit_review["commit_executed"] = False
    commit_review["skip_reason"] = "user_explicit_skip"
    stage6_artifact["commit_review"] = commit_review
    write_json(paths.stage_artifact(6), stage6_artifact)
    append_event(
        paths,
        "stage6_commit_review_skipped",
        {
            "result": "skipped_by_user",
            "trigger_source": "resume",
        },
    )
    return {"enforced": True, "decision": "skip"}


def _pick(artifact: dict[str, Any], *keys: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in keys:
        if key in artifact:
            payload[key] = artifact[key]
    return payload


def summarize_stage_artifact(stage: int, artifact: dict[str, Any] | None, paths: Paths | None = None) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        return {"available": False}

    summary: dict[str, Any] = {"available": True, "stage": stage}
    summary.update(_pick(artifact, "generated_at"))

    if stage == 0:
        summary.update(
            _pick(
                artifact,
                "api_candidates",
                "required_case_codes",
                "risk_assessment",
                "semantic_gap_report",
                "candidate_source",
                "session_api_candidate_file",
                "session_api_candidate_count",
                "session_api_warnings",
                "stage0_replan_required",
                "stage0_entered_at",
                "session_api_candidate_mtime",
                "session_api_fresh",
                "session_api_replan_blocking",
                "touched_entities",
            )
        )
    elif stage == 2:
        summary.update(
            _pick(
                artifact,
                "source",
                "scan_error",
                "new_api_keys",
                "drift_items",
                "bootstrap_mode",
            )
        )
    elif stage == 3:
        candidate_events = artifact.get("business_event_candidates", [])
        selected_events = artifact.get("business_events", [])
        summary.update(
            _pick(
                artifact,
                "focus_api_keys",
                "progress_snapshot",
                "semantic_context",
                "session_event_candidate_file",
                "session_event_warnings",
                "semantic_review",
                "editor_notes",
            )
        )
        summary["candidate_events_count"] = len(candidate_events) if isinstance(candidate_events, list) else 0
        summary["selected_events_count"] = len(selected_events) if isinstance(selected_events, list) else 0
        if isinstance(candidate_events, list) and candidate_events:
            preview: list[dict[str, Any]] = []
            for event in candidate_events[:6]:
                if not isinstance(event, dict):
                    continue
                preview.append(
                    {
                        "event_id": event.get("event_id"),
                        "title": event.get("title"),
                        "api_refs": event.get("api_refs"),
                        "expected_business_codes": event.get("expected_business_codes", []),
                    }
                )
            summary["event_preview"] = preview
    elif stage == 4:
        candidate_cases = artifact.get("candidate_cases", [])
        generated_cases = artifact.get("generated_cases", [])
        test_generation = artifact.get("test_generation")
        if paths is not None and isinstance(generated_cases, list):
            test_generation = analyze_stage4_test_generation(paths, [item for item in generated_cases if isinstance(item, dict)])
        summary.update(
            _pick(
                artifact,
                "generated_test_files",
                "business_event_files",
                "semantic_review",
                "coverage_by_event",
                "business_code_fields",
                "strict_business_code",
                "case_design_mode",
                "session_case_candidate_file",
                "session_case_warnings",
                "session_codegen_note",
            )
        )
        if isinstance(test_generation, dict):
            summary["test_generation"] = test_generation
        summary["candidate_cases_count"] = len(candidate_cases) if isinstance(candidate_cases, list) else 0
        summary["generated_cases_count"] = len(generated_cases) if isinstance(generated_cases, list) else 0
        if isinstance(candidate_cases, list) and candidate_cases:
            preview: list[dict[str, Any]] = []
            for case in candidate_cases[:6]:
                if not isinstance(case, dict):
                    continue
                preview.append(
                    {
                        "case_id": case.get("case_id"),
                        "event_id": case.get("event_id"),
                        "api_refs": case.get("api_refs"),
                        "expected_business_code": case.get("expected_business_code"),
                    }
                )
            summary["candidate_preview"] = preview
    elif stage == 5:
        reg = artifact.get("regression_summary", {})
        project_suite = artifact.get("project_internal_suite", {})
        project_sink = artifact.get("project_test_sink", {})
        summary.update(
            {
                "executed_cases": artifact.get("executed_cases"),
                "failed_cases": artifact.get("failed_cases"),
                "return_code": reg.get("return_code") if isinstance(reg, dict) else None,
                "consecutive_failures": reg.get("consecutive_failures") if isinstance(reg, dict) else None,
                "failure_distribution": reg.get("failure_distribution") if isinstance(reg, dict) else None,
                "project_internal_suite": project_suite if isinstance(project_suite, dict) else {},
                "project_test_sink": {
                    "status": project_sink.get("status"),
                    "reason": project_sink.get("reason"),
                    "matched_case_ids": project_sink.get("matched_case_ids", []),
                    "unmatched_case_ids": project_sink.get("unmatched_case_ids", []),
                    "mapping": project_sink.get("mapping", []),
                }
                if isinstance(project_sink, dict)
                else {},
            }
        )
    elif stage == 6:
        summary.update(
            _pick(
                artifact,
                "registry_updates",
                "docs_updates",
                "entity_review",
                "business_doc_sync",
                "doc_sync_autofix",
                "commit_review",
                "permission_doc_changed",
                "settlement_commit_message",
            )
        )
    elif stage == 7:
        summary.update(
            _pick(
                artifact,
                "intervention_summary",
                "recommendations",
            )
        )
    elif stage == 8:
        summary.update(
            _pick(
                artifact,
                "decision",
                "reasoning",
                "actions",
            )
        )
    else:
        summary.update(_pick(artifact, "notes"))
    return summary


def build_stage_brief(stage: int, artifact: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        return {"available": False}

    brief: dict[str, Any] = {
        "logical_stage": logical_stage_for_internal(stage),
        "stage_name": logical_stage_name(stage),
    }

    if stage == 0:
        candidates = artifact.get("api_candidates", [])
        touched_entities = artifact.get("touched_entities", [])
        first_candidate = candidates[0] if isinstance(candidates, list) and candidates else {}
        brief.update(
            {
                "candidate_count": len(candidates) if isinstance(candidates, list) else 0,
                "candidate_api": first_candidate.get("api_key") if isinstance(first_candidate, dict) else "",
                "candidate_source": artifact.get("candidate_source", ""),
                "touched_entities": touched_entities if isinstance(touched_entities, list) else [],
            }
        )
        return brief

    if stage == 1:
        tasks = artifact.get("implementation_tasks", [])
        brief.update(
            {
                "task_count": len(tasks) if isinstance(tasks, list) else 0,
                "touched_entities": artifact.get("touched_entities", []),
            }
        )
        return brief

    if stage == 2:
        scanned = artifact.get("scanned_apis", [])
        new_api_keys = artifact.get("new_api_keys", [])
        drift_items = artifact.get("drift_items", [])
        brief.update(
            {
                "scanned_api_count": len(scanned) if isinstance(scanned, list) else 0,
                "new_api_count": len(new_api_keys) if isinstance(new_api_keys, list) else 0,
                "drift_count": len(drift_items) if isinstance(drift_items, list) else 0,
                "scan_source": artifact.get("source", ""),
            }
        )
        return brief

    if stage == 3:
        business_events = artifact.get("business_events", [])
        semantic_review = artifact.get("semantic_review", {})
        focus_api_keys = artifact.get("focus_api_keys", [])
        brief.update(
            {
                "focus_api_count": len(focus_api_keys) if isinstance(focus_api_keys, list) else 0,
                "event_count": len(business_events) if isinstance(business_events, list) else 0,
                "semantic_review": (
                    str(semantic_review.get("status", "")).strip().lower()
                    if isinstance(semantic_review, dict)
                    else "unknown"
                ),
            }
        )
        return brief

    if stage == 4:
        generated_cases = artifact.get("generated_cases", [])
        traceability = artifact.get("traceability_check", {})
        test_generation = artifact.get("test_generation", {})
        missing_tests = test_generation.get("missing_tests", []) if isinstance(test_generation, dict) else []
        brief.update(
            {
                "case_count": len(generated_cases) if isinstance(generated_cases, list) else 0,
                "traceability": (
                    str(traceability.get("status", "")).strip().lower()
                    if isinstance(traceability, dict)
                    else "unknown"
                ),
                "test_generation": (
                    str(test_generation.get("status", "")).strip().lower()
                    if isinstance(test_generation, dict)
                    else "unknown"
                ),
                "missing_test_count": len(missing_tests) if isinstance(missing_tests, list) else 0,
            }
        )
        return brief

    if stage == 5:
        regression = artifact.get("regression_summary", {})
        project_sink = artifact.get("project_test_sink", {})
        failed_cases = artifact.get("failed_cases", [])
        unmatched_case_ids = project_sink.get("unmatched_case_ids", []) if isinstance(project_sink, dict) else []
        brief.update(
            {
                "return_code": regression.get("return_code") if isinstance(regression, dict) else None,
                "failed_case_count": len(failed_cases) if isinstance(failed_cases, list) else 0,
                "project_test_sink": (
                    str(project_sink.get("status", "")).strip().lower()
                    if isinstance(project_sink, dict)
                    else "unknown"
                ),
                "unsunk_case_count": len(unmatched_case_ids) if isinstance(unmatched_case_ids, list) else 0,
            }
        )
        return brief

    if stage == 6:
        doc_sync = artifact.get("business_doc_sync", {})
        entity_review = artifact.get("entity_review", {})
        commit_review = artifact.get("commit_review", {})
        final_entities = entity_review.get("final_entities", []) if isinstance(entity_review, dict) else []
        brief.update(
            {
                "doc_sync_passed": bool(doc_sync.get("passed", False)) if isinstance(doc_sync, dict) else False,
                "entity_count": len(final_entities) if isinstance(final_entities, list) else 0,
                "commit_review_status": (
                    str(commit_review.get("review_status", "")).strip().lower()
                    if isinstance(commit_review, dict)
                    else "unknown"
                ),
            }
        )
        return brief

    if stage == 7:
        recommendations = artifact.get("recommendations", [])
        first = recommendations[0] if isinstance(recommendations, list) and recommendations else {}
        brief.update(
            {
                "recommendation_count": len(recommendations) if isinstance(recommendations, list) else 0,
                "recommended_action": first.get("action") if isinstance(first, dict) else "",
            }
        )
        return brief

    if stage == 8:
        actions = artifact.get("actions", {})
        brief.update(
            {
                "decision": artifact.get("decision", ""),
                "target_ref": actions.get("suggested_target_ref", "") if isinstance(actions, dict) else "",
            }
        )
        return brief

    return brief


def build_running_stage_brief(state: dict[str, Any]) -> dict[str, Any]:
    stage = _running_stage_from_state(state)
    return {
        "logical_stage": logical_stage_for_internal(stage),
        "stage_name": logical_stage_name(stage),
        "status": "running",
        "running_started_at": str(state.get("running_started_at", "")).strip(),
        "trigger_source": str(state.get("running_trigger_source", "")).strip(),
        "latest_completed_stage": state.get("last_completed_stage"),
        "artifact_snapshot": "suppressed_while_stage_running",
    }


def recommend_pending_action(
    pending: dict[str, Any] | None,
    artifact: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(pending, dict):
        return {
            "recommended_decision": "",
            "recommended_reason": "",
            "recommended_target_stage": None,
            "recommended_target_logical_stage": None,
        }

    stage = int(pending.get("stage", 0))
    reason = str(pending.get("reason", "")).strip().lower()
    allowed_actions = [str(item).strip() for item in pending.get("allowed_actions", []) if str(item).strip()]
    fallback_stage = int(pending.get("fallback_stage", stage))

    if stage == 0 and isinstance(artifact, dict):
        candidates = artifact.get("api_candidates", [])
        semantic_gap = artifact.get("semantic_gap_report", {})
        replan_blocking = bool(artifact.get("session_api_replan_blocking", False))
        has_candidates = isinstance(candidates, list) and bool(candidates)
        semantic_blocking = bool(isinstance(semantic_gap, dict) and semantic_gap.get("blocking"))
        if has_candidates and not semantic_blocking and not replan_blocking and "approve" in allowed_actions:
            return {
                "recommended_decision": "approve",
                "recommended_reason": "候选 API 已准备好，可以进入实现阶段",
                "recommended_target_stage": None,
                "recommended_target_logical_stage": None,
            }

    if stage == 3 and isinstance(artifact, dict):
        semantic_review = artifact.get("semantic_review", {})
        business_events = artifact.get("business_events", [])
        semantic_ready = str(semantic_review.get("status", "")).strip().lower() == "approved" if isinstance(semantic_review, dict) else False
        events_ready = isinstance(business_events, list) and bool(business_events)
        if semantic_ready and events_ready and "approve" in allowed_actions:
            return {
                "recommended_decision": "approve",
                "recommended_reason": "业务事件已收敛，可以进入测试资产阶段",
                "recommended_target_stage": None,
                "recommended_target_logical_stage": None,
            }

    if stage == 4 and isinstance(artifact, dict):
        semantic_review = artifact.get("semantic_review", {})
        traceability_check = artifact.get("traceability_check", {})
        test_generation = artifact.get("test_generation", {})
        semantic_ready = str(semantic_review.get("status", "")).strip().lower() == "approved" if isinstance(semantic_review, dict) else False
        trace_ready = str(traceability_check.get("status", "")).strip().lower() == "ok" if isinstance(traceability_check, dict) else False
        test_ready = str(test_generation.get("status", "")).strip().lower() == "ready" if isinstance(test_generation, dict) else False
        if semantic_ready and trace_ready and test_ready and "approve" in allowed_actions:
            return {
                "recommended_decision": "approve",
                "recommended_reason": "测试资产已收敛，可以进入回归阶段",
                "recommended_target_stage": None,
                "recommended_target_logical_stage": None,
            }
        if "goto_stage" in allowed_actions:
            return {
                "recommended_decision": "goto_stage",
                "recommended_reason": "测试资产尚未完成，先在当前逻辑阶段补齐 case 或测试代码",
                "recommended_target_stage": stage,
                "recommended_target_logical_stage": logical_stage_for_internal(stage),
            }

    if stage == 6 and isinstance(artifact, dict):
        business_doc_sync = artifact.get("business_doc_sync", {})
        doc_ready = bool(business_doc_sync.get("passed", False)) if isinstance(business_doc_sync, dict) else False
        if doc_ready and "approve" in allowed_actions:
            return {
                "recommended_decision": "approve",
                "recommended_reason": "结算已完成，可以进入下一轮",
                "recommended_target_stage": None,
                "recommended_target_logical_stage": None,
            }

    if stage == 7 and isinstance(artifact, dict):
        recommendations = artifact.get("recommendations", [])
        if isinstance(recommendations, list):
            for item in recommendations:
                if not isinstance(item, dict):
                    continue
                action = str(item.get("action", "")).strip()
                if action not in allowed_actions:
                    continue
                target_stage = item.get("next_stage") if action == "goto_stage" else None
                target_logical = (
                    logical_stage_for_internal(target_stage)
                    if isinstance(target_stage, int)
                    else None
                )
                return {
                    "recommended_decision": display_action_name(action),
                    "recommended_reason": str(item.get("reason", "")).strip() or "按干预建议处理",
                    "recommended_target_stage": target_stage,
                    "recommended_target_logical_stage": target_logical,
                }

    if stage == 8:
        decision = str(artifact.get("decision", "")).strip().lower() if isinstance(artifact, dict) else ""
        reason_text = "接受当前回退决策" if decision else "接受当前异常收口决策"
        recommended_decision = "approve" if "approve" in allowed_actions else ""
        if not recommended_decision and allowed_actions:
            recommended_decision = display_action_name(allowed_actions[0])
        return {
            "recommended_decision": recommended_decision,
            "recommended_reason": reason_text,
            "recommended_target_stage": None,
            "recommended_target_logical_stage": None,
        }

    blocking_tokens = (
        "blocking",
        "not_ready",
        "unsynced",
        "no_candidate",
        "replan",
        "failed",
        "invalid",
        "unresolved",
    )
    if any(token in reason for token in blocking_tokens):
        if "goto_stage" in allowed_actions:
            return {
                "recommended_decision": "goto_stage",
                "recommended_reason": "当前阶段尚未满足推进条件，建议在推荐修复阶段继续处理",
                "recommended_target_stage": fallback_stage,
                "recommended_target_logical_stage": logical_stage_for_internal(fallback_stage),
            }
        fallback_action = display_action_name(allowed_actions[0]) if allowed_actions else ""
        return {
            "recommended_decision": fallback_action,
            "recommended_reason": "当前阶段尚未满足推进条件",
            "recommended_target_stage": None,
            "recommended_target_logical_stage": None,
        }

    if "approve" in allowed_actions:
        return {
            "recommended_decision": "approve",
            "recommended_reason": "当前阶段产物已收敛，可以进入下一阶段",
            "recommended_target_stage": None,
            "recommended_target_logical_stage": None,
        }

    if allowed_actions:
        return {
            "recommended_decision": display_action_name(allowed_actions[0]),
            "recommended_reason": "按当前门禁允许的默认动作继续",
            "recommended_target_stage": None,
            "recommended_target_logical_stage": None,
        }

    return {
        "recommended_decision": "",
        "recommended_reason": "",
        "recommended_target_stage": None,
        "recommended_target_logical_stage": None,
    }


def _build_status_brief_payload_unlocked(paths: Paths) -> dict[str, Any]:
    state = load_state(paths)
    pending = read_json(paths.pending, DEFAULT_PENDING)
    readiness = build_onboarding_readiness(paths)
    internal_stage = _running_stage_from_state(state)
    if state.get("status") == "running":
        stage_brief = build_running_stage_brief(state)
    else:
        artifact = read_json(paths.stage_artifact(internal_stage), None) if internal_stage in STAGES else None
        stage_brief = build_stage_brief(internal_stage, artifact if isinstance(artifact, dict) else None)
    payload: dict[str, Any] = {
        "status": state.get("status"),
        "iteration": int(state.get("running_iteration", 1)),
        "logical_stage": logical_stage_for_internal(internal_stage),
        "stage_name": logical_stage_name(internal_stage),
        "internal_stage": internal_stage,
        "decision_pending": bool(state.get("status") == "waiting_decision"),
        "stage_brief": stage_brief,
        "project_rules": build_project_rules_payload(paths, internal_stage),
        "config_readiness": build_config_readiness_brief(readiness),
    }
    if state.get("status") == "waiting_decision" and isinstance(pending, dict):
        payload["pending"] = {
            "decision_id": str(pending.get("decision_id", "")).strip(),
            "reason": str(pending.get("reason", "")).strip(),
            "allowed_actions": display_action_names(pending.get("allowed_actions", [])),
        }
    return payload


def build_status_brief_payload(paths: Paths) -> dict[str, Any]:
    recover_stale_running_state(paths)
    with workflow_file_lock(paths, exclusive=False):
        return _build_status_brief_payload_unlocked(paths)


def _build_gate_brief_payload_unlocked(paths: Paths) -> dict[str, Any]:
    state = load_state(paths)
    pending = read_json(paths.pending, DEFAULT_PENDING)
    if state.get("status") != "waiting_decision" or not isinstance(pending, dict):
        return _build_status_brief_payload_unlocked(paths)

    stage = int(pending.get("stage", state.get("current_stage", 0)))
    artifact_path = paths.stage_artifact(stage)
    artifact = read_json(artifact_path, None)
    recommendation = recommend_pending_action(pending, artifact if isinstance(artifact, dict) else None)

    return {
        "status": "waiting_decision",
        "logical_stage": logical_stage_for_internal(stage),
        "stage_name": logical_stage_name(stage),
        "internal_stage": stage,
        "decision_id": str(pending.get("decision_id", "")).strip(),
        "reason": str(pending.get("reason", "")).strip(),
        "allowed_actions": display_action_names(pending.get("allowed_actions", [])),
        "recommended_decision": recommendation.get("recommended_decision", ""),
        "recommended_reason": recommendation.get("recommended_reason", ""),
        "recommended_target_stage": recommendation.get("recommended_target_logical_stage"),
        "stage_brief": build_stage_brief(stage, artifact if isinstance(artifact, dict) else None),
        "project_rules": build_project_rules_payload(paths, stage),
        "config_readiness": build_config_readiness_brief(build_onboarding_readiness(paths)),
        "artifact_path": rel_path(paths, artifact_path),
        "continue_hint": "输入 continue 将执行推荐动作",
    }


def build_gate_brief_payload(paths: Paths) -> dict[str, Any]:
    recover_stale_running_state(paths)
    with workflow_file_lock(paths, exclusive=False):
        return _build_gate_brief_payload_unlocked(paths)


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def tail_events(paths: Paths, limit: int = 5) -> list[dict[str, Any]]:
    if not paths.events.exists():
        return []
    lines = paths.events.read_text(encoding="utf-8").splitlines()
    rows = lines[-limit:]
    events: list[dict[str, Any]] = []
    for line in rows:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _build_gate_review_payload_unlocked(paths: Paths, include_events: bool = True) -> dict[str, Any]:
    _refresh_waiting_stage4_pending_if_resolved(paths, trigger_source="gate_payload")
    state = load_state(paths)
    pending = read_json(paths.pending, DEFAULT_PENDING)

    pending_stage = None
    if isinstance(pending, dict) and pending.get("stage") in STAGES:
        pending_stage = int(pending["stage"])
    current_stage = _running_stage_from_state(state)
    effective_stage = pending_stage if pending_stage is not None else current_stage

    payload: dict[str, Any] = {
        "status": state.get("status"),
        "current_stage": current_stage,
        "logical_current_stage": logical_stage_for_internal(effective_stage),
        "stage_name": logical_stage_name(effective_stage),
        "pending": pending,
        "commit_controls": resolve_commit_controls(paths, state),
        "project_rules": build_project_rules_payload(paths, effective_stage),
        "config_readiness": build_onboarding_readiness(paths),
    }

    stage = None
    if isinstance(pending, dict) and pending.get("stage") in STAGES:
        stage = int(pending["stage"])
    elif state.get("current_stage") in STAGES:
        stage = int(state["current_stage"])

    if stage is not None:
        if state.get("status") == "running" and pending_stage is None:
            payload["review"] = {
                "stage": stage,
                "logical_stage": logical_stage_for_internal(stage),
                "stage_name": logical_stage_name(stage),
                "artifact_path": None,
                "artifact_summary": {"available": False, "reason": "suppressed_while_stage_running"},
                "stage_brief": build_running_stage_brief(state),
                "decision_actions": [],
                "approval_mode": approval_mode(read_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC)),
                "recommended_decision": "",
                "recommended_reason": "当前阶段正在执行中，待完成后再读取稳定产物",
                "recommended_target_stage": None,
                "recommended_target_logical_stage": None,
                "continue_hint": "阶段运行中，等待本次执行结束",
            }
        else:
            artifact_path = paths.stage_artifact(stage)
            artifact = read_json(artifact_path, None)
            recommendation = recommend_pending_action(pending if isinstance(pending, dict) else None, artifact if isinstance(artifact, dict) else None)
            payload["review"] = {
                "stage": stage,
                "logical_stage": logical_stage_for_internal(stage),
                "stage_name": logical_stage_name(stage),
                "artifact_path": rel_path(paths, artifact_path),
                "artifact_summary": summarize_stage_artifact(stage, artifact if isinstance(artifact, dict) else None, paths=paths),
                "stage_brief": build_stage_brief(stage, artifact if isinstance(artifact, dict) else None),
                "decision_actions": display_action_names(pending.get("allowed_actions", [])) if isinstance(pending, dict) else [],
                "approval_mode": approval_mode(read_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC)),
                "recommended_decision": recommendation.get("recommended_decision", ""),
                "recommended_reason": recommendation.get("recommended_reason", ""),
                "recommended_target_stage": recommendation.get("recommended_target_stage"),
                "recommended_target_logical_stage": recommendation.get("recommended_target_logical_stage"),
                "continue_hint": "输入 continue 将执行推荐动作",
            }
    else:
        payload["review"] = {
            "stage": None,
            "logical_stage": None,
            "stage_name": "",
            "artifact_path": None,
            "artifact_summary": {"available": False},
            "stage_brief": {"available": False},
            "decision_actions": [],
            "approval_mode": approval_mode(read_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC)),
            "recommended_decision": "",
            "recommended_reason": "",
            "recommended_target_stage": None,
            "recommended_target_logical_stage": None,
            "continue_hint": "",
        }

    if include_events:
        payload["recent_events"] = tail_events(paths, limit=6)
    return payload


def build_gate_review_payload(paths: Paths, include_events: bool = True) -> dict[str, Any]:
    recover_stale_running_state(paths)
    with workflow_file_lock(paths, exclusive=True):
        return _build_gate_review_payload_unlocked(paths, include_events=include_events)


def ensure_scaffold(paths: Paths, force: bool = False, preset: str = "generic") -> None:
    ensure_skill_controls(paths, force=force, preset=preset)

    base_dirs = [
        paths.scaffold / "artifacts",
        paths.scaffold / "inputs",
        paths.scaffold / "schemas",
        paths.scaffold / "prompts",
        paths.scaffold / "registry",
        paths.scaffold / "cases",
        paths.scaffold / "decisions",
        paths.scaffold / "logs",
        paths.scaffold / "docs",
        paths.scaffold / "semantic",
        paths.scaffold / "tests" / "generated",
    ]
    for folder in base_dirs:
        folder.mkdir(parents=True, exist_ok=True)

    for stage in STAGES:
        (paths.scaffold / "artifacts" / f"stage{stage}").mkdir(parents=True, exist_ok=True)
        ensure_json(paths.stage_artifact(stage), {"stage": stage, "initialized": True}, force)
        ensure_text(paths.prompt(stage), default_prompt(stage), force)
        ensure_json(paths.stage_schema(stage), default_stage_schema(stage), force)

    ensure_json(paths.workflow_spec, build_init_workflow_spec_template(preset), force)

    state = dict(DEFAULT_STATE)
    state["updated_at"] = now_iso()
    ensure_json(paths.state, state, force)

    ensure_json(paths.semantic_model, INIT_SEMANTIC_MODEL, force)
    ensure_json(
        paths.semantic_snapshot,
        {
            "schema_version": 1,
            "file": paths.semantic_model.as_posix(),
            "hash": sha256_file(paths.semantic_model),
            "updated_at": now_iso(),
        },
        force,
    )
    ensure_json(paths.api_registry, DEFAULT_API_REGISTRY, force)
    ensure_json(paths.case_registry, DEFAULT_CASE_REGISTRY, force)
    ensure_json(paths.pending, DEFAULT_PENDING, force)
    ensure_json(paths.decision, DEFAULT_DECISION, force)

    if force or not paths.events.exists():
        write_text(paths.events, "")

    if force or not paths.case_descriptions.exists():
        write_text(paths.case_descriptions, "")

    ensure_json(paths.session_entity_review, DEFAULT_ENTITY_REVIEW, force)

    if force or not paths.session_case_candidates.exists():
        write_text(paths.session_case_candidates, "")

    if force or not paths.session_event_candidates.exists():
        write_text(paths.session_event_candidates, "")

    if force or not paths.session_api_candidates.exists():
        write_text(paths.session_api_candidates, "")

    ensure_json(paths.scaffold / "schemas" / "semantic_model.schema.json", semantic_model_schema(), force)
    ensure_json(paths.scaffold / "schemas" / "case_description.schema.json", case_description_schema(), force)
    ensure_json(paths.scaffold / "schemas" / "case_registry_item.schema.json", case_registry_item_schema(), force)
    ensure_json(paths.scaffold / "schemas" / "api_discovery_result.schema.json", api_discovery_schema(), force)
    ensure_json(paths.scaffold / "schemas" / "stage4_test_plan.schema.json", stage4_schema(), force)
    ensure_json(paths.scaffold / "schemas" / "stage5_test_report.schema.json", stage5_schema(), force)
    ensure_json(paths.scaffold / "schemas" / "pending_decision.schema.json", pending_decision_schema(), force)
    ensure_json(paths.scaffold / "schemas" / "decision_input.schema.json", decision_input_schema(), force)

    for doc_name, content in DEFAULT_DOCS.items():
        ensure_text(paths.scaffold / "docs" / doc_name, content, force)

    ensure_text(generated_test_file_path(paths), DEFAULT_GENERATED_TEST, force)
    sync_stage_input_snapshots(paths, force=force)


def default_prompt(stage: int) -> str:
    return (
        f"# Stage {stage}\n\n"
        "根据本阶段输入生成结构化 JSON 产物。\n"
        "要求：\n"
        "- 不编造未声明业务事实。\n"
        "- 输出必须满足 stage schema。\n"
        "- 信息缺失时写出 gap 与风险。\n"
    )


def default_stage_schema(stage: int) -> dict[str, Any]:
    required = STAGE_REQUIRED_FIELDS[stage]
    return {
        "schema_version": 1,
        "type": "object",
        "required": required,
    }


def semantic_model_schema() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "type": "object",
        "required": [
            "business_goal",
            "roles",
            "resources",
            "actions",
            "state_machines",
            "constraints",
            "permission_boundary",
        ],
    }


def case_description_schema() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "type": "object",
        "required": ["case_id", "event_id", "api_refs", "expected_business_code", "status"],
    }


def case_registry_item_schema() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "type": "object",
        "required": ["case_id", "test_file", "test_name", "event_id", "api_refs", "enabled"],
    }


def api_discovery_schema() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "type": "object",
        "required": ["scanned_apis", "existing_api_keys", "new_api_keys", "drift_items"],
    }


def stage4_schema() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "type": "object",
        "required": [
            "candidate_cases",
            "generated_cases",
            "generated_test_files",
            "coverage_by_event",
            "semantic_review",
            "test_generation",
        ],
    }


def stage5_schema() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "type": "object",
        "required": ["executed_cases", "failed_cases", "project_internal_suite", "project_test_sink", "regression_summary"],
    }


def pending_decision_schema() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "type": "object",
        "required": [
            "decision_id",
            "stage",
            "reason",
            "allowed_actions",
            "next_stage_on_approve",
            "fallback_stage",
            "created_at",
        ],
    }


def decision_input_schema() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "type": "object",
        "required": ["decision_id", "action"],
    }


# ----------------------------
# validators
# ----------------------------


def validate_semantic_model(model: dict[str, Any]) -> list[str]:
    required = semantic_model_schema()["required"]
    missing = [field for field in required if field not in model]
    return missing


def validate_stage_artifact(stage: int, artifact: dict[str, Any]) -> list[str]:
    required = STAGE_REQUIRED_FIELDS[stage]
    return [field for field in required if field not in artifact]


def validate_pending_decision(pending: dict[str, Any] | None) -> list[str]:
    if not pending:
        return ["pending.json 不存在待决策对象"]
    required = pending_decision_schema()["required"]
    missing = [field for field in required if field not in pending]
    errors: list[str] = []
    if missing:
        errors.append(f"pending 缺少字段: {missing}")

    stage = pending.get("stage")
    if stage not in STAGES:
        errors.append("pending.stage 非法")

    allowed_actions = pending.get("allowed_actions", [])
    if not isinstance(allowed_actions, list) or not allowed_actions:
        errors.append("pending.allowed_actions 非法")

    return errors


def validate_decision_input(decision: dict[str, Any], pending: dict[str, Any]) -> list[str]:
    required = decision_input_schema()["required"]
    missing = [field for field in required if field not in decision]
    errors: list[str] = []
    if missing:
        errors.append(f"decision 缺少字段: {missing}")
        return errors

    if decision.get("decision_id") != pending.get("decision_id"):
        errors.append("decision_id 与 pending 不匹配")

    action = decision.get("action")
    allowed = pending.get("allowed_actions", [])
    if action not in allowed:
        errors.append(f"action `{action}` 不在允许列表: {allowed}")

    if action == "goto_stage" and decision.get("next_stage") not in STAGES:
        errors.append("action=goto_stage 时 next_stage 必须是 0-8")

    if action == "run_stage8" and pending.get("stage") != 7:
        errors.append("run_stage8 仅允许在 stage7 门禁使用")

    return errors


def parse_stage_from_filename(name: str, prefix: str, suffix: str) -> int | None:
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    if suffix:
        middle = name[len(prefix) : -len(suffix)]
    else:
        middle = name[len(prefix) :]
    if not middle.isdigit():
        return None
    stage = int(middle)
    if stage not in STAGES:
        return None
    return stage


def analyze_recalc_graph(paths: Paths, changed_paths: list[str] | None = None) -> dict[str, Any]:
    graph = {
        "semantic": [0, 1, 3, 4, 7, 8],
        "workflow_spec": [0, 1, 2, 3, 4, 5, 6, 7, 8],
        "api_registry": [0, 2, 3, 4, 6],
        "case_registry": [4, 5, 6, 7, 8],
        "session_api_candidates": [0, 1, 2, 3, 4, 5, 6],
        "session_event_candidates": [3, 4, 5, 6],
        "case_descriptions": [4, 5, 6],
        "session_case_candidates": [4, 5, 6],
        "business_docs": [0, 1, 3, 4, 6, 7, 8],
        "permission_docs": [0, 1, 3, 4, 6, 7, 8],
    }

    if changed_paths is None:
        changed_paths = git_changed_paths(paths)

    changed_paths = [item.replace("\\", "/") for item in changed_paths]
    scaffold_prefix = rel_path(paths, paths.scaffold).strip("/") or paths.scaffold.name
    scaffold_prefix = scaffold_prefix.replace("\\", "/")
    semantic_model_path = rel_path(paths, paths.semantic_model)
    workflow_spec_path = rel_path(paths, paths.workflow_spec)
    api_registry_path = rel_path(paths, paths.api_registry)
    case_registry_path = rel_path(paths, paths.case_registry)
    session_api_candidates_path = rel_path(paths, paths.session_api_candidates)
    session_event_candidates_path = rel_path(paths, paths.session_event_candidates)
    case_descriptions_path = rel_path(paths, paths.case_descriptions)
    session_case_candidates_path = rel_path(paths, paths.session_case_candidates)
    semantic_dirs = resolve_semantic_dir_names(paths)
    business_docs_pattern = f"{semantic_dirs['business'].replace('\\', '/').strip('/')}/*"
    permission_dir = semantic_dirs.get("permission", "").replace("\\", "/").strip("/")
    permission_docs_pattern = f"{permission_dir}/*" if permission_dir else ""
    affected: set[int] = set()
    reasons: list[dict[str, Any]] = []

    snapshot = read_json(paths.semantic_snapshot, {})
    old_hash = snapshot.get("hash", "") if isinstance(snapshot, dict) else ""
    current_hash = sha256_file(paths.semantic_model)
    semantic_changed = bool(current_hash and old_hash and current_hash != old_hash)
    if semantic_changed:
        changed_paths.append(semantic_model_path)
        reasons.append(
            {
                "path": semantic_model_path,
                "reason": "semantic hash changed",
                "stages": graph["semantic"],
            }
        )
        affected.update(graph["semantic"])

    for path in changed_paths:
        if fnmatch.fnmatch(path, workflow_spec_path):
            stages = graph["workflow_spec"]
        elif fnmatch.fnmatch(path, semantic_model_path):
            stages = graph["semantic"]
        elif fnmatch.fnmatch(path, api_registry_path):
            stages = graph["api_registry"]
        elif fnmatch.fnmatch(path, case_registry_path):
            stages = graph["case_registry"]
        elif fnmatch.fnmatch(path, session_api_candidates_path):
            stages = graph["session_api_candidates"]
        elif fnmatch.fnmatch(path, session_event_candidates_path):
            stages = graph["session_event_candidates"]
        elif fnmatch.fnmatch(path, case_descriptions_path):
            stages = graph["case_descriptions"]
        elif fnmatch.fnmatch(path, session_case_candidates_path):
            stages = graph["session_case_candidates"]
        elif fnmatch.fnmatch(path, f"{scaffold_prefix}/prompts/stage*.md"):
            stage = parse_stage_from_filename(Path(path).name, "stage", ".md")
            stages = list(range(stage, 9)) if stage is not None else []
        elif fnmatch.fnmatch(path, f"{scaffold_prefix}/schemas/stage*_output.schema.json"):
            stage = parse_stage_from_filename(Path(path).name, "stage", "_output.schema.json")
            stages = [stage] if stage is not None else []
        elif fnmatch.fnmatch(path, f"{scaffold_prefix}/artifacts/stage*/latest.json"):
            folder = Path(path).parts[-2]
            stage = parse_stage_from_filename(folder, "stage", "")
            stages = list(range(stage, 9)) if stage is not None else []
        elif fnmatch.fnmatch(path, f"{scaffold_prefix}/inputs/stage*.json"):
            stage = parse_stage_from_filename(Path(path).name, "stage", ".json")
            stages = [stage] if stage is not None else []
        elif fnmatch.fnmatch(path, business_docs_pattern):
            stages = graph["business_docs"]
        elif permission_docs_pattern and fnmatch.fnmatch(path, permission_docs_pattern):
            stages = graph["permission_docs"]
        else:
            stages = []

        if not stages:
            continue
        affected.update(stages)
        reasons.append({"path": path, "reason": "dependency hit", "stages": stages})

    recommended = min(affected) if affected else 0
    return {
        "analyzed_at": now_iso(),
        "changed_paths": sorted(set(changed_paths)),
        "semantic_snapshot": {"old_hash": old_hash, "current_hash": current_hash, "changed": semantic_changed},
        "affected_stages": sorted(affected),
        "recommended_rerun_from_stage": recommended,
        "reasons": reasons,
    }


# ----------------------------
# stage implementations
# ----------------------------


def stage0(paths: Paths, spec: dict[str, Any]) -> StageResult:
    model = read_json(paths.semantic_model, {})
    api_registry = read_json(paths.api_registry, DEFAULT_API_REGISTRY)
    ignore_patterns = configured_ignore_api_patterns(spec)

    missing = validate_semantic_model(model if isinstance(model, dict) else {})
    missing_dirs = missing_semantic_directories(paths)
    blocking = bool(missing) or bool(missing_dirs)
    semantic_gap_report = {
        "blocking": blocking,
        "missing_fields": missing,
        "missing_semantic_directories": missing_dirs,
    }

    existing_api_keys = set(_extract_registry_api_keys(api_registry, ignore_patterns))

    bootstrap_done = bool(api_registry.get("bootstrap_done", False)) if isinstance(api_registry, dict) else False
    bootstrap_mode = (not existing_api_keys) and (not bootstrap_done)

    scanned_apis, scan_source, scan_error = discover_apis(paths, spec)
    observed_api_keys = dedupe_keep_order(
        [normalize_api_key(item) for item in scanned_apis if isinstance(item, str) and normalize_api_key(item)]
    )
    observed_set = set(observed_api_keys)

    session_candidates, session_api_warnings = load_stage0_session_api_candidates(
        paths,
        model if isinstance(model, dict) else {},
        existing_api_keys,
        observed_set,
    )
    state_snapshot = read_json(paths.state, {})
    stage0_replan_required = False
    stage0_entered_at = ""
    if isinstance(state_snapshot, dict):
        stage0_replan_required = bool(state_snapshot.get("stage0_replan_required", False))
        stage0_entered_at = str(state_snapshot.get("stage0_entered_at", "")).strip()
    stage0_entered_epoch = parse_iso_to_epoch(stage0_entered_at)
    session_api_candidate_mtime_epoch = file_mtime_epoch(paths.session_api_candidates)
    session_api_candidate_mtime = epoch_to_iso(session_api_candidate_mtime_epoch)
    session_api_fresh = True
    if stage0_replan_required and not bootstrap_mode:
        if stage0_entered_epoch is None or session_api_candidate_mtime_epoch is None:
            session_api_fresh = False
        else:
            session_api_fresh = session_api_candidate_mtime_epoch >= stage0_entered_epoch
        if not session_api_fresh:
            session_api_warnings.append(
                "stage0_replan_required: session_api_candidates.jsonl must be rewritten after entering stage0"
            )

    selected_candidates: list[dict[str, Any]] = []
    candidate_source = "none"
    api_candidates: list[dict[str, Any]] = []
    if not blocking:
        if bootstrap_mode and observed_api_keys:
            bootstrap_entity = unambiguous_business_entity(paths, model if isinstance(model, dict) else {})
            selected_candidates = [
                {
                    "entity": bootstrap_entity,
                    "api_key": api_key,
                    "expected_business_codes": [],
                    "semantic_title": "",
                    "semantic_description": "",
                }
                for api_key in observed_api_keys
                if api_key not in existing_api_keys
            ]
            candidate_source = "bootstrap_observed_unregistered"
        else:
            if stage0_replan_required and not session_api_fresh:
                selected_candidates = []
                candidate_source = "session_semantic_replan_required"
            else:
                # 非 bootstrap 轮次由当前 session 基于业务描述与项目文档语义提名 API。
                selected_candidates = [dict(item) for item in session_candidates]
                candidate_source = "session_semantic"

    max_new_api = int(spec.get("max_new_api_per_iteration", 1))
    if not bootstrap_mode:
        selected_candidates = selected_candidates[: max(1, max_new_api)]
    for item in selected_candidates:
        reason = str(item.get("reason", "")).strip() or "由当前 Codex session 基于业务语义提名，需人工 review"
        risk = str(item.get("risk", "medium")).strip().lower()
        if risk not in {"low", "medium", "high"}:
            risk = "medium"
        entity = normalize_entity_name(str(item.get("entity", "")))
        if bootstrap_mode and observed_set and item["api_key"] in observed_set:
            reason = "bootstrap 扫描发现存量 API，按流程批量初始化处理"
            risk = "medium"
        api_candidates.append(
            {
                "entity": entity,
                "api_key": item["api_key"],
                "expected_business_codes": normalize_business_code_list(item.get("expected_business_codes", [])),
                "semantic_title": str(item.get("semantic_title", "")).strip(),
                "semantic_description": str(item.get("semantic_description", "")).strip(),
                "reason": reason,
                "risk": risk,
            }
        )

    touched_entities = dedupe_keep_order(
        [
            normalize_entity_name(str(item.get("entity", "")))
            for item in selected_candidates
            if isinstance(item, dict) and normalize_entity_name(str(item.get("entity", "")))
        ]
    )

    artifact = {
        "stage": 0,
        "generated_at": now_iso(),
        "api_candidates": api_candidates,
        "biz_status_codes": [
            "SUCCESS",
            "INVALID_PARAMS",
            "PERMISSION_DENIED",
            "RESOURCE_NOT_FOUND",
            "STATE_CONFLICT",
            "IDEMPOTENT_DUPLICATE",
        ],
        "required_case_codes": [
            "SUCCESS",
            "INVALID_PARAMS",
            "PERMISSION_DENIED",
            "RESOURCE_NOT_FOUND",
            "STATE_CONFLICT",
            "IDEMPOTENT_DUPLICATE",
        ],
        "risk_assessment": {
            "level": "high" if blocking else "medium",
            "notes": "语义缺口存在时禁止进入开发执行",
        },
        "commit_message": "stage-0(api-seed): generate next api candidate",
        "semantic_gap_report": semantic_gap_report,
        "api_registry_items": len(existing_api_keys),
        "candidate_source": candidate_source,
        "session_api_candidate_file": rel_path(paths, paths.session_api_candidates),
        "session_api_candidate_count": len(session_candidates),
        "session_api_warnings": session_api_warnings,
        "stage0_replan_required": stage0_replan_required,
        "stage0_entered_at": stage0_entered_at,
        "session_api_candidate_mtime": session_api_candidate_mtime,
        "session_api_fresh": session_api_fresh,
        "session_api_replan_blocking": bool(stage0_replan_required and not session_api_fresh and not bootstrap_mode),
        "bootstrap_mode": bootstrap_mode,
        "scan_source": scan_source,
        "scan_error": scan_error,
        "observed_api_count": len(observed_api_keys),
        "touched_entities": touched_entities,
    }

    return StageResult(stage=0, artifact=artifact, next_stage=1, gate=True)


def stage1(paths: Paths, spec: dict[str, Any]) -> StageResult:
    stage0_artifact = read_json(paths.stage_artifact(0), {})
    candidate = None
    touched_entities: list[str] = []
    if isinstance(stage0_artifact, dict):
        candidates = stage0_artifact.get("api_candidates", [])
        if isinstance(candidates, list) and candidates:
            candidate = candidates[0]
        entities = stage0_artifact.get("touched_entities", [])
        if isinstance(entities, list):
            normalized_entities: list[str] = []
            for item in entities:
                normalized = normalize_entity_name(str(item))
                if normalized:
                    normalized_entities.append(normalized)
            touched_entities = dedupe_keep_order(normalized_entities)

    artifact = {
        "stage": 1,
        "generated_at": now_iso(),
        "implementation_tasks": [
            "根据 stage0 候选 API 完成 serializer/view/urls 与权限接入",
            "补充对应测试与业务状态码处理",
        ],
        "changed_files": [],
        "notes": [
            "Runner 当前生成的是执行计划模板，具体代码变更由开发会话执行",
            f"candidate={candidate}",
        ],
        "touched_entities": touched_entities,
    }
    return StageResult(stage=1, artifact=artifact, next_stage=2)


def _scan_api_from_schema(schema_url: str, timeout_seconds: int) -> tuple[list[str], str]:
    req = urllib.request.Request(schema_url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    paths = payload.get("paths", {})
    scanned: list[str] = []
    if isinstance(paths, dict):
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method in methods.keys():
                if method.lower() in HTTP_METHODS:
                    scanned.append(f"{method.upper()} {path}")

    scanned = sorted(set(scanned))
    return scanned, "openapi"


def _scan_api_from_root(root_url: str, timeout_seconds: int) -> tuple[list[str], str]:
    req = urllib.request.Request(root_url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    endpoints = payload.get("endpoints", {})
    scanned: list[str] = []
    if isinstance(endpoints, dict):
        for _, endpoint_url in endpoints.items():
            if isinstance(endpoint_url, str):
                scanned.append(f"GET {endpoint_url}")

    scanned = sorted(set(scanned))
    return scanned, "api_root"


def _scan_api_from_local_schema(paths: Paths, command: list[str]) -> tuple[list[str], str]:
    proc = subprocess.run(
        command,
        cwd=str(paths.root),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(proc.stderr.strip() or proc.stdout.strip() or f"local scan command failed: {command}")

    raw = proc.stdout.strip()
    if not raw:
        raise ValueError("local scan command returned empty stdout")

    payload = json.loads(raw)
    schema_paths = payload.get("paths", {})
    scanned: list[str] = []
    if isinstance(schema_paths, dict):
        for path, methods in schema_paths.items():
            if not isinstance(methods, dict):
                continue
            for method in methods.keys():
                if method.lower() in HTTP_METHODS:
                    scanned.append(f"{method.upper()} {path}")

    scanned = sorted(set(scanned))
    return scanned, "local_openapi"


def discover_apis(paths: Paths, spec: dict[str, Any]) -> tuple[list[str], str, str]:
    business_api = spec.get("business_api", {})
    schema_url = str(business_api.get("schema_url", "")).strip()
    root_url = str(business_api.get("root_url", "")).strip()
    timeout_seconds = int(business_api.get("timeout_seconds", 5))
    ignore_patterns = configured_ignore_api_patterns(spec)
    errors: list[str] = []

    if schema_url:
        try:
            raw_scanned, source = _scan_api_from_schema(schema_url, timeout_seconds)
            scanned = filter_api_keys(raw_scanned, ignore_patterns)
            if scanned:
                return scanned, source, ""
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError) as exc:
            errors.append(f"schema_error={exc}")
        else:
            errors.append("schema_error=schema_empty_after_ignore" if raw_scanned else "schema_error=schema_empty")

    if root_url:
        try:
            raw_scanned, source = _scan_api_from_root(root_url, timeout_seconds)
            scanned = filter_api_keys(raw_scanned, ignore_patterns)
            if scanned:
                return scanned, source, ""
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError) as exc:
            errors.append(f"root_error={exc}")
        else:
            errors.append("root_error=root_empty_after_ignore" if raw_scanned else "root_error=root_empty")

    local_enabled = bool(business_api.get("local_scan_enabled", True))
    if local_enabled:
        local_command = business_api.get("local_scan_command")
        if (
            isinstance(local_command, list)
            and local_command
            and all(isinstance(item, str) and item.strip() for item in local_command)
        ):
            command = [item.strip() for item in local_command]
        else:
            local_urlconf = str(business_api.get("local_scan_urlconf", "config.business_api_urlconf")).strip()
            venv_python = paths.root / ".venv" / "bin" / "python"
            python_bin = venv_python.as_posix() if venv_python.exists() else sys.executable
            command = [
                python_bin,
                "manage.py",
                "spectacular",
                "--format",
                "openapi-json",
                "--urlconf",
                local_urlconf,
            ]
        try:
            raw_scanned, source = _scan_api_from_local_schema(paths, command)
            scanned = filter_api_keys(raw_scanned, ignore_patterns)
            if scanned:
                return scanned, source, ""
            errors.append(
                "local_error=local_schema_empty_after_ignore" if raw_scanned else "local_error=local_schema_empty"
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"local_error={exc}")
    else:
        errors.append("local_error=local_scan_disabled")

    if not errors:
        errors.append("scan_error=unknown")
    return [], "none", "; ".join(errors)


def _extract_registry_api_keys(api_registry: dict[str, Any], ignore_patterns: list[str] | None = None) -> list[str]:
    keys: list[str] = []
    items = api_registry.get("items", []) if isinstance(api_registry, dict) else []
    if not isinstance(items, list):
        return keys
    effective_ignore_patterns = ignore_patterns or []
    for item in items:
        if not isinstance(item, dict):
            continue
        api_key = item.get("api_key")
        if not isinstance(api_key, str):
            continue
        normalized = normalize_api_key(api_key)
        if normalized:
            if any(api_key_matches_pattern(normalized, pattern) for pattern in effective_ignore_patterns):
                continue
            keys.append(normalized)
    return sorted(set(keys))


def _stage2_autofix_missing_online(
    paths: Paths,
    api_registry: dict[str, Any],
    missing_online: list[str],
    bootstrap_mode: bool,
) -> dict[str, Any]:
    autofix: dict[str, Any] = {
        "attempted": False,
        "removed_api_keys": [],
        "remaining_missing_online": list(missing_online),
        "error": "",
    }
    if bootstrap_mode or not missing_online:
        return autofix
    if not isinstance(api_registry, dict):
        autofix["error"] = "api_registry_invalid"
        return autofix

    items = api_registry.get("items", [])
    if not isinstance(items, list):
        autofix["error"] = "api_registry_items_invalid"
        return autofix

    remove_set = {normalize_api_key(item) for item in missing_online if normalize_api_key(item)}
    if not remove_set:
        return autofix

    autofix["attempted"] = True
    kept_items: list[Any] = []
    removed: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            kept_items.append(item)
            continue
        raw_key = item.get("api_key")
        if not isinstance(raw_key, str):
            kept_items.append(item)
            continue
        normalized = normalize_api_key(raw_key)
        if normalized in remove_set:
            removed.append(normalized)
            continue
        kept_items.append(item)

    api_registry["items"] = kept_items
    try:
        write_json(paths.api_registry, api_registry)
    except OSError as exc:
        autofix["error"] = str(exc)
        return autofix

    removed_unique = sorted(set(removed))
    autofix["removed_api_keys"] = removed_unique
    autofix["remaining_missing_online"] = sorted(remove_set - set(removed_unique))
    return autofix


def stage2(paths: Paths, spec: dict[str, Any], state: dict[str, Any]) -> StageResult:
    api_registry = read_json(paths.api_registry, DEFAULT_API_REGISTRY)
    ignore_patterns = configured_ignore_api_patterns(spec)
    registry_ignore_autofix = {
        "attempted": False,
        "removed_api_keys": [],
        "error": "",
    }
    removed_ignored_keys, registry_changed = _prune_ignored_api_registry_items(api_registry, ignore_patterns)
    if registry_changed:
        registry_ignore_autofix["attempted"] = True
        registry_ignore_autofix["removed_api_keys"] = removed_ignored_keys
        try:
            write_json(paths.api_registry, api_registry)
        except OSError as exc:
            registry_ignore_autofix["error"] = str(exc)
        else:
            api_registry = read_json(paths.api_registry, DEFAULT_API_REGISTRY)
    scanned_apis, source, scan_error = discover_apis(paths, spec)
    if registry_ignore_autofix["error"]:
        scan_error = "; ".join(
            [item for item in [scan_error, f"registry_ignore_error={registry_ignore_autofix['error']}"] if item]
        )

    existing_api_keys = _extract_registry_api_keys(api_registry, ignore_patterns)

    bootstrap_done = bool(api_registry.get("bootstrap_done", False))
    bootstrap_mode = (not existing_api_keys) and (not bootstrap_done)
    missing_online = sorted(set(existing_api_keys) - set(scanned_apis))
    drift_autofix = _stage2_autofix_missing_online(paths, api_registry, missing_online, bootstrap_mode)
    if drift_autofix.get("attempted"):
        api_registry = read_json(paths.api_registry, DEFAULT_API_REGISTRY)
        existing_api_keys = _extract_registry_api_keys(api_registry)
        missing_online = sorted(set(existing_api_keys) - set(scanned_apis))

    new_api_keys = sorted(set(scanned_apis) - set(existing_api_keys))
    if bootstrap_mode:
        # bootstrap 轮次用于按 SKILL 规则对既有 API 做批量初始化校验。
        new_api_keys = sorted(set(scanned_apis))

    drift_items: list[dict[str, Any]] = []
    if missing_online:
        drift_items.extend(
            {"type": "missing_online", "api_key": api_key} for api_key in missing_online
        )

    artifact = {
        "stage": 2,
        "generated_at": now_iso(),
        "source": source,
        "scan_error": scan_error,
        "scanned_apis": scanned_apis,
        "existing_api_keys": existing_api_keys,
        "new_api_keys": new_api_keys,
        "drift_items": drift_items,
        "drift_autofix": drift_autofix,
        "registry_ignore_autofix": registry_ignore_autofix,
        "bootstrap_mode": bootstrap_mode,
    }

    if scan_error:
        return StageResult(stage=2, artifact=artifact, next_stage=7)
    if drift_items and not bootstrap_mode:
        return StageResult(stage=2, artifact=artifact, next_stage=7)

    return StageResult(stage=2, artifact=artifact, next_stage=3)


def parse_api_key_parts(api_key: str) -> tuple[str, str]:
    normalized = normalize_api_key(api_key)
    parts = normalized.split(" ", 1)
    if len(parts) != 2:
        return "GET", "/"
    return parts[0].upper(), parts[1]


def stage0_candidate_api_items_from_artifact(stage0_artifact: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_api_keys: set[str] = set()
    candidates = stage0_artifact.get("api_candidates", []) if isinstance(stage0_artifact, dict) else []
    if not isinstance(candidates, list):
        return items

    for item in candidates:
        if not isinstance(item, dict):
            continue
        api_key = normalize_api_key(str(item.get("api_key", "")))
        if not api_key or api_key in seen_api_keys:
            continue
        seen_api_keys.add(api_key)
        items.append(
            {
                "api_key": api_key,
                "entity": normalize_entity_name(str(item.get("entity", ""))),
                "expected_business_codes": normalize_business_code_list(item.get("expected_business_codes", [])),
                "semantic_title": str(item.get("semantic_title", "")).strip(),
                "semantic_description": str(item.get("semantic_description", "")).strip(),
                "reason": str(item.get("reason", "")).strip(),
                "risk": str(item.get("risk", "")).strip().lower(),
            }
        )
    return items


def stage0_candidate_api_keys_from_artifact(stage0_artifact: dict[str, Any]) -> list[str]:
    return [item["api_key"] for item in stage0_candidate_api_items_from_artifact(stage0_artifact)]


def stage3_focus_api_items(
    paths: Paths,
    stage0_artifact: dict[str, Any],
    stage2_artifact: dict[str, Any],
    semantic_model: dict[str, Any],
) -> list[dict[str, Any]]:
    return stage0_candidate_api_items_from_artifact(stage0_artifact)


def should_increment_iteration_on_decision(pending_stage: int, next_stage: int, action: str) -> bool:
    if action != "approve":
        return False
    if pending_stage == 6 and next_stage == 0:
        return True
    if pending_stage == 8 and next_stage == 0:
        return True
    return False


def stage3_semantic_context_for_entity(entity: str, semantic_model: dict[str, Any]) -> dict[str, Any]:
    state_machine = None
    raw_machines = semantic_model.get("state_machines", []) if isinstance(semantic_model, dict) else []
    if isinstance(raw_machines, list):
        for item in raw_machines:
            if not isinstance(item, dict):
                continue
            resource = normalize_entity_name(str(item.get("resource", "")))
            if entity and resource == entity:
                state_machine = item
                break

    constraints = semantic_model.get("constraints", []) if isinstance(semantic_model, dict) else []
    constraint_rows = [str(item) for item in constraints if isinstance(item, str)]
    permission_boundary = semantic_model.get("permission_boundary", {}) if isinstance(semantic_model, dict) else {}

    return {
        "entity": entity,
        "state_machine": state_machine if isinstance(state_machine, dict) else {},
        "constraints": constraint_rows,
        "permission_boundary": permission_boundary if isinstance(permission_boundary, dict) else {},
    }


def stage3_progress_snapshot(
    paths: Paths,
    stage0_artifact: dict[str, Any],
    stage1_artifact: dict[str, Any],
    stage2_artifact: dict[str, Any],
) -> dict[str, Any]:
    stage0_candidates = stage0_candidate_api_keys_from_artifact(stage0_artifact)
    scanned_apis = stage2_artifact.get("scanned_apis", []) if isinstance(stage2_artifact, dict) else []
    scanned_keys = dedupe_keep_order([normalize_api_key(item) for item in scanned_apis if isinstance(item, str)])
    scanned_set = set(scanned_keys)
    candidate_set = set(stage0_candidates)

    implemented_candidates = [api_key for api_key in stage0_candidates if api_key in scanned_set]
    unimplemented_candidates = [api_key for api_key in stage0_candidates if api_key not in scanned_set]
    changed_paths = git_changed_paths(paths)
    changed_files = stage1_artifact.get("changed_files", []) if isinstance(stage1_artifact, dict) else []
    changed_files = [str(item) for item in changed_files if isinstance(item, str)]
    implementation_tasks = stage1_artifact.get("implementation_tasks", []) if isinstance(stage1_artifact, dict) else []
    implementation_tasks = [str(item) for item in implementation_tasks if isinstance(item, str)]

    return {
        "stage0_candidate_api_count": len(stage0_candidates),
        "stage2_scanned_api_count": len(scanned_keys),
        "implemented_candidate_api_count": len(implemented_candidates),
        "unimplemented_candidate_api_count": len(unimplemented_candidates),
        "implemented_candidate_apis": implemented_candidates[:20],
        "unimplemented_candidate_apis": unimplemented_candidates[:20],
        "stage1_task_count": len(implementation_tasks),
        "stage1_changed_files_count": len(changed_files),
        "stage1_changed_files": changed_files[:30],
        "git_changed_paths_count": len(changed_paths),
        "git_changed_paths": changed_paths[:40],
        "bootstrap_mode": bool(stage2_artifact.get("bootstrap_mode", False)) if isinstance(stage2_artifact, dict) else False,
        "candidate_apis_observed_online": len(candidate_set & scanned_set),
    }


def stage3_required_case_codes(stage0_artifact: dict[str, Any]) -> list[str]:
    required_codes = normalize_business_code_list(stage0_artifact.get("required_case_codes", []))
    if required_codes:
        return required_codes
    return ["SUCCESS", "INVALID_PARAMS"]


def stage3(paths: Paths, spec: dict[str, Any]) -> StageResult:
    stage2_artifact = read_json(paths.stage_artifact(2), {})
    stage0_artifact = read_json(paths.stage_artifact(0), {})
    stage1_artifact = read_json(paths.stage_artifact(1), {})
    semantic_model = read_json(paths.semantic_model, DEFAULT_SEMANTIC_MODEL)
    focus_api_items = stage3_focus_api_items(
        paths,
        stage0_artifact if isinstance(stage0_artifact, dict) else {},
        stage2_artifact if isinstance(stage2_artifact, dict) else {},
        semantic_model if isinstance(semantic_model, dict) else {},
    )
    focus_api_keys = [str(item.get("api_key", "")).strip() for item in focus_api_items if str(item.get("api_key", "")).strip()]
    progress = stage3_progress_snapshot(
        paths,
        stage0_artifact if isinstance(stage0_artifact, dict) else {},
        stage1_artifact if isinstance(stage1_artifact, dict) else {},
        stage2_artifact if isinstance(stage2_artifact, dict) else {},
    )
    required_case_codes = stage3_required_case_codes(stage0_artifact if isinstance(stage0_artifact, dict) else {})
    state = read_json(paths.state, DEFAULT_STATE)
    running_iteration = int(state.get("running_iteration", 1)) if isinstance(state, dict) else 1
    business_event_candidates, session_event_warnings = load_session_event_candidates(
        paths,
        focus_api_items,
        required_case_codes,
        running_iteration,
    )

    business_events = clone_case_rows(business_event_candidates)
    semantic_resources = semantic_model.get("resources", []) if isinstance(semantic_model, dict) else []
    semantic_actions = semantic_model.get("actions", []) if isinstance(semantic_model, dict) else []
    semantic_constraints = semantic_model.get("constraints", []) if isinstance(semantic_model, dict) else []
    semantic_context = {
        "resources": [str(item) for item in semantic_resources if isinstance(item, str)],
        "actions": [str(item) for item in semantic_actions if isinstance(item, str)],
        "constraints": [str(item) for item in semantic_constraints if isinstance(item, str)],
    }

    artifact = {
        "stage": 3,
        "generated_at": now_iso(),
        "business_events": business_events,
        "business_event_candidates": business_event_candidates,
        "focus_api_items": focus_api_items,
        "focus_api_keys": focus_api_keys,
        "progress_snapshot": progress,
        "semantic_context": semantic_context,
        "session_event_candidate_file": rel_path(paths, paths.session_event_candidates),
        "session_event_warnings": session_event_warnings,
        "semantic_review": {
            "status": "pending",
            "reviewer": "",
            "reason": (
                "等待当前 Codex session 从 session_event_candidates.jsonl 同步并审阅业务事件"
                if business_event_candidates
                else "session_event_candidates.jsonl 为空或无有效记录；需由当前 Codex session 先补齐业务事件候选"
            ),
            "candidate_count": len(business_event_candidates),
            "selected_count": len(business_events),
            "selected_event_ids": [
                str(item.get("event_id", "")) for item in business_events if isinstance(item, dict)
            ],
            "rejected_event_ids": [],
            "updated_at": now_iso(),
        },
        "editor_notes": ["可在 Stage3 语义事件基础上继续补充复杂业务流程与跨 API 串联场景"],
    }
    return StageResult(stage=3, artifact=artifact, next_stage=4, gate=True)


def _write_case_descriptions(paths: Paths, cases: list[dict[str, Any]]) -> None:
    lines = [json.dumps(item, ensure_ascii=False) for item in cases]
    text = "\n".join(lines)
    if text:
        text += "\n"
    write_text(paths.case_descriptions, text)


def _test_name_for_case(case_id: str, seq: int) -> str:
    token = re.sub(r"[^a-z0-9_]+", "_", case_id.lower().replace("-", "_")).strip("_")
    if not token:
        token = f"case_{seq:03d}"
    if token[0].isdigit():
        token = f"case_{token}"
    return f"test_auto__{token}"


def assign_case_test_names(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for idx, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id", f"CASE-AUTO-{idx:03d}"))
        candidate = str(case.get("test_name", "")).strip()
        test_name = candidate if candidate.startswith("test_") else _test_name_for_case(case_id, idx)
        while test_name in used_names:
            test_name = f"{test_name}_{idx}"
        used_names.add(test_name)
        case["test_name"] = test_name
        rows.append({"case": case, "case_id": case_id, "test_name": test_name})
    return rows


def expected_test_names_from_cases(cases: list[dict[str, Any]]) -> list[str]:
    rows = assign_case_test_names(cases)
    return [str(row["test_name"]) for row in rows]


def extract_test_functions_from_text(test_text: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"(?m)^\s*def\s+(test_[A-Za-z0-9_]+)\s*\(", test_text or ""):
        name = str(match.group(1)).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def extract_test_function_blocks_from_text(test_text: str) -> dict[str, str]:
    content = test_text or ""
    matches = list(re.finditer(r"(?m)^\s*def\s+(test_[A-Za-z0-9_]+)\s*\(", content))
    blocks: dict[str, str] = {}
    for idx, match in enumerate(matches):
        name = str(match.group(1)).strip()
        if not name:
            continue
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
        blocks[name] = content[start:end]
    return blocks


_HTTP_CLIENT_METHOD_MARKERS = {
    "GET": ("self.client.get(", "client.get(", "requests.get(", "httpx.get(", "api_client.get("),
    "POST": ("self.client.post(", "client.post(", "requests.post(", "httpx.post(", "api_client.post("),
    "PUT": ("self.client.put(", "client.put(", "requests.put(", "httpx.put(", "api_client.put("),
    "PATCH": ("self.client.patch(", "client.patch(", "requests.patch(", "httpx.patch(", "api_client.patch("),
    "DELETE": ("self.client.delete(", "client.delete(", "requests.delete(", "httpx.delete(", "api_client.delete("),
}


def _api_ref_path_evidence_markers(path: str) -> tuple[list[str], list[str]]:
    placeholder_pattern = r"\{[^}]+\}"
    has_placeholder = bool(re.search(placeholder_pattern, path))
    exact_markers: list[str] = []
    if not has_placeholder:
        exact_markers = [
            f'"{path}"',
            f"'{path}'",
            f'f"{path}"',
            f"f'{path}'",
        ]

    fragments = [fragment for fragment in re.split(placeholder_pattern, path) if fragment and fragment != "/"]
    return exact_markers, fragments


def _test_block_matches_api_ref(test_block: str, api_ref: str) -> bool:
    normalized = normalize_api_key(api_ref)
    if not normalized or " " not in normalized:
        return False

    method, path = normalized.split(" ", 1)
    block = str(test_block)
    block_lower = block.lower()
    if normalized.lower() in block_lower:
        return True

    method_markers = _HTTP_CLIENT_METHOD_MARKERS.get(method.upper(), ())
    if not any(marker in block_lower for marker in method_markers):
        return False

    exact_markers, fragments = _api_ref_path_evidence_markers(path)
    if exact_markers and any(marker.lower() in block_lower for marker in exact_markers):
        return True

    return bool(fragments) and all(fragment.lower() in block_lower for fragment in fragments)


def _semantic_check_for_test_block(test_block: str, case: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not str(test_block).strip():
        return ["missing_test_body"]

    request_markers = [
        "reverse(",
        "self.client.",
        "client.get(",
        "client.post(",
        "client.put(",
        "client.patch(",
        "client.delete(",
        ".request(",
        "requests.",
        "httpx.",
        "api_client.",
        "api_ref =",
        "url =",
        "path =",
    ]
    if not any(marker in test_block for marker in request_markers):
        issues.append("missing_api_request_evidence")

    api_refs = [str(item).strip() for item in case.get("api_refs", []) if str(item).strip()]
    if api_refs and not any(_test_block_matches_api_ref(test_block, api_ref) for api_ref in api_refs):
        issues.append(f"missing_api_ref_evidence:{api_refs[0]}")

    business_code = str(case.get("expected_business_code", "")).strip().upper()
    if business_code:
        if "business_code" not in test_block or business_code not in test_block:
            issues.append(f"missing_business_code_assert:{business_code}")

    if "business_detail_code" not in test_block:
        issues.append("missing_business_detail_code_assert")

    return issues


def analyze_stage4_test_generation(paths: Paths, generated_cases: list[dict[str, Any]]) -> dict[str, Any]:
    expected_tests = expected_test_names_from_cases(generated_cases)
    test_file = generated_test_file_path(paths)
    rel_file = rel_path(paths, test_file)

    file_exists = test_file.exists()
    content = ""
    if file_exists:
        content = test_file.read_text(encoding="utf-8")

    observed_tests = extract_test_functions_from_text(content)
    observed_set = set(observed_tests)
    expected_set = set(expected_tests)
    missing_tests = [name for name in expected_tests if name not in observed_set]
    extra_tests = [name for name in observed_tests if name not in expected_set]
    placeholder_detected = "test_placeholder_generated_case" in observed_set
    test_blocks = extract_test_function_blocks_from_text(content)

    spec = read_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC)
    semantic_required = stage4_semantic_test_required(spec if isinstance(spec, dict) else DEFAULT_WORKFLOW_SPEC)
    semantic_failures: dict[str, list[str]] = {}
    semantic_checked_tests = 0

    if semantic_required:
        for row in assign_case_test_names(generated_cases):
            test_name = str(row.get("test_name", "")).strip()
            if not test_name or test_name in missing_tests:
                continue
            case = row.get("case", {})
            if not isinstance(case, dict):
                continue
            test_block = test_blocks.get(test_name, "")
            issues = _semantic_check_for_test_block(test_block, case)
            semantic_checked_tests += 1
            if issues:
                semantic_failures[test_name] = issues

    semantic_failed_tests = sorted(semantic_failures.keys())
    semantic_failure_count = len(semantic_failed_tests)
    semantic_preview = [
        {"test_name": name, "issues": semantic_failures.get(name, [])}
        for name in semantic_failed_tests[:20]
    ]

    ready_base = bool(expected_tests) and not missing_tests and not placeholder_detected
    ready = ready_base and (not semantic_required or semantic_failure_count == 0)
    if ready:
        reason = "ready_for_stage5"
    elif semantic_required and semantic_failure_count > 0 and ready_base:
        reason = "waiting_semantic_test_codegen"
    else:
        reason = "waiting_session_codegen"

    return {
        "status": "ready" if ready else "pending",
        "reason": reason,
        "test_file": rel_file,
        "file_exists": file_exists,
        "expected_tests": expected_tests,
        "observed_tests": observed_tests,
        "missing_tests": missing_tests,
        "extra_tests": extra_tests,
        "placeholder_detected": placeholder_detected,
        "semantic_test_required": semantic_required,
        "semantic_checked_tests": semantic_checked_tests,
        "semantic_failure_count": semantic_failure_count,
        "semantic_failed_tests": semantic_failed_tests,
        "semantic_failure_preview": semantic_preview,
        "updated_at": now_iso(),
    }


def clone_case_rows(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        if isinstance(case, dict):
            rows.append(dict(case))
    return rows


def normalize_case_id_tokens(raw_tokens: list[str]) -> list[str]:
    tokens: list[str] = []
    for raw in raw_tokens:
        for chunk in str(raw).split(","):
            token = chunk.strip()
            if token:
                tokens.append(token)
    return dedupe_keep_order(tokens)


def normalize_event_id_tokens(raw_tokens: list[str]) -> list[str]:
    tokens: list[str] = []
    for raw in raw_tokens:
        for chunk in str(raw).split(","):
            token = chunk.strip()
            if token:
                tokens.append(token)
    return dedupe_keep_order(tokens)


def apply_stage3_semantic_selection(
    paths: Paths,
    stage3_artifact: dict[str, Any],
    selected_event_ids: list[str],
    reason: str,
    reviewer: str,
) -> dict[str, Any]:
    candidate_events_raw = stage3_artifact.get("business_event_candidates", [])
    candidate_events = [dict(item) for item in candidate_events_raw if isinstance(item, dict)]
    if not candidate_events:
        raise WorkflowError("stage3 artifact 缺少 business_event_candidates，无法执行语义筛选")

    event_by_id: dict[str, dict[str, Any]] = {}
    candidate_ids: list[str] = []
    for event in candidate_events:
        event_id = str(event.get("event_id", "")).strip()
        if not event_id or event_id in event_by_id:
            continue
        event_by_id[event_id] = event
        candidate_ids.append(event_id)

    normalized_selected = normalize_event_id_tokens(selected_event_ids)
    if not normalized_selected:
        normalized_selected = list(candidate_ids)

    missing_ids = [event_id for event_id in normalized_selected if event_id not in event_by_id]
    if missing_ids:
        preview = missing_ids[:8]
        tail = "..." if len(missing_ids) > 8 else ""
        raise WorkflowError(f"存在未知 event_id: {preview}{tail}")

    selected_set = set(normalized_selected)
    ordered_selected_ids = [event_id for event_id in candidate_ids if event_id in selected_set]
    if not ordered_selected_ids:
        raise WorkflowError("语义筛选后 business_events 为空，请至少保留 1 个事件")

    selected_events = [dict(event_by_id[event_id]) for event_id in ordered_selected_ids]
    rejected_event_ids = [event_id for event_id in candidate_ids if event_id not in selected_set]

    stage3_artifact["business_events"] = selected_events
    stage3_artifact["semantic_review"] = {
        "status": "approved",
        "reviewer": reviewer,
        "reason": reason or "session semantic review",
        "candidate_count": len(candidate_ids),
        "selected_count": len(ordered_selected_ids),
        "selected_event_ids": ordered_selected_ids,
        "rejected_event_ids": rejected_event_ids,
        "updated_at": now_iso(),
    }
    stage3_artifact["selection_updated_at"] = now_iso()
    write_json(paths.stage_artifact(3), stage3_artifact)

    append_event(
        paths,
        "stage3_semantic_review_applied",
        {
            "selected_count": len(ordered_selected_ids),
            "candidate_count": len(candidate_ids),
            "reviewer": reviewer,
        },
    )

    return {
        "selected_count": len(ordered_selected_ids),
        "candidate_count": len(candidate_ids),
        "rejected_count": len(rejected_event_ids),
        "selected_event_ids": ordered_selected_ids,
        "rejected_event_ids": rejected_event_ids,
    }


def build_stage4_coverage(
    business_events: list[dict[str, Any]],
    required_codes: list[str],
    generated_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    coverage_by_event: list[dict[str, Any]] = []
    event_ids: list[str] = []
    for event in business_events:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id", "")).strip()
        if event_id:
            event_ids.append(event_id)

    event_ids = dedupe_keep_order(event_ids)
    if not event_ids:
        for case in generated_cases:
            if not isinstance(case, dict):
                continue
            event_id = str(case.get("event_id", "")).strip()
            if event_id:
                event_ids.append(event_id)
        event_ids = dedupe_keep_order(event_ids)

    normalized_codes = dedupe_keep_order([str(code).strip() for code in required_codes if str(code).strip()])
    expected_by_event: dict[str, list[str]] = {}
    for event in business_events:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id", "")).strip()
        if not event_id:
            continue
        event_codes = event.get("expected_business_codes", [])
        if not isinstance(event_codes, list):
            event_codes = []
        normalized_event_codes = dedupe_keep_order([str(code).strip() for code in event_codes if str(code).strip()])
        expected_by_event[event_id] = normalized_event_codes or normalized_codes

    for event_id in event_ids:
        event_codes: list[str] = []
        for item in generated_cases:
            if not isinstance(item, dict):
                continue
            if str(item.get("event_id", "")) != event_id:
                continue
            code = str(item.get("expected_business_code", "")).strip()
            if code:
                event_codes.append(code)
        covered = dedupe_keep_order(event_codes)
        covered_set = set(covered)
        expected_codes = expected_by_event.get(event_id, normalized_codes)
        coverage_by_event.append(
            {
                "event_id": event_id,
                "covered_codes": covered,
                "missing_codes": [code for code in expected_codes if code not in covered_set],
            }
        )
    return coverage_by_event


def evaluate_stage4_case_traceability(
    stage0_artifact: dict[str, Any],
    business_events: list[dict[str, Any]],
    generated_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    focus_api_keys = stage0_candidate_api_keys_from_artifact(stage0_artifact)
    event_lookup = _stage4_event_lookup(business_events)
    mismatch_items: list[dict[str, Any]] = []

    for case in generated_cases:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id", "")).strip() or "(missing_case_id)"
        event_id = str(case.get("event_id", "")).strip()
        refs_raw = case.get("api_refs", [])
        refs = [str(item) for item in refs_raw if isinstance(item, str)] if isinstance(refs_raw, list) else []
        normalized_refs = dedupe_keep_order([normalize_api_key(item) for item in refs if normalize_api_key(item)])
        event_meta = event_lookup.get(event_id, {})
        event_refs_raw = event_meta.get("api_refs", []) if isinstance(event_meta, dict) else []
        event_refs = dedupe_keep_order(
            [
                normalize_api_key(item)
                for item in event_refs_raw
                if isinstance(item, str) and normalize_api_key(item)
            ]
        )
        expected_refs = event_refs if event_refs else list(focus_api_keys)
        expected_set = set(expected_refs)

        if not normalized_refs:
            mismatch_items.append(
                {
                    "case_id": case_id,
                    "event_id": event_id or "(missing_event_id)",
                    "api_refs": normalized_refs,
                    "expected_api_refs": expected_refs,
                    "reason": "empty_api_refs",
                }
            )
            continue

        if expected_set and not any(item in expected_set for item in normalized_refs):
            mismatch_items.append(
                {
                    "case_id": case_id,
                    "event_id": event_id or "(missing_event_id)",
                    "api_refs": normalized_refs,
                    "expected_api_refs": expected_refs,
                    "reason": "api_refs_not_traceable_to_event_or_stage0_focus",
                }
            )

    return {
        "status": "ok" if not mismatch_items else "mismatch",
        "reason": "stage4_case_traceability_ok" if not mismatch_items else "stage4_case_traceability_failed",
        "focus_api_keys": focus_api_keys,
        "business_event_ids": sorted(event_lookup.keys()),
        "checked_case_count": len(generated_cases),
        "mismatch_count": len(mismatch_items),
        "mismatch_items": mismatch_items,
    }


def apply_stage4_semantic_selection(
    paths: Paths,
    stage4_artifact: dict[str, Any],
    selected_case_ids: list[str],
    reason: str,
    reviewer: str,
) -> dict[str, Any]:
    candidate_cases_raw = stage4_artifact.get("candidate_cases", [])
    candidate_cases = [dict(item) for item in candidate_cases_raw if isinstance(item, dict)]
    if not candidate_cases:
        raise WorkflowError("stage4 artifact 缺少 candidate_cases，无法执行语义筛选")

    case_id_to_case: dict[str, dict[str, Any]] = {}
    candidate_ids: list[str] = []
    for case in candidate_cases:
        case_id = str(case.get("case_id", "")).strip()
        if not case_id or case_id in case_id_to_case:
            continue
        case_id_to_case[case_id] = case
        candidate_ids.append(case_id)

    normalized_selected = normalize_case_id_tokens(selected_case_ids)
    if not normalized_selected:
        normalized_selected = list(candidate_ids)

    missing_case_ids = [case_id for case_id in normalized_selected if case_id not in case_id_to_case]
    if missing_case_ids:
        preview = missing_case_ids[:8]
        tail = "..." if len(missing_case_ids) > 8 else ""
        raise WorkflowError(f"存在未知 case_id: {preview}{tail}")

    selected_set = set(normalized_selected)
    ordered_selected_ids = [case_id for case_id in candidate_ids if case_id in selected_set]
    if not ordered_selected_ids:
        raise WorkflowError("语义筛选后无可执行用例，请至少保留 1 条 case")

    generated_cases = [dict(case_id_to_case[case_id]) for case_id in ordered_selected_ids]
    assign_case_test_names(generated_cases)

    business_events_raw = stage4_artifact.get("business_events", [])
    business_events = [dict(item) for item in business_events_raw if isinstance(item, dict)]
    required_codes_raw = stage4_artifact.get("required_case_codes", [])
    required_codes = dedupe_keep_order([str(item).strip() for item in required_codes_raw if str(item).strip()])

    _write_case_descriptions(paths, generated_cases)
    _write_business_event_notes(paths, business_events)

    coverage_by_event = build_stage4_coverage(business_events, required_codes, generated_cases)
    stage0_artifact = read_json(paths.stage_artifact(0), {})
    traceability_check = evaluate_stage4_case_traceability(
        stage0_artifact if isinstance(stage0_artifact, dict) else {},
        business_events,
        generated_cases,
    )
    if traceability_check.get("status") != "ok":
        mismatch_items = traceability_check.get("mismatch_items", [])
        preview_items: list[str] = []
        if isinstance(mismatch_items, list):
            for item in mismatch_items[:5]:
                if not isinstance(item, dict):
                    continue
                case_id = str(item.get("case_id", "")).strip() or "unknown_case"
                refs = item.get("api_refs", [])
                refs_text = ", ".join(str(ref) for ref in refs) if isinstance(refs, list) and refs else "(empty)"
                preview_items.append(f"{case_id}=>{refs_text}")
        preview = "; ".join(preview_items)
        if isinstance(mismatch_items, list) and len(mismatch_items) > 5:
            preview = f"{preview}; ..."
        raise WorkflowError(
            "Stage4 可追溯性校验失败：generated_cases 的 api_refs 必须可追溯到对应业务事件（event.api_refs）"
            "或 Stage0 本轮 focus API。"
            f" mismatch_preview={preview}"
        )
    rejected_case_ids = [case_id for case_id in candidate_ids if case_id not in selected_set]
    test_generation = analyze_stage4_test_generation(paths, generated_cases)

    stage4_artifact["generated_cases"] = generated_cases
    stage4_artifact["generated_test_files"] = [rel_path(paths, generated_test_file_path(paths))]
    stage4_artifact["business_event_files"] = [rel_path(paths, generated_event_notes_path(paths))]
    stage4_artifact["coverage_by_event"] = coverage_by_event
    stage4_artifact["traceability_check"] = traceability_check
    stage4_artifact["test_generation"] = test_generation
    stage4_artifact["semantic_review"] = {
        "status": "approved",
        "reviewer": reviewer,
        "reason": reason or "session semantic review",
        "candidate_count": len(candidate_ids),
        "selected_count": len(ordered_selected_ids),
        "rejected_count": len(rejected_case_ids),
        "selected_case_ids": ordered_selected_ids,
        "rejected_case_ids": rejected_case_ids,
        "updated_at": now_iso(),
    }
    stage4_artifact["selection_updated_at"] = now_iso()
    write_json(paths.stage_artifact(4), stage4_artifact)

    append_event(
        paths,
        "stage4_semantic_review_applied",
        {
            "selected_count": len(ordered_selected_ids),
            "candidate_count": len(candidate_ids),
            "reviewer": reviewer,
        },
    )

    return {
        "selected_count": len(ordered_selected_ids),
        "candidate_count": len(candidate_ids),
        "rejected_count": len(rejected_case_ids),
        "selected_case_ids": ordered_selected_ids,
        "rejected_case_ids": rejected_case_ids,
        "coverage_by_event": coverage_by_event,
        "traceability_check": traceability_check,
        "test_generation": test_generation,
    }


def _write_business_event_notes(paths: Paths, events: list[dict[str, Any]]) -> None:
    lines = ["# Business Events (中文)", ""]
    if not events:
        lines.append("- 本轮未生成业务事件。")
    for event in events:
        event_id = str(event.get("event_id", "EVT-UNKNOWN"))
        title = str(event.get("title", "未命名业务事件"))
        description = str(event.get("description", "无补充说明"))
        entity = normalize_entity_name(str(event.get("entity", "")))
        codes = normalize_business_code_list(event.get("expected_business_codes", []))
        refs = event.get("api_refs", [])
        api_refs = refs if isinstance(refs, list) else []
        api_text = ", ".join(str(item) for item in api_refs) if api_refs else "(未声明)"
        lines.append(f"## {event_id} {title}")
        lines.append("")
        if entity:
            lines.append(f"- 实体：{entity}")
        lines.append(f"- 描述：{description}")
        if codes:
            lines.append(f"- 业务码：{', '.join(codes)}")
        lines.append(f"- 关联 API：{api_text}")
        lines.append("")

    notes_file = generated_event_notes_path(paths)
    write_text(notes_file, "\n".join(lines).rstrip() + "\n")


def parse_generated_test_status(output: str) -> tuple[set[str], set[str], set[str], int]:
    observed: set[str] = set()
    failed: set[str] = set()
    skipped: set[str] = set()
    pending_test: str | None = None
    for line in output.splitlines():
        line = line.rstrip()

        m_pending = re.match(r"^\s*(test_[a-zA-Z0-9_]+)\s+\([^)]+\)\s*$", line)
        if m_pending:
            pending_test = m_pending.group(1).strip()
            continue

        if pending_test:
            m_follow = re.match(r"^\s*.*\.\.\.\s+(.+?)\s*$", line)
            if m_follow:
                status = m_follow.group(1).strip().lower()
                observed.add(pending_test)
                if status.startswith("fail") or status.startswith("error"):
                    failed.add(pending_test)
                elif status.startswith("skip"):
                    skipped.add(pending_test)
                pending_test = None
                continue

        m = re.match(r"^\s*(test_[a-zA-Z0-9_]+)\s+\([^)]+\)\s+\.\.\.\s+(.+?)\s*$", line)
        if not m:
            continue
        test_name = m.group(1).strip()
        status = m.group(2).strip().lower()
        observed.add(test_name)
        if status.startswith("fail") or status.startswith("error"):
            failed.add(test_name)
        elif status.startswith("skip"):
            skipped.add(test_name)

    ran_count = len(observed)
    if ran_count == 0:
        m = re.search(r"Ran\s+(\d+)\s+tests?", output)
        if m:
            ran_count = int(m.group(1))
    return observed, failed, skipped, ran_count


def generated_test_label(paths: Paths) -> str:
    test_file = generated_test_file_path(paths)
    try:
        rel = test_file.resolve().relative_to(paths.root.resolve()).as_posix()
        return rel.removesuffix(".py").replace("/", ".")
    except ValueError:
        return test_file.parent.as_posix()


def resolve_python_from_test_command(paths: Paths, test_command: list[str]) -> str:
    if test_command:
        first = str(test_command[0]).strip()
        if first and "python" in Path(first).name.lower():
            candidate = Path(first)
            if candidate.is_absolute():
                if candidate.exists():
                    return candidate.as_posix()
            elif "/" in first or "\\" in first:
                resolved = (paths.root / candidate).resolve()
                if resolved.exists():
                    return resolved.as_posix()
            else:
                return first
    venv_python = paths.root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return venv_python.as_posix()
    return sys.executable


def normalize_command(paths: Paths, raw_command: list[str]) -> list[str]:
    command = [str(item).strip() for item in raw_command if str(item).strip()]
    if not command:
        return []

    first = command[0]
    candidate = Path(first)
    if "python" in candidate.name.lower():
        if candidate.is_absolute():
            if candidate.exists():
                command[0] = candidate.as_posix()
            else:
                command[0] = resolve_python_from_test_command(paths, [])
        elif "/" in first or "\\" in first:
            resolved = (paths.root / candidate).resolve()
            command[0] = resolved.as_posix() if resolved.exists() else resolve_python_from_test_command(paths, [])
    return command


def render_command_template(paths: Paths, raw_command: list[str], test_command: list[str]) -> list[str]:
    project_layout = resolve_project_layout(paths)
    project_internal_target = (
        project_layout["entity_code_roots"][0]
        if project_layout["entity_code_roots"]
        else "."
    )
    replacements = {
        "{python}": resolve_python_from_test_command(paths, test_command),
        "{generated_test_label}": generated_test_label(paths),
        "{project_internal_test_target}": project_internal_target,
    }

    rendered: list[str] = []
    for item in raw_command:
        token = str(item)
        for source, target in replacements.items():
            token = token.replace(source, target)
        token = token.strip()
        if token:
            rendered.append(token)
    return normalize_command(paths, rendered)


def build_generated_suite_command(paths: Paths, spec: dict[str, Any], test_command: list[str]) -> list[str]:
    raw_command = spec.get("generated_test_command")
    if isinstance(raw_command, list) and raw_command:
        command = render_command_template(paths, raw_command, test_command)
        if command:
            return command
    return [
        resolve_python_from_test_command(paths, test_command),
        "manage.py",
        "test",
        generated_test_label(paths),
        "-v",
        "2",
    ]


def build_project_internal_suite_command(paths: Paths, spec: dict[str, Any], test_command: list[str]) -> list[str]:
    raw_command = spec.get("project_internal_test_command")
    if isinstance(raw_command, list) and raw_command:
        command = render_command_template(paths, raw_command, test_command)
        if command:
            return command

    project_layout = resolve_project_layout(paths)
    target = project_layout["entity_code_roots"][0] if project_layout["entity_code_roots"] else "."
    return [
        resolve_python_from_test_command(paths, test_command),
        "manage.py",
        "test",
        target,
        "-v",
        "2",
    ]


def _normalize_command_tokens(command: list[str]) -> list[str]:
    return [str(item).strip().replace("\\", "/") for item in command if str(item).strip()]


def _command_basename(token: str) -> str:
    return Path(str(token).strip()).name.lower()


def _find_django_test_subcommand_index(command: list[str]) -> int | None:
    tokens = _normalize_command_tokens(command)
    if len(tokens) >= 2 and _command_basename(tokens[0]) == "django-admin" and tokens[1] == "test":
        return 1
    if len(tokens) >= 2 and _command_basename(tokens[0]) == "manage.py" and tokens[1] == "test":
        return 1
    if len(tokens) >= 3 and _command_basename(tokens[1]) == "manage.py" and tokens[2] == "test":
        return 2
    if (
        len(tokens) >= 4
        and _command_basename(tokens[0]).startswith("python")
        and tokens[1] == "-m"
        and tokens[2] in {"django", "django.core.management"}
        and tokens[3] == "test"
    ):
        return 3
    return None


def _normalize_test_label(token: str) -> str:
    return str(token).strip().replace("\\", "/").strip("/")


def _full_project_test_scope_labels(paths: Paths) -> list[str]:
    layout = resolve_project_layout(paths)
    roots = [_normalize_test_label(item) for item in layout.get("project_test_roots", []) if _normalize_test_label(item)]
    return dedupe_keep_order(roots) or ["."]


def _extract_django_test_scope_labels(paths: Paths, command: list[str]) -> list[str] | None:
    test_index = _find_django_test_subcommand_index(command)
    if test_index is None:
        return None

    bool_flags = {
        "--debug-mode",
        "--debug-sql",
        "--failfast",
        "--keepdb",
        "--no-faulthandler",
        "--pdb",
        "--reverse",
        "--timing",
    }
    value_flags = {
        "-k",
        "-p",
        "-t",
        "-v",
        "--buffer",
        "--durations",
        "--exclude-tag",
        "--parallel",
        "--pattern",
        "--pythonpath",
        "--settings",
        "--shuffle",
        "--tag",
        "--testrunner",
        "--top-level-directory",
        "--verbosity",
    }

    tokens = _normalize_command_tokens(command)
    labels: list[str] = []
    skip_next = False
    for token in tokens[test_index + 1 :]:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-"):
            if "=" in token or token in bool_flags:
                continue
            skip_next = token in value_flags or (token.startswith("-") and not token.startswith("---"))
            continue
        label = _normalize_test_label(token)
        if label:
            labels.append(label)

    normalized_labels = dedupe_keep_order(labels)
    if not normalized_labels or normalized_labels == ["."]:
        return _full_project_test_scope_labels(paths)
    return normalized_labels


def detect_duplicate_stage5_regression_run(
    paths: Paths,
    project_internal_command: list[str],
    regression_command: list[str],
) -> dict[str, Any]:
    project_tokens = _normalize_command_tokens(project_internal_command)
    regression_tokens = _normalize_command_tokens(regression_command)
    if not project_tokens or not regression_tokens:
        return {"skip": False, "reason": "empty_command"}

    if project_tokens == regression_tokens:
        return {
            "skip": True,
            "reason": "exact_command_match",
            "project_internal_scope": [],
            "regression_scope": [],
        }

    project_scope = _extract_django_test_scope_labels(paths, project_internal_command)
    regression_scope = _extract_django_test_scope_labels(paths, regression_command)
    if project_scope is None or regression_scope is None:
        return {
            "skip": False,
            "reason": "non_django_test_command",
            "project_internal_scope": project_scope or [],
            "regression_scope": regression_scope or [],
        }

    if set(project_scope) == set(regression_scope):
        return {
            "skip": True,
            "reason": "same_django_test_scope",
            "project_internal_scope": project_scope,
            "regression_scope": regression_scope,
        }

    return {
        "skip": False,
        "reason": "different_django_test_scope",
        "project_internal_scope": project_scope,
        "regression_scope": regression_scope,
    }


def run_generated_case_suite(
    paths: Paths,
    generated_cases: list[dict[str, Any]],
    command: list[str],
) -> dict[str, Any]:
    rows = assign_case_test_names(generated_cases)
    expected_names = [row["test_name"] for row in rows]
    case_by_test = {row["test_name"]: row["case_id"] for row in rows}

    if not expected_names:
        return {
            "command": [],
            "return_code": 0,
            "tests_run": 0,
            "expected_count": 0,
            "expected_tests": [],
            "observed_tests": [],
            "failed_tests": [],
            "skipped_tests": [],
            "missing_tests": [],
            "failed_cases": [],
            "stdout_tail": "",
            "stderr_tail": "",
            "mismatch": False,
            "parse_source": "empty_cases",
        }

    try:
        proc = subprocess.run(
            command,
            cwd=str(paths.root),
            capture_output=True,
            text=True,
            check=False,
        )
        return_code = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except OSError as exc:
        return {
            "command": command,
            "return_code": 1,
            "tests_run": 0,
            "expected_count": len(expected_names),
            "expected_tests": expected_names,
            "observed_tests": [],
            "failed_tests": [],
            "skipped_tests": [],
            "missing_tests": expected_names,
            "failed_cases": [row["case_id"] for row in rows],
            "stdout_tail": "",
            "stderr_tail": str(exc),
            "mismatch": True,
            "parse_source": "oserror",
        }

    combined = "\n".join([stdout, stderr])
    observed, failed, skipped, _ran_count = parse_generated_test_status(combined)
    expected_set = set(expected_names)

    # 历史轮次会与当前轮次共用 generated 测试文件；Stage5 只校验“本轮期望用例”是否完整执行。
    observed_expected = {name for name in observed if name in expected_set}
    failed_expected = {name for name in failed if name in expected_set}
    skipped_expected = {name for name in skipped if name in expected_set}

    missing = [name for name in expected_names if name not in observed_expected]
    failed_case_ids = [
        case_by_test[name] for name in expected_names if name in failed_expected or name in missing or name in skipped_expected
    ]
    mismatch = bool(missing)

    return {
        "command": command,
        "return_code": return_code,
        "tests_run": len(observed_expected),
        "expected_count": len(expected_names),
        "expected_tests": expected_names,
        "observed_tests": sorted(observed_expected),
        "failed_tests": sorted(failed_expected),
        "skipped_tests": sorted(skipped_expected),
        "missing_tests": missing,
        "failed_cases": failed_case_ids,
        "stdout_tail": "\n".join(stdout.splitlines()[-80:]),
        "stderr_tail": "\n".join(stderr.splitlines()[-80:]),
        "mismatch": mismatch,
        "parse_source": "verbose_status" if observed else "summary_only",
    }


def run_project_internal_suite(paths: Paths, command: list[str]) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            cwd=str(paths.root),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return {
            "command": command,
            "return_code": 1,
            "tests_run": 0,
            "stdout_tail": "",
            "stderr_tail": str(exc),
        }

    combined = "\n".join([proc.stdout, proc.stderr])
    tests_run = 0
    match = re.search(r"Ran\s+(\d+)\s+tests?", combined)
    if match:
        tests_run = int(match.group(1))

    return {
        "command": command,
        "return_code": int(proc.returncode),
        "tests_run": tests_run,
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-80:]),
        "stderr_tail": "\n".join(proc.stderr.splitlines()[-80:]),
    }


def collect_project_test_function_blocks(paths: Paths) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    project_layout = resolve_project_layout(paths)
    seen_files: set[str] = set()
    for root_name in project_layout["project_test_roots"]:
        root_dir = paths.root / root_name
        if not root_dir.exists() or not root_dir.is_dir():
            continue
        for pattern in project_layout["project_test_file_globs"]:
            for test_file in sorted(root_dir.rglob(pattern)):
                if not test_file.is_file():
                    continue
                rel = rel_path(paths, test_file)
                if rel in seen_files:
                    continue
                seen_files.add(rel)
                content = _safe_read_text(test_file)
                blocks = extract_test_function_blocks_from_text(content)
                for test_name, block in blocks.items():
                    rows.append(
                        {
                            "test_file": rel,
                            "test_name": test_name,
                            "test_id": f"{rel}::{test_name}",
                            "block": block,
                        }
                    )
    return rows


def evaluate_project_test_sink(paths: Paths, generated_cases: list[dict[str, Any]]) -> dict[str, Any]:
    rows = assign_case_test_names(generated_cases)
    selected_case_ids = [str(row["case_id"]) for row in rows]
    test_blocks = collect_project_test_function_blocks(paths)
    mapping: list[dict[str, Any]] = []
    matched_case_ids: list[str] = []
    unmatched_case_ids: list[str] = []

    for row in rows:
        case = row.get("case", {})
        if not isinstance(case, dict):
            continue
        case_id = str(row.get("case_id", "")).strip()
        matched_tests: list[dict[str, str]] = []
        for item in test_blocks:
            block = str(item.get("block", ""))
            issues = _semantic_check_for_test_block(block, case)
            if issues:
                continue
            matched_tests.append(
                {
                    "test_file": str(item.get("test_file", "")),
                    "test_name": str(item.get("test_name", "")),
                }
            )

        mapping.append(
            {
                "case_id": case_id,
                "api_refs": case.get("api_refs", []),
                "expected_business_code": case.get("expected_business_code", ""),
                "matched_tests": matched_tests,
            }
        )
        if matched_tests:
            matched_case_ids.append(case_id)
        else:
            unmatched_case_ids.append(case_id)

    return {
        "status": "ready" if not unmatched_case_ids else "blocked",
        "reason": "all_selected_cases_sunk_to_project_tests" if not unmatched_case_ids else "missing_project_test_sinking",
        "selected_case_ids": selected_case_ids,
        "matched_case_ids": matched_case_ids,
        "unmatched_case_ids": unmatched_case_ids,
        "project_test_file_count": len({str(item.get("test_file", "")) for item in test_blocks if str(item.get("test_file", ""))}),
        "project_test_function_count": len(test_blocks),
        "mapping": mapping,
        "checked_at": now_iso(),
    }

def _normalize_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        if isinstance(item, str):
            token = item.strip()
            if token:
                rows.append(token)
    return rows


def _stage4_event_lookup(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id", "")).strip()
        if event_id and event_id not in lookup:
            lookup[event_id] = event
    return lookup


def _normalize_stage3_session_event_candidate(
    raw: dict[str, Any],
    idx: int,
    running_iteration: int,
    focus_lookup: dict[str, dict[str, Any]],
    required_case_codes: list[str],
) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(raw, dict):
        return None, "not_object"

    api_refs = dedupe_keep_order(
        [
            normalize_api_key(item)
            for item in _normalize_str_list(raw.get("api_refs"))
            if normalize_api_key(item)
        ]
    )
    if not api_refs:
        return None, "missing_api_refs"

    normalized_refs = [api_ref for api_ref in api_refs if api_ref in focus_lookup]
    if not normalized_refs:
        return None, "api_refs_not_traceable_to_focus_api"

    expected_codes = normalize_business_code_list(
        raw.get("expected_business_codes", raw.get("business_codes", [])),
        allowed_codes=required_case_codes,
    )
    if not expected_codes:
        return None, "missing_expected_business_codes"

    event_id = str(raw.get("event_id", "")).strip() or f"EVT-SESSION-I{running_iteration:03d}-{idx:03d}"
    title = str(raw.get("title", "")).strip() or f"Session 业务事件 {idx}"
    description = str(raw.get("description", raw.get("event_description_zh", ""))).strip()
    reasoning = str(raw.get("reasoning_zh", raw.get("reason", ""))).strip()
    entity = normalize_entity_name(str(raw.get("entity", "")))
    if not entity:
        ref_entities = dedupe_keep_order(
            [
                normalize_entity_name(str(focus_lookup.get(api_ref, {}).get("entity", "")))
                for api_ref in normalized_refs
                if normalize_entity_name(str(focus_lookup.get(api_ref, {}).get("entity", "")))
            ]
        )
        if len(ref_entities) == 1:
            entity = ref_entities[0]

    return {
        "event_id": event_id,
        "title": title,
        "api_refs": normalized_refs,
        "description": description,
        "expected_business_codes": expected_codes,
        "reasoning_zh": reasoning,
        "entity": entity,
    }, ""


def load_session_event_candidates(
    paths: Paths,
    focus_api_items: list[dict[str, Any]],
    required_case_codes: list[str],
    running_iteration: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows = read_jsonl(paths.session_event_candidates)
    focus_lookup: dict[str, dict[str, Any]] = {}
    for item in focus_api_items:
        if not isinstance(item, dict):
            continue
        api_key = normalize_api_key(str(item.get("api_key", "")))
        if api_key and api_key not in focus_lookup:
            focus_lookup[api_key] = item

    normalized: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_event_ids: set[str] = set()

    for idx, item in enumerate(rows, start=1):
        candidate, warning = _normalize_stage3_session_event_candidate(
            item,
            idx,
            running_iteration,
            focus_lookup,
            required_case_codes,
        )
        if not isinstance(candidate, dict):
            warnings.append(f"line_{idx}: {warning or 'missing_required_fields'}")
            continue

        event_id = str(candidate.get("event_id", "")).strip()
        base_event_id = event_id or f"EVT-SESSION-I{running_iteration:03d}-{idx:03d}"
        seq = 1
        while event_id in seen_event_ids or not event_id:
            event_id = f"{base_event_id}-{seq:02d}"
            seq += 1
        candidate["event_id"] = event_id
        seen_event_ids.add(event_id)
        normalized.append(candidate)

    return normalized, warnings


def _normalize_session_case_candidate(
    raw: dict[str, Any],
    idx: int,
    running_iteration: int,
    event_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    event_id = str(raw.get("event_id", "")).strip()
    if not event_id:
        return None

    event_meta = event_lookup.get(event_id, {})
    if not isinstance(event_meta, dict) or not event_meta:
        return None
    api_refs = _normalize_str_list(raw.get("api_refs"))
    if not api_refs and isinstance(event_meta, dict):
        api_refs = _normalize_str_list(event_meta.get("api_refs"))
    if not api_refs:
        return None

    expected_code = str(raw.get("expected_business_code", raw.get("business_code", ""))).strip().upper().replace("-", "_")
    if not expected_code:
        return None

    case_id = str(raw.get("case_id", "")).strip()
    if not case_id:
        case_id = f"CASE-SESSION-I{running_iteration:03d}-{idx:03d}"

    title = str(raw.get("title", "")).strip() or f"{event_id or 'EVT-SESSION'} 会话用例 {expected_code}"
    event_title = str(raw.get("event_title_zh", "")).strip()
    if not event_title and isinstance(event_meta, dict):
        event_title = str(event_meta.get("title", "")).strip()

    event_description = str(raw.get("event_description_zh", "")).strip()
    if not event_description and isinstance(event_meta, dict):
        event_description = str(event_meta.get("description", "")).strip()
    entity = normalize_entity_name(str(raw.get("entity", "")))
    if not entity and isinstance(event_meta, dict):
        entity = normalize_entity_name(str(event_meta.get("entity", "")))

    preconditions = _normalize_str_list(raw.get("preconditions"))
    steps = _normalize_str_list(raw.get("steps"))
    if not preconditions or not steps:
        return None
    tags = _normalize_str_list(raw.get("tags")) or ["session", "stage4"]

    return {
        "case_id": case_id,
        "title": title,
        "event_id": event_id or "EVT-SESSION",
        "event_title_zh": event_title or "Session 业务事件",
        "event_description_zh": event_description or "由当前 Codex session 设计并录入。",
        "entity": entity,
        "api_refs": api_refs,
        "preconditions": preconditions,
        "steps": steps,
        "expected_business_code": expected_code,
        "tags": tags,
        "status": str(raw.get("status", "draft")).strip() or "draft",
        "owner": str(raw.get("owner", "codex_session")).strip() or "codex_session",
        "updated_at": now_iso(),
    }


def load_session_case_candidates(
    paths: Paths,
    business_events: list[dict[str, Any]],
    running_iteration: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows = read_jsonl(paths.session_case_candidates)
    event_lookup = _stage4_event_lookup(business_events)
    normalized: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_case_ids: set[str] = set()

    for idx, item in enumerate(rows, start=1):
        if not isinstance(item, dict):
            warnings.append(f"line_{idx}: not_object")
            continue
        case = _normalize_session_case_candidate(item, idx, running_iteration, event_lookup)
        if not isinstance(case, dict):
            warnings.append(f"line_{idx}: missing_required_fields")
            continue

        case_id = str(case.get("case_id", "")).strip()
        base_case_id = case_id or f"CASE-SESSION-I{running_iteration:03d}-{idx:03d}"
        seq = 1
        while case_id in seen_case_ids or not case_id:
            case_id = f"{base_case_id}-{seq:02d}"
            seq += 1
        case["case_id"] = case_id
        seen_case_ids.add(case_id)
        normalized.append(case)

    assign_case_test_names(normalized)
    return normalized, warnings

def stage4(paths: Paths, spec: dict[str, Any]) -> StageResult:
    stage0_artifact = read_json(paths.stage_artifact(0), {})
    stage3_artifact = read_json(paths.stage_artifact(3), {})
    required_codes = stage0_artifact.get("required_case_codes", []) if isinstance(stage0_artifact, dict) else []
    events = stage3_artifact.get("business_events", []) if isinstance(stage3_artifact, dict) else []

    if not required_codes:
        required_codes = ["SUCCESS", "INVALID_PARAMS"]
    required_codes = dedupe_keep_order([str(item) for item in required_codes if isinstance(item, str) and item.strip()])
    if not required_codes:
        required_codes = ["SUCCESS", "INVALID_PARAMS"]

    normalized_events: list[dict[str, Any]] = []
    if isinstance(events, list):
        for idx, event in enumerate(events, start=1):
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("event_id", f"EVT-{idx:03d}"))
            refs = event.get("api_refs", [])
            api_refs = [str(item) for item in refs if isinstance(item, str)] if isinstance(refs, list) else []
            title = str(event.get("title", f"业务事件 {event_id}"))
            description = str(event.get("description", ""))
            entity = normalize_entity_name(str(event.get("entity", "")))
            event_codes = event.get("expected_business_codes", [])
            if not isinstance(event_codes, list):
                event_codes = []
            expected_codes = dedupe_keep_order([str(item).strip() for item in event_codes if str(item).strip()])
            normalized_events.append(
                {
                    "event_id": event_id,
                    "api_refs": api_refs,
                    "title": title,
                    "description": description,
                    "entity": entity,
                    "expected_business_codes": expected_codes,
                }
            )

    state = read_json(paths.state, DEFAULT_STATE)
    running_iteration = int(state.get("running_iteration", 1)) if isinstance(state, dict) else 1
    case_design_mode = "session"
    candidate_cases, session_case_warnings = load_session_case_candidates(paths, normalized_events, running_iteration)

    generated_cases = clone_case_rows(candidate_cases)
    _write_case_descriptions(paths, generated_cases)
    _write_business_event_notes(paths, normalized_events)

    coverage_by_event = build_stage4_coverage(normalized_events, required_codes, generated_cases)
    traceability_check = evaluate_stage4_case_traceability(
        stage0_artifact if isinstance(stage0_artifact, dict) else {},
        normalized_events,
        generated_cases,
    )
    test_generation = analyze_stage4_test_generation(paths, generated_cases)

    artifact = {
        "stage": 4,
        "generated_at": now_iso(),
        "business_events": normalized_events,
        "required_case_codes": required_codes,
        "business_code_fields": stage4_business_code_fields(spec),
        "strict_business_code": stage4_business_code_strict(spec),
        "candidate_cases": candidate_cases,
        "generated_cases": generated_cases,
        "generated_test_files": [rel_path(paths, generated_test_file_path(paths))],
        "business_event_files": [rel_path(paths, generated_event_notes_path(paths))],
        "session_case_candidate_file": rel_path(paths, paths.session_case_candidates),
        "case_design_mode": case_design_mode,
        "session_case_warnings": session_case_warnings,
        "coverage_by_event": coverage_by_event,
        "traceability_check": traceability_check,
        "test_generation": test_generation,
        "semantic_review": {
            "status": "pending",
            "reviewer": "",
            "reason": (
                "等待当前 Codex session 基于 session_case_candidates.jsonl 审阅测试资产"
                if candidate_cases
                else "session_case_candidates.jsonl 为空或无有效记录；需由当前 Codex session 先补齐 case 产物"
            ),
            "candidate_count": len(candidate_cases),
            "selected_count": len(generated_cases),
            "rejected_count": 0,
            "selected_case_ids": [str(item.get("case_id", "")) for item in generated_cases if isinstance(item, dict)],
            "rejected_case_ids": [],
            "updated_at": now_iso(),
        },
        "session_codegen_note": (
            "测试代码需由当前 Codex session 生成并写入 generated_test_files。"
            "Runner 不再补齐或生成默认 case。"
            "断言必须覆盖 business_code 与 business_detail_code。"
            "Runner 仅校验命名覆盖、API 路径引用与业务码断言。"
        ),
    }

    return StageResult(stage=4, artifact=artifact, next_stage=5, gate=True)


def stage5(paths: Paths, spec: dict[str, Any], state: dict[str, Any]) -> StageResult:
    test_command = spec.get("test_command", [".venv/bin/python", "manage.py", "test"])
    if not isinstance(test_command, list) or not test_command:
        raise WorkflowError("workflow_spec.test_command 配置非法")
    test_command = normalize_command(paths, test_command)
    if not test_command:
        raise WorkflowError("workflow_spec.test_command 配置非法")

    stage4_artifact = read_json(paths.stage_artifact(4), {})
    generated_cases_raw = stage4_artifact.get("generated_cases", []) if isinstance(stage4_artifact, dict) else []
    generated_cases = [item for item in generated_cases_raw if isinstance(item, dict)]
    generated_suite_command = build_generated_suite_command(paths, spec, test_command)
    project_internal_suite_command = build_project_internal_suite_command(paths, spec, test_command)
    generated_suite = run_generated_case_suite(paths, generated_cases, generated_suite_command)
    project_internal_suite = run_project_internal_suite(paths, project_internal_suite_command)
    project_test_sink = evaluate_project_test_sink(paths, generated_cases)
    duplicate_regression = detect_duplicate_stage5_regression_run(paths, project_internal_suite_command, test_command)
    regression_skipped = bool(duplicate_regression.get("skip", False))

    if regression_skipped:
        regression_return_code = 0
        stdout_tail = ""
        stderr_tail = (
            "skipped_duplicate_regression_run:"
            f"{duplicate_regression.get('reason', 'same_scope')};"
            " reused project_internal_suite result"
        )
    else:
        try:
            proc = subprocess.run(
                test_command,
                cwd=str(paths.root),
                capture_output=True,
                text=True,
                check=False,
            )
            regression_return_code = proc.returncode
            stdout_tail = "\n".join(proc.stdout.splitlines()[-80:])
            stderr_tail = "\n".join(proc.stderr.splitlines()[-80:])
        except OSError as exc:
            regression_return_code = 1
            stdout_tail = ""
            stderr_tail = str(exc)

    failed_cases = [item for item in generated_suite.get("failed_cases", []) if isinstance(item, str)]
    generated_suite_failed = bool(failed_cases) or bool(generated_suite.get("mismatch", False))
    generated_suite_error = int(generated_suite.get("return_code", 1)) != 0
    project_internal_suite_failed = int(project_internal_suite.get("return_code", 1)) != 0
    project_sink_failed = str(project_test_sink.get("status", "")).strip().lower() != "ready"
    return_code = (
        0
        if (
            regression_return_code == 0
            and not generated_suite_failed
            and not generated_suite_error
            and not project_internal_suite_failed
            and not project_sink_failed
        )
        else 1
    )
    skipped_tests = [item for item in generated_suite.get("skipped_tests", []) if isinstance(item, str)]
    missing_tests = [item for item in generated_suite.get("missing_tests", []) if isinstance(item, str)]
    unmatched_case_ids = [item for item in project_test_sink.get("unmatched_case_ids", []) if isinstance(item, str)]

    failure_distribution = {}
    for case_id in failed_cases:
        failure_distribution[case_id] = {
            "error_type": "generated_case_test_failed",
            "fail_count": 1,
        }
    if skipped_tests:
        failure_distribution["__generated_suite_skipped__"] = {
            "error_type": "generated_case_skipped",
            "fail_count": len(skipped_tests),
            "tests": skipped_tests,
        }
    if bool(generated_suite.get("mismatch", False)):
        failure_distribution["__stage4_stage5_consistency__"] = {
            "error_type": "generated_suite_count_mismatch",
            "fail_count": 1,
            "expected": generated_suite.get("expected_count", 0),
            "actual": generated_suite.get("tests_run", 0),
            "missing_tests": missing_tests,
        }
    if project_internal_suite_failed:
        failure_distribution["__project_internal_suite__"] = {
            "error_type": "project_internal_tests_failed",
            "fail_count": 1,
            "tests_run": project_internal_suite.get("tests_run", 0),
        }
    if project_sink_failed:
        failure_distribution["__project_test_sinking__"] = {
            "error_type": "stage4_cases_not_sunk_to_project_tests",
            "fail_count": len(unmatched_case_ids),
            "unmatched_case_ids": unmatched_case_ids,
        }
    if regression_return_code != 0:
        failure_distribution["__regression_suite__"] = {
            "error_type": "regression_command_failed",
            "fail_count": 1,
        }

    threshold = int(spec.get("stage5_failure_threshold", 2))
    consecutive = int(state.get("stage5_consecutive_failures", 0))
    if return_code == 0:
        consecutive = 0
        next_stage = 6
    else:
        consecutive += 1
        next_stage = 7 if consecutive >= threshold else 1

    state["stage5_consecutive_failures"] = consecutive

    artifact = {
        "stage": 5,
        "generated_at": now_iso(),
        "executed_cases": int(generated_suite.get("tests_run", 0)),
        "failed_cases": failed_cases,
        "project_internal_suite": project_internal_suite,
        "project_test_sink": project_test_sink,
        "regression_summary": {
            "return_code": return_code,
            "command": test_command,
            "regression_return_code": regression_return_code,
            "skipped_as_duplicate": regression_skipped,
            "duplicate_check": duplicate_regression,
            "project_internal_suite": project_internal_suite,
            "consecutive_failures": consecutive,
            "failure_threshold": threshold,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "generated_suite": generated_suite,
            "failure_distribution": failure_distribution,
        },
    }

    return StageResult(stage=5, artifact=artifact, next_stage=next_stage)


def _upsert_api_registry(paths: Paths) -> dict[str, Any]:
    api_registry = read_json(paths.api_registry, DEFAULT_API_REGISTRY)
    stage2_artifact = read_json(paths.stage_artifact(2), {})
    stage0_artifact = read_json(paths.stage_artifact(0), {})
    workflow_spec = read_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC)
    if not isinstance(workflow_spec, dict):
        workflow_spec = DEFAULT_WORKFLOW_SPEC
    ignore_patterns = configured_ignore_api_patterns(workflow_spec)
    _prune_ignored_api_registry_items(api_registry, ignore_patterns)

    scanned_apis = stage2_artifact.get("scanned_apis", []) if isinstance(stage2_artifact, dict) else []
    new_api_keys = stage2_artifact.get("new_api_keys", []) if isinstance(stage2_artifact, dict) else []
    bootstrap_mode = bool(stage2_artifact.get("bootstrap_mode", False)) if isinstance(stage2_artifact, dict) else False
    stage0_candidate_keys = stage0_candidate_api_keys_from_artifact(stage0_artifact if isinstance(stage0_artifact, dict) else {})

    observed_now, _, _ = discover_apis(paths, workflow_spec)
    observed_now_set = {
        normalize_api_key(item)
        for item in observed_now
        if isinstance(item, str) and normalize_api_key(item)
    }

    existing = {
        item.get("api_key"): item
        for item in api_registry.get("items", [])
        if isinstance(item, dict) and isinstance(item.get("api_key"), str)
    }

    now = now_iso()
    if bootstrap_mode and not api_registry.get("bootstrap_done", False):
        for api_key in scanned_apis:
            normalized = normalize_api_key(api_key)
            if not normalized:
                continue
            existing[normalized] = {
                "api_key": normalized,
                "source": "bootstrap_scan",
                "created_at": now,
                "updated_at": now,
                "enabled": True,
            }
        api_registry["bootstrap_done"] = True

    for api_key in new_api_keys:
        normalized = normalize_api_key(api_key)
        if not normalized:
            continue
        if normalized in existing:
            existing[normalized]["updated_at"] = now
        else:
            existing[normalized] = {
                "api_key": normalized,
                "source": "stage2_new",
                "created_at": now,
                "updated_at": now,
                "enabled": True,
            }

    # 兜底修复：若 Stage2 快照早于代码落地时间，可能漏记本轮候选 API。
    # 这里用“当前代码扫描结果 ∩ Stage0 候选”补齐 registry，避免下一轮重复提名同一接口。
    for api_key in stage0_candidate_keys:
        normalized = normalize_api_key(api_key)
        if not normalized or normalized not in observed_now_set:
            continue
        if normalized in existing:
            existing[normalized]["updated_at"] = now
            continue
        existing[normalized] = {
            "api_key": normalized,
            "source": "stage0_candidate_observed",
            "created_at": now,
            "updated_at": now,
            "enabled": True,
        }

    api_registry["items"] = sorted(existing.values(), key=lambda item: item["api_key"])
    write_json(paths.api_registry, api_registry)
    return api_registry


def _upsert_case_registry(paths: Paths) -> dict[str, Any]:
    case_registry = read_json(paths.case_registry, DEFAULT_CASE_REGISTRY)
    stage4_artifact = read_json(paths.stage_artifact(4), {})
    generated_cases = stage4_artifact.get("generated_cases", []) if isinstance(stage4_artifact, dict) else []

    existing = {
        (item.get("case_id"), item.get("test_name")): item
        for item in case_registry.get("items", [])
        if isinstance(item, dict)
    }

    now = now_iso()
    for row in assign_case_test_names(generated_cases if isinstance(generated_cases, list) else []):
        item = row["case"]
        case_id = row["case_id"]
        event_id = str(item.get("event_id", ""))
        api_refs = item.get("api_refs", [])
        test_name = row["test_name"]
        key = (case_id, test_name)
        existing[key] = {
            "case_id": case_id,
            "event_id": event_id,
            "api_refs": api_refs if isinstance(api_refs, list) else [],
            "test_file": rel_path(paths, generated_test_file_path(paths)),
            "test_name": test_name,
            "test_type": "api",
            "enabled": True,
            "updated_at": now,
        }

    case_registry["items"] = sorted(existing.values(), key=lambda row: (row["case_id"], row["test_name"]))
    write_json(paths.case_registry, case_registry)
    return case_registry


def _update_docs(
    paths: Paths,
    touched_entities: list[str],
    doc_sync: dict[str, Any],
    permission_changed: bool,
    permission_paths: list[str],
    changed_paths: list[str],
) -> list[str]:
    stage0_artifact = read_json(paths.stage_artifact(0), {})
    stage2_artifact = read_json(paths.stage_artifact(2), {})
    stage4_artifact = read_json(paths.stage_artifact(4), {})
    stage5_artifact = read_json(paths.stage_artifact(5), {})
    entity_contexts = {
        entity: _collect_entity_doc_context(paths, entity, changed_paths)
        for entity in touched_entities
    }

    docs_updates: list[str] = []

    summary_lines = [
        "# API Change Summary",
        "",
        f"- generated_at: {now_iso()}",
        f"- focus_api_keys: {stage0_candidate_api_keys_from_artifact(stage0_artifact if isinstance(stage0_artifact, dict) else {})}",
        f"- stage2.new_api_keys: {stage2_artifact.get('new_api_keys', [])}",
        f"- touched_entities: {touched_entities}",
    ]
    if bool(stage2_artifact.get("bootstrap_mode", False)):
        summary_lines.append("- bootstrap_mode=true，本轮按扫描结果批量初始化校验。")
    else:
        summary_lines.append("- 本轮候选按增量迭代推进，仍需人工门禁确认。")
    missing_docs = doc_sync.get("missing_docs", {})
    unsynced_entities = set(doc_sync.get("unsynced_entities", []))
    low_quality_docs = doc_sync.get("low_quality_docs", {})
    semantic_unsynced_entities = doc_sync.get("semantic_unsynced_entities", {})
    if touched_entities:
        for entity in touched_entities:
            if entity in missing_docs:
                summary_lines.append(f"- {entity} 四件套缺失：{missing_docs[entity]}")
            elif entity in unsynced_entities:
                summary_lines.append(f"- {entity} 四件套未与本轮代码变更同步（需补文档后重跑）")
            elif entity in low_quality_docs:
                summary_lines.append(f"- {entity} 四件套存在低质量占位内容（需按业务语义重写）：{low_quality_docs[entity]}")
            elif entity in semantic_unsynced_entities:
                summary_lines.append(f"- {entity} 四件套与本轮 focus API 语义不一致（缺少本轮正文增补或 API 语义引用）：{semantic_unsynced_entities[entity]}")
            else:
                ctx = entity_contexts.get(entity, {})
                event_count = len(_entity_event_rows(ctx)) if isinstance(ctx, dict) else 0
                case_count = len(_entity_case_rows(ctx)) if isinstance(ctx, dict) else 0
                api_refs = ctx.get("api_refs", []) if isinstance(ctx, dict) else []
                summary_lines.append(
                    f"- {entity} 四件套已对齐检查：api={_join_items(api_refs, limit=6)}; events={event_count}; cases={case_count}"
                )
    else:
        summary_lines.append("- 本轮未识别到实体变更")

    failed_cases = stage5_artifact.get("failed_cases", []) if isinstance(stage5_artifact, dict) else []
    if isinstance(failed_cases, list) and failed_cases:
        summary_lines.append(f"- 测试覆盖风险：存在失败用例 {failed_cases}")
    else:
        summary_lines.append("- 测试覆盖风险：无新增失败用例")

    if permission_changed:
        summary_lines.append("- 权限矩阵发生变更，详见 permission_impact.md")
    else:
        summary_lines.append("- 权限矩阵无变更")

    api_change_summary = "\n".join(summary_lines) + "\n"
    write_text(paths.scaffold / "docs" / "api_change_summary.md", api_change_summary)
    docs_updates.append("api_change_summary.md")

    api_catalog_lines = [
        "# API Catalog",
        "",
        f"- generated_at: {now_iso()}",
        f"- scanned_apis: {stage2_artifact.get('scanned_apis', [])}",
        f"- new_api_keys: {stage2_artifact.get('new_api_keys', [])}",
        "- 业务状态码以 Stage0 `required_case_codes` 为准。",
    ]
    for entity, ctx in entity_contexts.items():
        api_catalog_lines.extend(
            [
                "",
                f"## {entity}",
                f"- focus_api_keys: {_join_items(ctx.get('api_refs', []), limit=10)}",
                f"- 提名依据: {ctx.get('stage0_reason', 'N/A')}",
                f"- 业务事件: {_join_items([row['event_id'] + ' ' + row['title'] for row in _entity_event_rows(ctx)], limit=6)}",
                f"- 业务码目标: {_join_items(ctx.get('required_codes', []), limit=8)}",
            ]
        )
    write_text(paths.scaffold / "docs" / "api_catalog.md", "\n".join(api_catalog_lines).rstrip() + "\n")
    docs_updates.append("api_catalog.md")

    data_model_lines = [
        "# Data Model Delta",
        "",
        f"- generated_at: {now_iso()}",
        f"- touched_entities: {touched_entities}",
        "- 文档优先记录本轮显式业务语义；模型代码仅作为窄补充。",
    ]
    for entity, ctx in entity_contexts.items():
        data_model_lines.extend(
            [
                "",
                f"## {entity}",
                f"- db_table: {ctx.get('db_table', 'N/A')}",
                f"- 状态机: {_join_items(_entity_state_machine_lines(ctx), limit=4)}",
                f"- 业务约束: {_join_items(ctx.get('constraints', []), limit=8)}",
                f"- 字段补充: {_join_items([str(item).split(':', 1)[0] for item in _entity_field_lines(ctx, limit=8)], limit=8)}",
            ]
        )
    write_text(paths.scaffold / "docs" / "data_model_delta.md", "\n".join(data_model_lines).rstrip() + "\n")
    docs_updates.append("data_model_delta.md")

    traceability_lines = [
        "# Traceability Matrix",
        "",
        "| entity | event_id | api | test_case | expected_code | project_sink |",
        "|---|---|---|---|---|---|",
    ]
    for entity, ctx in entity_contexts.items():
        matched = set(ctx.get("execution", {}).get("matched_case_ids", [])) if isinstance(ctx.get("execution", {}), dict) else set()
        for case in _entity_case_rows(ctx):
            sink_status = "matched" if case["case_id"] in matched else "pending_sink"
            traceability_lines.append(
                f"| {entity} | {case['event_id'] or '-'} | {_join_items(case['api_refs'], limit=3)} | {case['case_id'] or '-'} | {case['expected_business_code'] or '-'} | {sink_status} |"
            )
    write_text(paths.scaffold / "docs" / "traceability_matrix.md", "\n".join(traceability_lines).rstrip() + "\n")
    docs_updates.append("traceability_matrix.md")

    test_record_lines = [
        "# Test Example Records",
        "",
        f"- generated_at: {now_iso()}",
        f"- case_count: {len(stage4_artifact.get('generated_cases', [])) if isinstance(stage4_artifact, dict) else 0}",
    ]
    for entity, ctx in entity_contexts.items():
        test_record_lines.extend(
            [
                "",
                f"## {entity}",
                f"- focus_api_keys: {_join_items(ctx.get('api_refs', []), limit=8)}",
                f"- 业务事件样例: {_join_items([row['event_id'] + ' ' + row['title'] for row in _entity_event_rows(ctx)], limit=6)}",
                f"- 测试用例样例: {_join_items([row['case_id'] for row in _entity_case_rows(ctx)], limit=8)}",
            ]
        )
        for line in _entity_execution_lines(ctx):
            test_record_lines.append(f"- {line}")
    write_text(paths.scaffold / "docs" / "test_example_records.md", "\n".join(test_record_lines).rstrip() + "\n")
    docs_updates.append("test_example_records.md")

    if permission_changed:
        permission_doc = [
            "# Permission Impact",
            "",
            f"- generated_at: {now_iso()}",
            f"- touched_entities: {touched_entities}",
            "- changed_paths:",
        ]
        permission_doc.extend(f"  - {path}" for path in permission_paths)
        write_text(paths.scaffold / "docs" / "permission_impact.md", "\n".join(permission_doc) + "\n")
        docs_updates.append("permission_impact.md")

    return docs_updates


def stage6(paths: Paths, spec: dict[str, Any]) -> StageResult:
    state = load_state(paths)
    changed_paths = git_changed_paths(paths)
    permission_changed, permission_paths = detect_permission_changes(paths, changed_paths)

    stage0_artifact = read_json(paths.stage_artifact(0), {})
    stage1_artifact = read_json(paths.stage_artifact(1), {})
    stage2_artifact = read_json(paths.stage_artifact(2), {})
    inferred_entities = infer_touched_entities(
        paths,
        stage0_artifact if isinstance(stage0_artifact, dict) else {},
        stage1_artifact if isinstance(stage1_artifact, dict) else {},
        stage2_artifact if isinstance(stage2_artifact, dict) else {},
    )
    focus_api_keys = stage0_candidate_api_keys_from_artifact(stage0_artifact if isinstance(stage0_artifact, dict) else {})
    entity_review_input = load_stage6_entity_review(paths)
    touched_entities = apply_stage6_entity_review(inferred_entities, entity_review_input)
    entity_review = build_stage6_entity_review_summary(
        paths,
        inferred_entities=inferred_entities,
        final_entities=touched_entities,
        review=entity_review_input,
        focus_api_keys=focus_api_keys,
    )
    round_started_at = str(stage0_artifact.get("stage0_entered_at", "")).strip() if isinstance(stage0_artifact, dict) else ""
    if not round_started_at:
        state_snapshot = read_json(paths.state, {})
        if isinstance(state_snapshot, dict):
            round_started_at = str(state_snapshot.get("stage0_entered_at", "")).strip()
    if not round_started_at and isinstance(stage0_artifact, dict):
        round_started_at = str(stage0_artifact.get("generated_at", "")).strip()

    doc_sync_autofix = {
        "attempted": False,
        "attempted_entities": [],
        "created_docs": [],
        "updated_docs": [],
        "skipped_docs": [],
    }

    doc_sync_autofix = auto_sync_entity_docs(paths, touched_entities, changed_paths)
    changed_paths = git_changed_paths(paths)
    permission_changed, permission_paths = detect_permission_changes(paths, changed_paths)
    doc_sync = evaluate_entity_doc_sync(
        paths,
        touched_entities,
        changed_paths,
        focus_api_keys=focus_api_keys,
        round_started_at=round_started_at,
    )

    docs_updates = _update_docs(paths, touched_entities, doc_sync, permission_changed, permission_paths, changed_paths)
    commit_review = build_stage6_commit_review(
        paths,
        state,
        stage0_artifact if isinstance(stage0_artifact, dict) else {},
        touched_entities,
        changed_paths,
    )

    if not doc_sync.get("passed", False):
        api_registry = read_json(paths.api_registry, DEFAULT_API_REGISTRY)
        case_registry = read_json(paths.case_registry, DEFAULT_CASE_REGISTRY)
        artifact = {
            "stage": 6,
            "generated_at": now_iso(),
            "registry_updates": {
                "api_registry_count": len(api_registry.get("items", [])) if isinstance(api_registry, dict) else 0,
                "case_registry_count": len(case_registry.get("items", [])) if isinstance(case_registry, dict) else 0,
                "skipped": True,
            },
            "docs_updates": docs_updates,
            "entity_review": entity_review,
            "doc_sync_autofix": doc_sync_autofix,
            "commit_review": commit_review,
            "settlement_commit_message": "stage-6(blocked): business doc sync still failed after auto-sync",
            "business_doc_sync": doc_sync,
        }
        # 文档同步不通过属于 Stage6 结算阻断，必须停在 Stage6 门禁等待人工决策，
        # 不能自动滑回 Stage1 导致用户误判“流程已通过”。
        return StageResult(stage=6, artifact=artifact, next_stage=6, gate=True)

    api_registry = _upsert_api_registry(paths)
    case_registry = _upsert_case_registry(paths)

    artifact = {
        "stage": 6,
        "generated_at": now_iso(),
        "registry_updates": {
            "api_registry_count": len(api_registry.get("items", [])),
            "case_registry_count": len(case_registry.get("items", [])),
        },
        "docs_updates": docs_updates,
        "entity_review": entity_review,
        "doc_sync_autofix": doc_sync_autofix,
        "commit_review": commit_review,
        "settlement_commit_message": "stage-6(settlement): update registry and docs",
        "business_doc_sync": doc_sync,
        "permission_doc_changed": permission_changed,
    }

    return StageResult(stage=6, artifact=artifact, next_stage=0, gate=True)


def stage7(paths: Paths, spec: dict[str, Any], state: dict[str, Any]) -> StageResult:
    stage5_artifact = read_json(paths.stage_artifact(5), {})
    recalc = analyze_recalc_graph(paths)
    write_json(paths.scaffold / "artifacts" / "stage7" / "recalc_analysis.json", recalc)

    summary = {
        "current_stage": state.get("current_stage"),
        "consecutive_stage5_failures": state.get("stage5_consecutive_failures", 0),
        "stage5_failed_cases": stage5_artifact.get("failed_cases", []),
    }

    artifact = {
        "stage": 7,
        "generated_at": now_iso(),
        "intervention_summary": summary,
        "recalc_analysis": recalc,
        "recommendations": [
            {
                "action": "goto_stage",
                "next_stage": recalc.get("recommended_rerun_from_stage", 1),
                "reason": "根据重算分析返回建议阶段",
            },
            {"action": "run_stage8", "reason": "进入回退决策"},
        ],
    }

    return StageResult(stage=7, artifact=artifact, next_stage=1, gate=True)


def stage8(paths: Paths, spec: dict[str, Any], state: dict[str, Any]) -> StageResult:
    stage5_artifact = read_json(paths.stage_artifact(5), {})
    failures = int(state.get("stage5_consecutive_failures", 0))
    threshold = int(spec.get("stage5_failure_threshold", 2))

    decision = "rollback" if failures >= threshold else "continue"
    reasoning = (
        "连续失败次数达到阈值，建议回退到稳定点"
        if decision == "rollback"
        else "失败未超阈值，建议继续增量开发"
    )

    artifact = {
        "stage": 8,
        "generated_at": now_iso(),
        "decision": decision,
        "reasoning": reasoning,
        "actions": {
            "suggested_target_ref": "HEAD",
            "next_step": "可执行 stage7-rollback --apply 自动创建回退分支",
        },
    }

    return StageResult(stage=8, artifact=artifact, next_stage=0, gate=True)


def execute_stage(paths: Paths, spec: dict[str, Any], state: dict[str, Any], stage: int) -> StageResult:
    if stage == 0:
        return stage0(paths, spec)
    if stage == 1:
        return stage1(paths, spec)
    if stage == 2:
        return stage2(paths, spec, state)
    if stage == 3:
        return stage3(paths, spec)
    if stage == 4:
        return stage4(paths, spec)
    if stage == 5:
        return stage5(paths, spec, state)
    if stage == 6:
        return stage6(paths, spec)
    if stage == 7:
        return stage7(paths, spec, state)
    if stage == 8:
        return stage8(paths, spec, state)
    raise WorkflowError(f"不支持的 stage: {stage}")


# ----------------------------
# transitions & gates
# ----------------------------


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def evaluate_stage_contract(paths: Paths, stage: int, artifact: dict[str, Any]) -> dict[str, Any]:
    target_next_stage = stage + 1 if stage < 8 else 0
    contract: dict[str, Any] = {
        "stage": stage,
        "status": "ok",
        "reason": "stage_contract_satisfied",
        "blocking": False,
        "target_next_stage": target_next_stage,
        "ready_for_next_stage": True,
        "recommended_fix_stage": stage,
        "allowed_actions": [],
        "details": {},
        "checked_at": now_iso(),
    }

    if not isinstance(artifact, dict):
        contract.update(
            {
                "status": "blocked",
                "reason": f"stage{stage}_not_ready_for_stage{target_next_stage}_artifact_invalid",
                "blocking": True,
                "ready_for_next_stage": False,
                "recommended_fix_stage": stage,
                "allowed_actions": ["reject", "goto_stage"],
                "details": {"artifact_type": type(artifact).__name__},
            }
        )
        return contract

    if stage == 0:
        semantic_gap = artifact.get("semantic_gap_report", {})
        replan_blocking = bool(artifact.get("session_api_replan_blocking", False))
        candidates = artifact.get("api_candidates", [])
        has_candidates = isinstance(candidates, list) and bool(candidates)
        blocking = bool(isinstance(semantic_gap, dict) and semantic_gap.get("blocking")) or replan_blocking or (not has_candidates)
        contract["details"] = {
            "semantic_gap_blocking": bool(isinstance(semantic_gap, dict) and semantic_gap.get("blocking")),
            "replan_blocking": replan_blocking,
            "candidate_count": len(candidates) if isinstance(candidates, list) else 0,
        }
        if blocking:
            contract.update(
                {
                    "status": "blocked",
                    "reason": "stage0_not_ready_for_stage1",
                    "blocking": True,
                    "ready_for_next_stage": False,
                    "recommended_fix_stage": 0,
                    "allowed_actions": ["reject", "goto_stage"],
                }
            )
        return contract

    if stage == 1:
        tasks = artifact.get("implementation_tasks", [])
        task_count = len(tasks) if isinstance(tasks, list) else 0
        contract["details"] = {"task_count": task_count}
        if task_count <= 0:
            contract.update(
                {
                    "status": "blocked",
                    "reason": "stage1_not_ready_for_stage2",
                    "blocking": True,
                    "ready_for_next_stage": False,
                    "recommended_fix_stage": 0,
                    "allowed_actions": ["reject", "goto_stage"],
                }
            )
        return contract

    if stage == 2:
        stage0_artifact = read_json(paths.stage_artifact(0), {})
        stage0_candidates = stage0_candidate_api_keys_from_artifact(stage0_artifact if isinstance(stage0_artifact, dict) else {})
        scanned_raw = artifact.get("scanned_apis", [])
        scanned_set = {
            normalize_api_key(item)
            for item in scanned_raw
            if isinstance(item, str) and normalize_api_key(item)
        }
        bootstrap_mode = bool(artifact.get("bootstrap_mode", False))
        drift_raw = artifact.get("drift_items", [])
        unresolved_missing_online: list[str] = []
        if isinstance(drift_raw, list):
            for item in drift_raw:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type", "")).strip() != "missing_online":
                    continue
                api_key = item.get("api_key")
                if isinstance(api_key, str):
                    normalized = normalize_api_key(api_key)
                    if normalized:
                        unresolved_missing_online.append(normalized)
        unresolved_missing_online = dedupe_keep_order(unresolved_missing_online)
        missing_candidates = [api_key for api_key in stage0_candidates if api_key not in scanned_set]
        drift_autofix = artifact.get("drift_autofix", {})
        contract["details"] = {
            "bootstrap_mode": bootstrap_mode,
            "stage0_candidate_count": len(stage0_candidates),
            "missing_candidate_apis": missing_candidates,
            "unresolved_missing_online": unresolved_missing_online,
            "drift_autofix": drift_autofix if isinstance(drift_autofix, dict) else {},
        }
        if unresolved_missing_online and not bootstrap_mode:
            contract.update(
                {
                    "status": "blocked",
                    "reason": "stage2_not_ready_for_stage3_drift_unresolved",
                    "blocking": True,
                    "ready_for_next_stage": False,
                    "recommended_fix_stage": 2,
                    "allowed_actions": ["reject", "goto_stage"],
                }
            )
            return contract
        if missing_candidates and not bootstrap_mode:
            contract.update(
                {
                    "status": "blocked",
                    "reason": "stage2_not_ready_for_stage3_candidate_unimplemented",
                    "blocking": True,
                    "ready_for_next_stage": False,
                    "recommended_fix_stage": 2,
                    "allowed_actions": ["reject", "goto_stage"],
                }
            )
        return contract

    if stage == 3:
        progress = artifact.get("progress_snapshot", {})
        unimplemented_count = _safe_int(
            progress.get("unimplemented_candidate_api_count", 0) if isinstance(progress, dict) else 0,
            0,
        )
        unimplemented_apis = (
            progress.get("unimplemented_candidate_apis", [])
            if isinstance(progress, dict) and isinstance(progress.get("unimplemented_candidate_apis", []), list)
            else []
        )
        focus_api_keys = dedupe_keep_order(
            [
                normalize_api_key(item)
                for item in artifact.get("focus_api_keys", [])
                if isinstance(item, str) and normalize_api_key(item)
            ]
        )
        focus_set = set(focus_api_keys)
        business_events = artifact.get("business_events", [])
        normalized_events = [item for item in business_events if isinstance(item, dict)] if isinstance(business_events, list) else []
        event_mismatches: list[dict[str, Any]] = []
        for event in normalized_events:
            event_id = str(event.get("event_id", "")).strip() or "(missing_event_id)"
            refs = event.get("api_refs", [])
            normalized_refs = dedupe_keep_order(
                [normalize_api_key(item) for item in refs if isinstance(item, str) and normalize_api_key(item)]
            )
            codes = event.get("expected_business_codes", [])
            normalized_codes = dedupe_keep_order([str(item).strip() for item in codes if str(item).strip()]) if isinstance(codes, list) else []
            if not normalized_refs:
                event_mismatches.append(
                    {
                        "event_id": event_id,
                        "reason": "empty_api_refs",
                        "api_refs": normalized_refs,
                        "expected_focus_api_keys": focus_api_keys,
                    }
                )
                continue
            if focus_set and not any(item in focus_set for item in normalized_refs):
                event_mismatches.append(
                    {
                        "event_id": event_id,
                        "reason": "api_refs_not_traceable_to_focus_api",
                        "api_refs": normalized_refs,
                        "expected_focus_api_keys": focus_api_keys,
                    }
                )
            if not normalized_codes:
                event_mismatches.append(
                    {
                        "event_id": event_id,
                        "reason": "empty_expected_business_codes",
                        "api_refs": normalized_refs,
                        "expected_focus_api_keys": focus_api_keys,
                    }
                )

        contract["details"] = {
            "unimplemented_candidate_api_count": unimplemented_count,
            "unimplemented_candidate_apis": unimplemented_apis,
            "focus_api_keys": focus_api_keys,
            "business_event_count": len(normalized_events),
            "event_mismatch_count": len(event_mismatches),
            "event_mismatches": event_mismatches,
        }
        if unimplemented_count > 0:
            contract.update(
                {
                    "status": "blocked",
                    "reason": "stage3_not_ready_for_stage4_api_not_observable",
                    "blocking": True,
                    "ready_for_next_stage": False,
                    "recommended_fix_stage": 1,
                    "allowed_actions": ["reject", "goto_stage"],
                }
            )
            return contract
        if not normalized_events:
            contract.update(
                {
                    "status": "blocked",
                    "reason": "stage3_not_ready_for_stage4_no_business_events",
                    "blocking": True,
                    "ready_for_next_stage": False,
                    "recommended_fix_stage": 3,
                    "allowed_actions": ["reject", "goto_stage"],
                }
            )
            return contract
        if event_mismatches:
            contract.update(
                {
                    "status": "blocked",
                    "reason": "stage3_not_ready_for_stage4_event_semantics_invalid",
                    "blocking": True,
                    "ready_for_next_stage": False,
                    "recommended_fix_stage": 3,
                    "allowed_actions": ["reject", "goto_stage"],
                }
            )
        return contract

    if stage == 4:
        traceability = artifact.get("traceability_check", {})
        trace_status = str(traceability.get("status", "")).strip().lower() if isinstance(traceability, dict) else ""
        candidate_cases = artifact.get("candidate_cases", [])
        candidate_count = len(candidate_cases) if isinstance(candidate_cases, list) else 0
        contract["details"] = {
            "candidate_case_count": candidate_count,
            "traceability_status": trace_status or "unknown",
            "traceability_reason": traceability.get("reason", "") if isinstance(traceability, dict) else "",
        }
        if candidate_count <= 0:
            contract.update(
                {
                    "status": "blocked",
                    "reason": "stage4_not_ready_for_stage5_no_candidate_cases",
                    "blocking": True,
                    "ready_for_next_stage": False,
                    "recommended_fix_stage": 3,
                    "allowed_actions": ["reject", "goto_stage"],
                }
            )
            return contract
        if trace_status == "mismatch":
            contract.update(
                {
                    "status": "blocked",
                    "reason": "stage4_not_ready_for_stage5_traceability_failed",
                    "blocking": True,
                    "ready_for_next_stage": False,
                    "recommended_fix_stage": 4,
                    "allowed_actions": ["reject", "goto_stage"],
                }
            )
        return contract

    if stage == 5:
        regression = artifact.get("regression_summary", {})
        return_code = _safe_int(regression.get("return_code", 1) if isinstance(regression, dict) else 1, 1)
        project_internal = artifact.get("project_internal_suite", {})
        project_internal_return_code = _safe_int(
            project_internal.get("return_code", 1) if isinstance(project_internal, dict) else 1,
            1,
        )
        project_sink = artifact.get("project_test_sink", {})
        unmatched_case_ids = (
            [item for item in project_sink.get("unmatched_case_ids", []) if isinstance(item, str)]
            if isinstance(project_sink, dict)
            else []
        )
        contract["details"] = {
            "regression_return_code": return_code,
            "project_internal_return_code": project_internal_return_code,
            "project_test_sink_status": str(project_sink.get("status", "")) if isinstance(project_sink, dict) else "",
            "project_test_sink_unmatched_count": len(unmatched_case_ids),
            "project_test_sink_unmatched_case_ids": unmatched_case_ids,
        }
        if return_code != 0:
            reason = "stage5_not_ready_for_stage6_regression_failed"
            if unmatched_case_ids:
                reason = "stage5_not_ready_for_stage6_project_test_unsunk"
            elif project_internal_return_code != 0:
                reason = "stage5_not_ready_for_stage6_project_internal_tests_failed"
            contract.update(
                {
                    "status": "blocked",
                    "reason": reason,
                    "blocking": False,
                    "ready_for_next_stage": False,
                    "recommended_fix_stage": 1,
                    "allowed_actions": [],
                }
            )
        return contract

    if stage == 6:
        doc_sync = artifact.get("business_doc_sync", {})
        passed = bool(doc_sync.get("passed", False)) if isinstance(doc_sync, dict) else False
        auto_fix = artifact.get("doc_sync_autofix", {})
        entity_review = artifact.get("entity_review", {})
        commit_review = artifact.get("commit_review", {})
        semantic_unsynced = doc_sync.get("semantic_unsynced_entities", {}) if isinstance(doc_sync, dict) else {}
        final_entities = entity_review.get("final_entities", []) if isinstance(entity_review, dict) else []
        focus_api_keys = entity_review.get("focus_api_keys", []) if isinstance(entity_review, dict) else []
        final_entity_count = len(final_entities) if isinstance(final_entities, list) else 0
        focus_api_count = len(focus_api_keys) if isinstance(focus_api_keys, list) else 0
        contract["details"] = {
            "business_doc_sync_passed": passed,
            "entity_review_mode": (
                str(entity_review.get("inference_mode", "")).strip().lower()
                if isinstance(entity_review, dict)
                else "unknown"
            ),
            "entity_review_final_count": final_entity_count,
            "focus_api_count": focus_api_count,
            "doc_sync_autofix_attempted": bool(auto_fix.get("attempted", False)) if isinstance(auto_fix, dict) else False,
            "doc_sync_autofix_updated_docs": (
                len(auto_fix.get("updated_docs", []))
                if isinstance(auto_fix, dict) and isinstance(auto_fix.get("updated_docs"), list)
                else 0
            ),
            "commit_review_status": (
                str(commit_review.get("review_status", "")).strip().lower()
                if isinstance(commit_review, dict)
                else "unknown"
            ),
            "semantic_unsynced_entity_count": (
                len(semantic_unsynced)
                if isinstance(semantic_unsynced, dict)
                else 0
            ),
        }
        if focus_api_count > 0 and final_entity_count <= 0:
            contract.update(
                {
                    "status": "blocked",
                    "reason": "stage6_not_ready_for_stage0_no_touched_entities_for_doc_sync",
                    "blocking": True,
                    "ready_for_next_stage": False,
                    "recommended_fix_stage": 6,
                    "allowed_actions": ["reject", "goto_stage"],
                }
            )
            return contract
        if not passed:
            contract.update(
                {
                    "status": "blocked",
                    "reason": "stage6_not_ready_for_stage0_business_doc_unsynced_after_auto_sync",
                    "blocking": True,
                    "ready_for_next_stage": False,
                    "recommended_fix_stage": 6,
                    "allowed_actions": ["reject", "goto_stage"],
                }
            )
        return contract

    if stage == 7:
        recommendations = artifact.get("recommendations", [])
        recommendation_count = len(recommendations) if isinstance(recommendations, list) else 0
        contract["details"] = {"recommendation_count": recommendation_count}
        if recommendation_count <= 0:
            contract.update(
                {
                    "status": "blocked",
                    "reason": "stage7_not_ready_for_stage1_no_recommendations",
                    "blocking": True,
                    "ready_for_next_stage": False,
                    "recommended_fix_stage": 1,
                    "allowed_actions": ["reject", "goto_stage", "run_stage8"],
                }
            )
        return contract

    if stage == 8:
        decision = str(artifact.get("decision", "")).strip().lower()
        contract["details"] = {"decision": decision}
        if decision not in {"rollback", "continue"}:
            contract.update(
                {
                    "status": "blocked",
                    "reason": "stage8_not_ready_for_stage0_invalid_decision",
                    "blocking": True,
                    "ready_for_next_stage": False,
                    "recommended_fix_stage": 8,
                    "allowed_actions": ["reject", "goto_stage"],
                }
            )
        return contract

    return contract


def refresh_stage_contract(paths: Paths, stage: int) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact = read_json(paths.stage_artifact(stage), None)
    if not isinstance(artifact, dict):
        raise WorkflowError(f"stage{stage} artifact 不存在或格式错误，无法校验阶段契约")
    missing = validate_stage_artifact(stage, artifact)
    if missing:
        raise WorkflowError(f"stage{stage} artifact 缺少字段，无法进入下一阶段: {missing}")
    contract = evaluate_stage_contract(paths, stage, artifact)
    if artifact.get("stage_contract") != contract:
        artifact = copy.deepcopy(artifact)
        artifact["stage_contract"] = contract
        write_json(paths.stage_artifact(stage), artifact)
    return artifact, contract


def ensure_stage_entry_ready(paths: Paths, spec: dict[str, Any], state: dict[str, Any], stage: int) -> None:
    if stage == 0:
        return

    if stage == 7:
        artifact, _ = refresh_stage_contract(paths, 5)
        regression = artifact.get("regression_summary", {})
        return_code = _safe_int(regression.get("return_code", 1) if isinstance(regression, dict) else 1, 1)
        threshold = int(spec.get("stage5_failure_threshold", 2))
        failures = int(state.get("stage5_consecutive_failures", 0))
        if return_code == 0 or failures < threshold:
            raise WorkflowError("stage7 前置条件不满足：stage5 尚未达到失败干预阈值")
        return

    previous_stage_map = {
        1: 0,
        2: 1,
        3: 2,
        4: 3,
        5: 4,
        6: 5,
        8: 7,
    }
    previous_stage = previous_stage_map.get(stage)
    if previous_stage is None:
        return

    _, contract = refresh_stage_contract(paths, previous_stage)
    if not bool(contract.get("ready_for_next_stage", False)):
        raise WorkflowError(
            f"stage{stage} 前置 stage{previous_stage} 产物未通过复核: {contract.get('reason', 'unknown')}"
        )


def ensure_transition_ready(
    paths: Paths,
    spec: dict[str, Any],
    state: dict[str, Any],
    pending_stage: int,
    next_stage: int,
    action: str,
) -> None:
    if action not in {"approve", "run_stage8"}:
        return

    if action == "run_stage8":
        ensure_stage_entry_ready(paths, spec, state, 8)
        return

    _, contract = refresh_stage_contract(paths, pending_stage)
    if not bool(contract.get("ready_for_next_stage", False)):
        raise WorkflowError(
            f"stage{pending_stage} 当前产物未通过复核，不能推进到 stage{next_stage}: "
            f"{contract.get('reason', 'unknown')}"
        )

    if next_stage in STAGES and next_stage != 0:
        ensure_stage_entry_ready(paths, spec, state, next_stage)


def gate_for_stage(spec: dict[str, Any], stage: int) -> dict[str, Any] | None:
    policy = spec.get("gate_policy", {})
    gate = policy.get(str(stage))
    if isinstance(gate, dict):
        return gate
    return None


def set_pending(paths: Paths, stage: int, gate: dict[str, Any]) -> dict[str, Any]:
    pending = {
        "schema_version": 1,
        "decision_id": str(uuid.uuid4()),
        "stage": stage,
        "reason": gate.get("reason", "manual_gate"),
        "allowed_actions": gate.get("allowed_actions", ["approve", "reject", "goto_stage"]),
        "next_stage_on_approve": int(gate.get("next_stage_on_approve", stage + 1 if stage < 8 else 0)),
        "fallback_stage": int(gate.get("fallback_stage", stage)),
        "created_at": now_iso(),
    }
    write_json(paths.pending, pending)
    return pending


def clear_pending(paths: Paths) -> None:
    write_json(paths.pending, DEFAULT_PENDING)


def run_one_stage(
    paths: Paths, spec: dict[str, Any], state: dict[str, Any], stage: int, trigger_source: str = "manual"
) -> str:
    recover_stale_running_state(paths)

    with workflow_file_lock(paths, exclusive=True):
        state = load_state(paths)
        if state.get("status") == "running":
            raise WorkflowError("当前已有阶段在执行中，请等待其完成后再继续")
        if state.get("status") == "waiting_decision":
            raise WorkflowError("当前处于等待决策状态，请先执行 resume")

        if stage not in STAGES:
            raise WorkflowError("stage 必须在 0-8")

        if int(state.get("current_stage", 0)) != stage:
            raise WorkflowError(f"当前阶段为 {state.get('current_stage')}，不能直接执行 stage {stage}")

        ensure_runtime_config_ready(paths, spec)
        ensure_semantic_directories_for_stage(paths, stage)
        ensure_stage_entry_ready(paths, spec, state, stage)

        _mark_state_running(state, stage, trigger_source)
        save_state(paths, state)
        append_event(paths, "stage_started", {"stage": stage, "trigger_source": trigger_source})

    start = time.perf_counter()
    try:
        result = execute_stage(paths, spec, state, stage)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        with workflow_file_lock(paths, exclusive=True):
            failed_state = load_state(paths)
            failed_state["status"] = "idle"
            failed_state["current_stage"] = stage
            _clear_running_state(failed_state)
            append_event(
                paths,
                "stage_failed",
                {
                    "stage": stage,
                    "error": str(exc),
                    "failure_type": exc.__class__.__name__,
                    "duration_ms": duration_ms,
                    "trigger_source": trigger_source,
                },
            )
            save_state(paths, failed_state)
        raise

    duration_ms = int((time.perf_counter() - start) * 1000)
    stage_contract = evaluate_stage_contract(paths, stage, result.artifact)
    if isinstance(result.artifact, dict):
        result.artifact["stage_contract"] = stage_contract

    artifact_missing = validate_stage_artifact(stage, result.artifact)
    if artifact_missing:
        raise WorkflowError(f"Stage {stage} 产物缺少字段: {artifact_missing}")
    with workflow_file_lock(paths, exclusive=True):
        write_json(paths.stage_artifact(stage), result.artifact)
        write_json(
            paths.stage_input(stage),
            build_stage_input_payload(paths, stage),
        )

        event_id = append_event(
            paths,
            "stage_completed",
            {
                "stage": stage,
                "next_stage": result.next_stage,
                "gate": result.gate,
                "duration_ms": duration_ms,
                "trigger_source": trigger_source,
            },
        )

        state["last_completed_stage"] = stage
        state["last_event_id"] = event_id
        if stage == 0 and isinstance(result.artifact, dict):
            semantic_gap_report = result.artifact.get("semantic_gap_report", {})
            api_candidates = result.artifact.get("api_candidates", [])
            replan_blocking = bool(result.artifact.get("session_api_replan_blocking", False))
            semantic_blocking = isinstance(semantic_gap_report, dict) and bool(semantic_gap_report.get("blocking"))
            has_candidates = isinstance(api_candidates, list) and bool(api_candidates)
            if has_candidates and not semantic_blocking and not replan_blocking:
                state["stage0_replan_required"] = False
                state["stage0_last_replan_at"] = str(result.artifact.get("session_api_candidate_mtime", "")).strip()

        gate_policy = gate_for_stage(spec, stage) if result.gate else None
        if stage == 3 and result.gate and not gate_policy:
            gate_policy = {
                "reason": "stage3_semantic_event_review_required",
                "allowed_actions": ["approve", "reject", "goto_stage"],
                "next_stage_on_approve": 4,
                "fallback_stage": 2,
            }
        if stage == 4 and result.gate and not gate_policy:
            gate_policy = {
                "reason": "stage4_semantic_review_required",
                "allowed_actions": ["approve", "reject", "goto_stage"],
                "next_stage_on_approve": 5,
                "fallback_stage": 3,
            }
        if stage == 0 and isinstance(result.artifact, dict):
            semantic_gap = result.artifact.get("semantic_gap_report", {})
            api_candidates = result.artifact.get("api_candidates", [])
            replan_blocking = bool(result.artifact.get("session_api_replan_blocking", False))
            if isinstance(semantic_gap, dict) and semantic_gap.get("blocking"):
                gate_policy = {
                    "reason": "stage0_semantic_gap_blocking",
                    "allowed_actions": ["reject", "goto_stage"],
                    "next_stage_on_approve": 0,
                    "fallback_stage": 0,
                }
            elif replan_blocking:
                gate_policy = {
                    "reason": "stage0_session_replan_required",
                    "allowed_actions": ["reject", "goto_stage"],
                    "next_stage_on_approve": 0,
                    "fallback_stage": 0,
                }
            elif isinstance(api_candidates, list) and not api_candidates:
                gate_policy = {
                    "reason": "stage0_no_candidate_blocking",
                    "allowed_actions": ["reject", "goto_stage"],
                    "next_stage_on_approve": 0,
                    "fallback_stage": 0,
                }

        contract_blocking = bool(isinstance(stage_contract, dict) and stage_contract.get("blocking", False))
        if contract_blocking:
            recommended_fix_stage = _safe_int(
                stage_contract.get("recommended_fix_stage", stage) if isinstance(stage_contract, dict) else stage,
                stage,
            )
            allowed_actions = stage_contract.get("allowed_actions", []) if isinstance(stage_contract, dict) else []
            normalized_actions = [str(item).strip() for item in allowed_actions if str(item).strip()]
            if not normalized_actions:
                normalized_actions = ["reject", "goto_stage"]
            gate_policy = {
                "reason": str(stage_contract.get("reason", "stage_contract_blocking")),
                "allowed_actions": normalized_actions,
                "next_stage_on_approve": stage,
                "fallback_stage": recommended_fix_stage,
            }

        if gate_policy:
            pending = set_pending(paths, stage, gate_policy)
            state["status"] = "waiting_decision"
            _clear_running_state(state)
            save_state(paths, state)
            append_event(
                paths,
                "decision_required",
                {
                    "decision_id": pending["decision_id"],
                    "stage": stage,
                    "allowed_actions": pending["allowed_actions"],
                },
            )
            return "waiting_decision"

        clear_pending(paths)
        state["status"] = "idle"
        state["current_stage"] = result.next_stage
        _clear_running_state(state)
        save_state(paths, state)
        return "idle"


def run_auto(paths: Paths) -> str:
    recover_stale_running_state(paths)
    spec = read_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC)

    while True:
        with workflow_file_lock(paths, exclusive=False):
            state = load_state(paths)
        status = state.get("status")
        if status == "running":
            return "running"
        if status == "waiting_decision":
            return "waiting_decision"
        if status in {"completed", "aborted", "rolled_back"}:
            return str(status)

        current_stage = int(state.get("current_stage", 0))
        result = run_one_stage(paths, spec, state, current_stage, trigger_source="run_auto")
        with workflow_file_lock(paths, exclusive=False):
            state = load_state(paths)

        if result == "waiting_decision":
            return "waiting_decision"


def stage8_rollback(paths: Paths, target_ref: str | None, branch_name: str | None, apply: bool) -> dict[str, Any]:
    stage8_artifact = read_json(paths.stage_artifact(8), {})
    decision = stage8_artifact.get("decision") if isinstance(stage8_artifact, dict) else None
    suggested = None
    if isinstance(stage8_artifact, dict):
        actions = stage8_artifact.get("actions", {})
        if isinstance(actions, dict):
            suggested = actions.get("suggested_target_ref")

    target = target_ref or suggested or "HEAD"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = branch_name or f"rollback/stage8-{stamp}"

    rc, stdout, stderr = run_git(paths, ["rev-parse", "--verify", f"{target}^{{commit}}"])
    if rc != 0:
        raise WorkflowError(f"target_ref 无效: {target}; {stderr or stdout}")

    plan = {
        "decision": decision,
        "target_ref": target,
        "branch_name": branch,
        "commands": [f"git switch -c {branch} {target}"],
        "applied": False,
    }

    append_event(
        paths,
        "stage8_rollback_planned",
        {
            "target_ref": target,
            "branch_name": branch,
            "trigger_source": "stage8-rollback",
            "apply": apply,
        },
    )

    if not apply:
        return plan

    rc2, out2, err2 = run_git(paths, ["switch", "-c", branch, target])
    if rc2 != 0:
        raise WorkflowError(f"创建回退分支失败: {err2 or out2}")

    state = load_state(paths)
    state["status"] = "rolled_back"
    state["current_stage"] = 0
    save_state(paths, state)

    append_event(
        paths,
        "stage8_rollback_applied",
        {
            "target_ref": target,
            "branch_name": branch,
            "trigger_source": "stage8-rollback",
        },
    )

    plan["applied"] = True
    return plan


def update_semantic_snapshot(paths: Paths) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "file": paths.semantic_model.as_posix(),
        "hash": sha256_file(paths.semantic_model),
        "updated_at": now_iso(),
    }
    write_json(paths.semantic_snapshot, payload)
    append_event(paths, "semantic_snapshot_updated", {"hash": payload["hash"], "trigger_source": "semantic-snapshot"})
    return payload


def ensure_stage3_semantic_gate_ready(paths: Paths, pending: dict[str, Any], action: str) -> None:
    if int(pending.get("stage", -1)) != 3 or action != "approve":
        return

    artifact = read_json(paths.stage_artifact(3), {})
    if not isinstance(artifact, dict):
        raise WorkflowError("stage3 产物不存在，无法继续审批")

    semantic_review = artifact.get("semantic_review", {})
    status = str(semantic_review.get("status", "")).strip().lower() if isinstance(semantic_review, dict) else ""
    if status != "approved":
        raise WorkflowError(
            "Stage3 语义事件筛选尚未确认。"
            "请先执行 `stage3-semantic --keep-all --reason \"...\"` "
            "或指定 `--keep-event-id` 完成筛选，再 approve。"
        )

    business_events = artifact.get("business_events", [])
    if not isinstance(business_events, list) or not business_events:
        raise WorkflowError("Stage3 语义筛选后 business_events 为空，不能推进到 Stage4")


def ensure_stage4_semantic_gate_ready(paths: Paths, pending: dict[str, Any], action: str) -> None:
    if int(pending.get("stage", -1)) != 4 or action != "approve":
        return

    artifact = read_json(paths.stage_artifact(4), {})
    if not isinstance(artifact, dict):
        raise WorkflowError("stage4 产物不存在，无法继续审批")

    semantic_review = artifact.get("semantic_review", {})
    status = str(semantic_review.get("status", "")).strip().lower() if isinstance(semantic_review, dict) else ""
    if status != "approved":
        raise WorkflowError(
            "Stage4 语义筛选尚未确认。"
            "请先执行 `stage4-semantic --keep-all --reason \"...\"` "
            "或指定 `--keep-case-id` 完成筛选，再 approve。"
        )

    generated_cases = artifact.get("generated_cases", [])
    if not isinstance(generated_cases, list) or not generated_cases:
        raise WorkflowError("Stage4 语义筛选后 generated_cases 为空，不能推进到 Stage5")

    rows = [item for item in generated_cases if isinstance(item, dict)]
    stage0_artifact = read_json(paths.stage_artifact(0), {})
    business_events = artifact.get("business_events", [])
    normalized_events = [item for item in business_events if isinstance(item, dict)]
    traceability_check = evaluate_stage4_case_traceability(
        stage0_artifact if isinstance(stage0_artifact, dict) else {},
        normalized_events,
        rows,
    )
    if traceability_check.get("status") != "ok":
        mismatch_items = traceability_check.get("mismatch_items", [])
        preview_items: list[str] = []
        if isinstance(mismatch_items, list):
            for item in mismatch_items[:5]:
                if not isinstance(item, dict):
                    continue
                case_id = str(item.get("case_id", "")).strip() or "unknown_case"
                refs = item.get("api_refs", [])
                refs_text = ", ".join(str(ref) for ref in refs) if isinstance(refs, list) and refs else "(empty)"
                preview_items.append(f"{case_id}=>{refs_text}")
        preview = "; ".join(preview_items)
        if isinstance(mismatch_items, list) and len(mismatch_items) > 5:
            preview = f"{preview}; ..."
        raise WorkflowError(
            "Stage4 用例可追溯性校验失败，禁止 approve。"
            "请先让当前 Codex session 修正 session case 产物与 generated_test_files，确保 case.api_refs 可追溯到业务事件或 Stage0 focus API 后重试。"
            f" mismatch_preview={preview}"
        )

    test_generation = analyze_stage4_test_generation(paths, rows)
    if test_generation.get("status") != "ready":
        missing = test_generation.get("missing_tests", [])
        missing_preview = ", ".join(missing[:8]) if isinstance(missing, list) else ""
        if isinstance(missing, list) and len(missing) > 8:
            missing_preview = f"{missing_preview}, ..."
        placeholder = bool(test_generation.get("placeholder_detected", False))
        semantic_failed = test_generation.get("semantic_failed_tests", [])
        semantic_failed_preview = ", ".join(semantic_failed[:6]) if isinstance(semantic_failed, list) else ""
        if isinstance(semantic_failed, list) and len(semantic_failed) > 6:
            semantic_failed_preview = f"{semantic_failed_preview}, ..."
        test_file = str(test_generation.get("test_file", generated_test_file_path(paths)))
        details = []
        if missing_preview:
            details.append(f"missing_tests={missing_preview}")
        if placeholder:
            details.append("detected_placeholder_test=true")
        if semantic_failed_preview:
            details.append(f"semantic_failed_tests={semantic_failed_preview}")
        detail_text = f" ({'; '.join(details)})" if details else ""
        raise WorkflowError(
            "Stage4 测试代码尚未就绪。"
            f"请由当前 Codex session 先更新 `{test_file}` 并覆盖全部 expected_tests，"
            "且每个测试必须包含真实 API 请求与 business_code/business_detail_code 断言后再 approve"
            f"{detail_text}。"
        )

    artifact["test_generation"] = test_generation
    artifact["traceability_check"] = traceability_check
    write_json(paths.stage_artifact(4), artifact)

def _refresh_waiting_stage4_pending_if_resolved(paths: Paths, trigger_source: str) -> dict[str, Any]:
    state = load_state(paths)
    pending = read_json(paths.pending, DEFAULT_PENDING)
    if state.get("status") != "waiting_decision":
        return {"refreshed": False, "reason": "not_waiting_decision"}
    if int(pending.get("stage", -1)) != 4:
        return {"refreshed": False, "reason": "pending_stage_not_4"}

    artifact = read_json(paths.stage_artifact(4), {})
    if not isinstance(artifact, dict):
        return {"refreshed": False, "reason": "stage4_artifact_invalid"}

    semantic_review = artifact.get("semantic_review", {})
    semantic_status = str(semantic_review.get("status", "")).strip().lower() if isinstance(semantic_review, dict) else ""
    traceability_check = artifact.get("traceability_check", {})
    traceability_status = (
        str(traceability_check.get("status", "")).strip().lower() if isinstance(traceability_check, dict) else ""
    )
    test_generation = artifact.get("test_generation", {})
    test_generation_status = (
        str(test_generation.get("status", "")).strip().lower() if isinstance(test_generation, dict) else ""
    )

    stage_contract = evaluate_stage_contract(paths, 4, artifact)
    artifact["stage_contract"] = stage_contract
    write_json(paths.stage_artifact(4), artifact)

    if semantic_status != "approved":
        return {"refreshed": False, "reason": "semantic_not_approved"}
    if traceability_status != "ok":
        return {"refreshed": False, "reason": "traceability_not_ok"}
    if test_generation_status != "ready":
        return {"refreshed": False, "reason": "test_generation_not_ready"}
    if bool(isinstance(stage_contract, dict) and stage_contract.get("blocking", False)):
        return {"refreshed": False, "reason": "stage_contract_blocking"}

    spec = read_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC)
    gate = gate_for_stage(spec if isinstance(spec, dict) else DEFAULT_WORKFLOW_SPEC, 4)
    target_gate = gate or {
        "reason": "stage4_semantic_review_required",
        "allowed_actions": ["approve", "reject", "goto_stage"],
        "next_stage_on_approve": 5,
        "fallback_stage": 3,
    }

    current_reason = str(pending.get("reason", "")).strip()
    current_actions = sorted(str(item).strip() for item in pending.get("allowed_actions", []) if str(item).strip())
    target_reason = str(target_gate.get("reason", "")).strip()
    target_actions = sorted(str(item).strip() for item in target_gate.get("allowed_actions", []) if str(item).strip())
    if current_reason == target_reason and current_actions == target_actions:
        return {"refreshed": False, "reason": "pending_already_fresh"}

    old_decision_id = str(pending.get("decision_id", "")).strip()
    new_pending = set_pending(paths, 4, target_gate)
    append_event(
        paths,
        "stage4_pending_refreshed_after_autofix",
        {
            "trigger_source": trigger_source,
            "old_decision_id": old_decision_id,
            "new_decision_id": str(new_pending.get("decision_id", "")).strip(),
            "old_reason": current_reason,
            "new_reason": target_reason,
            "new_allowed_actions": target_actions,
        },
    )
    return {
        "refreshed": True,
        "old_decision_id": old_decision_id,
        "new_decision_id": str(new_pending.get("decision_id", "")).strip(),
        "new_reason": target_reason,
        "new_allowed_actions": target_actions,
    }


def resume(paths: Paths, auto_continue: bool) -> str:
    recover_stale_running_state(paths)
    spec = read_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC)
    started = time.perf_counter()
    with workflow_file_lock(paths, exclusive=True):
        state = load_state(paths)
        if state.get("status") != "waiting_decision":
            raise WorkflowError("当前不在等待决策状态")

        pending = read_json(paths.pending, None)
        pending_errors = validate_pending_decision(pending if isinstance(pending, dict) else None)
        if pending_errors:
            raise WorkflowError(f"pending.json 校验失败: {pending_errors}")

        decision = read_json(paths.decision, None)
        if not isinstance(decision, dict):
            raise WorkflowError("decision.json 不存在或格式错误")

        decision_errors = validate_decision_input(decision, pending)
        if decision_errors:
            raise WorkflowError(f"decision.json 校验失败: {decision_errors}")

        action = decision["action"]
        enforce_stage6_commit_decision(paths, pending, decision, action)
        ensure_stage3_semantic_gate_ready(paths, pending, action)
        ensure_stage4_semantic_gate_ready(paths, pending, action)
        next_stage = int(state.get("current_stage", 0))

        if action == "approve":
            next_stage = int(pending["next_stage_on_approve"])
        elif action == "reject":
            next_stage = int(pending["fallback_stage"])
        elif action == "goto_stage":
            next_stage = int(decision["next_stage"])
        elif action == "run_stage8":
            next_stage = 8

        ensure_transition_ready(paths, spec, state, int(pending.get("stage", -1)), next_stage, action)

        clear_pending(paths)
        write_json(paths.decision, DEFAULT_DECISION)

        state["status"] = "idle"
        state["current_stage"] = next_stage
        _clear_running_state(state)
        if next_stage == 0:
            state["stage0_replan_required"] = True
            state["stage0_entered_at"] = now_iso()
        pending_stage = int(pending.get("stage", -1))
        if should_increment_iteration_on_decision(pending_stage, next_stage, action):
            state["running_iteration"] = int(state.get("running_iteration", 1)) + 1

        duration_ms = int((time.perf_counter() - started) * 1000)
        event_id = append_event(
            paths,
            "decision_applied",
            {
                "decision_id": decision["decision_id"],
                "action": action,
                "next_stage": next_stage,
                "duration_ms": duration_ms,
                "trigger_source": "resume",
            },
        )
        state["last_event_id"] = event_id
        save_state(paths, state)

    if auto_continue:
        return run_auto(paths)
    return "idle"


# ----------------------------
# cli
# ----------------------------


def cmd_init(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=args.force, preset=args.preset)
    append_event(
        paths,
        "workflow_initialized",
        {"force": bool(args.force), "preset": args.preset, "trigger_source": "init"},
    )
    print(f"initialized scaffold at {paths.scaffold}")
    return 0


def cmd_detect_preset(args: argparse.Namespace, paths: Paths) -> int:
    print_json(detect_project_preset(paths))
    return 0


def cmd_check_config(args: argparse.Namespace, paths: Paths) -> int:
    print_json(build_config_check_payload(paths))
    return 0


def cmd_status(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    if args.json:
        recover_stale_running_state(paths)
        with workflow_file_lock(paths, exclusive=False):
            state = load_state(paths)
            pending = read_json(paths.pending, DEFAULT_PENDING)
        print_json(
            {
                "state": state,
                "pending": pending,
                "commit_controls": resolve_commit_controls(paths, state),
                "project_rules": build_project_rules_payload(paths, int(state.get("current_stage", 0))),
                "config_readiness": build_onboarding_readiness(paths),
            }
        )
        return 0
    print_json(build_status_brief_payload(paths))
    return 0


def cmd_gate(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    if args.json:
        print_json(build_gate_review_payload(paths, include_events=True))
        return 0
    print_json(build_gate_brief_payload(paths))
    return 0


def cmd_stage3_semantic(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    state = load_state(paths)
    pending = read_json(paths.pending, DEFAULT_PENDING)

    if state.get("status") != "waiting_decision" or int(pending.get("stage", -1)) != 3:
        raise WorkflowError("stage3-semantic 仅可在 Stage3 waiting_decision 门禁时执行")

    stage3_artifact = read_json(paths.stage_artifact(3), {})
    if not isinstance(stage3_artifact, dict):
        raise WorkflowError("stage3 artifact 不存在或格式错误")

    if args.sync_session_events:
        stage0_artifact = read_json(paths.stage_artifact(0), {})
        running_iteration = int(state.get("running_iteration", 1))
        focus_api_items = stage3_artifact.get("focus_api_items", [])
        normalized_focus_api_items = [item for item in focus_api_items if isinstance(item, dict)]
        required_case_codes = stage3_required_case_codes(stage0_artifact if isinstance(stage0_artifact, dict) else {})
        synced_events, warnings = load_session_event_candidates(
            paths,
            normalized_focus_api_items,
            required_case_codes,
            running_iteration,
        )
        stage3_artifact["session_event_candidate_file"] = rel_path(paths, paths.session_event_candidates)
        stage3_artifact["session_event_warnings"] = warnings
        stage3_artifact["business_event_candidates"] = synced_events
        stage3_artifact["business_events"] = clone_case_rows(synced_events)
        stage3_artifact["semantic_review"] = {
            "status": "pending",
            "reviewer": "",
            "reason": (
                "等待当前 Codex session 对同步后的业务事件进行筛选"
                if synced_events
                else "session_event_candidates.jsonl 为空或无有效记录；无法执行 Stage3 语义筛选"
            ),
            "candidate_count": len(synced_events),
            "selected_count": len(synced_events),
            "selected_event_ids": [
                str(item.get("event_id", "")).strip()
                for item in synced_events
                if isinstance(item, dict) and str(item.get("event_id", "")).strip()
            ],
            "rejected_event_ids": [],
            "updated_at": now_iso(),
        }
        write_json(paths.stage_artifact(3), stage3_artifact)

    candidate_events = stage3_artifact.get("business_event_candidates", [])
    candidate_ids: list[str] = []
    if isinstance(candidate_events, list):
        for item in candidate_events:
            if isinstance(item, dict):
                event_id = str(item.get("event_id", "")).strip()
                if event_id:
                    candidate_ids.append(event_id)
    candidate_ids = dedupe_keep_order(candidate_ids)
    if not candidate_ids:
        raise WorkflowError("stage3 artifact 缺少 business_event_candidates，无法执行语义筛选")

    raw_keep_ids = args.keep_event_id or []
    if args.keep_all:
        selected_event_ids = list(candidate_ids)
    elif raw_keep_ids:
        selected_event_ids = normalize_event_id_tokens(raw_keep_ids)
    else:
        existing_events = stage3_artifact.get("business_events", [])
        selected_event_ids = []
        if isinstance(existing_events, list):
            for item in existing_events:
                if isinstance(item, dict):
                    event_id = str(item.get("event_id", "")).strip()
                    if event_id:
                        selected_event_ids.append(event_id)
        selected_event_ids = dedupe_keep_order(selected_event_ids) or list(candidate_ids)

    if args.max_events is not None:
        if args.max_events <= 0:
            raise WorkflowError("--max-events 必须 > 0")
        selected_set = set(selected_event_ids)
        selected_event_ids = [event_id for event_id in candidate_ids if event_id in selected_set][: args.max_events]

    summary = apply_stage3_semantic_selection(
        paths,
        stage3_artifact,
        selected_event_ids=selected_event_ids,
        reason=args.reason or "session semantic event review",
        reviewer=args.reviewer or "codex_session",
    )
    payload = {
        "status": "ok",
        "stage": 3,
        "logical_stage": logical_stage_for_internal(3),
        "stage_name": logical_stage_name(3),
        "artifact_path": rel_path(paths, paths.stage_artifact(3)),
        "summary": summary,
    }
    if args.json:
        print_json(payload)
        return 0
    print_json(
        {
            "status": "ok",
            "logical_stage": logical_stage_for_internal(3),
            "stage_name": logical_stage_name(3),
            "selected_event_count": summary.get("selected_count", 0),
            "rejected_event_count": summary.get("rejected_count", 0),
        }
    )
    return 0


def cmd_stage4_semantic(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    state = load_state(paths)
    pending = read_json(paths.pending, DEFAULT_PENDING)

    if state.get("status") != "waiting_decision" or int(pending.get("stage", -1)) != 4:
        raise WorkflowError("stage4-semantic 仅可在 Stage4 waiting_decision 门禁时执行")

    stage4_artifact = read_json(paths.stage_artifact(4), {})
    if not isinstance(stage4_artifact, dict):
        raise WorkflowError("stage4 artifact 不存在或格式错误")

    if args.sync_session_cases:
        running_iteration = int(state.get("running_iteration", 1))
        business_events = stage4_artifact.get("business_events", [])
        normalized_events = [item for item in business_events if isinstance(item, dict)]
        synced_cases, warnings = load_session_case_candidates(paths, normalized_events, running_iteration)
        stage4_artifact["session_case_warnings"] = warnings
        stage4_artifact["candidate_cases"] = synced_cases
        stage4_artifact["generated_cases"] = clone_case_rows(synced_cases)
        stage4_artifact["selection_updated_at"] = now_iso()
        write_json(paths.stage_artifact(4), stage4_artifact)

    candidate_cases = stage4_artifact.get("candidate_cases", [])
    candidate_ids: list[str] = []
    if isinstance(candidate_cases, list):
        for item in candidate_cases:
            if isinstance(item, dict):
                case_id = str(item.get("case_id", "")).strip()
                if case_id:
                    candidate_ids.append(case_id)
    candidate_ids = dedupe_keep_order(candidate_ids)
    if not candidate_ids:
        raise WorkflowError("stage4 artifact 缺少 candidate_cases，无法执行语义筛选")

    raw_keep_ids = args.keep_case_id or []
    if args.keep_all:
        selected_case_ids = list(candidate_ids)
    elif raw_keep_ids:
        selected_case_ids = normalize_case_id_tokens(raw_keep_ids)
    else:
        existing_generated = stage4_artifact.get("generated_cases", [])
        selected_case_ids = []
        if isinstance(existing_generated, list):
            for item in existing_generated:
                if isinstance(item, dict):
                    case_id = str(item.get("case_id", "")).strip()
                    if case_id:
                        selected_case_ids.append(case_id)
        selected_case_ids = dedupe_keep_order(selected_case_ids) or list(candidate_ids)

    if args.max_cases is not None:
        if args.max_cases <= 0:
            raise WorkflowError("--max-cases 必须 > 0")
        selected_set = set(selected_case_ids)
        selected_case_ids = [case_id for case_id in candidate_ids if case_id in selected_set][: args.max_cases]

    summary = apply_stage4_semantic_selection(
        paths,
        stage4_artifact,
        selected_case_ids=selected_case_ids,
        reason=args.reason or "session semantic review",
        reviewer=args.reviewer or "codex_session",
    )
    pending_refresh = _refresh_waiting_stage4_pending_if_resolved(paths, trigger_source="stage4-semantic")
    payload = {
        "status": "ok",
        "stage": 4,
        "logical_stage": logical_stage_for_internal(4),
        "stage_name": logical_stage_name(4),
        "artifact_path": rel_path(paths, paths.stage_artifact(4)),
        "summary": summary,
        "pending_refresh": pending_refresh,
    }
    if args.json:
        print_json(payload)
        return 0
    print_json(
        {
            "status": "ok",
            "logical_stage": logical_stage_for_internal(4),
            "stage_name": logical_stage_name(4),
            "selected_case_count": summary.get("selected_count", 0),
            "rejected_case_count": summary.get("rejected_count", 0),
            "traceability": summary.get("traceability_check", {}).get("status", ""),
            "test_generation": summary.get("test_generation", {}).get("status", ""),
        }
    )
    return 0


def cmd_run_stage(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    spec = read_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC)
    state = load_state(paths)
    current_internal_stage = int(state.get("current_stage", 0))
    current_logical_stage = logical_stage_for_internal(current_internal_stage)
    if current_logical_stage != args.stage:
        raise WorkflowError(
            f"当前逻辑阶段为 {current_logical_stage}（internal stage={current_internal_stage}），"
            f"不能直接执行 logical stage {args.stage}"
        )

    result = run_one_stage(paths, spec, state, current_internal_stage, trigger_source="run_stage")
    if args.json:
        if result == "waiting_decision":
            print_json({"gate_review": build_gate_review_payload(paths, include_events=True)})
        else:
            print_json(build_status_brief_payload(paths))
        return 0
    if result == "waiting_decision":
        print_json(build_gate_brief_payload(paths))
    else:
        print_json(build_status_brief_payload(paths))
    return 0


def cmd_run_auto(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    result = run_auto(paths)
    if args.json:
        if result == "waiting_decision":
            print_json({"gate_review": build_gate_review_payload(paths, include_events=True)})
        else:
            print_json(build_status_brief_payload(paths))
        return 0
    if result == "waiting_decision":
        print_json(build_gate_brief_payload(paths))
    else:
        print_json(build_status_brief_payload(paths))
    return 0


def cmd_resume(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    spec = read_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC)
    if args.auto:
        ensure_auto_continue_allowed(spec, allowed_by_flag=bool(args.allow_auto_continue), command_name="resume")
    result = resume(paths, auto_continue=args.auto)
    if args.json:
        if result == "waiting_decision":
            print_json({"gate_review": build_gate_review_payload(paths, include_events=True)})
        else:
            print_json(build_status_brief_payload(paths))
        return 0
    if result == "waiting_decision":
        print_json(build_gate_brief_payload(paths))
    else:
        print_json(build_status_brief_payload(paths))
    return 0


def cmd_decision_template(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    pending = read_json(paths.pending, None)
    pending_errors = validate_pending_decision(pending if isinstance(pending, dict) else None)
    if pending_errors:
        raise WorkflowError(f"pending.json 校验失败: {pending_errors}")

    allowed_actions = pending["allowed_actions"]
    chosen_action = args.action or ("approve" if "approve" in allowed_actions else allowed_actions[0])
    if chosen_action == "enter_rollback":
        chosen_action = "run_stage8"
    if chosen_action not in allowed_actions:
        raise WorkflowError(f"action `{chosen_action}` 不在允许列表: {allowed_actions}")

    template: dict[str, Any] = {
        "schema_version": 1,
        "decision_id": pending["decision_id"],
        "action": chosen_action,
        "reason": "fill_reason_here",
        "timestamp": now_iso(),
    }
    if chosen_action == "goto_stage":
        default_logical_stage = logical_stage_for_internal(int(pending.get("fallback_stage", pending.get("next_stage_on_approve", 0))))
        target_logical_stage = args.next_stage if args.next_stage is not None else default_logical_stage
        template["next_stage"] = internal_stage_for_logical(target_logical_stage)

    write_json(paths.decision, template)
    append_event(
        paths,
        "decision_template_written",
        {
            "decision_id": pending["decision_id"],
            "action": chosen_action,
            "trigger_source": "decision-template",
        },
    )
    if args.json:
        print_json({"status": "ok", "decision_path": paths.decision.as_posix(), "action": display_action_name(chosen_action)})
        return 0
    print_json({"status": "ok", "decision_path": rel_path(paths, paths.decision), "action": display_action_name(chosen_action)})
    return 0


def cmd_decide(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    state = load_state(paths)
    if state.get("status") != "waiting_decision":
        raise WorkflowError("当前不在等待决策状态，请先执行 run-auto 或 gate 查看状态")
    pending = read_json(paths.pending, None)
    pending_errors = validate_pending_decision(pending if isinstance(pending, dict) else None)
    if pending_errors:
        raise WorkflowError(f"pending.json 校验失败: {pending_errors}")

    action, goto_stage = action_from_decide_args(args)
    if (args.confirm_stage6_commit or args.skip_stage6_commit) and action != "approve":
        raise WorkflowError("--confirm-stage6-commit/--skip-stage6-commit 仅可与 --approve 一起使用")

    allowed_actions = pending["allowed_actions"]
    if action not in allowed_actions:
        raise WorkflowError(f"action `{action}` 不在允许列表: {allowed_actions}")

    decision: dict[str, Any] = {
        "schema_version": 1,
        "decision_id": pending["decision_id"],
        "action": action,
        "reason": args.reason or "decide command",
        "timestamp": now_iso(),
    }
    if action == "goto_stage":
        current_internal_stage = int(state.get("current_stage", 0))
        decision["next_stage"] = internal_stage_for_logical(int(goto_stage), current_internal_stage=current_internal_stage)
    if args.target_ref:
        decision["target_ref"] = args.target_ref
    if args.confirm_stage6_commit:
        decision["stage6_commit_decision"] = "confirm"
    elif args.skip_stage6_commit:
        decision["stage6_commit_decision"] = "skip"

    write_json(paths.decision, decision)
    append_event(
        paths,
        "decision_written",
        {
            "decision_id": pending["decision_id"],
            "action": action,
            "trigger_source": "decide",
        },
    )

    if args.apply:
        spec = read_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC)
        if args.auto:
            ensure_auto_continue_allowed(spec, allowed_by_flag=bool(args.allow_auto_continue), command_name="decide")
        result = resume(paths, auto_continue=args.auto)
        if args.json:
            if result == "waiting_decision":
                print_json({"gate_review": build_gate_review_payload(paths, include_events=True)})
            else:
                print_json(build_status_brief_payload(paths))
            return 0
        if result == "waiting_decision":
            print_json(build_gate_brief_payload(paths))
        else:
            print_json(build_status_brief_payload(paths))
        return 0

    if args.json:
        print_json({"status": "ok", "decision_path": paths.decision.as_posix(), "action": display_action_name(action)})
        return 0
    print_json({"status": "ok", "decision_path": rel_path(paths, paths.decision), "action": display_action_name(action)})
    return 0


def cmd_recalc_analyze(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    analysis = analyze_recalc_graph(paths, changed_paths=args.changed)
    output_path = paths.scaffold / "artifacts" / "stage7" / "recalc_analysis.json"
    write_json(output_path, analysis)
    append_event(
        paths,
        "recalc_analyzed",
        {
            "recommended_stage": analysis.get("recommended_rerun_from_stage"),
            "changed_count": len(analysis.get("changed_paths", [])),
            "trigger_source": "recalc-analyze",
        },
    )
    if args.json:
        print_json({"analysis": analysis, "output": output_path.as_posix()})
        return 0
    print_json(
        {
            "status": "ok",
            "recommended_stage": logical_stage_for_internal(analysis.get("recommended_rerun_from_stage", 0)),
            "changed_count": len(analysis.get("changed_paths", [])),
            "reason_count": len(analysis.get("reasons", [])),
            "output": rel_path(paths, output_path),
        }
    )
    return 0


def cmd_stage8_rollback(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    plan = stage8_rollback(paths, target_ref=args.target_ref, branch_name=args.branch_name, apply=args.apply)
    if args.json:
        print_json(plan)
        return 0
    print_json(
        {
            "status": "ok",
            "logical_stage": 7,
            "stage_name": logical_stage_name(8),
            "decision": plan.get("decision", ""),
            "target_ref": plan.get("target_ref", ""),
            "branch_name": plan.get("branch_name", ""),
            "applied": bool(plan.get("applied", False)),
        }
    )
    return 0


def cmd_semantic_snapshot(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    payload = update_semantic_snapshot(paths)
    if args.json:
        print_json(payload)
        return 0
    print_json({"status": "ok", "hash": payload.get("hash", ""), "updated_at": payload.get("updated_at", "")})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex TDD workflow runner")
    parser.add_argument(
        "--workspace",
        "--root-dir",
        dest="workspace",
        help="Repository root path; default auto-detect or CODEX_DEVFLOW_ROOT",
    )
    parser.add_argument("--scaffold-dir", default="codex_devflow_scaffold", help="Scaffold directory path")
    parser.add_argument("--json", action="store_true", help="Print full JSON payload instead of concise summary")

    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialize scaffold core files")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing files")
    p_init.add_argument(
        "--preset",
        choices=list(SUPPORTED_INIT_PRESETS),
        default="generic",
        help="Config template preset for init",
    )

    sub.add_parser("detect-preset", help="Detect project framework/preset suggestion")
    sub.add_parser("check-config", help="Check whether scaffold config is ready for bootstrap")
    sub.add_parser("status", help="Show current state and pending decision")
    sub.add_parser("gate", help="Show gate-centric view (status + pending)")
    p_stage3_semantic = sub.add_parser("stage3-semantic", help="Apply stage3 semantic event selection in current session")
    p_stage3_semantic.add_argument(
        "--keep-event-id",
        action="append",
        default=[],
        help="Event id to keep; can repeat or use comma-separated values",
    )
    p_stage3_semantic.add_argument("--keep-all", action="store_true", help="Keep all stage3 candidate events")
    p_stage3_semantic.add_argument("--max-events", type=int, help="Optional cap after semantic selection")
    p_stage3_semantic.add_argument(
        "--sync-session-events",
        dest="sync_session_events",
        action="store_true",
        default=True,
        help="Sync business_event_candidates from current session event artifact before semantic selection",
    )
    p_stage3_semantic.add_argument(
        "--no-sync-session-events",
        dest="sync_session_events",
        action="store_false",
        help="Do not sync business_event_candidates from session file",
    )
    p_stage3_semantic.add_argument("--reason", help="Semantic review reason")
    p_stage3_semantic.add_argument("--reviewer", help="Semantic reviewer label")
    p_stage4_semantic = sub.add_parser("stage4-semantic", help="Apply stage4 semantic case selection in current session")
    p_stage4_semantic.add_argument(
        "--keep-case-id",
        action="append",
        default=[],
        help="Case id to keep; can repeat or use comma-separated values",
    )
    p_stage4_semantic.add_argument("--keep-all", action="store_true", help="Keep all stage4 candidate cases")
    p_stage4_semantic.add_argument("--max-cases", type=int, help="Optional cap after semantic selection")
    p_stage4_semantic.add_argument(
        "--sync-session-cases",
        dest="sync_session_cases",
        action="store_true",
        default=True,
        help="Sync candidate_cases from current session case artifact before semantic selection",
    )
    p_stage4_semantic.add_argument(
        "--no-sync-session-cases",
        dest="sync_session_cases",
        action="store_false",
        help="Do not sync candidate_cases from session file",
    )
    p_stage4_semantic.add_argument("--reason", help="Semantic review reason")
    p_stage4_semantic.add_argument("--reviewer", help="Semantic reviewer label")
    p_stage = sub.add_parser("run-stage", help="Run a single stage")
    p_stage.add_argument("stage", type=int, choices=list(LOGICAL_STAGES), help="Logical stage id (0-7)")

    sub.add_parser("run-auto", help="Run from current stage until gate")

    p_resume = sub.add_parser("resume", help="Apply decision.json and continue")
    p_resume.add_argument("--auto", action="store_true", help="Continue auto-run after resume")
    p_resume.add_argument(
        "--allow-auto-continue",
        action="store_true",
        help="Override manual approval mode and allow --auto to continue after decision",
    )

    p_tpl = sub.add_parser("decision-template", help="Generate decision.json template from pending gate")
    p_tpl.add_argument("--action", choices=["approve", "reject", "goto_stage", "run_stage8", "enter_rollback"], help="Decision action")
    p_tpl.add_argument("--next-stage", type=int, choices=list(LOGICAL_STAGES), help="Used when action=goto_stage")

    p_decide = sub.add_parser("decide", help="Standardized decision command (write decision.json)")
    action_group = p_decide.add_mutually_exclusive_group(required=True)
    action_group.add_argument("--approve", action="store_true", help="Approve pending gate")
    action_group.add_argument("--reject", action="store_true", help="Reject pending gate")
    action_group.add_argument("--run-stage8", action="store_true", help="Run stage8 from stage7 gate")
    action_group.add_argument("--enter-rollback", action="store_true", help="Alias of --run-stage8 for logical rollback stage")
    action_group.add_argument("--goto-stage", type=int, choices=list(LOGICAL_STAGES), help="Jump to target logical stage")
    p_decide.add_argument("--reason", help="Decision reason text")
    p_decide.add_argument("--target-ref", help="Optional rollback target ref")
    commit_decision_group = p_decide.add_mutually_exclusive_group()
    commit_decision_group.add_argument(
        "--confirm-stage6-commit",
        action="store_true",
        help="When approving stage6, explicitly confirm executing git add/git commit from commit_review",
    )
    commit_decision_group.add_argument(
        "--skip-stage6-commit",
        action="store_true",
        help="When approving stage6, explicitly skip executing git commit for this round",
    )
    p_decide.add_argument("--apply", action="store_true", help="Immediately apply decision (call resume)")
    p_decide.add_argument("--auto", action="store_true", help="Use with --apply: continue auto-run after resume")
    p_decide.add_argument(
        "--allow-auto-continue",
        action="store_true",
        help="Override manual approval mode and allow --auto to continue after decision",
    )

    p_recalc = sub.add_parser("recalc-analyze", help="Analyze semantic change -> downstream rerun stages")
    p_recalc.add_argument("--changed", nargs="*", help="Optional changed paths; default from git status")

    p_rb = sub.add_parser("stage8-rollback", aliases=["stage7-rollback"], help="Auto create rollback branch from rollback artifact")
    p_rb.add_argument("--target-ref", help="Rollback target ref; default from stage8 artifact")
    p_rb.add_argument("--branch-name", help="Rollback branch name")
    p_rb.add_argument("--apply", action="store_true", help="Apply git switch -c (without this, dry-run only)")

    sub.add_parser("semantic-snapshot", help="Update semantic snapshot baseline")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        try:
            root = _detect_repo_root(args.workspace)
        except RuntimeError as exc:
            raise WorkflowError(str(exc)) from exc
        scaffold_arg = Path(args.scaffold_dir).expanduser()
        scaffold = scaffold_arg.resolve() if scaffold_arg.is_absolute() else (root / scaffold_arg).resolve()
        paths = Paths(root=root, scaffold=scaffold)
        if args.command == "init":
            return cmd_init(args, paths)
        if args.command == "detect-preset":
            return cmd_detect_preset(args, paths)
        if args.command == "check-config":
            return cmd_check_config(args, paths)
        if args.command == "status":
            return cmd_status(args, paths)
        if args.command == "gate":
            return cmd_gate(args, paths)
        if args.command == "stage3-semantic":
            return cmd_stage3_semantic(args, paths)
        if args.command == "stage4-semantic":
            return cmd_stage4_semantic(args, paths)
        if args.command == "run-stage":
            return cmd_run_stage(args, paths)
        if args.command == "run-auto":
            return cmd_run_auto(args, paths)
        if args.command == "resume":
            return cmd_resume(args, paths)
        if args.command == "decision-template":
            return cmd_decision_template(args, paths)
        if args.command == "decide":
            return cmd_decide(args, paths)
        if args.command == "recalc-analyze":
            return cmd_recalc_analyze(args, paths)
        if args.command in {"stage8-rollback", "stage7-rollback"}:
            return cmd_stage8_rollback(args, paths)
        if args.command == "semantic-snapshot":
            return cmd_semantic_snapshot(args, paths)
    except WorkflowError as exc:
        print(f"[workflow-error] {exc}")
        return 2
    except Exception as exc:  # pragma: no cover
        print(f"[unexpected-error] {exc}")
        return 3

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
