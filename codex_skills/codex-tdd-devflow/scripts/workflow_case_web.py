#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

CASE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{3,128}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CaseStore:
    def __init__(self, scaffold_dir: Path):
        self.scaffold_dir = scaffold_dir
        self.case_file = scaffold_dir / "cases" / "case_descriptions.jsonl"
        self.case_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.case_file.exists():
            self.case_file.write_text("", encoding="utf-8")

    def _load(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line in self.case_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
        return rows

    def _save(self, rows: list[dict[str, Any]]) -> None:
        content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
        if content:
            content += "\n"
        self.case_file.write_text(content, encoding="utf-8")

    @staticmethod
    def _validate_payload(payload: dict[str, Any], *, allow_partial: bool = False) -> tuple[bool, str]:
        required = ["case_id", "event_id", "api_refs", "expected_business_code", "status"]
        if not allow_partial:
            for field in required:
                if field not in payload:
                    return False, f"missing field: {field}"

        if "case_id" in payload:
            case_id = payload.get("case_id")
            if not isinstance(case_id, str) or not CASE_ID_RE.match(case_id):
                return False, "invalid case_id"

        if "event_id" in payload:
            event_id = payload.get("event_id")
            if not isinstance(event_id, str) or not event_id.strip():
                return False, "invalid event_id"

        if "api_refs" in payload:
            api_refs = payload.get("api_refs")
            if not isinstance(api_refs, list) or any(not isinstance(item, str) for item in api_refs):
                return False, "invalid api_refs"

        if "expected_business_code" in payload:
            code = payload.get("expected_business_code")
            if not isinstance(code, str) or not code.strip():
                return False, "invalid expected_business_code"

        if "status" in payload:
            status = payload.get("status")
            if status not in {"draft", "approved", "deprecated", "enabled", "disabled"}:
                return False, "invalid status"

        return True, ""

    def list_cases(self) -> list[dict[str, Any]]:
        rows = self._load()
        return sorted(rows, key=lambda row: str(row.get("case_id", "")))

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        for row in self._load():
            if row.get("case_id") == case_id:
                return row
        return None

    def create_case(self, payload: dict[str, Any]) -> dict[str, Any]:
        ok, err = self._validate_payload(payload)
        if not ok:
            raise ValueError(err)

        rows = self._load()
        case_id = payload["case_id"]
        if any(row.get("case_id") == case_id for row in rows):
            raise ValueError("case_id already exists")

        payload = dict(payload)
        payload.setdefault("title", payload["case_id"])
        payload.setdefault("tags", ["web"])
        payload.setdefault("owner", "case-web")
        payload["updated_at"] = now_iso()

        rows.append(payload)
        self._save(rows)
        return payload

    def update_case(self, case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        ok, err = self._validate_payload(payload, allow_partial=True)
        if not ok:
            raise ValueError(err)

        rows = self._load()
        found = None
        for idx, row in enumerate(rows):
            if row.get("case_id") == case_id:
                found = idx
                break
        if found is None:
            raise KeyError("case not found")

        current = dict(rows[found])
        for key, value in payload.items():
            if key == "case_id" and value != case_id:
                raise ValueError("case_id cannot be changed")
            current[key] = value

        ok2, err2 = self._validate_payload(current)
        if not ok2:
            raise ValueError(err2)

        current["updated_at"] = now_iso()
        rows[found] = current
        self._save(rows)
        return current

    def delete_case(self, case_id: str) -> None:
        rows = self._load()
        filtered = [row for row in rows if row.get("case_id") != case_id]
        if len(filtered) == len(rows):
            raise KeyError("case not found")
        self._save(filtered)


class CaseWebHandler(BaseHTTPRequestHandler):
    store: CaseStore

    def _send_json(self, status: int, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("body must be JSON object")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(HTTPStatus.OK, html_page())
            return

        if parsed.path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return

        if parsed.path == "/api/cases":
            self._send_json(HTTPStatus.OK, {"items": self.store.list_cases()})
            return

        if parsed.path.startswith("/api/cases/"):
            case_id = parsed.path.split("/", 3)[3]
            case = self.store.get_case(case_id)
            if not case:
                self._send_json(HTTPStatus.NOT_FOUND, {"detail": "case not found"})
                return
            self._send_json(HTTPStatus.OK, case)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/cases":
            self._send_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return

        try:
            payload = self._read_json_body()
            created = self.store.create_case(payload)
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"detail": str(exc)})
            return

        self._send_json(HTTPStatus.CREATED, created)

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/cases/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return

        case_id = parsed.path.split("/", 3)[3]
        try:
            payload = self._read_json_body()
            updated = self.store.update_case(case_id, payload)
        except KeyError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"detail": str(exc)})
            return
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"detail": str(exc)})
            return

        self._send_json(HTTPStatus.OK, updated)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/cases/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return

        case_id = parsed.path.split("/", 3)[3]
        try:
            self.store.delete_case(case_id)
        except KeyError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"detail": str(exc)})
            return

        self._send_json(HTTPStatus.OK, {"deleted": case_id})


