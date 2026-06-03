# API v2 Docs Code Synchronization Plan

## TL;DR
> **Summary**: Keep `http://110.42.32.122:8001/api/v2/docs/` synchronized with API v2 code by adding executable schema parity and contract guardrails. Current live schema and local schema already match exactly, so this plan avoids blind endpoint changes and focuses on repeatable detection plus targeted fixes only when tests prove drift.
> **Deliverables**:
> - reusable live-vs-local v2 schema parity command
> - stronger v2 schema boundary tests for representative field-level contracts
> - deterministic local docs/code checks in the existing v2 regression gate
> - live HTTP QA artifacts for `/api/v2/docs/` and `/api/v2/docs/schema/`
> - explicit no-v1/no-tenant/no-legacy boundary checks
> **Effort**: Medium
> **Parallel**: LIMITED - audit/test/tooling can be parallelized, but any schema/runtime fix must be serialized after RED evidence.
> **Critical Path**: Baseline parity -> reusable parity tool -> schema boundary test expansion -> targeted fixes, if any -> live docs/schema QA -> review.

## Context
### Original Request
- `$ulw-plan 同步http://110.42.32.122:8001/api/v2/docs/ 和v2的代码实现`

### Current Findings
- Live Swagger HTML at `http://110.42.32.122:8001/api/v2/docs/` returns HTTP 200 and points to `/api/v2/docs/schema/`.
- Live schema at `http://110.42.32.122:8001/api/v2/docs/schema/` returns:
  - title `低空平台 API v2`
  - version `2.0.0`
  - 62 paths
  - 78 component schemas
- Local generated schema via `APIClient().get("/api/v2/docs/schema/")` returns the same title, version, 62 paths, and 78 components.
- Structured local-vs-live comparison found:
  - no paths only in live
  - no paths only in local
  - no method diffs
  - no operation diffs
  - components equal
- Existing `apps/api_v2/tests.py::ApiV2SchemaBoundaryTests` already verifies:
  - only `/api/v2/*` paths are exposed
  - removed legacy paths are absent
  - tenant concepts are absent
  - documented path/method set matches implemented URLConf
  - success responses use the standard `code/msg/data` envelope
  - list responses expose `list` and `total`
  - expected success status codes are documented
  - body mutations declare request bodies
  - route cover multipart contract is documented correctly
  - `/api/v2/docs/` exists and does not mention `/api/v1`

### Key Decisions
- Treat `/api/v2/docs/schema/` as the canonical machine-readable docs surface. `/api/v2/docs/` is an HTML shell smoke check only.
- Do not make blind code changes because current live and local schemas match.
- If a mismatch is found later, source of truth is the actual v2 implementation and confirmed business contract, not the live schema snapshot.
- Keep deterministic local checks in `scripts/test_v2_regression.sh fast`.
- Keep external-network checks opt-in through a dedicated script or regression mode, not in the default fast path.
- Use schema canonicalization before comparison: compare `info`, `paths`, operations, request bodies, responses, and `components`; ignore HTTP headers and JSON formatting.
- Evidence snapshots under `evidence/v2-docs-sync/` are run artifacts, not golden fixtures.
- No credentials, access tokens, refresh tokens, or DB passwords may be written into docs-sync evidence.
- Representative field assertions must use the current v2 implementation/schema contract unless a separate business artifact explicitly requires a field rename:
  - IAM account reads expose `department` and `roleCodes`; account writes accept `departmentId` and `roleCodes`.
  - DJI connection reads expose `workspaceId`; writes accept `ownerDepartmentId`.
  - Resource reads expose `bindingId`, not a nested `binding` object.
  - Pilot reads expose `display_name`; writes accept `displayName`.
- Live host parity is a deployment-state check. If implementation changes alter the local schema, live no-drift cannot be claimed until `110.42.32.122:8001` is redeployed or a deploy gap is explicitly recorded.

## Work Objectives
### Core Objective
Make docs/code synchronization executable: local schema must match the v2 URLConf and field-level contracts, and live `/api/v2/docs/schema/` must be explicitly comparable to the local schema with actionable drift output.

