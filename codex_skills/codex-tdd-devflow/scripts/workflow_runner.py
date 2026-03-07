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
GENERATED_TEST_FILENAME = "test_generated_specs.py"
GENERATED_EVENT_NOTES_FILENAME = "business_events_zh.md"
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
    "business_code_fields": ["business_code", "biz_code", "code", "status_code"],
    "stage4_business_code_strict": False,
    "test_command": [".venv/bin/python", "manage.py", "test"],
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
    3: [
        "business_event_candidates",
        "business_events",
        "focus_api_keys",
        "progress_snapshot",
        "semantic_context",
        "semantic_review",
        "editor_notes",
    ],
    4: [
        "candidate_cases",
        "generated_cases",
        "generated_test_files",
        "coverage_by_event",
        "semantic_review",
        "test_generation",
    ],
    5: ["executed_cases", "failed_cases", "regression_summary"],
    6: ["registry_updates", "docs_updates", "settlement_commit_message"],
    7: ["intervention_summary", "recommendations"],
    8: ["decision", "reasoning", "actions"],
}

HTTP_STATUS_BY_BIZ_CODE = {
    "SUCCESS": 200,
    "INVALID_PARAMS": 400,
    "PERMISSION_DENIED": 403,
    "RESOURCE_NOT_FOUND": 404,
    "STATE_CONFLICT": 409,
    "IDEMPOTENT_DUPLICATE": 409,
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
        fields = ["business_code", "biz_code", "code", "status_code"]
    return dedupe_keep_order(fields)


def stage4_business_code_strict(spec: dict[str, Any]) -> bool:
    return bool(spec.get("stage4_business_code_strict", False))


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


def normalize_api_key(api_key: str) -> str:
    token = " ".join(api_key.strip().split())
    if not token:
        return ""
    parts = token.split(" ", 1)
    if len(parts) == 1:
        return parts[0].upper()
    return f"{parts[0].upper()} {parts[1]}"


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


def pluralize_entity_slug(entity: str) -> str:
    token = entity.strip().lower().replace("_", "-")
    if not token:
        return "todos"
    if token.endswith("y") and len(token) > 1 and token[-2] not in "aeiou":
        return f"{token[:-1]}ies"
    if token.endswith("s"):
        return token
    return f"{token}s"


def normalize_action_slug(raw: str) -> str:
    token = raw.strip().lower().replace("_", "-")
    token = re.sub(r"[^a-z0-9-]+", "-", token).strip("-")
    return token


def stage0_candidate_api_keys(entity: str, actions: list[str]) -> list[str]:
    plural = pluralize_entity_slug(entity)
    base = f"/api/v1/{plural}"
    candidates = [
        f"POST {base}",
        f"PATCH {base}/{{id}}",
        f"PUT {base}/{{id}}",
        f"DELETE {base}/{{id}}",
    ]

    action_slugs: list[str] = []
    for action in actions:
        slug = normalize_action_slug(action)
        if slug:
            action_slugs.append(slug)

    for action in dedupe_keep_order(action_slugs):
        if action in {"view", "create", "update"}:
            continue
        candidates.append(f"POST {base}/{{id}}/{action}")

    return dedupe_keep_order([normalize_api_key(item) for item in candidates if item])


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
            "artifact_summary": summarize_stage_artifact(stage, artifact if isinstance(artifact, dict) else None, paths=paths),
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

    ensure_text(generated_test_file_path(paths), DEFAULT_GENERATED_TEST, force)


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
    resource_entities: list[str] = []
    for resource in resources:
        normalized = normalize_entity_name(str(resource))
        if normalized:
            resource_entities.append(normalized)
    resource_entities = dedupe_keep_order(resource_entities)

    actions = model.get("actions", []) if isinstance(model, dict) else []
    action_tokens: list[str] = []
    if isinstance(actions, list):
        for action in actions:
            token = str(action).strip()
            if token:
                action_tokens.append(token)

    blocking = bool(missing) or bool(missing_dirs)
    semantic_gap_report = {
        "blocking": blocking,
        "missing_fields": missing,
        "missing_semantic_directories": missing_dirs,
    }

    existing_api_keys: set[str] = set()
    if isinstance(api_registry, dict):
        items = api_registry.get("items", [])
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = item.get("api_key")
                if isinstance(key, str):
                    normalized_key = normalize_api_key(key)
                    if normalized_key:
                        existing_api_keys.add(normalized_key)

    candidate_pool: list[dict[str, str]] = []
    for entity in resource_entities:
        for api_key in stage0_candidate_api_keys(entity, action_tokens):
            candidate_pool.append({"entity": entity, "api_key": api_key})

    fresh_candidates: list[dict[str, str]] = []
    skipped_existing = 0
    seen_keys: set[str] = set()
    for item in candidate_pool:
        api_key = normalize_api_key(item["api_key"])
        if not api_key or api_key in seen_keys:
            continue
        seen_keys.add(api_key)
        if api_key in existing_api_keys:
            skipped_existing += 1
            continue
        fresh_candidates.append({"entity": item["entity"], "api_key": api_key})

    selected_candidates: list[dict[str, str]] = []
    api_candidates = []
    if not blocking:
        selected_candidates = fresh_candidates

    max_new_api = int(spec.get("max_new_api_per_iteration", 1))
    selected_candidates = selected_candidates[: max(1, max_new_api)]
    for item in selected_candidates:
        api_candidates.append(
            {
                "api_key": item["api_key"],
                "reason": "由 Stage0 按语义资源自动生成且未在 api_registry 出现，需人工 review",
                "risk": "medium",
            }
        )

    touched_entities = dedupe_keep_order([item["entity"] for item in selected_candidates])

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
        "candidate_pool_size": len(candidate_pool),
        "candidate_skipped_existing": skipped_existing,
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
    if bootstrap_mode:
        # bootstrap 轮次用于按 SKILL 规则对既有 API 做批量初始化校验。
        new_api_keys = sorted(set(scanned_apis))
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


def parse_api_key_parts(api_key: str) -> tuple[str, str]:
    normalized = normalize_api_key(api_key)
    parts = normalized.split(" ", 1)
    if len(parts) != 2:
        return "GET", "/"
    return parts[0].upper(), parts[1]


def stage0_candidate_api_keys_from_artifact(stage0_artifact: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    candidates = stage0_artifact.get("api_candidates", []) if isinstance(stage0_artifact, dict) else []
    if isinstance(candidates, list):
        for item in candidates:
            if not isinstance(item, dict):
                continue
            api_key = item.get("api_key")
            if isinstance(api_key, str):
                normalized = normalize_api_key(api_key)
                if normalized:
                    keys.append(normalized)
    return dedupe_keep_order(keys)


def stage3_focus_api_keys(stage0_artifact: dict[str, Any], stage2_artifact: dict[str, Any]) -> list[str]:
    stage2_keys = stage2_artifact.get("new_api_keys", []) if isinstance(stage2_artifact, dict) else []
    stage2_normalized: list[str] = []
    if isinstance(stage2_keys, list):
        for item in stage2_keys:
            if not isinstance(item, str):
                continue
            normalized = normalize_api_key(item)
            if normalized:
                stage2_normalized.append(normalized)
    stage2_normalized = dedupe_keep_order(stage2_normalized)

    bootstrap_mode = bool(stage2_artifact.get("bootstrap_mode", False)) if isinstance(stage2_artifact, dict) else False
    if bootstrap_mode and stage2_normalized:
        # bootstrap 期望批量初始化既有 API，优先采用 Stage2 扫描全集。
        return stage2_normalized

    keys = stage0_candidate_api_keys_from_artifact(stage0_artifact)
    if keys:
        return keys

    # 非 bootstrap 且 Stage0 无候选时，回退到 Stage2 扫描增量。
    if stage2_normalized:
        return stage2_normalized

    # 最终兜底：空集合（上游会走默认事件）。
    return []


def should_increment_iteration_on_decision(pending_stage: int, next_stage: int, action: str) -> bool:
    if action != "approve":
        return False
    if pending_stage == 6 and next_stage == 0:
        return True
    if pending_stage == 8 and next_stage == 0:
        return True
    return False


def infer_entity_from_api_key(api_key: str, semantic_model: dict[str, Any]) -> str:
    resources = semantic_model.get("resources", []) if isinstance(semantic_model, dict) else []
    available_entities = {
        normalized
        for raw in resources if isinstance(raw, str)
        for normalized in [normalize_entity_name(raw)]
        if normalized
    }
    if not available_entities:
        return ""

    _, raw_path = parse_api_key_parts(api_key)
    segments = [segment for segment in raw_path.split("/") if segment and not segment.startswith("{")]
    candidates: list[str] = []
    for segment in segments:
        token = segment.strip().lower()
        if token in {"api", "v1"} or token in IGNORE_ENTITY_SEGMENTS:
            continue
        normalized = normalize_entity_name(token)
        if normalized:
            candidates.append(normalized)
        if normalized.endswith("ies") and len(normalized) > 3:
            candidates.append(normalized[:-3] + "y")
        if normalized.endswith("s") and len(normalized) > 1:
            candidates.append(normalized[:-1])
        break

    resolved = resolve_entities(candidates, available_entities)
    if resolved:
        return resolved[0]
    return sorted(available_entities)[0]


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


def stage3_event_candidates_for_api(
    api_key: str,
    entity: str,
    semantic_context: dict[str, Any],
    progress_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    method, raw_path = parse_api_key_parts(api_key)
    state_machine = semantic_context.get("state_machine", {}) if isinstance(semantic_context, dict) else {}
    constraints = semantic_context.get("constraints", []) if isinstance(semantic_context, dict) else []
    permissions = semantic_context.get("permission_boundary", {}) if isinstance(semantic_context, dict) else {}

    entity_title = entity or "目标实体"
    has_state_machine = isinstance(state_machine, dict) and bool(state_machine.get("states", []))
    has_path_id = "{" in raw_path and "}" in raw_path
    api_seen = api_key in set(progress_snapshot.get("implemented_candidate_apis", []))
    progress_note = "已在当前扫描中观测到该 API" if api_seen else "当前扫描尚未观测到该 API"

    templates: list[dict[str, Any]] = [
        {
            "title": f"{entity_title}主流程成功",
            "description": f"{progress_note}，验证 {api_key} 在标准输入下返回 SUCCESS 并完成主流程。",
            "expected_business_codes": ["SUCCESS"],
            "reasoning_zh": "主流程成功是上线前最小闭环。",
        },
        {
            "title": f"{entity_title}参数校验异常",
            "description": f"覆盖 {api_key} 的非法输入路径，确认返回 INVALID_PARAMS。",
            "expected_business_codes": ["INVALID_PARAMS"],
            "reasoning_zh": "参数校验是最常见失败路径，必须稳定。",
        },
        {
            "title": f"{entity_title}权限边界校验",
            "description": (
                f"基于权限边界 {permissions or '(未声明)'}，验证 {api_key} 在越权访问时返回 PERMISSION_DENIED。"
            ),
            "expected_business_codes": ["PERMISSION_DENIED"],
            "reasoning_zh": "跨角色访问是业务风险高点，需要独立覆盖。",
        },
    ]

    if has_path_id or method in {"GET", "PUT", "PATCH", "DELETE"}:
        templates.append(
            {
                "title": f"{entity_title}资源不存在",
                "description": f"对 {api_key} 提供不存在资源标识，验证 RESOURCE_NOT_FOUND。",
                "expected_business_codes": ["RESOURCE_NOT_FOUND"],
                "reasoning_zh": "对象级 API 需覆盖不存在资源场景。",
            }
        )

    if has_state_machine or any("不可逆" in str(item) or "ACTIVE" in str(item).upper() for item in constraints):
        state_text = state_machine.get("states", []) if isinstance(state_machine, dict) else []
        templates.append(
            {
                "title": f"{entity_title}状态冲突校验",
                "description": f"结合状态机 {state_text} 与约束 {constraints}，验证非法状态迁移返回 STATE_CONFLICT。",
                "expected_business_codes": ["STATE_CONFLICT"],
                "reasoning_zh": "状态机与业务约束是语义冲突高发点。",
            }
        )

    if method == "POST":
        templates.append(
            {
                "title": f"{entity_title}幂等重复提交",
                "description": f"对 {api_key} 模拟重复请求，验证 IDEMPOTENT_DUPLICATE。",
                "expected_business_codes": ["IDEMPOTENT_DUPLICATE"],
                "reasoning_zh": "创建型接口需明确幂等语义。",
            }
        )

    # 去重，避免语义模板重复。
    deduped: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for item in templates:
        codes = dedupe_keep_order(
            [str(code).strip() for code in item.get("expected_business_codes", []) if str(code).strip()]
        )
        if not codes:
            continue
        key = (str(item.get("title", "")).strip(), ",".join(codes))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        row = dict(item)
        row["expected_business_codes"] = codes
        deduped.append(row)
    return deduped


def stage3(paths: Paths, spec: dict[str, Any]) -> StageResult:
    stage2_artifact = read_json(paths.stage_artifact(2), {})
    stage0_artifact = read_json(paths.stage_artifact(0), {})
    stage1_artifact = read_json(paths.stage_artifact(1), {})
    semantic_model = read_json(paths.semantic_model, DEFAULT_SEMANTIC_MODEL)
    focus_api_keys = stage3_focus_api_keys(
        stage0_artifact if isinstance(stage0_artifact, dict) else {},
        stage2_artifact if isinstance(stage2_artifact, dict) else {},
    )
    progress = stage3_progress_snapshot(
        paths,
        stage0_artifact if isinstance(stage0_artifact, dict) else {},
        stage1_artifact if isinstance(stage1_artifact, dict) else {},
        stage2_artifact if isinstance(stage2_artifact, dict) else {},
    )

    business_event_candidates: list[dict[str, Any]] = []
    seq = 1
    for api_key in focus_api_keys:
        entity = infer_entity_from_api_key(api_key, semantic_model if isinstance(semantic_model, dict) else {})
        semantic_context = stage3_semantic_context_for_entity(entity, semantic_model if isinstance(semantic_model, dict) else {})
        for template in stage3_event_candidates_for_api(api_key, entity, semantic_context, progress):
            business_event_candidates.append(
                {
                    "event_id": f"EVT-{seq:03d}",
                    "title": str(template.get("title", f"业务事件 {seq}")),
                    "api_refs": [api_key],
                    "description": str(template.get("description", "由 Stage3 结合业务语义自动生成")),
                    "expected_business_codes": template.get("expected_business_codes", []),
                    "reasoning_zh": str(template.get("reasoning_zh", "")),
                    "entity": entity,
                }
            )
            seq += 1

    if not business_event_candidates:
        fallback_refs = focus_api_keys[:1]
        business_event_candidates = [
            {
                "event_id": "EVT-001",
                "title": "候选 API 语义回归事件",
                "api_refs": fallback_refs,
                "description": "未识别到可细分事件模板，使用兜底语义事件。",
                "expected_business_codes": ["SUCCESS", "INVALID_PARAMS"],
                "reasoning_zh": "兜底事件用于保证 Stage4 可持续生成可执行测试。",
                "entity": infer_entity_from_api_key(fallback_refs[0], semantic_model) if fallback_refs else "",
            }
        ]

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
        "focus_api_keys": focus_api_keys,
        "progress_snapshot": progress,
        "semantic_context": semantic_context,
        "semantic_review": {
            "status": "pending",
            "reviewer": "",
            "reason": "等待当前 Codex session 结合业务语义审阅测试事件",
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

    ready = bool(expected_tests) and not missing_tests and not placeholder_detected
    reason = "ready_for_stage5" if ready else "waiting_session_codegen"

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
    rejected_case_ids = [case_id for case_id in candidate_ids if case_id not in selected_set]
    test_generation = analyze_stage4_test_generation(paths, generated_cases)

    stage4_artifact["generated_cases"] = generated_cases
    stage4_artifact["generated_test_files"] = [rel_path(paths, generated_test_file_path(paths))]
    stage4_artifact["business_event_files"] = [rel_path(paths, generated_event_notes_path(paths))]
    stage4_artifact["coverage_by_event"] = coverage_by_event
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
        refs = event.get("api_refs", [])
        api_refs = refs if isinstance(refs, list) else []
        api_text = ", ".join(str(item) for item in api_refs) if api_refs else "(未声明)"
        lines.extend(
            [
                f"## {event_id} {title}",
                "",
                f"- 描述：{description}",
                f"- 关联 API：{api_text}",
                "",
            ]
        )

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
            return first
    venv_python = paths.root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return venv_python.as_posix()
    return sys.executable


def run_generated_case_suite(
    paths: Paths,
    generated_cases: list[dict[str, Any]],
    test_command: list[str],
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

    command = [
        resolve_python_from_test_command(paths, test_command),
        "manage.py",
        "test",
        generated_test_label(paths),
        "-v",
        "2",
    ]
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
    observed, failed, skipped, ran_count = parse_generated_test_status(combined)
    missing = [name for name in expected_names if name not in observed]
    failed_case_ids = [case_by_test[name] for name in expected_names if name in failed or name in missing or name in skipped]
    mismatch = ran_count != len(expected_names)

    return {
        "command": command,
        "return_code": return_code,
        "tests_run": ran_count,
        "expected_count": len(expected_names),
        "expected_tests": expected_names,
        "observed_tests": sorted(observed),
        "failed_tests": sorted(failed),
        "skipped_tests": sorted(skipped),
        "missing_tests": missing,
        "failed_cases": failed_case_ids,
        "stdout_tail": "\n".join(stdout.splitlines()[-80:]),
        "stderr_tail": "\n".join(stderr.splitlines()[-80:]),
        "mismatch": mismatch,
        "parse_source": "verbose_status" if observed else "summary_only",
    }


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

    fallback_api_refs: list[str] = []
    if isinstance(stage0_artifact, dict):
        stage0_candidates = stage0_artifact.get("api_candidates", [])
        if isinstance(stage0_candidates, list):
            for item in stage0_candidates:
                if isinstance(item, dict) and isinstance(item.get("api_key"), str):
                    fallback_api_refs.append(item["api_key"])
    fallback_api_refs = dedupe_keep_order(fallback_api_refs)[:1]

    normalized_events: list[dict[str, Any]] = []
    if isinstance(events, list):
        for idx, event in enumerate(events, start=1):
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("event_id", f"EVT-{idx:03d}"))
            refs = event.get("api_refs", [])
            api_refs = [str(item) for item in refs if isinstance(item, str)] if isinstance(refs, list) else []
            if not api_refs:
                api_refs = list(fallback_api_refs)
            title = str(event.get("title", f"业务事件 {event_id}"))
            description = str(event.get("description", "由 Stage4 生成测试时自动补全"))
            event_codes = event.get("expected_business_codes", [])
            if not isinstance(event_codes, list):
                event_codes = []
            expected_codes = dedupe_keep_order([str(item).strip() for item in event_codes if str(item).strip()])
            if not expected_codes:
                expected_codes = list(required_codes)
            normalized_events.append(
                {
                    "event_id": event_id,
                    "api_refs": api_refs,
                    "title": title,
                    "description": description,
                    "expected_business_codes": expected_codes,
                }
            )
    if not normalized_events:
        normalized_events = [
            {
                "event_id": "EVT-001",
                "api_refs": list(fallback_api_refs),
                "title": "默认业务事件",
                "description": "未读取到 Stage3 业务事件，使用默认事件兜底。",
                "expected_business_codes": list(required_codes),
            }
        ]

    state = read_json(paths.state, DEFAULT_STATE)
    running_iteration = int(state.get("running_iteration", 1)) if isinstance(state, dict) else 1

    candidate_cases: list[dict[str, Any]] = []
    case_idx = 1
    for event in normalized_events:
        event_codes = event.get("expected_business_codes", [])
        if not isinstance(event_codes, list):
            event_codes = []
        expected_codes = dedupe_keep_order([str(code).strip() for code in event_codes if str(code).strip()])
        if not expected_codes:
            expected_codes = list(required_codes)
        for code in expected_codes:
            expected_http_status = HTTP_STATUS_BY_BIZ_CODE.get(code, 400)
            candidate_cases.append(
                {
                    "case_id": f"CASE-AUTO-I{running_iteration:03d}-{case_idx:03d}",
                    "title": f"{event['event_id']} 自动生成用例 {code}",
                    "event_id": event["event_id"],
                    "event_title_zh": event["title"],
                    "event_description_zh": event["description"],
                    "api_refs": event["api_refs"],
                    "preconditions": [],
                    "steps": [],
                    "expected_business_code": code,
                    "expected_http_status": expected_http_status,
                    "tags": ["auto", "stage4"],
                    "status": "draft",
                    "owner": "workflow_runner",
                    "updated_at": now_iso(),
                }
            )
            case_idx += 1

    assign_case_test_names(candidate_cases)
    generated_cases = clone_case_rows(candidate_cases)
    _write_case_descriptions(paths, generated_cases)
    _write_business_event_notes(paths, normalized_events)

    coverage_by_event = build_stage4_coverage(normalized_events, required_codes, generated_cases)
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
        "coverage_by_event": coverage_by_event,
        "test_generation": test_generation,
        "semantic_review": {
            "status": "pending",
            "reviewer": "",
            "reason": "等待当前 Codex session 进行语义筛选（可读摘要见 gate 输出）",
            "candidate_count": len(candidate_cases),
            "selected_count": len(generated_cases),
            "rejected_count": 0,
            "selected_case_ids": [str(item.get("case_id", "")) for item in generated_cases if isinstance(item, dict)],
            "rejected_case_ids": [],
            "updated_at": now_iso(),
        },
        "session_codegen_note": (
            "测试代码需由当前 Codex session 生成并写入 generated_test_files。"
            "断言应优先使用业务状态码字段 business_code/biz_code/code/status_code，HTTP 仅作辅助。"
            "Runner 仅校验测试函数命名与覆盖完整性。"
        ),
    }

    return StageResult(stage=4, artifact=artifact, next_stage=5, gate=True)


def stage5(paths: Paths, spec: dict[str, Any], state: dict[str, Any]) -> StageResult:
    test_command = spec.get("test_command", [".venv/bin/python", "manage.py", "test"])
    if not isinstance(test_command, list) or not test_command:
        raise WorkflowError("workflow_spec.test_command 配置非法")

    stage4_artifact = read_json(paths.stage_artifact(4), {})
    generated_cases_raw = stage4_artifact.get("generated_cases", []) if isinstance(stage4_artifact, dict) else []
    generated_cases = [item for item in generated_cases_raw if isinstance(item, dict)]
    generated_suite = run_generated_case_suite(paths, generated_cases, test_command)

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
    return_code = 0 if (regression_return_code == 0 and not generated_suite_failed and not generated_suite_error) else 1
    skipped_tests = [item for item in generated_suite.get("skipped_tests", []) if isinstance(item, str)]
    missing_tests = [item for item in generated_suite.get("missing_tests", []) if isinstance(item, str)]

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
        "regression_summary": {
            "return_code": return_code,
            "command": test_command,
            "regression_return_code": regression_return_code,
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
        f"- touched_entities: {touched_entities}",
    ]
    if bool(stage2_artifact.get("bootstrap_mode", False)):
        summary_lines.append("- bootstrap_mode=true，本轮按扫描结果批量初始化校验。")
    else:
        summary_lines.append("- 本轮候选按增量迭代推进，仍需人工门禁确认。")
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
    generated_cases = stage4_artifact.get("generated_cases", []) if isinstance(stage4_artifact, dict) else []
    if isinstance(generated_cases, list) and generated_cases:
        test_records += "\n## 业务事件（中文）\n\n"
        seen_event_ids: set[str] = set()
        for case in generated_cases:
            if not isinstance(case, dict):
                continue
            event_id = str(case.get("event_id", "EVT-UNKNOWN"))
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)
            event_title = str(case.get("event_title_zh", "未命名业务事件"))
            event_desc = str(case.get("event_description_zh", "无补充说明"))
            api_refs = case.get("api_refs", [])
            api_text = ", ".join(str(item) for item in api_refs) if isinstance(api_refs, list) and api_refs else "(未声明)"
            test_records += f"- {event_id} {event_title}\n"
            test_records += f"  - 描述: {event_desc}\n"
            test_records += f"  - 关联 API: {api_text}\n"

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
        if isinstance(semantic_gap, dict) and semantic_gap.get("blocking"):
            gate_policy = {
                "reason": "stage0_semantic_gap_blocking",
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
    test_generation = analyze_stage4_test_generation(paths, rows)
    if test_generation.get("status") != "ready":
        missing = test_generation.get("missing_tests", [])
        missing_preview = ", ".join(missing[:8]) if isinstance(missing, list) else ""
        if isinstance(missing, list) and len(missing) > 8:
            missing_preview = f"{missing_preview}, ..."
        placeholder = bool(test_generation.get("placeholder_detected", False))
        test_file = str(test_generation.get("test_file", generated_test_file_path(paths)))
        details = []
        if missing_preview:
            details.append(f"missing_tests={missing_preview}")
        if placeholder:
            details.append("detected_placeholder_test=true")
        detail_text = f" ({'; '.join(details)})" if details else ""
        raise WorkflowError(
            "Stage4 测试代码尚未就绪。"
            f"请由当前 Codex session 先更新 `{test_file}` 并覆盖全部 expected_tests 后再 approve{detail_text}。"
        )

    artifact["test_generation"] = test_generation
    write_json(paths.stage_artifact(4), artifact)


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

    clear_pending(paths)
    write_json(paths.decision, DEFAULT_DECISION)

    state["status"] = "idle"
    state["current_stage"] = next_stage
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


def cmd_stage3_semantic(args: argparse.Namespace, paths: Paths) -> int:
    ensure_scaffold(paths, force=False)
    state = load_state(paths)
    pending = read_json(paths.pending, DEFAULT_PENDING)

    if state.get("status") != "waiting_decision" or int(pending.get("stage", -1)) != 3:
        raise WorkflowError("stage3-semantic 仅可在 Stage3 waiting_decision 门禁时执行")

    stage3_artifact = read_json(paths.stage_artifact(3), {})
    if not isinstance(stage3_artifact, dict):
        raise WorkflowError("stage3 artifact 不存在或格式错误")

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
    print(
        json.dumps(
            {
                "status": "ok",
                "stage": 3,
                "artifact_path": rel_path(paths, paths.stage_artifact(3)),
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        )
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
    print(
        json.dumps(
            {
                "status": "ok",
                "stage": 4,
                "artifact_path": rel_path(paths, paths.stage_artifact(4)),
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
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
    p_stage3_semantic = sub.add_parser("stage3-semantic", help="Apply stage3 semantic event selection in current session")
    p_stage3_semantic.add_argument(
        "--keep-event-id",
        action="append",
        default=[],
        help="Event id to keep; can repeat or use comma-separated values",
    )
    p_stage3_semantic.add_argument("--keep-all", action="store_true", help="Keep all stage3 candidate events")
    p_stage3_semantic.add_argument("--max-events", type=int, help="Optional cap after semantic selection")
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
    p_stage4_semantic.add_argument("--reason", help="Semantic review reason")
    p_stage4_semantic.add_argument("--reviewer", help="Semantic reviewer label")
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
        if args.command == "stage3-semantic":
            return cmd_stage3_semantic(args, paths)
        if args.command == "stage4-semantic":
            return cmd_stage4_semantic(args, paths)
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
