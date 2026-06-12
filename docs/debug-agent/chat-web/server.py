#!/usr/bin/env python3
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


DEFAULT_ENV_PATH = Path("/home/charles/.hermes/.env")
DEFAULT_SESSIONS_PATH = Path("/home/charles/.hermes/dikong-debug-chat/sessions.json")
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 7860
DEFAULT_HERMES_HOST = "127.0.0.1"
DEFAULT_HERMES_PORT = "8642"
DEFAULT_PROJECT_ROOT = Path("/home/charles/dikong-tech-design")
DEFAULT_DJI_CLOUD_API_DEMO_ROOT = Path("/home/charles/DJI-Cloud-API-Demo")
HERMES_MODEL_NAME = "hermes-agent"
MAX_PROJECT_CONTEXT_BYTES = 24000
PROJECT_SEARCH_LIMIT = 24
CHAT_INTRO_TEXT = """# Dikong Debug Agent"""
CHAT_WARNING_TEXT = "可提供部署账号密码用于本地认证调试。"
SYSTEM_PROMPT_TEMPLATE = """You are the Dikong project debug assistant.

Scope:
- Project root: {project_root}
- Reference implementation root: {dji_cloud_api_demo_root}
- Treat these two roots as cooperating business codebases, not alternatives. Search and compare them together when debugging upstream cloud API behavior.
- Only answer questions about this repository, its frontend/API debug workflows, and the DJI Cloud API Demo reference implementation when it helps debug upstream cloud API behavior.
- Treat this chatbot as project-bound. Explain, diagnose, compare contracts, inspect files/logs, run bounded read-only diagnostic commands, and when the user explicitly supplies deployment credentials, make one-use local authenticated HTTP requests for full-chain debugging.
- If a request is unrelated to this project, asks for general coding help, asks you to operate on another repo, or asks you to modify/deploy/run mutating commands outside the explicit local authenticated HTTP debug flow, say it is out of scope.

Allowed local debug skills:

- frontend-api-debug
- schema-debug
- log-debug
- regression-debug

Behavior:
- Use only evidence from the project context, user-provided request data, and the four debug skills above.
- For DJI 上云 API contract or implementation questions, actively inspect both business roots together: {project_root} and {dji_cloud_api_demo_root}. dikong_code_search and dikong_file_list search/list both by default and return root-qualified paths such as dikong:apps/... and dji:python-backend/....
- Use the project_root tool argument only when narrowing a follow-up read or bash command to one root; otherwise keep both roots available.
- When the current context is insufficient, actively use the Dikong debug tools:
  - dikong_code_search: find project files and line snippets by endpoint, class, field, or error text; use scope='source' when looking for implementation code.
  - dikong_code_read: read a bounded line range from an allowed project file.
  - dikong_graph_query: inspect the local Understand Anything knowledge graph.
  - dikong_log_search: search allowed project logs by request_id, trace_id, log name, or error text.
  - dikong_log_read: read a bounded range or tail from allowed logs such as logs/app.log, logs/error.log, or run/*.log.
  - dikong_file_list: inspect the project filesystem tree without reading file contents.
  - dikong_file_read: read bounded line ranges from allowed project text files and documents.
  - dikong_bash: run bounded read-only diagnostic bash commands in the project root for deeper code/log/debug inspection.
  - dikong_docker_logs: read bounded Docker or Docker Compose logs for local debug evidence.
  - dikong_deployment_scan: inspect local Docker containers and listening TCP ports to discover deployed components and exposed ports.
  - dikong_http_request: make bounded read-only HTTP requests to local project URLs only; use for live deployment checks such as http://127.0.0.1:8000/api/v2/docs/.
  - dikong_authenticated_http_request: when the user explicitly supplies deployment username/password for debugging, perform a one-use login to a local login URL and make one bounded local authenticated HTTP request with the returned Bearer token or cookies; for full-chain debug this may use GET/HEAD/OPTIONS/POST/PUT/PATCH/DELETE with an optional request body.
- Do not modify files, write files, patch code, browse the external web, manage Hermes skills, create cron jobs, or call non-debug tools.
- Do not run mutating shell commands. Use dikong_bash only for read-only diagnostic commands such as rg/find/ls/sed/head/tail/python checks/tests/docker ps/docker logs.
- Do not request external network URLs. Use dikong_http_request and dikong_authenticated_http_request only for localhost/127.0.0.1/0.0.0.0/::1; unauthenticated target requests must use only GET/HEAD/OPTIONS, and authenticated target requests may use GET/HEAD/OPTIONS/POST/PUT/PATCH/DELETE only when the user explicitly supplies credentials for debugging.
- Do not provide fallback implementation advice for out-of-scope requests; refuse briefly and redirect to Dikong project debug questions.
- Do not store access tokens, refresh tokens, passwords, DJI credentials, authorization headers, API keys, or raw secret-bearing logs. If a user provides a deployment password for debugging, use it only in the one-use authenticated tool call and never repeat it.
- Redact any secret-looking value that appears in user text before repeating it.
- Frontend business APIs use /api/v2/*.
- Current schema and implementation outrank prose docs.
- If evidence is incomplete, state exactly what is missing.

Use this structure for frontend/API debug answers:

Conclusion:
Evidence:
Most likely causes:
Next verification:
Frontend action:
Backend/API issue assessment:
Confidence:

Project context gathered from allowed files:
{project_context}
"""