### Deliverables
- Add a reusable schema parity utility, recommended path `scripts/compare_v2_docs_schema.py`.
- Add deterministic tests in `apps/api_v2/test_schema_docs_sync.py` for representative field-level contracts across IAM, resource, workforce, and inspection. Keep the existing oversized `apps/api_v2/tests.py` untouched except for future behavior fixes that truly require it.
- Keep local docs/code checks covered by `scripts/test_v2_regression.sh fast`; do not add a network-dependent mode unless a later implementation need proves it.
- Add live HTTP QA artifacts for Swagger HTML and OpenAPI JSON when running the opt-in live check.
- Update `docs/v2-regression-testing.md` with local and live docs synchronization commands.
- Fix only schema/runtime mismatches exposed by RED tests.

### Definition of Done
- Local schema path/method/operation/component parity against URLConf is covered by tests.
- Representative field-level schema assertions cover every v2 domain: IAM, resource, workforce, inspection.
- A live-vs-local parity command produces actionable diff output and exits non-zero on drift.
- The local schema used for live comparison is generated from the current worktree in the same run; an old `evidence/v2-docs-sync/local-schema.json` file is never used as the source of truth.
- `/api/v2/docs/` HTTP QA proves Swagger HTML is reachable and references `/api/v2/docs/schema/`.
- `/api/v2/docs/schema/` HTTP QA proves live schema can be fetched and canonicalized.
- No `/api/v1`, tenant vocabulary, legacy DJI/BFF route, or old resource alias appears in the v2 docs surface.
- `scripts/test_v2_regression.sh` remains deterministic by default and passes.

### Must Have
- Keep `config.api_v2_urlconf` as the only schema source for API v2 docs.
- Compare live schema to local generated schema using canonical JSON, not raw text.
- The parity utility must be import-safe and expose pure comparison helpers:
  - `canonicalize_schema(schema: dict) -> dict`
  - `diff_schemas(live: dict, local: dict) -> dict`
- Provide drift output grouped by:
  - `info_diff`
  - `paths_only_live`
  - `paths_only_local`
  - `method_diffs`
  - `operation_diffs`
  - `component_diffs`
- `operation_diffs` compare full canonical OpenAPI operation objects for live/local deployment drift, including `operationId`, `summary`, `description`, `tags`, `parameters`, `requestBody`, `responses`, and `security` when present.
- `component_diffs` compare `components.schemas` by schema name and canonical schema body; renamed schemas count as drift because schema names are part of the generated OpenAPI surface.
- Field-level assertions must resolve `$ref` using helpers like `_schema_ref`, not depend on raw component snapshot names where endpoint-level schema can be resolved.
- Live QA must use `curl -i` and capture status line, headers, and body.
- Any runtime code or schema annotation fix must follow RED -> GREEN.

### Must NOT Have
- Do not add network-dependent live schema checks to the default `fast` regression path.
- Do not compare `/api/v2/docs/` HTML as the canonical schema; use it only for smoke.
- Do not change v1 endpoints or v1 docs behavior.
- Do not edit legacy DJI/BFF route surfaces unless a v2 boundary test proves leakage.
- Do not commit access tokens, refresh tokens, passwords, cookies, or DB credentials in evidence.
- Do not make broad field assertions over every generated component if that would create brittle snapshot tests.

## Verification Strategy
> ZERO HUMAN INTERVENTION - all verification is agent-executed.
- TDD or characterization-guardrail tests with Django schema tests and parity utility tests. Existing passing behavior should be codified as characterization; only discovered mismatches require RED -> GREEN fixes.
- RED -> GREEN evidence captured under `evidence/v2-docs-sync/`.
- Live HTTP QA uses `curl -i` against:
  - `http://110.42.32.122:8001/api/v2/docs/`
  - `http://110.42.32.122:8001/api/v2/docs/schema/`
- Local deterministic verification:
  - `git diff --check`
  - `.venv/bin/python manage.py check`
  - `.venv/bin/python manage.py test apps.api_v2.tests apps.api_v2.test_schema_docs_sync`
  - `scripts/test_v2_regression.sh`
