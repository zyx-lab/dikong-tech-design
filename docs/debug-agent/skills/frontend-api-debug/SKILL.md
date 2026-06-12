---
name: frontend-api-debug
description: Use when a frontend developer reports an API request failure, status code, malformed response, missing field, upload problem, auth problem, permission problem, or integration mismatch.
---

# Frontend API Debug

Debug one frontend API failure against the Dikong API v2 backend.

## Trigger Examples

- "接口 403"
- "接口 400"
- "上传失败"
- "字段不对"
- "前端调不通"
- "response 里没有这个字段"
- "token 明明带了还是 401"
- "mission start 报错"

## Required Inputs

Try to collect:

```yaml
method:
url:
status:
request_body_or_form_fields:
response_body:
request_id_or_trace_id:
authorization_header_present:
current_page_or_workflow:
```

If method, URL, and status are missing, ask for them first.

## Debug Workflow

1. Normalize the path.
   - Frontend business API should be `/api/v2/*`.
   - If the user is calling frontend `/api/v1/*`, flag it immediately.

2. Classify the failure.
   - `401`: missing, expired, or malformed authentication.
   - `403`: authenticated but lacks permission, role, department, data scope, or menu/button grant.
   - `400`: request shape, field validation, multipart, enum, or workflow state issue.
   - `404`: wrong path, wrong id, invisible resource, or deleted object.
   - `409`: state conflict or business transition issue.
   - `5xx`: backend exception or external dependency failure.

3. Check schema and docs.
   - Inspect `/api/v2/docs/schema/` when available.
   - Check `docs/api-v2-frontend-guide.md`.

4. Check implementation when schema/docs are insufficient.
   - Find related view, serializer, permissions, service, and tests under `apps/*`.

5. Check logs when a request id or trace id is available.
   - Use `docs/django-logging-guide.md`.
   - Search `app.log` and `error.log`.

6. Give the answer in the required shape:

```text
Conclusion:

Evidence:

Most likely causes:

Next verification:

Frontend action:

Backend/API issue assessment:

Confidence:
```

## Hard Rules

- Do not store tokens or raw secret-bearing headers in memory.
- Do not say the backend is wrong until schema/docs/code/logs support it.
- Do not say the frontend is wrong until request evidence supports it.
- If evidence is incomplete, say exactly what is missing.

