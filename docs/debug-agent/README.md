# Debug Agent

This directory defines the project-specific debug agent package for frontend API
integration support. The runtime is Hermes Agent; this repository provides the
domain instructions, memory policy, tool contracts, and debug skills.

Do not vendor Hermes source code into this repository. Install and run Hermes as
the agent host, then load this directory as the Dikong project debug package.

## Purpose

The debug agent answers frontend integration questions against the current
Dikong API v2 backend. It must use project evidence before giving conclusions:

- API v2 frontend guide: `docs/api-v2-frontend-guide.md`
- OpenAPI schema: `/api/v2/docs/schema/`
- Swagger UI: `/api/v2/docs/`
- Django logging guide: `docs/django-logging-guide.md`
- MCP server: `docs/mcp-server/`
- Backend implementation: `apps/*`
- Tests and evidence: `apps/**/*test*.py`, `evidence/*`

The agent is a chatbot gateway with memory. It should feel like a debug partner
for frontend developers, but it must behave like an engineering tool: every
claim needs a source, a confidence level, and a next verification step.

## Runtime Boundary

Hermes owns:

- chat and gateway transport
- model routing
- conversation persistence
- memory storage
- skill loading
- scheduled or background learning

This directory owns:

- Dikong-specific system prompt
- memory admission rules
- debug workflows
- tool contracts
- example investigations
- project-specific skills

The existing `docs/mcp-server` remains the API tool backend generated from the
local OpenAPI schema. The debug agent may call or reference it, but this package
does not replace it.

## Recommended Setup

1. Install Hermes Agent outside this repository.
2. Start the Django API if live schema or live API calls are needed:

   ```bash
   export DJANGO_LOG_DIR=/tmp/dikong-tech-design-logs
   export DB_ENGINE=sqlite
   python manage.py migrate
   python manage.py runserver 127.0.0.1:8000
   ```

3. Start the existing MCP server when tool access to API operations is needed:

   ```bash
   cd docs/mcp-server
   uv run python server.py
   ```

4. Configure Hermes to load:

   - `docs/debug-agent/system-prompt.md` as the project system prompt
   - `docs/debug-agent/memory-policy.md` as mandatory memory policy
   - `docs/debug-agent/skills/*/SKILL.md` as project skills
   - `docs/debug-agent/tool-contracts.md` as tool usage contract

## How Frontend Developers Should Ask

Good debug requests include:

- HTTP method and URL
- status code
- request body or form fields
- response body
- `X-Request-ID` response header or `traceId` response body
- current page or workflow
- whether `Authorization: Bearer <accessToken>` was sent

Example:

```text
POST /api/v2/inspection/routes returns 400.
I used FormData with file=xxx.kmz and name=Test Route.
Response: {"code":"...","msg":"...","data":...}
traceId: req-123
```

Bad requests force the agent to ask for more data:

```text
The interface is broken.
```

## Required Answer Shape

Every final debug answer must use this shape:

```text
Conclusion:

Evidence:

Most likely causes:

Next verification:

Frontend action:

Backend/API issue assessment:

Confidence:
```

If key data is missing, the agent should ask for the smallest missing item
instead of guessing.

## Safety Rules

- Never store access tokens, refresh tokens, passwords, DJI credentials, or raw
  secret-bearing logs in long-term memory.
- Never turn an unverified guess into memory.
- Never answer from model memory when the repository, schema, logs, or tests can
  answer the question.
- Never treat `/api/v1/*` as frontend business API. In this project, frontend
  business API is `/api/v2/*`; DJI `/api/v1/manage/*`, `/api/v1/wayline/*`, and
  `/api/v1/media/*` are upstream protocol paths.