- Opt-in live verification:
  - `DB_ENGINE=sqlite .venv/bin/python scripts/compare_v2_docs_schema.py --live-url http://110.42.32.122:8001/api/v2/docs/schema/ --local-django --write-local evidence/v2-docs-sync/local-schema.json --output-json evidence/v2-docs-sync/schema-parity-result.json`
  - `--local-django` is the canonical local source for this project; `--local-url` and `--local-file` are fallback/debug inputs only.

## Execution Strategy
### Parallel Execution Waves
Wave 1: Baseline and parity utility
Wave 2: Field-level schema guardrails
Wave 3: Targeted schema/runtime fixes only if RED tests expose drift
Wave 4: Regression integration and live HTTP QA
Final Wave: plan compliance, reviewer, and cleanup

### Dependency Matrix
| Task | Depends On | Blocks |
| --- | --- | --- |
| 1. Capture and codify current docs/code parity baseline | None | 2, 4 |
| 2. Build reusable live-vs-local schema parity utility | 1 | 4 |
| 3. Expand field-level v2 schema contract tests | 1 | 4 |
| 4. Fix any proven schema/runtime mismatches | 2, 3 | 5 |
| 5. Integrate regression and run live docs QA | 2, 3, 4 | Final verification |

## TODOs

- [x] 1. Capture and codify the current v2 docs/code parity baseline

  **What to do**:
  - Add a characterization guardrail test in `apps/api_v2/test_schema_docs_sync.py`:
    - `ApiV2DocsSyncTests.test_v2_schema_should_match_local_urlconf_and_docs_metadata`
  - The test must assert:
    - `/api/v2/docs/schema/` returns title `低空平台 API v2`
    - version is `2.0.0`
    - all documented paths start with `/api/v2/`
    - schema path/method set equals the implemented URLConf path/method set
    - `/api/v2/docs/` response contains `/api/v2/docs/schema/`
  - Use existing helpers `_schema`, `_actual_v2_routes`, and `_schema_ref` where possible.
  - Reuse existing `test_v2_schema_should_match_implemented_urlconf_paths_and_methods` and `test_v2_docs_should_be_available_under_v2_only` logic instead of duplicating assertions blindly; either consolidate into the new test or add only the missing title/version assertions.
  - Do not create a raw golden schema snapshot.
  - Capture current passing characterization output to `evidence/v2-docs-sync/baseline-schema-boundary.txt`.

  **Must NOT do**:
  - Do not call the live external URL from this deterministic Django test.
  - Do not compare raw JSON ordering or HTTP headers.

  **Acceptance Criteria**:
  - `.venv/bin/python manage.py test apps.api_v2.test_schema_docs_sync.ApiV2DocsSyncTests.test_v2_schema_should_match_local_urlconf_and_docs_metadata` exits 0.
  - The test fails if a URL is implemented under `/api/v2/*` but missing from the schema.
  - The test fails if docs metadata no longer identifies API v2.

  **QA Scenario**:
  ```
  Scenario: Local v2 docs metadata and route table match implementation
    Tool: bash
    Command: .venv/bin/python manage.py test apps.api_v2.test_schema_docs_sync.ApiV2DocsSyncTests.test_v2_schema_should_match_local_urlconf_and_docs_metadata
    PASS observable: exit 0 and output includes OK
    Evidence: evidence/v2-docs-sync/baseline-schema-boundary.txt
  ```

  **Commit**: NO | Files: `apps/api_v2/test_schema_docs_sync.py`, `evidence/v2-docs-sync/baseline-schema-boundary.txt`

