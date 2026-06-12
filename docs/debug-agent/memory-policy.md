# Memory Policy

This policy controls what the debug agent may store in long-term memory.

The goal is useful engineering memory, not transcript hoarding. Bad memory is
worse than no memory because it causes confident wrong answers.

## Memory Types

### `project_fact`

Stable facts about this repository.

Example:

```yaml
id: dikong.api.v2.frontend-only
type: project_fact
claim: Frontend business APIs use /api/v2/* only.
source:
  - README.md
  - docs/api-v2-frontend-guide.md
confidence: high
```

### `api_contract_fact`

Stable request, response, authentication, or workflow facts confirmed by schema,
docs, implementation, or tests.

Example:

```yaml
id: dikong.auth.bearer-required
type: api_contract_fact
claim: Business requests after login must include Authorization: Bearer <accessToken>.
source:
  - docs/api-v2-frontend-guide.md
confidence: high
```

### `confirmed_debug_case`

A past bug or integration issue with confirmed symptom, cause, and verification.

Example:

```yaml
id: case.route-upload.multipart-required
type: confirmed_debug_case
symptom: POST /api/v2/inspection/routes returned 400 during route creation.
cause: The frontend sent JSON instead of multipart/form-data for KMZ upload.
verification:
  - OpenAPI requestBody checked
  - serializer behavior checked
  - reproduction or user confirmation captured
frontend_fix: Use FormData and let the browser set multipart Content-Type.
confidence: high
```

### `workflow_fact`

Stable ordering rule for a frontend workflow.

Example:

```yaml
id: dikong.workflow.initial-page-load
type: workflow_fact
claim: After login, frontend should request me/context, menus/current, and me/profile.
source:
  - docs/api-v2-frontend-guide.md
confidence: high
```

### `session_context`

Short-lived context for the current debugging session.

Example:

```yaml
type: session_context
current_page: route-management
current_endpoint: POST /api/v2/inspection/routes
current_status: 400
expires: end_of_session
```

## Required Memory Fields

Every long-term memory entry must include:

```yaml
id:
type:
claim:
source:
confidence:
created_at:
last_verified_at:
anchors:
```

Use stable identifiers. Do not use vague ids such as `bug1` or `frontend_issue`.

## Allowed Long-Term Memory

- facts confirmed by OpenAPI schema
- facts confirmed by repository docs
- facts confirmed by backend implementation
- facts confirmed by tests
- debug cases with verified cause and fix
- explicit project decisions
- repeated frontend integration pitfalls confirmed by evidence

## Forbidden Long-Term Memory

Never store:

- access tokens
- refresh tokens
- passwords
- DJI upstream credentials
- MinIO/S3 credentials
- raw request headers containing secrets
- raw logs containing secrets
- unverified guesses
- user frustration or subjective comments
- temporary one-off stack traces without a confirmed conclusion
- local machine-specific paths unless they are part of documented setup

## Admission Test

Before storing a memory, answer all questions:

1. Is the claim stable enough to help future debugging?
2. Is it verified by schema, docs, code, tests, logs, or explicit user
   confirmation?
3. Is the source recorded?
4. Is the confidence level justified?
5. Is it free of secrets?

If any answer is no, do not store it as long-term memory. Keep it only as
session context or discard it.

## Update and Correction

If schema, code, or docs contradict an existing memory, mark the old memory stale
instead of silently relying on it.

Prefer this correction shape:

```yaml
id: old-memory-id
status: stale
stale_reason: Current OpenAPI schema no longer matches this claim.
replaced_by: new-memory-id
last_verified_at: 2026-06-11
```

