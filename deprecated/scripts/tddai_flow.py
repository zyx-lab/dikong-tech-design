#!/usr/bin/env python3
"""TDD + AI workflow scaffold for dikong-tech-design.

Implements the requested skills pipeline:
- Seed skill
- Skill 0~6
- Search mode / Development mode
- Commit gating rules
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RUNTIME_ROOT = PROJECT_ROOT / ".tdd_ai"
ARTIFACTS_ROOT = RUNTIME_ROOT / "artifacts"
COMMIT_LOG_ROOT = RUNTIME_ROOT / "commit_logs"
SCAFFOLD_ROOT = PROJECT_ROOT / "tests_scaffold"
GENERATED_ROOT = SCAFFOLD_ROOT / "generated"

STATE_FILE = RUNTIME_ROOT / "state.json"
CONFIG_FILE = RUNTIME_ROOT / "config.json"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def mkdirs() -> None:
    for path in [RUNTIME_ROOT, ARTIFACTS_ROOT, COMMIT_LOG_ROOT, GENERATED_ROOT]:
        path.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as file_handle:
        return json.load(file_handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, ensure_ascii=False, indent=2)


def append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file_handle:
        file_handle.write(text)


def default_config() -> Dict[str, Any]:
    return {
        "project": "dikong-tech-design",
        "modes": ["development", "search"],
        "permission_case_constraints": {
            "description": "Use permission APIs only for create user and assign business roles.",
            "allowed_ops": [
                {"path_pattern": r"^/internal/auth/users/?$", "method": "POST", "purpose": "create_user"},
                {
                    "path_pattern": r"^/internal/auth/staff-types/.+/groups/?$",
                    "method": "POST",
                    "purpose": "assign_business_role",
                },
            ],
        },
        "required_status_coverage": [
            {"code": 200, "name": "success"},
            {"code": 400, "name": "invalid_params"},
            {"code": 401, "name": "unauthenticated"},
            {"code": 403, "name": "forbidden"},
            {"code": 404, "name": "not_found"},
            {"code": 409, "name": "state_conflict_or_idempotency"},
        ],
    }


def default_state() -> Dict[str, Any]:
    return {
        "mode": "development",
        "updated_at": now_iso(),
        "pipeline_version": 1,
        "last_test_passed": None,
        "last_run": None,
    }


def init_scaffold() -> None:
    mkdirs()

    if not CONFIG_FILE.exists():
        write_json(CONFIG_FILE, default_config())
    if not STATE_FILE.exists():
        write_json(STATE_FILE, default_state())

    changelog = SCAFFOLD_ROOT / "CHANGELOG.md"
    if not changelog.exists():
        changelog.write_text("# TDD-AI Scaffold Changelog\n\n", encoding="utf-8")

    readme = SCAFFOLD_ROOT / "README.md"
    if not readme.exists():
        readme.write_text(
            "# TDD + AI 测试脚手架\n\n"
            "- 入口脚本：`scripts/tddai_flow.py`\n"
            "- 运行模式：`development` / `search`\n"
            "- 产物目录：`.tdd_ai/artifacts/` + `tests_scaffold/generated/`\n"
            "- 技能流：Seed -> 0 -> 1 -> 2 -> 3 -> 4 -> 5 (+6 决策)\n",
            encoding="utf-8",
        )

    template = SCAFFOLD_ROOT / "templates" / "case.template.json"
    template.parent.mkdir(parents=True, exist_ok=True)
    if not template.exists():
        template.write_text(
            json.dumps(
                {
                    "case_id": "CASE-XXX",
                    "event_id": "EVT-XXX",
                    "mode": "development|search",
                    "actor_role": "dispatcher",
                    "request": {"method": "POST", "path": "/api/v1/example", "params": {}},
                    "expected_status": 200,
                    "notes": "Fill with concrete expectations.",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def load_state() -> Dict[str, Any]:
    return read_json(STATE_FILE, default_state())


def save_state(state: Dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    write_json(STATE_FILE, state)


def set_mode(mode: str) -> Dict[str, Any]:
    state = load_state()
    state["mode"] = mode
    save_state(state)
    return state


def _normalize_path(raw: str) -> str:
    route = raw.strip()
    route = route.lstrip("^")
    if not route.startswith("/"):
        route = "/" + route
    route = route.replace("$", "")
    route = re.sub(r"//+", "/", route)
    return route


def _infer_methods(pattern_obj: Any) -> List[str]:
    callback = getattr(pattern_obj, "callback", None)
    if callback is None:
        return ["GET"]

    if hasattr(callback, "actions") and isinstance(callback.actions, dict):
        methods = sorted({m.upper() for m in callback.actions.keys()})
        return methods or ["GET"]

    view_cls = getattr(callback, "cls", None)
    if view_cls is not None:
        names = getattr(view_cls, "http_method_names", []) or []
        methods = [m.upper() for m in names if m.lower() not in {"options", "head", "trace"}]
        if methods:
            return sorted(set(methods))

    return ["GET"]


def collect_api_inventory() -> Dict[str, Any]:
    try:
        import django  # type: ignore
        from django.urls import URLPattern, URLResolver, get_resolver  # type: ignore
    except Exception as exc:
        raise RuntimeError("Django import failed. Activate project venv first.") from exc

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()

    resolver = get_resolver()
    endpoints: List[Dict[str, Any]] = []

    def walk(patterns: List[Any], prefix: str = "") -> None:
        for item in patterns:
            if isinstance(item, URLResolver):
                walk(item.url_patterns, prefix + str(item.pattern))
            elif isinstance(item, URLPattern):
                raw_path = prefix + str(item.pattern)
                path = _normalize_path(raw_path)
                if not (path.startswith("/api/") or path.startswith("/internal/")):
                    continue
                endpoints.append(
                    {
                        "path": path,
                        "methods": _infer_methods(item),
                        "name": item.name,
                    }
                )

    walk(resolver.url_patterns)

    # Deduplicate by path + methods
    uniq: Dict[Tuple[str, Tuple[str, ...]], Dict[str, Any]] = {}
    for endpoint in endpoints:
        key = (endpoint["path"], tuple(sorted(endpoint["methods"])))
        uniq[key] = endpoint

    cleaned = sorted(uniq.values(), key=lambda x: (x["path"], ",".join(x["methods"])))
    payload = {"generated_at": now_iso(), "endpoints": cleaned}
    return payload


def diff_inventories(prev: Dict[str, Any], cur: Dict[str, Any]) -> Dict[str, Any]:
    def as_map(data: Dict[str, Any]) -> Dict[str, List[str]]:
        result = {}
        for endpoint in data.get("endpoints", []):
            result[endpoint["path"]] = sorted(endpoint["methods"])
        return result

    prev_map = as_map(prev)
    cur_map = as_map(cur)

    added = []
    removed = []
    changed = []

    for path, methods in cur_map.items():
        if path not in prev_map:
            added.append({"path": path, "methods": methods})
        elif methods != prev_map[path]:
            changed.append({"path": path, "old_methods": prev_map[path], "new_methods": methods})

    for path, methods in prev_map.items():
        if path not in cur_map:
            removed.append({"path": path, "methods": methods})

    return {
        "generated_at": now_iso(),
        "added": sorted(added, key=lambda x: x["path"]),
        "removed": sorted(removed, key=lambda x: x["path"]),
        "changed": sorted(changed, key=lambda x: x["path"]),
    }


def run_skill_seed(semantic_file: Path) -> Dict[str, Any]:
    semantic_text = semantic_file.read_text(encoding="utf-8") if semantic_file.exists() else ""
    inventory = read_json(ARTIFACTS_ROOT / "api_inventory" / "latest.json", {"endpoints": []})
    paths = {endpoint["path"] for endpoint in inventory.get("endpoints", [])}

    candidates = []

    # Conservative API suggestions based on current business docs and TODO in README.
    if not any(path.startswith("/api/v1/routes") for path in paths):
        candidates.append(
            {
                "domain": "route",
                "api": "/api/v1/routes",
                "methods": ["GET", "POST"],
                "reason": "Route domain appears in roadmap but is missing in current API inventory.",
            }
        )
    if not any(path.startswith("/api/v1/waypoints") for path in paths):
        candidates.append(
            {
                "domain": "waypoint",
                "api": "/api/v1/waypoints",
                "methods": ["GET", "POST"],
                "reason": "Waypoint domain appears in roadmap but is missing in current API inventory.",
            }
        )
    if not any(path.startswith("/api/v1/missions") for path in paths):
        candidates.append(
            {
                "domain": "mission",
                "api": "/api/v1/missions",
                "methods": ["GET", "POST", "PATCH"],
                "reason": "Mission domain in business semantics requires task lifecycle APIs.",
            }
        )

    status_codes = default_config()["required_status_coverage"]
    for item in candidates:
        item["status_code_scheme"] = status_codes

    payload = {
        "generated_at": now_iso(),
        "semantic_file": str(semantic_file),
        "semantic_hint": semantic_text[:500],
        "candidates": candidates,
        "strategy": "conservative_incremental",
    }
    return payload


def run_skill_events() -> Dict[str, Any]:
    diff = read_json(ARTIFACTS_ROOT / "api_diff" / "latest.json", {})
    seed = read_json(ARTIFACTS_ROOT / "seed" / "latest.json", {})
    inventory = read_json(ARTIFACTS_ROOT / "api_inventory" / "latest.json", {"endpoints": []})

    new_apis = [item["path"] for item in diff.get("added", [])]
    if not new_apis:
        new_apis = [item["api"] for item in seed.get("candidates", [])]

    old_apis = [item["path"] for item in inventory.get("endpoints", []) if item["path"] not in set(new_apis)]

    events = []
    event_id = 1

    for api in new_apis:
        for event_type, status in [
            ("happy_path", 200),
            ("unauthenticated", 401),
            ("forbidden", 403),
            ("invalid_params", 400),
        ]:
            events.append(
                {
                    "event_id": f"EVT-{event_id:04d}",
                    "type": event_type,
                    "api": api,
                    "expected_status": status,
                }
            )
            event_id += 1

        for old_api in old_apis[:3]:
            events.append(
                {
                    "event_id": f"EVT-{event_id:04d}",
                    "type": "new_old_combination",
                    "api": api,
                    "related_old_api": old_api,
                    "expected_status": 200,
                }
            )
            event_id += 1

    payload = {"generated_at": now_iso(), "events": events}
    return payload


def _permission_op_allowed(path: str, method: str, config: Dict[str, Any]) -> bool:
    rules = config["permission_case_constraints"]["allowed_ops"]
    for rule in rules:
        if rule["method"].upper() != method.upper():
            continue
        if re.match(rule["path_pattern"], path):
            return True
    return False


def run_skill_flows() -> Dict[str, Any]:
    config = read_json(CONFIG_FILE, default_config())
    events = read_json(ARTIFACTS_ROOT / "events" / "latest.json", {"events": []})["events"]

    flows = []
    flow_id = 1

    for event in events:
        path = event["api"]
        is_permission_api = path.startswith("/internal/auth/")

        # Permission API case-generation constraint
        if is_permission_api and not _permission_op_allowed(path, "POST", config):
            continue

        actor = "business_admin" if is_permission_api else "dispatcher"

        role_flow = {
            "flow_id": f"FLOW-{flow_id:04d}",
            "event_id": event["event_id"],
            "view": "role",
            "actor_role": actor,
            "steps": [
                {
                    "request": {"method": "POST", "path": path, "params": {"example": "value"}},
                    "expected_status": event["expected_status"],
                }
            ],
        }
        flow_id += 1

        business_flow = {
            "flow_id": f"FLOW-{flow_id:04d}",
            "event_id": event["event_id"],
            "view": "business",
            "actor_role": "dispatcher",
            "steps": [
                {
                    "request": {"method": "POST", "path": path, "params": {"example": "value"}},
                    "expected_status": event["expected_status"],
                    "status_note": "must include auth/permission/error coverage",
                }
            ],
        }
        flow_id += 1

        flows.extend([role_flow, business_flow])

    payload = {"generated_at": now_iso(), "flows": flows}
    return payload


def run_skill_cases() -> Dict[str, Any]:
    state = load_state()
    flows = read_json(ARTIFACTS_ROOT / "flows" / "latest.json", {"flows": []})["flows"]

    cases = []
    for idx, flow in enumerate(flows, start=1):
        step = flow["steps"][0]
        case = {
            "case_id": f"CASE-{idx:04d}",
            "event_id": flow["event_id"],
            "flow_id": flow["flow_id"],
            "mode": state["mode"],
            "actor_role": flow["actor_role"],
            "request": step["request"],
            "expected_status": step["expected_status"],
        }
        cases.append(case)

    payload = {"generated_at": now_iso(), "cases": cases}

    # Generate lightweight validation test file.
    GENERATED_ROOT.mkdir(parents=True, exist_ok=True)
    test_file = GENERATED_ROOT / "test_generated_specs.py"
    test_file.write_text(
        "import json\n"
        "import unittest\n"
        "from pathlib import Path\n"
        "\n"
        "class GeneratedCaseSpecTests(unittest.TestCase):\n"
        "    def test_generated_cases_have_required_fields(self):\n"
        "        path = Path('.tdd_ai/artifacts/cases/latest.json')\n"
        "        self.assertTrue(path.exists(), 'cases artifact missing')\n"
        "        payload = json.loads(path.read_text(encoding='utf-8'))\n"
        "        required = {'case_id', 'event_id', 'flow_id', 'request', 'expected_status'}\n"
        "        for case in payload.get('cases', []):\n"
        "            self.assertTrue(required.issubset(case.keys()))\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
        encoding="utf-8",
    )

    return payload


def run_history_tests() -> Dict[str, Any]:
    started = now_iso()

    cmd_main = [sys.executable, "manage.py", "test"]
    main = subprocess.run(cmd_main, cwd=PROJECT_ROOT, capture_output=True, text=True)

    cmd_generated = [sys.executable, "-m", "unittest", "discover", "-s", "tests_scaffold/generated", "-p", "test_*.py"]
    generated = subprocess.run(cmd_generated, cwd=PROJECT_ROOT, capture_output=True, text=True)

    passed = (main.returncode == 0) and (generated.returncode == 0)

    payload = {
        "started_at": started,
        "finished_at": now_iso(),
        "passed": passed,
        "main": {
            "cmd": " ".join(cmd_main),
            "returncode": main.returncode,
            "stdout": main.stdout[-6000:],
            "stderr": main.stderr[-6000:],
        },
        "generated": {
            "cmd": " ".join(cmd_generated),
            "returncode": generated.returncode,
            "stdout": generated.stdout[-6000:],
            "stderr": generated.stderr[-6000:],
        },
    }
    return payload


def run_skill_changelog(note: str, recorder_skill: str) -> str:
    state = load_state()
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    line = (
        f"## {run_id}\n"
        f"- mode: `{state['mode']}`\n"
        f"- recorder_skill: `{recorder_skill}`\n"
        f"- note: {note}\n"
    )
    append_text(SCAFFOLD_ROOT / "CHANGELOG.md", line + "\n")
    return run_id


def run_skill_decide(semantic_changed: bool, new_feature: bool) -> Dict[str, Any]:
    test_result = read_json(ARTIFACTS_ROOT / "test_runs" / "latest.json", {"passed": None})
    api_diff = read_json(ARTIFACTS_ROOT / "api_diff" / "latest.json", {"added": [], "changed": [], "removed": []})

    fail = test_result.get("passed") is False
    changed_count = len(api_diff.get("changed", [])) + len(api_diff.get("removed", []))

    if fail:
        decision = "rollback"
        reason = "Regression detected in historical or generated tests."
    elif semantic_changed and changed_count >= 3:
        decision = "rollback"
        reason = "Semantic changes with broad API impact; safer to rollback to stable commit."
    elif semantic_changed or new_feature:
        decision = "continue"
        reason = "Changes are incremental and tests pass; continue with constrained scope."
    else:
        decision = "continue"
        reason = "No risky signal found."

    return {
        "generated_at": now_iso(),
        "decision": decision,
        "reason": reason,
        "signals": {
            "semantic_changed": semantic_changed,
            "new_feature": new_feature,
            "tests_passed": test_result.get("passed"),
            "api_change_count": changed_count,
        },
    }


def commit_state(message: str, recorder_skill: str, approved_by: str, use_git: bool) -> Dict[str, Any]:
    state = load_state()
    mode = state["mode"]

    if mode == "development" and not approved_by:
        raise RuntimeError("development mode requires --approved-by before commit")

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_file = COMMIT_LOG_ROOT / f"{run_id}.md"
    log_file.write_text(
        "# State Commit Log\n\n"
        f"- run_id: `{run_id}`\n"
        f"- mode: `{mode}`\n"
        f"- message: {message}\n"
        f"- recorder_skill: `{recorder_skill}`\n"
        f"- approved_by: `{approved_by or 'N/A (search mode allowed)'}`\n"
        f"- generated_at: `{now_iso()}`\n",
        encoding="utf-8",
    )

    git_info = {"committed": False, "message": "dry-run"}
    if use_git:
        commit_message = f"[tddai:{mode}] {message}"
        subprocess.run(["git", "add", ".tdd_ai", "tests_scaffold", "note.md"], cwd=PROJECT_ROOT, check=True)
        subprocess.run(["git", "commit", "-m", commit_message], cwd=PROJECT_ROOT, check=True)
        git_info = {"committed": True, "message": commit_message}

    return {
        "run_id": run_id,
        "mode": mode,
        "log_file": str(log_file.relative_to(PROJECT_ROOT)),
        "git": git_info,
    }


def _save_artifact(group: str, payload: Dict[str, Any]) -> Path:
    group_dir = ARTIFACTS_ROOT / group
    group_dir.mkdir(parents=True, exist_ok=True)
    latest = group_dir / "latest.json"
    stamped = group_dir / (dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    write_json(latest, payload)
    write_json(stamped, payload)
    return latest


def run_pipeline(semantic_file: Path, run_tests: bool, note: str, recorder_skill: str) -> Dict[str, Any]:
    init_scaffold()

    inv = collect_api_inventory()
    prev = read_json(ARTIFACTS_ROOT / "api_inventory" / "latest.json", {"endpoints": []})
    diff = diff_inventories(prev, inv)

    _save_artifact("api_inventory", inv)
    _save_artifact("api_diff", diff)

    seed = run_skill_seed(semantic_file)
    _save_artifact("seed", seed)

    events = run_skill_events()
    _save_artifact("events", events)

    flows = run_skill_flows()
    _save_artifact("flows", flows)

    cases = run_skill_cases()
    _save_artifact("cases", cases)

    test_payload = {"passed": None, "skipped": True}
    if run_tests:
        test_payload = run_history_tests()
    _save_artifact("test_runs", test_payload)

    run_id = run_skill_changelog(note=note, recorder_skill=recorder_skill)

    state = load_state()
    state["last_run"] = {
        "run_id": run_id,
        "at": now_iso(),
        "tests_passed": test_payload.get("passed"),
    }
    state["last_test_passed"] = test_payload.get("passed")
    save_state(state)

    return {
        "run_id": run_id,
        "mode": state["mode"],
        "api_added": len(diff.get("added", [])),
        "events": len(events.get("events", [])),
        "flows": len(flows.get("flows", [])),
        "cases": len(cases.get("cases", [])),
        "tests_passed": test_payload.get("passed"),
        "tests_skipped": test_payload.get("skipped", False),
    }


def cmd_init(_: argparse.Namespace) -> None:
    init_scaffold()
    print("initialized")


def cmd_mode(args: argparse.Namespace) -> None:
    state = set_mode(args.set)
    print(json.dumps(state, ensure_ascii=False, indent=2))


def cmd_status(_: argparse.Namespace) -> None:
    init_scaffold()
    print(json.dumps(load_state(), ensure_ascii=False, indent=2))


def cmd_inventory(_: argparse.Namespace) -> None:
    init_scaffold()
    cur = collect_api_inventory()
    prev = read_json(ARTIFACTS_ROOT / "api_inventory" / "latest.json", {"endpoints": []})
    diff = diff_inventories(prev, cur)
    _save_artifact("api_inventory", cur)
    _save_artifact("api_diff", diff)
    print(json.dumps({"endpoints": len(cur["endpoints"]), **{k: len(diff[k]) for k in ["added", "removed", "changed"]}}, ensure_ascii=False, indent=2))


def cmd_seed(args: argparse.Namespace) -> None:
    init_scaffold()
    payload = run_skill_seed(Path(args.semantic_file))
    _save_artifact("seed", payload)
    print(json.dumps({"candidates": len(payload.get("candidates", []))}, ensure_ascii=False, indent=2))


def cmd_events(_: argparse.Namespace) -> None:
    init_scaffold()
    payload = run_skill_events()
    _save_artifact("events", payload)
    print(json.dumps({"events": len(payload.get("events", []))}, ensure_ascii=False, indent=2))


def cmd_flows(_: argparse.Namespace) -> None:
    init_scaffold()
    payload = run_skill_flows()
    _save_artifact("flows", payload)
    print(json.dumps({"flows": len(payload.get("flows", []))}, ensure_ascii=False, indent=2))


def cmd_cases(_: argparse.Namespace) -> None:
    init_scaffold()
    payload = run_skill_cases()
    _save_artifact("cases", payload)
    print(json.dumps({"cases": len(payload.get("cases", []))}, ensure_ascii=False, indent=2))


def cmd_test(_: argparse.Namespace) -> None:
    init_scaffold()
    payload = run_history_tests()
    _save_artifact("test_runs", payload)
    print(json.dumps({"passed": payload.get("passed")}, ensure_ascii=False, indent=2))
    if not payload.get("passed"):
        raise SystemExit(1)


def cmd_record(args: argparse.Namespace) -> None:
    init_scaffold()
    run_id = run_skill_changelog(note=args.note, recorder_skill=args.recorder_skill)
    print(json.dumps({"run_id": run_id}, ensure_ascii=False, indent=2))


def cmd_decide(args: argparse.Namespace) -> None:
    init_scaffold()
    payload = run_skill_decide(semantic_changed=args.semantic_changed, new_feature=args.new_feature)
    _save_artifact("decision", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_commit(args: argparse.Namespace) -> None:
    init_scaffold()
    payload = commit_state(
        message=args.message,
        recorder_skill=args.recorder_skill,
        approved_by=args.approved_by,
        use_git=args.git,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_pipeline(args: argparse.Namespace) -> None:
    init_scaffold()
    payload = run_pipeline(
        semantic_file=Path(args.semantic_file),
        run_tests=args.run_tests,
        note=args.note,
        recorder_skill=args.recorder_skill,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.run_tests and payload.get("tests_passed") is False:
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TDD + AI skill-flow scaffold")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("mode")
    p.add_argument("--set", choices=["development", "search"], required=True)
    p.set_defaults(func=cmd_mode)

    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("inventory")
    p.set_defaults(func=cmd_inventory)

    p = sub.add_parser("seed")
    p.add_argument("--semantic-file", default="note.md")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("events")
    p.set_defaults(func=cmd_events)

    p = sub.add_parser("flows")
    p.set_defaults(func=cmd_flows)

    p = sub.add_parser("cases")
    p.set_defaults(func=cmd_cases)

    p = sub.add_parser("test")
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("record")
    p.add_argument("--note", required=True)
    p.add_argument("--recorder-skill", default="skill0")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("decide")
    p.add_argument("--semantic-changed", action="store_true")
    p.add_argument("--new-feature", action="store_true")
    p.set_defaults(func=cmd_decide)

    p = sub.add_parser("commit-state")
    p.add_argument("--message", required=True)
    p.add_argument("--recorder-skill", default="skill0")
    p.add_argument("--approved-by", default="")
    p.add_argument("--git", action="store_true", help="actually create git commit")
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("pipeline")
    p.add_argument("--semantic-file", default="note.md")
    p.add_argument("--run-tests", action="store_true")
    p.add_argument("--note", default="pipeline run")
    p.add_argument("--recorder-skill", default="skill0")
    p.set_defaults(func=cmd_pipeline)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
