# Engineering review diagrams

These PlantUML sources are the current cross-cutting review aids for the API v2
codebase.

Use them by question:

- `stakeholder_context.puml`: align product, frontend, backend, operations, and upstream-system discussion.
- `runtime_architecture_review.puml`: review service boundaries, trust boundaries, sync/async paths, and shared stores.
- `core_domain_relationships.puml`: understand core v2 domain ownership, route, mission, resource, execution, record, and media relationships.
- `api_v2_request_review_sequence.puml`: review a normal authenticated API request and audit/logging evidence path.
- `redis_role.puml`: see Redis's two responsibilities only.
- `redis_worker_collaboration_sequence.puml`: see how Redis and `v2-dji-worker` cooperate.
- `dji_worker_loop.puml`: see the worker run loop only.
- `dji_worker_message_flow.puml`: see one MQTT message's handling path only.
- `mission_lifecycle_state.puml`: audit mission lifecycle states and legal transitions.
- `review_activity_flow.puml`: run lightweight engineering review from requirement to release evidence.
- `audit_traceability_chain.puml`: trace accepted knowledge, implementation, tests, logs, and residual risks without mixing mutable review state into source facts.

Keep each diagram at one abstraction level. Add details only when they answer a review question.
