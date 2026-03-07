#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
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

def _detect_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "manage.py").exists():
            return parent
    raise RuntimeError("cannot detect repository root from workflow_runner.py path")


ROOT = _detect_repo_root()
SCAFFOLD_DIR = ROOT / "codex_devflow_scaffold"
STAGES = tuple(range(9))
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
SEMANTIC_DIR_NAMES = {
    "overview": "项目总体概览",
    "business": "业务侧实现",
    "permission": "权限管理侧实现",
}
GENERATED_TEST_FILE = "codex_devflow_scaffold/tests/generated/test_generated_specs.py"
SEMANTIC_BLOCKED_STAGES = {1, 3, 4, 7, 8}
ENTITY_DOC_SUFFIXES = (
    "data_dictionary.md",
    "impl_desc.md",
    "logical_model.md",
    "schema.dbml",
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
        "local_scan_enabled": True,
        "local_scan_urlconf": "config.business_api_urlconf",
    },
    "test_command": [".venv/bin/python", "manage.py", "test"],
    "gate_policy": {
        "0": {
            "reason": "stage0_review_required",
            "allowed_actions": ["approve", "reject", "goto_stage"],
            "next_stage_on_approve": 1,
            "fallback_stage": 0,
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

DEFAULT_STATE: dict[str, Any] = {
    "schema_version": 1,
    "status": "idle",
    "current_stage": 0,
    "running_iteration": 1,
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
    0: ["api_candidates", "biz_status_codes", "required_case_codes", "risk_assessment", "semantic_gap_report"],
    1: ["implementation_tasks", "changed_files", "notes"],
    2: ["scanned_apis", "existing_api_keys", "new_api_keys", "drift_items"],
    3: ["business_events", "editor_notes"],
    4: ["generated_cases", "generated_test_files", "coverage_by_event"],
    5: ["executed_cases", "failed_cases", "regression_summary"],
    6: ["registry_updates", "docs_updates", "settlement_commit_message"],
    7: ["intervention_summary", "recommendations"],
    8: ["decision", "reasoning", "actions"],
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


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ensure_json(path: Path, payload: Any, force: bool) -> None:
    if force or not path.exists():
        write_json(path, payload)


def ensure_text(path: Path, text: str, force: bool) -> None:
    if force or not path.exists():
        write_text(path, text)


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


def semantic_directories(paths: Paths) -> dict[str, Path]:
    return {key: paths.root / dirname for key, dirname in SEMANTIC_DIR_NAMES.items()}


def missing_semantic_directories(paths: Paths) -> list[str]:
    missing: list[str] = []
    for folder in semantic_directories(paths).values():
        if not folder.exists() or not folder.is_dir():
            missing.append(rel_path(paths, folder))
    return sorted(missing)


def ensure_semantic_directories_for_stage(paths: Paths, stage: int) -> None:
    if stage not in SEMANTIC_BLOCKED_STAGES:
        return
    missing = missing_semantic_directories(paths)
    if missing:
        raise WorkflowError(f"stage{stage} 阻断：缺少业务语义目录 {missing}")


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

        normalized = candidate.strip().replace("\\", "/")
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

    suffix = "_data_dictionary.md"
    entities: set[str] = set()
    for item in business_dir.glob(f"*{suffix}"):
        name = item.name[: -len(suffix)]
        entity = normalize_entity_name(name)
        if entity:
            entities.add(entity)
    return entities


def entity_doc_paths(paths: Paths, entity: str) -> list[Path]:
    business_dir = semantic_directories(paths)["business"]
    return [business_dir / f"{entity}_{suffix}" for suffix in ENTITY_DOC_SUFFIXES]


def entity_candidates_from_api_key(api_key: str) -> list[str]:
    parts = api_key.split(maxsplit=1)
    if len(parts) != 2:
        return []

    raw_path = parts[1].split("?", 1)[0]
    candidates: list[str] = []
    for segment in raw_path.split("/"):
        segment = segment.strip()
        if not segment or segment.startswith("{"):
            continue
        normalized = normalize_entity_name(segment)
        if not normalized or normalized in IGNORE_ENTITY_SEGMENTS:
            continue
        candidates.append(normalized)
        if "_" in normalized:
            candidates.append(normalized.split("_", 1)[0])
    return dedupe_keep_order(candidates)


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


def infer_touched_entities(
    paths: Paths,
    stage0_artifact: dict[str, Any],
    stage1_artifact: dict[str, Any],
    stage2_artifact: dict[str, Any],
) -> list[str]:
    available = business_entities(paths)
    touched: list[str] = []

    for artifact in (stage1_artifact, stage0_artifact):
        entities = artifact.get("touched_entities", [])
        if not isinstance(entities, list):
            continue
        for item in entities:
            normalized = normalize_entity_name(str(item))
            if normalized:
                touched.append(normalized)

    api_keys: list[str] = []
    stage0_candidates = stage0_artifact.get("api_candidates", [])
    if isinstance(stage0_candidates, list):
        for item in stage0_candidates:
            if isinstance(item, dict) and isinstance(item.get("api_key"), str):
                api_keys.append(item["api_key"])
    stage2_new = stage2_artifact.get("new_api_keys", [])
    if isinstance(stage2_new, list):
        api_keys.extend(str(item) for item in stage2_new if isinstance(item, str))

    for api_key in api_keys:
        touched.extend(resolve_entities(entity_candidates_from_api_key(api_key), available))

    if not touched and available:
        semantic_model = read_json(paths.semantic_model, {})
        resources = semantic_model.get("resources", []) if isinstance(semantic_model, dict) else []
        for resource in resources:
            normalized = normalize_entity_name(str(resource))
            if not normalized:
                continue
            mapped = resolve_entities([normalized], available)
            if mapped:
                touched.extend(mapped)
                break

    return dedupe_keep_order(touched)


def evaluate_entity_doc_sync(paths: Paths, touched_entities: list[str], changed_paths: list[str]) -> dict[str, Any]:
    changed_set = {item.replace("\\", "/") for item in changed_paths}
    code_prefixes = ("apps/", "config/")
    code_changed = any(
        path == "manage.py" or path.startswith(code_prefixes)
        for path in changed_set
    )

    missing_docs: dict[str, list[str]] = {}
    unsynced_entities: list[str] = []

    for entity in touched_entities:
        docs = entity_doc_paths(paths, entity)
        rel_docs = [rel_path(paths, doc) for doc in docs]
        missing = [rel for rel, doc in zip(rel_docs, docs) if not doc.exists()]
        if missing:
            missing_docs[entity] = missing
            continue
        if code_changed and not any(rel in changed_set for rel in rel_docs):
            unsynced_entities.append(entity)

    return {
        "touched_entities": touched_entities,
        "code_changed": code_changed,
        "missing_docs": missing_docs,
        "unsynced_entities": unsynced_entities,
        "passed": not missing_docs and not unsynced_entities,
    }


def detect_permission_changes(changed_paths: list[str]) -> tuple[bool, list[str]]:
    impacted = []
    for path in changed_paths:
        normalized = path.replace("\\", "/")
        lower = normalized.lower()
        if (
            normalized.startswith("权限管理侧实现/")
            or normalized.startswith("apps/access/")
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
    if getattr(args, "run_stage8", False):
        return "run_stage8", None
    goto_stage = getattr(args, "goto_stage", None)
    if goto_stage is not None:
        return "goto_stage", int(goto_stage)
    raise WorkflowError("decide 命令必须指定一个动作")


def load_state(paths: Paths) -> dict[str, Any]:
    state = read_json(paths.state)
    if not isinstance(state, dict):
        raise WorkflowError("state.json 不存在或格式错误，请先执行 init")
    return state


def save_state(paths: Paths, state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    write_json(paths.state, state)


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


def _pick(artifact: dict[str, Any], *keys: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in keys:
        if key in artifact:
            payload[key] = artifact[key]
    return payload


def summarize_stage_artifact(stage: int, artifact: dict[str, Any] | None) -> dict[str, Any]:
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
    elif stage == 4:
        generated_cases = artifact.get("generated_cases", [])
        summary.update(
            _pick(
                artifact,
                "generated_test_files",
                "coverage_by_event",
            )
        )
        summary["generated_cases_count"] = len(generated_cases) if isinstance(generated_cases, list) else 0
    elif stage == 5:
        reg = artifact.get("regression_summary", {})
        summary.update(
            {
                "executed_cases": artifact.get("executed_cases"),
                "failed_cases": artifact.get("failed_cases"),
                "return_code": reg.get("return_code") if isinstance(reg, dict) else None,
                "consecutive_failures": reg.get("consecutive_failures") if isinstance(reg, dict) else None,
                "failure_distribution": reg.get("failure_distribution") if isinstance(reg, dict) else None,
            }
        )
    elif stage == 6:
        summary.update(
            _pick(
                artifact,
                "registry_updates",
                "docs_updates",
                "business_doc_sync",
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


def build_gate_review_payload(paths: Paths, include_events: bool = True) -> dict[str, Any]:
    state = load_state(paths)
    pending = read_json(paths.pending, DEFAULT_PENDING)

    payload: dict[str, Any] = {
        "status": state.get("status"),
        "current_stage": state.get("current_stage"),
        "pending": pending,
    }

    stage = None
    if isinstance(pending, dict) and pending.get("stage") in STAGES:
        stage = int(pending["stage"])
    elif state.get("current_stage") in STAGES:
        stage = int(state["current_stage"])

    if stage is not None:
        artifact_path = paths.stage_artifact(stage)
        artifact = read_json(artifact_path, None)
        payload["review"] = {
            "stage": stage,
            "artifact_path": rel_path(paths, artifact_path),
            "artifact_summary": summarize_stage_artifact(stage, artifact if isinstance(artifact, dict) else None),
            "decision_actions": pending.get("allowed_actions", []) if isinstance(pending, dict) else [],
            "approval_mode": approval_mode(read_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC)),
        }
    else:
        payload["review"] = {
            "stage": None,
            "artifact_path": None,
            "artifact_summary": {"available": False},
            "decision_actions": [],
            "approval_mode": approval_mode(read_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC)),
        }

    if include_events:
        payload["recent_events"] = tail_events(paths, limit=6)
    return payload


def ensure_scaffold(paths: Paths, force: bool = False) -> None:
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
        ensure_json(paths.stage_input(stage), {"stage": stage}, force)
        ensure_text(paths.prompt(stage), default_prompt(stage), force)
        ensure_json(paths.stage_schema(stage), default_stage_schema(stage), force)

    ensure_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC, force)

    state = dict(DEFAULT_STATE)
    state["updated_at"] = now_iso()
    ensure_json(paths.state, state, force)

    ensure_json(paths.semantic_model, DEFAULT_SEMANTIC_MODEL, force)
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

    ensure_text(paths.scaffold / "tests" / "generated" / "test_generated_specs.py", DEFAULT_GENERATED_TEST, force)


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
        "required": ["generated_cases", "generated_test_files", "coverage_by_event"],
    }


def stage5_schema() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "type": "object",
        "required": ["executed_cases", "failed_cases", "regression_summary"],
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
        "case_descriptions": [4, 5, 6],
        "business_docs": [0, 1, 3, 4, 6, 7, 8],
        "permission_docs": [0, 1, 3, 4, 6, 7, 8],
    }

    if changed_paths is None:
        changed_paths = git_changed_paths(paths)

    changed_paths = [item.replace("\\", "/") for item in changed_paths]
    affected: set[int] = set()
    reasons: list[dict[str, Any]] = []

    snapshot = read_json(paths.semantic_snapshot, {})
    old_hash = snapshot.get("hash", "") if isinstance(snapshot, dict) else ""
    current_hash = sha256_file(paths.semantic_model)
    semantic_changed = bool(current_hash and old_hash and current_hash != old_hash)
    if semantic_changed:
        changed_paths.append("codex_devflow_scaffold/semantic/business_semantic_model.json")
        reasons.append(
            {
                "path": "codex_devflow_scaffold/semantic/business_semantic_model.json",
                "reason": "semantic hash changed",
                "stages": graph["semantic"],
            }
        )
        affected.update(graph["semantic"])

    for path in changed_paths:
        if fnmatch.fnmatch(path, "codex_devflow_scaffold/workflow_spec.json"):
            stages = graph["workflow_spec"]
        elif fnmatch.fnmatch(path, "codex_devflow_scaffold/semantic/business_semantic_model.json"):
            stages = graph["semantic"]
        elif fnmatch.fnmatch(path, "codex_devflow_scaffold/registry/api_registry.json"):
            stages = graph["api_registry"]
        elif fnmatch.fnmatch(path, "codex_devflow_scaffold/registry/case_registry.json"):
            stages = graph["case_registry"]
        elif fnmatch.fnmatch(path, "codex_devflow_scaffold/cases/case_descriptions.jsonl"):
            stages = graph["case_descriptions"]
        elif fnmatch.fnmatch(path, "codex_devflow_scaffold/prompts/stage*.md"):
            stage = parse_stage_from_filename(Path(path).name, "stage", ".md")
            stages = list(range(stage, 9)) if stage is not None else []
        elif fnmatch.fnmatch(path, "codex_devflow_scaffold/schemas/stage*_output.schema.json"):
            stage = parse_stage_from_filename(Path(path).name, "stage", "_output.schema.json")
            stages = [stage] if stage is not None else []
        elif fnmatch.fnmatch(path, "codex_devflow_scaffold/artifacts/stage*/latest.json"):
            folder = Path(path).parts[-2]
            stage = parse_stage_from_filename(folder, "stage", "")
            stages = list(range(stage, 9)) if stage is not None else []
        elif fnmatch.fnmatch(path, "codex_devflow_scaffold/inputs/stage*.json"):
            stage = parse_stage_from_filename(Path(path).name, "stage", ".json")
            stages = [stage] if stage is not None else []
        elif fnmatch.fnmatch(path, "业务侧实现/*"):
            stages = graph["business_docs"]
        elif fnmatch.fnmatch(path, "权限管理侧实现/*"):
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

    missing = validate_semantic_model(model if isinstance(model, dict) else {})
    missing_dirs = missing_semantic_directories(paths)
    resources = model.get("resources", []) if isinstance(model, dict) else []
    touched_entities: list[str] = []
    for resource in resources:
        normalized = normalize_entity_name(str(resource))
        if normalized:
            touched_entities.append(normalized)
    touched_entities = dedupe_keep_order(touched_entities[:1])

    blocking = bool(missing) or bool(missing_dirs)
    semantic_gap_report = {
        "blocking": blocking,
        "missing_fields": missing,
        "missing_semantic_directories": missing_dirs,
    }

    api_candidates = []
    if not blocking:
        entity = touched_entities[0] if touched_entities else "todo"
        plural = f"{entity}s" if not entity.endswith("s") else entity
        api_candidates = [
            {
                "api_key": f"POST /api/v1/{plural.replace('_', '-')}",
                "reason": "由 Stage0 按语义资源自动生成候选，需人工 review",
                "risk": "medium",
            }
        ]

    max_new_api = int(spec.get("max_new_api_per_iteration", 1))
    api_candidates = api_candidates[: max(1, max_new_api)]

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
        "api_registry_items": len(api_registry.get("items", [])),
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
    errors: list[str] = []

    if schema_url:
        try:
            scanned, source = _scan_api_from_schema(schema_url, timeout_seconds)
            if scanned:
                return scanned, source, ""
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError) as exc:
            errors.append(f"schema_error={exc}")
        else:
            errors.append("schema_error=schema_empty")

    if root_url:
        try:
            scanned, source = _scan_api_from_root(root_url, timeout_seconds)
            if scanned:
                return scanned, source, ""
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError) as exc:
            errors.append(f"root_error={exc}")
        else:
            errors.append("root_error=root_empty")

    local_enabled = bool(business_api.get("local_scan_enabled", True))
    if local_enabled:
        local_command = business_api.get("local_scan_command")
        if isinstance(local_command, list) and all(isinstance(item, str) for item in local_command):
            command = list(local_command)
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
            scanned, source = _scan_api_from_local_schema(paths, command)
            if scanned:
                return scanned, source, ""
            errors.append("local_error=local_schema_empty")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"local_error={exc}")
    else:
        errors.append("local_error=local_scan_disabled")

    if not errors:
        errors.append("scan_error=unknown")
    return [], "none", "; ".join(errors)