- [x] 2. Add reusable live-vs-local v2 schema parity tooling

  **What to do**:
  - First write RED tests for the parity utility before implementing it:
    - Recommended file: `apps/api_v2/test_schema_docs_sync.py`
    - Test ids:
      - `ApiV2SchemaParityUtilityTests.test_compare_v2_schema_should_report_no_diff_for_equal_schemas`
      - `ApiV2SchemaParityUtilityTests.test_compare_v2_schema_should_report_actionable_path_method_and_component_diffs`
    - Load the script in tests with `importlib.util.spec_from_file_location("compare_v2_docs_schema", Path(__file__).resolve().parents[2] / "scripts" / "compare_v2_docs_schema.py")`; do not add `scripts/__init__.py`.
  - Add a small utility script:
    - `scripts/compare_v2_docs_schema.py`
  - Utility requirements:
    - Be import-safe: no network, Django setup, or CLI execution at import time.
    - Expose pure helpers:
      - `canonicalize_schema(schema: dict) -> dict`
      - `diff_schemas(live: dict, local: dict) -> dict`
    - Accept exactly one live source:
      - `--live-url`
      - `--live-file` for a raw JSON file or `curl -i` artifact
    - Accept exactly one local source:
      - `--local-django` (canonical for this project)
      - `--local-url` (debug fallback)
      - `--local-file` (debug fallback; accepts raw JSON or `curl -i` artifact)
    - Accept `--write-local PATH` when `--local-django` is used, writing the freshly generated current-worktree schema for evidence only.
    - Accept `--output-json`.
    - Fetch schemas without credentials.
    - Strip HTTP wrapper when reading any `curl -i` artifact.
    - Canonicalize JSON before comparison.
    - Report grouped keys:
      - `info_diff`
      - `paths_only_live`
      - `paths_only_local`
      - `method_diffs`
      - `operation_diffs`
      - `component_diffs`
    - Include summary metadata in `--output-json`:
      - `live_source`
      - `local_source`
      - `live_path_count`
      - `local_path_count`
      - `live_component_schema_count`
      - `local_component_schema_count`
      - `has_drift`
      - `diff`
    - Compare `paths` operation objects as canonical dictionaries; include descriptive OpenAPI fields because this command detects deployment drift between generated schemas, not runtime behavior by itself.
    - Compare only `components.schemas` under `components`; schema-name changes count as drift.
    - Use a 15-second default URL timeout and a 10 MiB maximum response/file size.
    - Reject zero or multiple live sources with exit 2.
    - Reject zero or multiple local sources with exit 2.
    - Exit 0 when no drift.
    - Exit 1 when drift exists.
    - Exit 2 for malformed input or network/read failure.
  - Keep URL/file comparison dependency-free beyond the Python standard library. `--local-django` may import Django/DRF from the project after CLI parsing.
  - Do not store live schemas as committed golden fixtures.

  **Must NOT do**:
  - Do not require a Bearer token.
  - Do not write secrets or cookies into output.
  - Do not add this live command to `scripts/test_v2_regression.sh fast`.

  **Acceptance Criteria**:
  - Equal-schema unit test passes.
  - Drift unit test passes and asserts grouped diff keys.
  - Running the utility against current live schema and freshly generated local schema reports no drift when no schema-changing fix has been made.

  **QA Scenario**:
  ```
  Scenario: Live docs schema and local v2 schema match
    Tool: HTTP call + bash
    Command:
      curl -i http://110.42.32.122:8001/api/v2/docs/schema/ > evidence/v2-docs-sync/live-schema-http.txt
      DB_ENGINE=sqlite .venv/bin/python scripts/compare_v2_docs_schema.py \
        --live-url http://110.42.32.122:8001/api/v2/docs/schema/ \
        --local-django \
        --write-local evidence/v2-docs-sync/local-schema.json \
        --output-json evidence/v2-docs-sync/schema-parity-result.json
    PASS observable: command exits 0 and output JSON has empty diff groups
    Evidence: evidence/v2-docs-sync/live-schema-http.txt, evidence/v2-docs-sync/schema-parity-result.json
  ```

  **Commit**: NO | Files: `scripts/compare_v2_docs_schema.py`, `apps/api_v2/test_schema_docs_sync.py`, `evidence/v2-docs-sync/*`