@dataclass(frozen=True)
class ServerConfig:
    api_key: str
    hermes_url: str
    host: str
    port: int
    sessions_path: Path
    project_root: Path = DEFAULT_PROJECT_ROOT


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def specific_session_title(timestamp: str | None = None) -> str:
    if timestamp:
        try:
            created_at = datetime.fromisoformat(timestamp)
        except ValueError:
            created_at = datetime.now(timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)
    return f"调试 {created_at.astimezone().strftime('%m-%d %H:%M:%S')}"


def session_display_title(session: dict[str, Any]) -> str:
    raw_title = str(session.get("title") or "").strip()
    if raw_title and raw_title != "新会话":
        return raw_title
    created_at = session.get("created_at")
    return specific_session_title(created_at if isinstance(created_at, str) else None)


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def env_value(env: dict[str, str], key: str, default: str = "") -> str:
    return os.getenv(key) or env.get(key, default)


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_project_root(raw_path: str, allowed_project_root: Path = DEFAULT_PROJECT_ROOT) -> Path:
    allowed_root = allowed_project_root.expanduser().resolve()
    project_root = Path(raw_path).expanduser().resolve()
    if not project_root.exists() or not project_root.is_dir():
        raise ValueError(f"DEBUG_AGENT_PROJECT_ROOT must be an existing directory: {project_root}")
    if not is_relative_to(project_root, allowed_root):
        raise ValueError(
            "DEBUG_AGENT_PROJECT_ROOT must stay inside the allowed checkout: "
            f"{allowed_root}"
        )
    return project_root


def load_config(
    env_path: Path = DEFAULT_ENV_PATH,
    allowed_project_root: Path = DEFAULT_PROJECT_ROOT,
) -> ServerConfig:
    env = parse_env_file(env_path)
    api_key = env_value(env, "API_SERVER_KEY")
    if not api_key:
        raise RuntimeError(f"API_SERVER_KEY not found in environment or {env_path}")

    hermes_host = env_value(env, "API_SERVER_HOST", DEFAULT_HERMES_HOST)
    hermes_port = env_value(env, "API_SERVER_PORT", DEFAULT_HERMES_PORT)
    host = env_value(env, "CHAT_WEB_HOST", DEFAULT_HOST)
    port = int(env_value(env, "CHAT_WEB_PORT", str(DEFAULT_PORT)))
    sessions_path = Path(env_value(env, "CHAT_WEB_SESSIONS_PATH", str(DEFAULT_SESSIONS_PATH)))
    project_root = resolve_project_root(
        env_value(env, "DEBUG_AGENT_PROJECT_ROOT", str(DEFAULT_PROJECT_ROOT)),
        allowed_project_root,
    )
    hermes_url = f"http://{hermes_host}:{hermes_port}/v1/chat/completions"
    return ServerConfig(
        api_key=api_key,
        hermes_url=hermes_url,
        host=host,
        port=port,
        sessions_path=sessions_path,
        project_root=project_root,
    )


class SessionStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"sessions": {}}
        try:
            data = json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return {"sessions": {}}
        if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
            return {"sessions": {}}
        return data

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2) + "\n")
        tmp.replace(self.path)

    def create_session(self, title: str = "") -> str:
        session_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
        timestamp = now_iso()
        title_lines = (title or "").strip().splitlines()
        clean_title = title_lines[0][:60] if title_lines else ""
        if not clean_title or clean_title == "新会话":
            clean_title = specific_session_title(timestamp)
        self._data["sessions"][session_id] = {
            "title": clean_title,
            "messages": [],
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        self._save()
        return session_id

    def delete_session(self, session_id: str) -> None:
        if session_id in self._data["sessions"]:
            del self._data["sessions"][session_id]
            self._save()

    def rename_session(self, session_id: str, title: str) -> bool:
        session = self._data["sessions"].get(session_id)
        title_lines = (title or "").strip().splitlines()
        clean_title = title_lines[0][:60] if title_lines else ""
        if not isinstance(session, dict) or not clean_title:
            return False
        session["title"] = clean_title
        session["updated_at"] = now_iso()
        self._save()
        return True

    def ensure_session(self, session_id: str | None, title: str = "") -> str:
        if session_id and session_id in self._data["sessions"]:
            return session_id
        return self.create_session(title)

    def list_sessions(self) -> list[tuple[str, str]]:
        sessions = self._data["sessions"]
        items = sorted(
            sessions.items(),
            key=lambda item: item[1].get("updated_at", item[1].get("created_at", "")),
            reverse=True,
        )
        return [
            (session_id, session_display_title(session if isinstance(session, dict) else {}))
            for session_id, session in items
        ]

    def get_messages(self, session_id: str) -> list[dict[str, str]]:
        session = self._data["sessions"].get(session_id)
        if not session:
            return []
        messages = session.get("messages", [])
        if not isinstance(messages, list):
            return []
        return [
            {"role": str(message["role"]), "content": str(message["content"])}
            for message in messages
            if isinstance(message, dict)
            and message.get("role") in {"user", "assistant"}
            and isinstance(message.get("content"), str)
        ]

    def append_message(self, session_id: str, role: str, content: str) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("role must be user or assistant")
        if session_id not in self._data["sessions"]:
            raise KeyError(f"unknown session: {session_id}")
        self._data["sessions"][session_id]["messages"].append({"role": role, "content": content})
        self._data["sessions"][session_id]["updated_at"] = now_iso()
        self._save()


def redact_secret_like_text(text: str) -> str:
    patterns = [
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
        r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password)\s*[:=]\s*)[^\s,;]+",
    ]
    redacted = text
    for pattern in patterns:
        redacted = re.sub(pattern, r"\1<redacted>", redacted)
    return redacted


def should_skip_context_file(path: Path) -> bool:
    skip_parts = {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "node_modules",
        "dist",
        "build",
    }
    if any(part in skip_parts for part in path.parts):
        return True
    if any(part.startswith(".trash-") for part in path.parts):
        return True
    name = path.name
    if name.startswith(".env") or name.endswith((".pyc", ".pyo", ".sqlite3", ".db")):
        return True
    return path.suffix.lower() not in {
        ".py",
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".sh",
        ".html",
        ".js",
        ".ts",
        ".tsx",
        ".css",
        ".sql",
        ".xml",
        ".csv",
    }


