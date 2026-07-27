# Engineering review diagrams

These PlantUML sources are cross-cutting review aids for the API v2 codebase.
They complement the per-component diagrams under `plantumls/*/`.

Use them by question:

- `stakeholder_context.puml`: align product, frontend, backend, operations, and upstream-system discussion.
- `runtime_architecture_review.puml`: review service boundaries, trust boundaries, sync/async paths, and shared stores.
- `core_domain_relationships.puml`: understand core v2 domain ownership, route, mission, resource, execution, record, and media relationships.
- `api_v2_request_review_sequence.puml`: review a normal authenticated API request and audit/logging evidence path.
- `dji_mission_execution_sequence.puml`: review dock/Pilot2 mission dispatch, upstream callbacks, telemetry, completion, and media binding.
- `mission_lifecycle_state.puml`: audit mission lifecycle states and legal transitions.
- `review_activity_flow.puml`: run lightweight engineering review from requirement to release evidence.
- `audit_traceability_chain.puml`: trace accepted knowledge, implementation, tests, logs, and residual risks without mixing mutable review state into source facts.

Keep each diagram at one abstraction level. Add details only when they answer a review question.