- [x] 3. Expand representative field-level v2 schema contract tests

  **What to do**:
  - First write RED tests or characterization tests in `apps/api_v2/test_schema_docs_sync.py`.
  - Add a test:
    - `ApiV2DocsSyncTests.test_v2_schema_should_document_representative_domain_fields`
  - Resolve endpoint response/request schemas through operation paths, not brittle raw component names.
  - Mandatory representative assertions:
    - IAM:
      - `GET /api/v2/iam/accounts` list item includes `id`, `username`, `department`, `roleCodes`, `status`.
      - `POST /api/v2/iam/accounts` request includes `username`, `password`, `departmentId`, `roleCodes`.
    - Resource:
      - `GET /api/v2/resource/dji-connections` list item includes `id`, `name`, `baseUrl`, `workspaceId`, `status`.
      - `POST /api/v2/resource/dji-connections` request includes `name`, `baseUrl`, `username`, `password`, `ownerDepartmentId`.
      - `GET /api/v2/resource/drones` list item includes `id`, `deviceSn`, `name`, `onlineStatus`, `bindingId`, `effectivePermissions`.
    - Workforce:
      - `GET /api/v2/workforce/pilots` list item includes `id`, `display_name`, `departmentId`, `status`, `qualifications`.
      - `POST /api/v2/workforce/pilots` request includes `accountProfileId`, `displayName`, `phone`, `level`.
    - Inspection:
      - `GET /api/v2/inspection/routes` list item includes `id`, `name`, `coverImageUrl`, `waypoints`.
      - `POST /api/v2/inspection/routes` JSON request has `waypoints` as array.
      - `POST /api/v2/inspection/routes` multipart request has `coverImage` as binary and `waypoints` as string.
      - `GET /api/v2/inspection/missions` list item includes `id`, `routeSnapshot`, `droneId`, `pilotId`, `status`.
  - If any assertion fails against the current implementation-backed schema, treat it as a proven local docs/code drift and move the fix to Task 4.
  - If a desired business contract differs from these current field names, do not silently rename fields. Add a failing contract test that cites the business artifact requiring the rename, then fix through Task 4.

  **Must NOT do**:
  - Do not assert every field of every component.
  - Do not snapshot the full schema.
  - Do not weaken existing tenant/no-v1 checks.

  **Acceptance Criteria**:
  - `.venv/bin/python manage.py test apps.api_v2.test_schema_docs_sync.ApiV2DocsSyncTests.test_v2_schema_should_document_representative_domain_fields` exits 0 after fixes.
  - Any failed assertion has corresponding RED evidence before code/schema annotation changes.

  **QA Scenario**:
  ```
  Scenario: Representative v2 domain fields are documented
    Tool: bash
    Command: .venv/bin/python manage.py test apps.api_v2.test_schema_docs_sync.ApiV2DocsSyncTests.test_v2_schema_should_document_representative_domain_fields
    PASS observable: exit 0 and output includes OK
    Evidence: evidence/v2-docs-sync/green-domain-fields.txt
  ```

  **Commit**: NO | Files: `apps/api_v2/test_schema_docs_sync.py`, possibly v2 serializers/views if RED exposes drift

