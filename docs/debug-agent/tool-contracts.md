# Tool Contracts

These contracts define how the debug agent should use project tools. They are
tool-facing behavior rules, not a runtime implementation.

## `inspect_openapi_operation`

Purpose: inspect one API operation by method and path.

Inputs:

```yaml
method: GET|POST|PUT|PATCH|DELETE
path: /api/v2/...
schema_url: http://127.0.0.1:8000/api/v2/docs/schema/
```

Expected output:

```yaml
operation_id:
summary:
security:
parameters:
request_body:
responses:
schemas:
```

Rules:

- Prefer live schema when the API server is running.
- If live schema is unavailable, say so and fall back to docs/code/tests.
- Do not invent request fields that are absent from schema or serializer code.

## `search_project_docs`

Purpose: search canonical docs for integration rules.

Default files:

- `README.md`
- `docs/api-v2-frontend-guide.md`
- `docs/django-logging-guide.md`
- `docs/v2-regression-testing.md`
- `docs/mcp-server/README.md`

Rules:

- Quote only short snippets when necessary.
- Return file path anchors.
- Prefer canonical docs over old plans or evidence.

## `find_endpoint_implementation`

Purpose: locate view, serializer, permission, route, and tests for an endpoint.

Search targets:

- `apps/api_v2`
- `apps/iam_v2`
- `apps/resource_v2`
- `apps/inspection_v2`
- `apps/system_v2`
- `apps/access`

Rules:

- Prefer `rg` over broad filesystem scans.
- Report exact files checked.
- Distinguish implementation evidence from documentation evidence.

## `inspect_django_logs`

Purpose: inspect request and exception logs.

Inputs:

```yaml
request_id: optional
trace_id: optional
path: optional
status: optional
time_window: optional
log_dir: logs or $DJANGO_LOG_DIR
```

Files:

- `app.log`
- `error.log`

Rules:

- Prefer `request_id` or `traceId`.
- Redact secrets before presenting logs.
- Use `jq` for JSON lines when available.
- If `DJANGO_LOG_REDACT_PAYLOADS=false`, treat headers and bodies as sensitive.

## `run_validation`

Purpose: run the smallest command that verifies or falsifies a hypothesis.

Known commands:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python -m compileall -q apps config scripts tools
scripts/test_v2_regression.sh fast
```

Rules:

- Do not run broad regression commands when a narrow test can answer the
  question.
- Report command, exit code, and relevant output.
- Never claim tests pass without fresh command output.

## `query_mcp_server`

Purpose: call the existing MCP server generated from the API v2 OpenAPI schema.

Endpoint:

```text
http://127.0.0.1:8001/mcp
```

Rules:

- Use MCP tools for API operation exploration or safe local API calls.
- Do not send real secrets through MCP unless the user explicitly provides a
  safe local token for the current session.
- Do not store tokens in memory.