def stage2(paths: Paths, spec: dict[str, Any], state: dict[str, Any]) -> StageResult:
    api_registry = read_json(paths.api_registry, DEFAULT_API_REGISTRY)
    scanned_apis, source, scan_error = discover_apis(paths, spec)

    existing_api_keys = []
    for item in api_registry.get("items", []):
        if isinstance(item, dict) and isinstance(item.get("api_key"), str):
            existing_api_keys.append(item["api_key"])
    existing_api_keys = sorted(set(existing_api_keys))

    bootstrap_done = bool(api_registry.get("bootstrap_done", False))
    bootstrap_mode = (not existing_api_keys) and (not bootstrap_done)

    new_api_keys = sorted(set(scanned_apis) - set(existing_api_keys))
    missing_online = sorted(set(existing_api_keys) - set(scanned_apis))

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
        "bootstrap_mode": bootstrap_mode,
    }

    if scan_error:
        return StageResult(stage=2, artifact=artifact, next_stage=7)
    if drift_items and not bootstrap_mode:
        return StageResult(stage=2, artifact=artifact, next_stage=7)

    return StageResult(stage=2, artifact=artifact, next_stage=3)


def stage3(paths: Paths, spec: dict[str, Any]) -> StageResult:
    stage2_artifact = read_json(paths.stage_artifact(2), {})
    new_api_keys = stage2_artifact.get("new_api_keys", []) if isinstance(stage2_artifact, dict) else []

    business_events = []
    for idx, api_key in enumerate(new_api_keys, start=1):
        business_events.append(
            {
                "event_id": f"EVT-{idx:03d}",
                "title": f"新 API 接入事件 {idx}",
                "api_refs": [api_key],
                "description": "由 Stage3 自动组合，开发者可编辑",
            }
        )

    if not business_events:
        business_events.append(
            {
                "event_id": "EVT-001",
                "title": "无新增 API 的占位事件",
                "api_refs": [],
                "description": "用于保持阶段产物结构完整",
            }
        )

    artifact = {
        "stage": 3,
        "generated_at": now_iso(),
        "business_events": business_events,
        "editor_notes": ["可在 Stage3 产物基础上手工补充复杂业务流"],
    }
    return StageResult(stage=3, artifact=artifact, next_stage=4)


