---
name: schema-debug
description: Use when the user asks what fields an API needs, what an endpoint returns, which enum values are valid, whether a request should be JSON or multipart, or whether docs and schema agree.
---

# Schema Debug

Answer API contract questions from the current Dikong API v2 schema and related
code/docs.

## Trigger Examples

- "这个接口要传什么字段"
- "这个字段是不是必填"
- "返回里有没有 xxx"
- "这个接口是 JSON 还是 form-data"
- "Swagger 和文档不一致"
- "枚举值有哪些"

## Workflow

1. Identify method and path.
2. Inspect live OpenAPI schema at `/api/v2/docs/schema/` when available.
3. Extract:
   - operation id
   - path parameters
   - query parameters
   - request body content type
   - required fields
   - response schema
   - security requirements
4. Compare with `docs/api-v2-frontend-guide.md` if the endpoint is mentioned.
5. If still unclear, inspect serializers, views, and tests under `apps/*`.
6. Return a frontend-ready request example.

## Answer Shape

```text
Conclusion:

Contract:

Request example:

Response shape:

Evidence:

Notes for frontend:

Confidence:
```

## Rules

- Current schema and implementation outrank prose docs.
- Do not invent fields.
- If docs and schema disagree, state the mismatch and suggest updating the stale
  source.

