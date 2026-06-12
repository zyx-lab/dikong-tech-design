# Hermes Integration

Hermes is the agent host. The Dikong debug agent package is loaded as project
knowledge and skills.

## Integration Model

```text
Frontend developer
        |
        v
Hermes chat gateway
        |
        v
Dikong debug agent package
        |
        +-- system-prompt.md
        +-- memory-policy.md
        +-- tool-contracts.md
        +-- skills/*
        |
        v
Project evidence
        |
        +-- docs/api-v2-frontend-guide.md
        +-- /api/v2/docs/schema/
        +-- docs/django-logging-guide.md
        +-- docs/mcp-server
        +-- apps/*
        +-- evidence/*
```

## Loading Order

1. Load `system-prompt.md`.
2. Load `memory-policy.md`.
3. Load `tool-contracts.md`.
4. Load skill files under `skills/*/SKILL.md`.
5. On each user question, choose the narrowest applicable skill.

## Hermes Memory Usage

Hermes may remember three classes of project information:

- stable API contract facts
- confirmed debug cases
- user/session working context

Hermes must not store raw secrets or speculative conclusions. Follow
`memory-policy.md` exactly.

## Suggested Gateway Use

Start with CLI usage for validation. Only expose the agent through a team chat
gateway after it consistently answers local debug cases with evidence.

Recommended staged rollout:

1. Local CLI: one backend engineer validates answers.
2. Limited frontend pilot: one or two frontend developers use it for API v2
   integration questions.
3. Team gateway: expose through the preferred chat platform after memory and
   secret handling have been reviewed.

## Project Tool Sources

Use these sources in priority order:

1. User-supplied request/response details.
2. Live OpenAPI schema from `/api/v2/docs/schema/`.
3. `docs/api-v2-frontend-guide.md`.
4. Related serializer/view/test files under `apps/*`.
5. Logs, when `request_id` or `traceId` is available.
6. Existing evidence files under `evidence/*`.

When live schema is unavailable, fall back to repository docs and tests, and
state that live schema was not checked.