def _write_case_descriptions(paths: Paths, cases: list[dict[str, Any]]) -> None:
    lines = [json.dumps(item, ensure_ascii=False) for item in cases]
    text = "\n".join(lines)
    if text:
        text += "\n"
    write_text(paths.case_descriptions, text)


def stage4(paths: Paths, spec: dict[str, Any]) -> StageResult:
    stage0_artifact = read_json(paths.stage_artifact(0), {})
    stage3_artifact = read_json(paths.stage_artifact(3), {})
    required_codes = stage0_artifact.get("required_case_codes", []) if isinstance(stage0_artifact, dict) else []
    events = stage3_artifact.get("business_events", []) if isinstance(stage3_artifact, dict) else []

    if not required_codes:
        required_codes = ["SUCCESS", "INVALID_PARAMS"]
    required_codes = dedupe_keep_order([str(item) for item in required_codes if isinstance(item, str) and item.strip()])

    normalized_events: list[dict[str, Any]] = []
    if isinstance(events, list):
        for idx, event in enumerate(events, start=1):
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("event_id", f"EVT-{idx:03d}"))
            refs = event.get("api_refs", [])
            api_refs = [str(item) for item in refs if isinstance(item, str)] if isinstance(refs, list) else []
            normalized_events.append({"event_id": event_id, "api_refs": api_refs})
    if not normalized_events:
        normalized_events = [{"event_id": "EVT-001", "api_refs": []}]

    generated_cases = []
    case_idx = 1
    for event in normalized_events:
        for code in required_codes:
            generated_cases.append(
                {
                    "case_id": f"CASE-AUTO-{case_idx:03d}",
                    "title": f"{event['event_id']} 自动生成用例 {code}",
                    "event_id": event["event_id"],
                    "api_refs": event["api_refs"],
                    "preconditions": [],
                    "steps": [],
                    "expected_business_code": code,
                    "expected_http_status": 200 if code == "SUCCESS" else 400,
                    "tags": ["auto", "stage4"],
                    "status": "draft",
                    "owner": "workflow_runner",
                    "updated_at": now_iso(),
                }
            )
            case_idx += 1

    _write_case_descriptions(paths, generated_cases)

    coverage_by_event = []
    for event in normalized_events:
        event_codes = [
            item["expected_business_code"]
            for item in generated_cases
            if item.get("event_id") == event["event_id"]
        ]
        covered_set = set(event_codes)
        coverage_by_event.append(
            {
                "event_id": event["event_id"],
                "covered_codes": dedupe_keep_order(event_codes),
                "missing_codes": [code for code in required_codes if code not in covered_set],
            }
        )

    artifact = {
        "stage": 4,
        "generated_at": now_iso(),
        "generated_cases": generated_cases,
        "generated_test_files": [GENERATED_TEST_FILE],
        "coverage_by_event": coverage_by_event,
    }

    return StageResult(stage=4, artifact=artifact, next_stage=5)


