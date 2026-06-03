# Draft: API v2 Docs Code Sync

## Requirements (confirmed)
- User request: `$ulw-plan 同步http://110.42.32.122:8001/api/v2/docs/ 和v2的代码实现`
- Produce a decision-complete work plan only; do not implement code in this planning turn.

## Technical Decisions
- Treat `/api/v2/docs/schema/` as the canonical machine-readable docs surface behind the Swagger UI.
- Define "sync" as executable parity among live docs schema, local generated v2 schema, URLConf route/method implementation, and selected field-level runtime contracts.
- Do not change v1 behavior or legacy DJI/media route surfaces.
- Current live schema and local schema are structurally identical: 62 paths, 78 components, no path/method/operation/component diff.
- Use the current serializer-backed v2 contract for representative fields: IAM account reads use `department`/`roleCodes`, DJI connection writes use `ownerDepartmentId`, resource reads use `bindingId`, and pilot reads use `display_name`.
- Generate local schema from the current worktree during live parity checks with `--local-django`; evidence JSON files are artifacts, not inputs of record.
- Treat live parity as deployment-state evidence. If local schema-changing fixes are made, live no-drift requires redeploying/restarting the live host or recording a deployment blocker.

## Research Findings
- `config/urls.py` mounts `/api/v2/docs/schema/` via `SpectacularJSONAPIView` with `urlconf="config.api_v2_urlconf"`.
- `config/api_v2_urlconf.py` includes only `apps.api_v2.urls`, which aggregates `iam`, `resource`, `workforce`, and `inspection`.
- `apps/api_v2/tests.py` already verifies v2-only paths, no tenant vocabulary, URLConf path/method parity, response envelope shape, list data shape, success status codes, mutation request bodies, route cover multipart schema, and docs availability.
- `scripts/test_v2_regression.sh` is the current v2 regression gate; `boundary` mode adds legacy/DJI boundary checks.
- The remaining drift risk is field-level request/response correctness for endpoint families, not current path/method divergence.
- Metis review caught and resolved a stale plan assumption: several proposed field names differed from current serializers, so the plan now asserts current implementation names unless a separate business artifact requires a rename.

## Open Questions
- None blocking. Default: plan for guardrails and targeted field-level contract fixes discovered by the audit.

## Scope Boundaries
- INCLUDE: v2 schema generation, v2 schema boundary tests, live-vs-local schema parity artifact, v2 URLConf/method parity, request/response field-level checks for representative endpoints, HTTP docs/schema QA.
- EXCLUDE: v1 route behavior, frontend UI, changing business semantics without a detected schema/runtime mismatch or explicit business artifact, replacing drf-spectacular, live server deployment unless separately requested.