- [x] 4. Fix only proven schema/runtime mismatches

  **What to do**:
  - Execute only if Task 2 or Task 3 produces a RED failure.
  - For each mismatch:
    - Identify whether runtime implementation is correct and schema annotation is wrong, or schema is correct and runtime implementation is wrong.
    - Prefer fixing schema annotations/serializers when runtime behavior already matches intended contract.
    - Prefer fixing runtime implementation only when live API behavior is wrong.
  - Likely fix locations:
    - `apps/iam_v2/views.py`, `apps/iam_v2/serializers.py`
    - `apps/resource_v2/views.py`, `apps/resource_v2/serializers.py`
    - `apps/workforce_v2/views.py`, `apps/workforce_v2/serializers.py`
    - `apps/inspection_v2/views.py`, `apps/inspection_v2/serializers.py`
    - `apps/api_v2/openapi.py`
    - `apps/api_v1/openapi_hooks.py` only if a failing v2 test proves the shared hook is the source of drift
  - Keep each fix minimal and paired with its RED test.

  **Must NOT do**:
  - Do not change endpoint semantics without a failing runtime test.
  - Do not rename public fields casually.
  - Do not touch v1 behavior except through a shared hook fix proven necessary and covered by v1/v2 regression.

  **Acceptance Criteria**:
  - Every mismatch has RED and GREEN evidence.
  - Existing v2 behavior tests still pass.
  - If `apps/api_v1/openapi_hooks.py` changes, run `scripts/test_v2_regression.sh boundary`.
  - If a fix changes local generated schema, do not claim live no-drift until the changed code is deployed to `110.42.32.122:8001` or the live deploy gap is recorded as a blocker.

  **QA Scenario**:
  ```
  Scenario: Fixed mismatch is visible through local generated docs and real endpoint
    Tool: bash
    Command:
      .venv/bin/python manage.py test apps.api_v2.tests apps.api_v2.test_schema_docs_sync apps.resource_v2.tests apps.workforce_v2.tests apps.inspection_v2.tests
      DB_ENGINE=sqlite .venv/bin/python manage.py shell -c "from pathlib import Path; import json; from rest_framework.test import APIClient; response = APIClient().get('/api/v2/docs/schema/'); assert response.status_code == 200, getattr(response, 'data', response.content); Path('evidence/v2-docs-sync/local-schema-after-fix.json').write_text(json.dumps(response.json(), ensure_ascii=False, indent=2), encoding='utf-8')"
    PASS observable: tests exit 0 and regenerated local schema contains the corrected field/type/status for the affected endpoint
    Evidence: evidence/v2-docs-sync/local-schema-after-fix.json
  ```

  **Commit**: NO | Files: only files required by proven mismatch

- [x] 5. Integrate deterministic regression and live docs QA

  **What to do**:
  - Leave `scripts/test_v2_regression.sh fast` deterministic and offline.
  - Do not add a `live-docs` regression mode in this implementation unless the user separately asks for a single wrapper command; the parity utility and runbook are sufficient for the current scope.
  - Update `docs/v2-regression-testing.md`.
  - Document:
    - local deterministic command
    - live schema parity command
    - how to interpret diff groups
    - no credentials required
    - evidence must be token/password-free
    - if local schema changed, live parity requires deploying/restarting the live server before no-drift can pass
  - Run full checks:
    - `git diff --check`
    - `.venv/bin/python manage.py check`
    - `.venv/bin/python manage.py test apps.api_v2.tests apps.api_v2.test_schema_docs_sync`
    - `scripts/test_v2_regression.sh`
    - If shared OpenAPI hook changed: `scripts/test_v2_regression.sh boundary`
  - Run HTTP QA:
    - `curl -i http://110.42.32.122:8001/api/v2/docs/`
    - `curl -i http://110.42.32.122:8001/api/v2/docs/schema/`
    - parity script against live schema using `--local-django`
  - Scrub evidence before delivery:
    - `rg -i "authorization|cookie|token|password|refresh|access" evidence/v2-docs-sync > evidence/v2-docs-sync/secret-scan.txt || true`
    - Any match must be removed or proven to be a non-secret schema field name before delivery.

  **Must NOT do**:
  - Do not require live network for the default local fast gate.
  - Do not leave temporary runserver/tmux/process artifacts.
  - Do not commit raw cookies or tokens from HTTP output.

  **Acceptance Criteria**:
  - Regression commands pass.
  - Live docs page returns HTTP status 200 and contains `/api/v2/docs/schema/`.
  - Live schema parity command reports no drift only if no schema-changing local fix was made, or after the live host has been redeployed/restarted with the changed code.
  - If schema-changing fixes were made but live deployment is not in scope, evidence records live parity as blocked by deployment instead of PASS.
  - Evidence directory has cleanup receipt and no secrets.

  **QA Scenario**:
  ```
  Scenario: Live docs surface is reachable and synchronized
    Tool: HTTP call
    Command:
      curl -i http://110.42.32.122:8001/api/v2/docs/ > evidence/v2-docs-sync/http-docs-page.txt
      curl -i http://110.42.32.122:8001/api/v2/docs/schema/ > evidence/v2-docs-sync/http-docs-schema.txt
      DB_ENGINE=sqlite .venv/bin/python scripts/compare_v2_docs_schema.py \
        --live-url http://110.42.32.122:8001/api/v2/docs/schema/ \
        --local-django \
        --write-local evidence/v2-docs-sync/local-schema.json \
        --output-json evidence/v2-docs-sync/schema-parity-result.json
      rg -i "authorization|cookie|token|password|refresh|access" evidence/v2-docs-sync > evidence/v2-docs-sync/secret-scan.txt || true
    PASS observable: both curl artifacts contain HTTP status 200, parity result has no drift when deployment state permits, and secret scan has no real secrets
    Evidence: evidence/v2-docs-sync/http-docs-page.txt, evidence/v2-docs-sync/http-docs-schema.txt, evidence/v2-docs-sync/schema-parity-result.json, evidence/v2-docs-sync/secret-scan.txt
  ```

  **Commit**: NO | Files: `scripts/test_v2_regression.sh`, `docs/v2-regression-testing.md`, evidence