def stage5(paths: Paths, spec: dict[str, Any], state: dict[str, Any]) -> StageResult:
    test_command = spec.get("test_command", [".venv/bin/python", "manage.py", "test"])
    if not isinstance(test_command, list) or not test_command:
        raise WorkflowError("workflow_spec.test_command 配置非法")

    stage4_artifact = read_json(paths.stage_artifact(4), {})
    generated_cases = stage4_artifact.get("generated_cases", []) if isinstance(stage4_artifact, dict) else []

    try:
        proc = subprocess.run(
            test_command,
            cwd=str(paths.root),
            capture_output=True,
            text=True,
            check=False,
        )
        return_code = proc.returncode
        stdout_tail = "\n".join(proc.stdout.splitlines()[-40:])
        stderr_tail = "\n".join(proc.stderr.splitlines()[-40:])
    except OSError as exc:
        return_code = 1
        stdout_tail = ""
        stderr_tail = str(exc)

    failed_cases = []
    if return_code != 0 and generated_cases:
        failed_cases = [item.get("case_id") for item in generated_cases if isinstance(item, dict)]
    failure_distribution = {}
    for case_id in failed_cases:
        if not isinstance(case_id, str):
            continue
        failure_distribution[case_id] = {
            "error_type": "test_command_failed",
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
        "executed_cases": len(generated_cases),
        "failed_cases": failed_cases,
        "regression_summary": {
            "return_code": return_code,
            "command": test_command,
            "consecutive_failures": consecutive,
            "failure_threshold": threshold,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "failure_distribution": failure_distribution,
        },
    }

    return StageResult(stage=5, artifact=artifact, next_stage=next_stage)