def looks_secretish(term: str) -> bool:
    if len(term) > 80:
        return True
    if len(term) >= 24 and re.fullmatch(r"[A-Za-z0-9_\-=.]+", term):
        has_alpha = bool(re.search(r"[A-Za-z]", term))
        has_digit = bool(re.search(r"\d", term))
        return has_alpha and has_digit
    return False


def search_terms(user_text: str) -> list[str]:
    endpoints = re.findall(r"/api/v2/[^\s`'\"，。；;,)）]+", user_text or "")
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_./:-]{2,}|[\u4e00-\u9fff]{2,}", user_text or "")
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "http",
        "https",
        "token",
        "password",
        "secret",
        "authorization",
    }
    terms: list[str] = []
    for raw in [*endpoints, *words]:
        term = raw.strip("`'\".,，。:：;；()（）[]{}")
        if len(term) < 3 or term.lower() in stopwords or looks_secretish(term):
            continue
        if term not in terms:
            terms.append(term)
        if len(terms) >= 8:
            break
    return terms


def understand_summary(project_root: Path) -> str:
    graph_path = project_root / ".understand-anything" / "knowledge-graph.json"
    if not graph_path.exists():
        return ""
    try:
        graph = json.loads(graph_path.read_text())
    except (OSError, json.JSONDecodeError):
        return ""

    lines = ["Understand graph summary:"]
    project = graph.get("project")
    if isinstance(project, dict):
        name = project.get("name") or project.get("title")
        summary = project.get("summary") or project.get("description")
        if name:
            lines.append(f"- Project: {name}")
        if summary:
            lines.append(f"- Summary: {summary}")

    layers = graph.get("layers")
    if isinstance(layers, list) and layers:
        lines.append("- Layers:")
        for layer in layers[:8]:
            if not isinstance(layer, dict):
                continue
            name = layer.get("name") or layer.get("title") or layer.get("id")
            description = layer.get("description") or layer.get("summary")
            if name and description:
                lines.append(f"  - {name}: {description}")

    tour = graph.get("tour")
    if isinstance(tour, list) and tour:
        lines.append("- Tour:")
        for step in tour[:8]:
            if not isinstance(step, dict):
                continue
            title = step.get("title") or step.get("name")
            description = step.get("description") or step.get("summary")
            if title and description:
                lines.append(f"  - {title}: {description}")
    return "\n".join(lines)


def rg_context(user_text: str, project_root: Path, max_matches: int = PROJECT_SEARCH_LIMIT) -> list[str]:
    terms = search_terms(user_text)
    if not terms:
        return []

    matches: list[str] = []
    seen: set[tuple[str, str]] = set()
    rg_base = [
        "rg",
        "-n",
        "--no-heading",
        "--fixed-strings",
        "--ignore-case",
        "--glob",
        "!**/.git/**",
        "--glob",
        "!**/.venv/**",
        "--glob",
        "!**/venv/**",
        "--glob",
        "!**/__pycache__/**",
        "--glob",
        "!**/node_modules/**",
        "--glob",
        "!**/.understand-anything/.trash-*/**",
        "--glob",
        "!**/.env*",
    ]

    for term in terms:
        try:
            result = subprocess.run(
                [*rg_base, term, "."],
                cwd=project_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode not in {0, 1}:
            continue
        for raw_line in result.stdout.splitlines():
            try:
                rel_raw, line_number, content = raw_line.split(":", 2)
            except ValueError:
                continue
            rel_path = Path(rel_raw.lstrip("./"))
            if should_skip_context_file(rel_path):
                continue
            key = (str(rel_path), line_number)
            if key in seen:
                continue
            seen.add(key)
            matches.append(f"{rel_path}:{line_number}: {redact_secret_like_text(content.strip())}")
            if len(matches) >= max_matches:
                return matches
    return matches


def truncate_context(parts: list[str], max_bytes: int) -> str:
    output: list[str] = []
    used = 0
    for part in parts:
        clean = redact_secret_like_text(part.strip())
        if not clean:
            continue
        part_bytes = len(clean.encode("utf-8"))
        if used + part_bytes > max_bytes:
            remaining = max_bytes - used
            if remaining > 200:
                output.append(clean.encode("utf-8")[:remaining].decode("utf-8", errors="ignore"))
            break
        output.append(clean)
        used += part_bytes
    return "\n\n".join(output) if output else "(No matching project context found.)"


def collect_project_context(
    user_text: str,
    project_root: Path,
    max_bytes: int = MAX_PROJECT_CONTEXT_BYTES,
) -> str:
    root = project_root.expanduser().resolve()
    parts: list[str] = []

    summary = understand_summary(root)
    if summary:
        parts.append(summary)

    matches = rg_context(user_text, root)
    if matches:
        parts.append("Search matches from project files:\n" + "\n".join(matches))

    return truncate_context(parts, max_bytes)


def build_system_prompt(project_root: Path, project_context: str = "") -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        project_root=project_root.expanduser().resolve(),
        dji_cloud_api_demo_root=DEFAULT_DJI_CLOUD_API_DEMO_ROOT.expanduser().resolve(),
        project_context=project_context or "(No matching project context found.)",
    )