## Final Verification Wave
> ALL must pass before delivery.

- [x] F1. Plan Compliance Audit
  - Command: `rg -n "compare_v2_docs_schema|test_v2_schema_should_document_representative_domain_fields|api/v2/docs/schema|schema-parity" apps/api_v2 scripts docs README.md`
  - Evidence: `evidence/v2-docs-sync/final-plan-compliance.txt`

- [x] F2. Local Deterministic Quality Gate
  - Commands:
    - `git diff --check`
    - `.venv/bin/python manage.py check`
    - `.venv/bin/python manage.py test apps.api_v2.tests apps.api_v2.test_schema_docs_sync`
    - `scripts/test_v2_regression.sh`
  - Evidence: `evidence/v2-docs-sync/final-local-gate.txt`

- [x] F3. Live Docs QA
  - Commands:
    - `curl -i http://110.42.32.122:8001/api/v2/docs/`
    - `curl -i http://110.42.32.122:8001/api/v2/docs/schema/`
    - parity command from Task 5 with `--local-django`
  - Expected:
    - PASS if live schema and regenerated local schema have no drift.
    - BLOCKED-BY-DEPLOY if local schema changed and the live server was not redeployed/restarted.
  - Evidence: `evidence/v2-docs-sync/http-docs-page.txt`, `evidence/v2-docs-sync/http-docs-schema.txt`, `evidence/v2-docs-sync/schema-parity-result.json`

- [x] F4. Scope Fidelity Check
  - Command: `git diff --name-only HEAD -- apps/api_v1 apps/route apps/mission apps/media_file apps/dji_bff apps/dji_cloud`
  - Expected:
    - no output unless Task 4 proved a shared OpenAPI hook fix is required
  - Evidence: `evidence/v2-docs-sync/final-scope.txt`

- [x] F5. Reviewer Gate
  - Spawn `codex-ultrawork-reviewer` with the plan, diff, tests, HTTP artifacts, and evidence.
  - If the reviewer agent returns only empty status notifications, do not treat it as PASS. Record `reviewer-gate-inconclusive` in `evidence/v2-docs-sync/reviewer.txt`, then use a `worker` fallback reviewer and record either PASS or blocking findings in the same evidence file.
  - Evidence: `evidence/v2-docs-sync/reviewer.txt`

## Commit Strategy
- Preferred final commit: `test(api-v2): guard docs schema sync`
- If implementation fixes are needed after RED tests, use: `fix(api-v2): align docs schema with runtime contract`
- Do not auto-commit unless the user explicitly requests commit after implementation.

## Success Criteria
- Live `/api/v2/docs/schema/` and local v2 schema can be compared by a reusable command.
- Current live/local schema parity remains no-drift.
- If schema-changing local fixes are needed, live/local no-drift is required only after the live server is redeployed/restarted with those fixes; otherwise the final state must explicitly report deployment-blocked live parity.
- v2 schema boundary tests cover path/method parity and representative field-level contracts.
- Default v2 regression remains deterministic and offline.
- Live HTTP docs QA has captured artifacts.
- No v1, tenant, or legacy route concept leaks into v2 docs.
- Any discovered mismatch is fixed only after RED evidence and verified through GREEN tests plus HTTP QA.