def _upsert_api_registry(paths: Paths) -> dict[str, Any]:
    api_registry = read_json(paths.api_registry, DEFAULT_API_REGISTRY)
    stage2_artifact = read_json(paths.stage_artifact(2), {})

    scanned_apis = stage2_artifact.get("scanned_apis", []) if isinstance(stage2_artifact, dict) else []
    new_api_keys = stage2_artifact.get("new_api_keys", []) if isinstance(stage2_artifact, dict) else []
    bootstrap_mode = bool(stage2_artifact.get("bootstrap_mode", False)) if isinstance(stage2_artifact, dict) else False

    existing = {
        item.get("api_key"): item
        for item in api_registry.get("items", [])
        if isinstance(item, dict) and isinstance(item.get("api_key"), str)
    }

    now = now_iso()
    if bootstrap_mode and not api_registry.get("bootstrap_done", False):
        for api_key in scanned_apis:
            existing[api_key] = {
                "api_key": api_key,
                "source": "bootstrap_scan",
                "created_at": now,
                "updated_at": now,
                "enabled": True,
            }
        api_registry["bootstrap_done"] = True

    for api_key in new_api_keys:
        if api_key in existing:
            existing[api_key]["updated_at"] = now
        else:
            existing[api_key] = {
                "api_key": api_key,
                "source": "stage2_new",
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
    for item in generated_cases:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("case_id", ""))
        event_id = str(item.get("event_id", ""))
        api_refs = item.get("api_refs", [])
        test_name = f"test_auto__{case_id.lower().replace('-', '_')}"
        key = (case_id, test_name)
        existing[key] = {
            "case_id": case_id,
            "event_id": event_id,
            "api_refs": api_refs if isinstance(api_refs, list) else [],
            "test_file": GENERATED_TEST_FILE,
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
) -> list[str]:
    stage2_artifact = read_json(paths.stage_artifact(2), {})
    stage4_artifact = read_json(paths.stage_artifact(4), {})
    stage5_artifact = read_json(paths.stage_artifact(5), {})

    docs_updates: list[str] = []

    summary_lines = [
        "# API Change Summary",
        "",
        f"- generated_at: {now_iso()}",
        f"- stage2.new_api_keys: {stage2_artifact.get('new_api_keys', [])}",
        "- 本轮仅新增 1 个 API（规划约束，候选仍需人工确认）。",
        f"- touched_entities: {touched_entities}",
    ]
    missing_docs = doc_sync.get("missing_docs", {})
    unsynced_entities = set(doc_sync.get("unsynced_entities", []))
    if touched_entities:
        for entity in touched_entities:
            if entity in missing_docs:
                summary_lines.append(f"- {entity} 四件套缺失：{missing_docs[entity]}")
            elif entity in unsynced_entities:
                summary_lines.append(f"- {entity} 四件套未与本轮代码变更同步（需补文档后重跑）")
            else:
                summary_lines.append(f"- {entity} 四件套已对齐检查")
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

    api_catalog = (
        "# API Catalog\n\n"
        f"- scanned_apis: {stage2_artifact.get('scanned_apis', [])}\n"
        f"- new_api_keys: {stage2_artifact.get('new_api_keys', [])}\n"
        "- 业务状态码以 Stage0 `required_case_codes` 为准。\n"
    )
    write_text(paths.scaffold / "docs" / "api_catalog.md", api_catalog)
    docs_updates.append("api_catalog.md")

    data_model_delta = (
        "# Data Model Delta\n\n"
        "- 本轮未检测到模型结构自动变更。\n"
        "- 若后续涉及实体变更，需同步更新业务侧四件套文档。\n"
        f"- touched_entities: {touched_entities}\n"
    )
    write_text(paths.scaffold / "docs" / "data_model_delta.md", data_model_delta)
    docs_updates.append("data_model_delta.md")

    traceability = (
        "# Traceability Matrix\n\n"
        "| requirement | api | data_model | permission | test_case |\n"
        "|---|---|---|---|---|\n"
    )
    for case in stage4_artifact.get("generated_cases", []) if isinstance(stage4_artifact, dict) else []:
        if not isinstance(case, dict):
            continue
        api_refs = case.get("api_refs", [])
        api_text = ", ".join(api_refs) if isinstance(api_refs, list) else ""
        traceability += (
            f"| {case.get('title','')} | {api_text} | - | - | {case.get('case_id','')} |\n"
        )
    write_text(paths.scaffold / "docs" / "traceability_matrix.md", traceability)
    docs_updates.append("traceability_matrix.md")

    test_records = (
        "# Test Example Records\n\n"
        f"- case_count: {len(stage4_artifact.get('generated_cases', [])) if isinstance(stage4_artifact, dict) else 0}\n"
    )
    write_text(paths.scaffold / "docs" / "test_example_records.md", test_records)
    docs_updates.append("test_example_records.md")

    if permission_changed:
        permission_doc = ["# Permission Impact", "", f"- generated_at: {now_iso()}", "- changed_paths:"]
        permission_doc.extend(f"  - {path}" for path in permission_paths)
        write_text(paths.scaffold / "docs" / "permission_impact.md", "\n".join(permission_doc) + "\n")
        docs_updates.append("permission_impact.md")

    return docs_updates