def build_hermes_payload(
    messages: list[dict[str, str]],
    project_root: Path = DEFAULT_PROJECT_ROOT,
    project_context: str = "",
) -> dict[str, Any]:
    return {
        "model": HERMES_MODEL_NAME,
        "messages": [
            {"role": "system", "content": build_system_prompt(project_root, project_context)},
            *messages,
        ],
        "temperature": 0.2,
    }


def post_json(
    url: str,
    payload: dict[str, Any],
    api_key: str,
    timeout: int = 180,
    session_id: str | None = None,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if session_id:
        headers["X-Hermes-Session-Id"] = session_id
        headers["X-Hermes-Session-Key"] = session_id

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def chatbot_history(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {"role": message["role"], "content": message["content"]}
        for message in messages
        if message["role"] in {"user", "assistant"} and message["content"].strip()
    ]


def extract_upstream_content(upstream: dict[str, Any]) -> str:
    message = upstream.get("choices", [{}])[0].get("message", {})
    content = message.get("content", "")
    return content if isinstance(content, str) and content.strip() else "(empty response)"


def send_message(
    user_text: str,
    session_id: str | None,
    config: ServerConfig,
    store: SessionStore,
) -> tuple[list[dict[str, str]], str, list[tuple[str, str]]]:
    clean_text = (user_text or "").strip()
    if not clean_text:
        active_session = store.ensure_session(session_id)
        return chatbot_history(store.get_messages(active_session)), active_session, store.list_sessions()

    active_session = store.ensure_session(session_id, clean_text)
    messages = store.get_messages(active_session)
    messages.append({"role": "user", "content": clean_text})
    project_context = collect_project_context(clean_text, config.project_root)

    upstream = post_json(
        config.hermes_url,
        build_hermes_payload(messages, config.project_root, project_context),
        config.api_key,
        session_id=active_session,
    )
    answer = extract_upstream_content(upstream)

    store.append_message(active_session, "user", clean_text)
    store.append_message(active_session, "assistant", answer)
    return chatbot_history(store.get_messages(active_session)), active_session, store.list_sessions()


def load_session(
    session_id: str | None,
    store: SessionStore,
) -> tuple[list[dict[str, str]], str | None, list[tuple[str, str]]]:
    if not session_id:
        return [], None, store.list_sessions()
    return chatbot_history(store.get_messages(session_id)), session_id, store.list_sessions()


def new_session(store: SessionStore) -> tuple[list[dict[str, str]], str, list[tuple[str, str]]]:
    session_id = store.create_session()
    return [], session_id, store.list_sessions()


def delete_session(
    session_id: str | None,
    store: SessionStore,
) -> tuple[list[dict[str, str]], str | None, list[tuple[str, str]]]:
    if session_id:
        store.delete_session(session_id)
    choices = store.list_sessions()
    next_session = choices[0][0] if choices else None
    history = chatbot_history(store.get_messages(next_session)) if next_session else []
    return history, next_session, choices


def dropdown_choices(sessions: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(title, session_id) for session_id, title in sessions]


def streamlit_session_label_lookup(sessions: list[tuple[str, str]]) -> dict[str, str]:
    return {session_id: title for session_id, title in sessions}


def resolve_active_session_id(
    session_id: str | None,
    sessions: list[tuple[str, str]],
) -> str | None:
    session_ids = [existing_id for existing_id, _ in sessions]
    if session_id in session_ids:
        return session_id
    return session_ids[0] if session_ids else None


STREAMLIT_LAYOUT_CSS = """
<style>
:root {
  --dk-bg: #f7f8fb;
  --dk-sidebar: #f1f3f8;
  --dk-panel: #ffffff;
  --dk-border: #dfe4ee;
  --dk-text: #111827;
  --dk-muted: #667085;
  --dk-blue: #4d6bfe;
  --dk-blue-hover: #3955df;
}

.stApp {
  background: var(--dk-bg);
  color: var(--dk-text);
}

[data-testid="stSidebar"] {
  background: var(--dk-sidebar);
  border-right: 1px solid var(--dk-border);
}

[data-testid="stDecoration"],
footer {
  display: none;
}

header[data-testid="stHeader"] {
  background: transparent;
}

[data-testid="stStatusWidget"],
[data-testid="stToolbarActions"],
[data-testid="stAppDeployButton"],
[data-testid="stMainMenu"] {
  display: none;
}

[data-testid="stSidebar"] > div {
  padding: 1.25rem 0.875rem;
}

.block-container {
  max-width: 920px;
  padding: 3rem 2.5rem 7rem;
}

.dk-brand {
  margin: 0.25rem 0 1.1rem;
}

.dk-brand-title {
  color: var(--dk-text);
  font-size: 1.05rem;
  font-weight: 720;
  line-height: 1.2;
}

.dk-brand-subtitle,
.dk-muted {
  color: var(--dk-muted);
  font-size: 0.82rem;
  line-height: 1.45;
}

.dk-main-title {
  margin: 0 0 0.35rem;
  text-align: center;
  color: var(--dk-text);
  font-size: 2rem;
  font-weight: 760;
  line-height: 1.15;
}

.dk-main-subtitle {
  margin: 0 auto 2.2rem;
  max-width: 44rem;
  text-align: center;
  color: var(--dk-muted);
  font-size: 0.95rem;
  line-height: 1.6;
}

.dk-empty {
  margin: 7rem auto 0;
  max-width: 36rem;
  text-align: center;
  color: var(--dk-muted);
  font-size: 0.95rem;
  line-height: 1.7;
}

[data-testid="stSidebar"] .stButton button {
  height: 2.55rem;
  border-radius: 0.85rem;
  border: 1px solid var(--dk-border);
  background: var(--dk-panel);
  color: var(--dk-text);
  font-weight: 650;
  box-shadow: none;
}

[data-testid="stSidebar"] .stButton button:hover {
  border-color: #c8d2ff;
  color: var(--dk-blue);
}

[data-testid="stSidebar"] [data-testid="stSelectbox"] label {
  color: var(--dk-muted);
  font-size: 0.78rem;
  font-weight: 650;
}

[data-testid="stChatMessage"] {
  padding: 0.65rem 0;
}

[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {
  line-height: 1.7;
}

[data-testid="stChatInput"] {
  max-width: 920px;
  margin: 0 auto;
}

[data-testid="stChatInput"] textarea {
  border-radius: 1.4rem;
  border-color: var(--dk-border);
  background: var(--dk-panel);
}

[data-testid="stChatInput"] textarea:focus {
  border-color: var(--dk-blue);
  box-shadow: 0 0 0 1px var(--dk-blue);
}

button[kind="primary"] {
  background: var(--dk-blue);
}

button[kind="primary"]:hover {
  background: var(--dk-blue-hover);
}
</style>
"""


def render_streamlit_app(config: ServerConfig, store: SessionStore) -> None:
    import streamlit as st

    st.set_page_config(
        page_title="Dikong Debug Agent",
        page_icon=None,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(STREAMLIT_LAYOUT_CSS, unsafe_allow_html=True)

    sessions = store.list_sessions()
    active_session = resolve_active_session_id(st.session_state.get("active_session_id"), sessions)
    st.session_state["active_session_id"] = active_session

    with st.sidebar:
        st.markdown(
            """
            <div class="dk-brand">
              <div class="dk-brand-title">Dikong Debug</div>
              <div class="dk-brand-subtitle">项目代码、日志和本地部署联调</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        new_col, delete_col = st.columns([1.15, 1])
        if new_col.button("新对话", use_container_width=True):
            _, active_session, _ = new_session(store)
            st.session_state["active_session_id"] = active_session
            st.rerun()
        if delete_col.button("删除当前", disabled=active_session is None, use_container_width=True):
            _, active_session, _ = delete_session(active_session, store)
            st.session_state["active_session_id"] = active_session
            st.rerun()

        sessions = store.list_sessions()
        active_session = resolve_active_session_id(st.session_state.get("active_session_id"), sessions)
        st.session_state["active_session_id"] = active_session
        labels = streamlit_session_label_lookup(sessions)

        if sessions:
            options = [session_id for session_id, _ in sessions]
            index = options.index(active_session) if active_session in options else 0
            selected_session = st.selectbox(
                "会话",
                options,
                index=index,
                format_func=lambda session_id: labels.get(session_id, session_id),
            )
            if selected_session != active_session:
                st.session_state["active_session_id"] = selected_session
                st.rerun()

            active_session = selected_session
            current_title = labels.get(active_session, "")
            rename_title = st.text_input(
                "重命名",
                value=current_title,
                max_chars=60,
                key=f"rename_title_{active_session}",
            )
            if st.button("保存名称", use_container_width=True):
                if store.rename_session(active_session, rename_title):
                    st.rerun()
                else:
                    st.warning("请输入非空会话名。")
        else:
            st.markdown('<div class="dk-muted">还没有会话，直接输入问题即可开始。</div>', unsafe_allow_html=True)

        st.markdown("---")
        st.caption(CHAT_WARNING_TEXT)

    st.markdown(
        """
        <h1 class="dk-main-title">Dikong Debug Agent</h1>
        <p class="dk-main-subtitle">
          输入接口现象、日志片段、traceId、X-Request-ID 或需要排查的问题。
        </p>
        """,
        unsafe_allow_html=True,
    )

    history = chatbot_history(store.get_messages(active_session)) if active_session else []
    if not history:
        st.markdown(
            """
            <div class="dk-empty">
              当前会话已准备好。Hermes 会按需检索 Dikong 和 DJI Cloud API Demo 的代码、
              日志、本地 Docker 和部署入口来辅助排查。
            </div>
            """,
            unsafe_allow_html=True,
        )

    for message in history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    user_text = st.chat_input("输入接口现象、日志片段、traceId 或需要排查的问题...")
    if not user_text:
        return

    try:
        with st.spinner("Hermes 正在检索代码、日志和部署状态..."):
            _, active_session, _ = send_message(user_text, active_session, config, store)
    except (OSError, RuntimeError, ValueError, KeyError, urllib.error.URLError, TimeoutError) as exc:
        st.error(f"Hermes 请求失败：{exc}")
        return

    st.session_state["active_session_id"] = active_session
    st.rerun()


def main() -> None:
    config = load_config()
    store = SessionStore(config.sessions_path)
    render_streamlit_app(config, store)


if __name__ == "__main__":
    main()