def html_page() -> str:
    return """<!doctype html>
<html lang=\"zh\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Case CRUD</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin: 24px; }
    textarea { width: 100%; height: 180px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    button { margin-right: 8px; }
    .row { margin-top: 10px; }
  </style>
</head>
<body>
  <h1>Case 管理（CRUD）</h1>
  <p>JSON 输入示例：{"case_id":"CASE-DRONE-001","event_id":"EVT-001","api_refs":["GET /api/v1/drones"],"expected_business_code":"SUCCESS","status":"draft"}</p>
  <textarea id=\"payload\"></textarea>
  <div class=\"row\">
    <button onclick=\"createCase()\">创建</button>
    <button onclick=\"refreshCases()\">刷新</button>
  </div>
  <pre id=\"result\"></pre>
  <table>
    <thead><tr><th>case_id</th><th>event_id</th><th>status</th><th>actions</th></tr></thead>
    <tbody id=\"tbody\"></tbody>
  </table>
<script>
async function refreshCases() {
  const res = await fetch('/api/cases');
  const data = await res.json();
  const tbody = document.getElementById('tbody');
  tbody.innerHTML = '';
  for (const item of data.items || []) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${item.case_id || ''}</td><td>${item.event_id || ''}</td><td>${item.status || ''}</td><td>
      <button onclick="loadCase('${item.case_id}')">编辑</button>
      <button onclick="deleteCase('${item.case_id}')">删除</button>
    </td>`;
    tbody.appendChild(tr);
  }
}

async function createCase() {
  try {
    const payload = JSON.parse(document.getElementById('payload').value || '{}');
    const res = await fetch('/api/cases', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) });
    const data = await res.json();
    document.getElementById('result').textContent = JSON.stringify(data, null, 2);
    await refreshCases();
  } catch (e) {
    document.getElementById('result').textContent = String(e);
  }
}

async function loadCase(caseId) {
  const res = await fetch('/api/cases/' + encodeURIComponent(caseId));
  const data = await res.json();
  document.getElementById('payload').value = JSON.stringify(data, null, 2);
}

async function deleteCase(caseId) {
  const res = await fetch('/api/cases/' + encodeURIComponent(caseId), { method: 'DELETE' });
  const data = await res.json();
  document.getElementById('result').textContent = JSON.stringify(data, null, 2);
  await refreshCases();
}

window.addEventListener('load', refreshCases);
</script>
</body>
</html>"""


def build_handler(store: CaseStore):
    class _Handler(CaseWebHandler):
        pass

    _Handler.store = store
    return _Handler


def serve(scaffold_dir: Path, host: str, port: int) -> None:
    store = CaseStore(scaffold_dir)
    handler = build_handler(store)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"case-web running on http://{host}:{port}")
    print(f"case file: {store.case_file}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\ncase-web stopped")
    finally:
        server.server_close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Workflow case CRUD web server")
    parser.add_argument("--scaffold-dir", default="codex_devflow_scaffold")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scaffold_dir = Path(args.scaffold_dir).resolve()
    serve(scaffold_dir, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