def stage6(paths: Paths, spec: dict[str, Any]) -> StageResult:
    changed_paths = git_changed_paths(paths)
    permission_changed, permission_paths = detect_permission_changes(changed_paths)

    stage0_artifact = read_json(paths.stage_artifact(0), {})
    stage1_artifact = read_json(paths.stage_artifact(1), {})
    stage2_artifact = read_json(paths.stage_artifact(2), {})
    touched_entities = infer_touched_entities(
        paths,
        stage0_artifact if isinstance(stage0_artifact, dict) else {},
        stage1_artifact if isinstance(stage1_artifact, dict) else {},
        stage2_artifact if isinstance(stage2_artifact, dict) else {},
    )
    doc_sync = evaluate_entity_doc_sync(paths, touched_entities, changed_paths)
    docs_updates = _update_docs(paths, touched_entities, doc_sync, permission_changed, permission_paths)

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
            "settlement_commit_message": "stage-6(blocked): missing business doc sync, rollback to stage1",
            "business_doc_sync": doc_sync,
        }
        return StageResult(stage=6, artifact=artifact, next_stage=1, gate=False)

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
            "next_step": "可执行 stage8-rollback --apply 自动创建回退分支",
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
    if state.get("status") == "waiting_decision":
        raise WorkflowError("当前处于等待决策状态，请先执行 resume")

    if stage not in STAGES:
        raise WorkflowError("stage 必须在 0-8")

    if int(state.get("current_stage", 0)) != stage:
        raise WorkflowError(f"当前阶段为 {state.get('current_stage')}，不能直接执行 stage {stage}")

    ensure_semantic_directories_for_stage(paths, stage)

    state["status"] = "running"
    save_state(paths, state)

    append_event(paths, "stage_started", {"stage": stage, "trigger_source": trigger_source})
    start = time.perf_counter()
    try:
        result = execute_stage(paths, spec, state, stage)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
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
        state["status"] = "idle"
        save_state(paths, state)
        raise

    duration_ms = int((time.perf_counter() - start) * 1000)
    artifact_missing = validate_stage_artifact(stage, result.artifact)
    if artifact_missing:
        raise WorkflowError(f"Stage {stage} 产物缺少字段: {artifact_missing}")

    write_json(paths.stage_artifact(stage), result.artifact)
    write_json(
        paths.stage_input(stage),
        {
            "stage": stage,
            "workflow_spec": paths.workflow_spec.as_posix(),
            "semantic_model": paths.semantic_model.as_posix(),
            "generated_at": now_iso(),
        },
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

    gate_policy = gate_for_stage(spec, stage) if result.gate else None
    if stage == 0 and isinstance(result.artifact, dict):
        semantic_gap = result.artifact.get("semantic_gap_report", {})
        if isinstance(semantic_gap, dict) and semantic_gap.get("blocking"):
            gate_policy = {
                "reason": "stage0_semantic_gap_blocking",
                "allowed_actions": ["reject", "goto_stage"],
                "next_stage_on_approve": 0,
                "fallback_stage": 0,
            }

    if gate_policy:
        pending = set_pending(paths, stage, gate_policy)
        state["status"] = "waiting_decision"
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
    if stage == 8 and result.next_stage == 0:
        state["running_iteration"] = int(state.get("running_iteration", 1)) + 1
    save_state(paths, state)
    return "idle"


def run_auto(paths: Paths) -> str:
    spec = read_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC)
    state = load_state(paths)

    while True:
        status = state.get("status")
        if status == "waiting_decision":
            return "waiting_decision"
        if status in {"completed", "aborted", "rolled_back"}:
            return str(status)

        current_stage = int(state.get("current_stage", 0))
        result = run_one_stage(paths, spec, state, current_stage, trigger_source="run_auto")
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


def resume(paths: Paths, auto_continue: bool) -> str:
    spec = read_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC)
    state = load_state(paths)

    if state.get("status") != "waiting_decision":
        raise WorkflowError("当前不在等待决策状态")
    started = time.perf_counter()

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
    next_stage = int(state.get("current_stage", 0))

    if action == "approve":
        next_stage = int(pending["next_stage_on_approve"])
    elif action == "reject":
        next_stage = int(pending["fallback_stage"])
    elif action == "goto_stage":
        next_stage = int(decision["next_stage"])
    elif action == "run_stage8":
        next_stage = 8

    clear_pending(paths)
    write_json(paths.decision, DEFAULT_DECISION)

    state["status"] = "idle"
    state["current_stage"] = next_stage

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
    ensure_scaffold(paths, force=args.force)
    append_event(paths, "workflow_initialized", {"force": bool(args.force), "trigger_source": "init"})
    print(f"initialized scaffold at {paths.scaffold}")
    return 0


def cmd_status(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    state = load_state(paths)
    pending = read_json(paths.pending, DEFAULT_PENDING)
    print(json.dumps({"state": state, "pending": pending}, ensure_ascii=False, indent=2))
    return 0


def cmd_gate(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    gate_payload = build_gate_review_payload(paths, include_events=True)
    print(json.dumps(gate_payload, ensure_ascii=False, indent=2))
    return 0


def cmd_case_web(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    from workflow_case_web import serve  # local import to avoid runtime cost

    append_event(
        paths,
        "case_web_started",
        {"host": args.host, "port": args.port, "trigger_source": "case-web"},
    )
    serve(paths.scaffold, args.host, args.port)
    return 0


def cmd_run_stage(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    spec = read_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC)
    state = load_state(paths)

    result = run_one_stage(paths, spec, state, args.stage, trigger_source="run_stage")
    print(f"stage {args.stage} done, status={result}")
    return 0


def cmd_run_auto(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    result = run_auto(paths)
    print(f"run-auto result={result}")
    if result == "waiting_decision":
        print(json.dumps({"gate_review": build_gate_review_payload(paths, include_events=True)}, ensure_ascii=False, indent=2))
    return 0


def cmd_resume(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    spec = read_json(paths.workflow_spec, DEFAULT_WORKFLOW_SPEC)
    if args.auto:
        ensure_auto_continue_allowed(spec, allowed_by_flag=bool(args.allow_auto_continue), command_name="resume")
    result = resume(paths, auto_continue=args.auto)
    print(f"resume result={result}")
    if result == "waiting_decision":
        print(json.dumps({"gate_review": build_gate_review_payload(paths, include_events=True)}, ensure_ascii=False, indent=2))
    return 0


def cmd_decision_template(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    pending = read_json(paths.pending, None)
    pending_errors = validate_pending_decision(pending if isinstance(pending, dict) else None)
    if pending_errors:
        raise WorkflowError(f"pending.json 校验失败: {pending_errors}")

    allowed_actions = pending["allowed_actions"]
    chosen_action = args.action or ("approve" if "approve" in allowed_actions else allowed_actions[0])
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
        template["next_stage"] = (
            args.next_stage if args.next_stage is not None else int(pending.get("next_stage_on_approve", 0))
        )

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
    print(f"decision template written: {paths.decision}")
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
        decision["next_stage"] = goto_stage
    if args.target_ref:
        decision["target_ref"] = args.target_ref

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
        print(f"decide+resume result={result}")
        if result == "waiting_decision":
            print(json.dumps({"gate_review": build_gate_review_payload(paths, include_events=True)}, ensure_ascii=False, indent=2))
        return 0

    print(f"decision written: {paths.decision}")
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
    print(json.dumps({"analysis": analysis, "output": output_path.as_posix()}, ensure_ascii=False, indent=2))
    return 0


def cmd_stage8_rollback(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    plan = stage8_rollback(paths, target_ref=args.target_ref, branch_name=args.branch_name, apply=args.apply)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


def cmd_semantic_snapshot(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    payload = update_semantic_snapshot(paths)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex TDD workflow runner")
    parser.add_argument("--scaffold-dir", default="codex_devflow_scaffold", help="Scaffold directory path")

    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialize scaffold core files")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing files")

    sub.add_parser("status", help="Show current state and pending decision")
    sub.add_parser("gate", help="Show gate-centric view (status + pending)")
    p_case_web = sub.add_parser("case-web", help="Run web case CRUD server")
    p_case_web.add_argument("--host", default="127.0.0.1", help="Bind host")
    p_case_web.add_argument("--port", type=int, default=8787, help="Bind port")

    p_stage = sub.add_parser("run-stage", help="Run a single stage")
    p_stage.add_argument("stage", type=int, choices=list(STAGES), help="Stage id (0-8)")

    sub.add_parser("run-auto", help="Run from current stage until gate")

    p_resume = sub.add_parser("resume", help="Apply decision.json and continue")
    p_resume.add_argument("--auto", action="store_true", help="Continue auto-run after resume")
    p_resume.add_argument(
        "--allow-auto-continue",
        action="store_true",
        help="Override manual approval mode and allow --auto to continue after decision",
    )

    p_tpl = sub.add_parser("decision-template", help="Generate decision.json template from pending gate")
    p_tpl.add_argument("--action", choices=["approve", "reject", "goto_stage", "run_stage8"], help="Decision action")
    p_tpl.add_argument("--next-stage", type=int, choices=list(STAGES), help="Used when action=goto_stage")

    p_decide = sub.add_parser("decide", help="Standardized decision command (write decision.json)")
    action_group = p_decide.add_mutually_exclusive_group(required=True)
    action_group.add_argument("--approve", action="store_true", help="Approve pending gate")
    action_group.add_argument("--reject", action="store_true", help="Reject pending gate")
    action_group.add_argument("--run-stage8", action="store_true", help="Run stage8 from stage7 gate")
    action_group.add_argument("--goto-stage", type=int, choices=list(STAGES), help="Jump to target stage")
    p_decide.add_argument("--reason", help="Decision reason text")
    p_decide.add_argument("--target-ref", help="Optional rollback target ref")
    p_decide.add_argument("--apply", action="store_true", help="Immediately apply decision (call resume)")
    p_decide.add_argument("--auto", action="store_true", help="Use with --apply: continue auto-run after resume")
    p_decide.add_argument(
        "--allow-auto-continue",
        action="store_true",
        help="Override manual approval mode and allow --auto to continue after decision",
    )

    p_recalc = sub.add_parser("recalc-analyze", help="Analyze semantic change -> downstream rerun stages")
    p_recalc.add_argument("--changed", nargs="*", help="Optional changed paths; default from git status")

    p_rb = sub.add_parser("stage8-rollback", help="Auto create rollback branch from stage8 artifact")
    p_rb.add_argument("--target-ref", help="Rollback target ref; default from stage8 artifact")
    p_rb.add_argument("--branch-name", help="Rollback branch name")
    p_rb.add_argument("--apply", action="store_true", help="Apply git switch -c (without this, dry-run only)")

    sub.add_parser("semantic-snapshot", help="Update semantic snapshot baseline")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    scaffold = (ROOT / args.scaffold_dir).resolve()
    paths = Paths(root=ROOT, scaffold=scaffold)

    try:
        if args.command == "init":
            return cmd_init(args, paths)
        if args.command == "status":
            return cmd_status(args, paths)
        if args.command == "gate":
            return cmd_gate(args, paths)
        if args.command == "case-web":
            return cmd_case_web(args, paths)
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
        if args.command == "stage8-rollback":
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
