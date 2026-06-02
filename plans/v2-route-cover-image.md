# API v2 Route Cover Image Upload Plan

## TL;DR
> **Summary**: Add a `coverImage` multipart form field to API v2 route save endpoints, persist it as the route cover image, expose `coverImageUrl` on route and mission-adjacent read surfaces, and keep existing JSON route saves compatible.
> **Deliverables**:
> - `WaypointRoute.cover_image` storage field and migration
> - `coverImage` multipart support on `POST /api/v2/inspection/routes` and `PUT /api/v2/inspection/routes/{id}`
> - `coverImageUrl` in route list/detail/create/update and mission `routeSnapshot`
> - OpenAPI/schema updates and RED->GREEN tests
> - HTTP manual QA evidence through the live API surface
> **Effort**: Short
> **Parallel**: NO - model, serializer, parser, schema, and route snapshot behavior are coupled
> **Critical Path**: Persistence -> serializer/parser contract -> route save endpoints -> route snapshot propagation -> regression/manual QA

## Context
### Original Request
- `保存航线的接口，增加个表单上传数据字段，作为航线的封面图。并且配套修改别的接口。`

### Interview Summary
- No follow-up question is required. Repo exploration identifies the "保存航线" endpoints as `POST /api/v2/inspection/routes` and `PUT /api/v2/inspection/routes/{id}`.
- Use request field `coverImage`, matching existing camelCase upload fields such as `kmzFile`.
- Use response field `coverImageUrl`; do not expose raw server paths.

### Metis Review (gaps addressed)
- Multipart bodies must send `waypoints` as one JSON string form field; JSON requests continue sending native arrays.
- `coverImageUrl` returns Django storage `.url` as-is, not an absolute request URL, because storage may be filesystem or S3 and `MEDIA_URL` is not mounted in `config/urls.py`.
- Use `FileField` plus explicit validation because `requirements.txt` has `django-storages[s3]` but no Pillow dependency.
- `PUT` without `coverImage` preserves the existing cover. Empty `coverImage` does not clear the cover. No delete endpoint is added.
- Allowed cover files: `.jpg`, `.jpeg`, `.png`, `.webp`; MIME `image/jpeg`, `image/png`, `image/webp`; max size 5 MiB; empty files rejected.
- On replacement, delete the previous stored file only after successful DB save, only when old name is non-empty and differs from the new name. If old-file deletion fails, log/ignore rather than fail the API response.
- Mission snapshots include `coverImageUrl` for newly created/updated mission snapshots. Existing historical snapshots are not backfilled.

## Work Objectives
### Core Objective
Route save APIs accept a multipart `coverImage` field and all related v2 read surfaces expose the route cover URL while preserving existing JSON route save behavior.

### Deliverables
- Add `cover_image` to `WaypointRoute`.
- Add upload path helper `route_cover_upload_to`.
- Add migration `apps/inspection_v2/migrations/0009_waypointroute_cover_image.py`.
- Add `coverImage` write support and `coverImageUrl` read support.
- Add multipart parsing to route create/update while preserving JSON parsing.
- Add `coverImageUrl` to `route_snapshot(route)`.
- Add tests, schema checks, and HTTP manual QA evidence.

### Definition of Done
- `.venv/bin/python manage.py test apps.inspection_v2.tests` passes.
- `.venv/bin/python manage.py test apps.api_v2.tests` passes.
- `scripts/test_v2_regression.sh` passes.
- HTTP manual QA creates a route with `coverImage`, reads it back, updates it with a replacement image, creates a mission from it, and confirms `routeSnapshot.coverImageUrl`.
- Invalid cover image multipart request returns HTTP 400 with standard envelope.
- No `/api/v1` route behavior is changed.

### Must Have
- Backward-compatible JSON create/update with no cover.
- Multipart create/update with `waypoints` as JSON string.
- `coverImageUrl` in route list/detail/create/update.
- `coverImageUrl` in mission `routeSnapshot`.
- OpenAPI documents multipart route create/update request bodies.
- Existing permissions remain unchanged: dispatcher/platform super can save routes; unauthorized roles cannot.

### Must NOT Have
- No v1 route cover feature.
- No frontend UI work.
- No DJI KMZ/upstream behavior change.
- No separate delete-cover endpoint.
- No Pillow dependency or `ImageField`.
- No request-derived absolute URL building.

## Verification Strategy
> ZERO HUMAN INTERVENTION - all verification is agent-executed.
- Test decision: TDD with Django/DRF integration tests.
- QA policy: every task has agent-executed scenarios.
- RED->GREEN evidence: capture failing and passing outputs in `evidence/route-cover/red-*.txt` and `evidence/route-cover/green-*.txt`.
- HTTP artifacts: capture `curl -i` output in `evidence/route-cover/http-*.txt`.
- Cleanup artifacts: capture cleanup command output in `evidence/route-cover/cleanup.txt`.

## Execution Strategy
### Parallel Execution Waves
Wave 1: Task 1
Wave 2: Task 2
Wave 3: Task 3
Wave 4: Task 4
Final Wave: F1-F4

### Dependency Matrix
| Task | Depends On | Blocks |
| --- | --- | --- |
| 1. Persistence and serializer contract | None | 2, 3 |
| 2. Multipart route save endpoints and schema | 1 | 3, 4 |
| 3. Mission snapshot and adjacent route surfaces | 1, 2 | 4 |
| 4. Regression and HTTP manual QA | 1, 2, 3 | Final verification |

## TODOs

- [x] 1. Add route cover persistence and serializer contract

  **What to do**:
  - First write RED tests in `apps/inspection_v2/tests.py`:
    - `InspectionV2ApiTests.test_route_save_should_accept_cover_image_and_return_cover_image_url`
    - `InspectionV2ApiTests.test_route_save_should_reject_non_image_cover_file`
    - `InspectionV2ApiTests.test_route_multipart_should_reject_invalid_waypoints_json`
  - Add `route_cover_upload_to(instance, filename)` in `apps/inspection_v2/models.py`.
  - Upload path must be `inspection/routes/covers/route-{route_id_or_new}/{uuid}.{ext}`; use a UUID to avoid collisions, preserve validated lowercase extension, and use `route-new` before the instance has an ID.
  - Add `cover_image = models.FileField(upload_to=route_cover_upload_to, blank=True, default="")` to `WaypointRoute`.
  - Generate migration `apps/inspection_v2/migrations/0009_waypointroute_cover_image.py`.
  - Add optional write-only `coverImage = serializers.FileField(required=False, allow_empty_file=False, write_only=True)` to `RouteWriteSerializer`.
  - Add `validate_coverImage`: reject files larger than 5 MiB, files without allowed extension, files with disallowed MIME when `content_type` is present, and empty files. Use error text: `只支持上传 jpg/jpeg/png/webp 图片，且大小不能超过 5MB`.
  - Update `RouteWriteSerializer.validate_waypoints` or a helper so `waypoints` accepts a native list for JSON requests and a JSON string for multipart requests. Invalid JSON must raise a validation error on `waypoints`.
  - Add `coverImageUrl = serializers.SerializerMethodField()` to `RouteReadSerializer`. Return `instance.cover_image.url` when present; otherwise `""`. Catch storage URL exceptions and return `""`.

  **Must NOT do**:
  - Do not use `ImageField`.
  - Do not add Pillow.
  - Do not expose `cover_image.name` or raw storage path in API responses.
  - Do not make cover required.

  **Parallelization**: Can Parallel: NO | Wave 1 | Blocks: 2, 3 | Blocked By: none

  **References**:
  - Pattern: `apps/inspection_v2/models.py:61` - `WaypointRoute` model to extend.
  - Pattern: `apps/inspection_v2/serializers.py:40` - route read response fields.
  - Pattern: `apps/inspection_v2/serializers.py:63` - route write serializer and waypoint validation.
  - Pattern: `apps/inspection_v2/serializers.py:78` - existing file validation pattern for KMZ upload.
  - Pattern: `apps/route/models.py:6` - v1 upload path helper style.
  - Test data pattern: `apps/inspection_v2/tests.py:137` - existing route payload helper.

  **Acceptance Criteria**:
  - [ ] RED: `.venv/bin/python manage.py test apps.inspection_v2.tests.InspectionV2ApiTests.test_route_save_should_accept_cover_image_and_return_cover_image_url` fails before implementation with missing/unsupported `coverImageUrl` or `coverImage`. Capture to `evidence/route-cover/red-task-1.txt`.
  - [ ] GREEN: same test passes after implementation. Capture to `evidence/route-cover/green-task-1.txt`.
  - [ ] `.venv/bin/python manage.py makemigrations --check --dry-run` reports no changes after the migration is present.

  **QA Scenarios**:
  ```
  Scenario: Serializer accepts multipart cover and returns coverImageUrl
    Tool: bash
    Steps: .venv/bin/python manage.py test apps.inspection_v2.tests.InspectionV2ApiTests.test_route_save_should_accept_cover_image_and_return_cover_image_url
    Expected: exits 0; created route response includes non-empty coverImageUrl ending in .jpg/.jpeg/.png/.webp
    Evidence: evidence/route-cover/green-task-1.txt

  Scenario: Serializer rejects invalid cover file
    Tool: bash
    Steps: .venv/bin/python manage.py test apps.inspection_v2.tests.InspectionV2ApiTests.test_route_save_should_reject_non_image_cover_file
    Expected: exits 0; API response status is 400 and error references coverImage
    Evidence: evidence/route-cover/green-task-1-invalid.txt
  ```

  **Commit**: YES | Message: `feat(inspection-v2): add route cover persistence` | Files: `apps/inspection_v2/models.py`, `apps/inspection_v2/serializers.py`, `apps/inspection_v2/migrations/0009_waypointroute_cover_image.py`, `apps/inspection_v2/tests.py`

- [x] 2. Enable multipart route create/update and OpenAPI schema

  **What to do**:
  - First write RED tests in `apps/inspection_v2/tests.py`:
    - `InspectionV2ApiTests.test_route_update_should_replace_cover_image_and_preserve_when_omitted`
    - `InspectionV2ApiTests.test_route_json_save_should_remain_backward_compatible_without_cover_image`
  - First write RED schema test in `apps/api_v2/tests.py`:
    - `ApiV2SchemaBoundaryTests.test_v2_schema_should_document_route_cover_multipart_request_body`
  - Add `parser_classes = [parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser]` to `RouteListCreateView` and `RouteDetailView`.
  - Route create must persist `coverImage` when supplied and return `coverImageUrl`.
  - Route update must preserve existing cover when no `coverImage` is supplied.
  - Route update must replace cover when a new `coverImage` is supplied and delete the old stored file after successful DB save if old name is non-empty and changed.
  - Both create and update must continue accepting existing JSON payloads with native `waypoints` list.
  - Update route create/update `@extend_schema` request metadata so OpenAPI documents `multipart/form-data` and `application/json` for both save endpoints.

  **Must NOT do**:
  - Do not change `/api/v2/inspection/routes/{id}/kmz`.
  - Do not require multipart for existing JSON clients.
  - Do not support bracketed multipart waypoint fields such as `waypoints[0][sequence]`; only support JSON-string `waypoints` for multipart.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: 3, 4 | Blocked By: 1

  **References**:
  - Pattern: `apps/inspection_v2/views.py:166` - route list/create view.
  - Pattern: `apps/inspection_v2/views.py:220` - route detail/update view.
  - Pattern: `apps/inspection_v2/views.py:269` - multipart parser pattern in KMZ upload.
  - Pattern: `apps/api_v2/tests.py:281` - schema request-body tests to extend.

  **Acceptance Criteria**:
  - [ ] RED: `.venv/bin/python manage.py test apps.inspection_v2.tests.InspectionV2ApiTests.test_route_update_should_replace_cover_image_and_preserve_when_omitted` fails before endpoint implementation. Capture to `evidence/route-cover/red-task-2.txt`.
  - [ ] GREEN: same test passes after endpoint implementation. Capture to `evidence/route-cover/green-task-2.txt`.
  - [ ] `.venv/bin/python manage.py test apps.api_v2.tests.ApiV2SchemaBoundaryTests.test_v2_schema_should_document_route_cover_multipart_request_body` passes and confirms `multipart/form-data`, `application/json`, and `coverImage` are documented.

  **QA Scenarios**:
  ```
  Scenario: Multipart create route with cover image
    Tool: bash
    Steps: .venv/bin/python manage.py test apps.inspection_v2.tests.InspectionV2ApiTests.test_route_save_should_accept_cover_image_and_return_cover_image_url
    Expected: exits 0; created route response includes non-empty coverImageUrl
    Evidence: evidence/route-cover/green-task-2-create.txt

  Scenario: JSON route save remains backward compatible
    Tool: bash
    Steps: .venv/bin/python manage.py test apps.inspection_v2.tests.InspectionV2ApiTests.test_route_json_save_should_remain_backward_compatible_without_cover_image
    Expected: exits 0; JSON create/update return 201/200 and coverImageUrl is empty string
    Evidence: evidence/route-cover/green-task-2-json.txt
  ```

  **Commit**: YES | Message: `feat(inspection-v2): accept route cover multipart saves` | Files: `apps/inspection_v2/views.py`, `apps/inspection_v2/serializers.py`, `apps/api_v2/tests.py`, `apps/inspection_v2/tests.py`

- [x] 3. Propagate cover image to mission route snapshots and adjacent read surfaces

  **What to do**:
  - First write RED test in `apps/inspection_v2/tests.py`:
    - `InspectionV2ApiTests.test_mission_route_snapshot_should_include_route_cover_image_url`
  - Add a shared helper in `apps/inspection_v2/serializers.py` or `apps/inspection_v2/services.py` for route cover URL generation so `RouteReadSerializer` and `route_snapshot(route)` use identical behavior.
  - Update `route_snapshot(route)` in `apps/inspection_v2/services.py` to include `coverImageUrl`.
  - Confirm mission create/detail/list responses expose the new key through `MissionReadSerializer.routeSnapshot`.
  - Accept that route create/update audit log payloads now include `coverImageUrl`, because they serialize with `RouteReadSerializer`.

  **Must NOT do**:
  - Do not store raw cover path in mission snapshot.
  - Do not backfill existing historical mission snapshots in a migration.

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: 4 | Blocked By: 1, 2

  **References**:
  - Pattern: `apps/inspection_v2/services.py:289` - route snapshot contract.
  - Pattern: `apps/inspection_v2/views.py:421` - mission create uses route snapshot.
  - Pattern: `apps/inspection_v2/serializers.py:176` - mission read exposes `routeSnapshot` JSON.
  - Pattern: `apps/inspection_v2/tests.py:363` - mission tests that create routes and missions.

  **Acceptance Criteria**:
  - [ ] RED: `.venv/bin/python manage.py test apps.inspection_v2.tests.InspectionV2ApiTests.test_mission_route_snapshot_should_include_route_cover_image_url` fails before snapshot implementation. Capture to `evidence/route-cover/red-task-3.txt`.
  - [ ] GREEN: same test passes after implementation. Capture to `evidence/route-cover/green-task-3.txt`.
  - [ ] Pilot-visible route list still only shows assigned routes and includes `coverImageUrl` for visible routes.

  **QA Scenarios**:
  ```
  Scenario: Mission created from covered route returns cover in snapshot
    Tool: bash
    Steps: .venv/bin/python manage.py test apps.inspection_v2.tests.InspectionV2ApiTests.test_mission_route_snapshot_should_include_route_cover_image_url
    Expected: exits 0; response data.routeSnapshot.coverImageUrl is non-empty
    Evidence: evidence/route-cover/green-task-3.txt

  Scenario: No-cover route remains valid
    Tool: bash
    Steps: .venv/bin/python manage.py test apps.inspection_v2.tests.InspectionV2ApiTests.test_route_json_save_should_remain_backward_compatible_without_cover_image
    Expected: exits 0; no-cover responses use coverImageUrl="" and do not fail route saves
    Evidence: evidence/route-cover/green-task-3-no-cover.txt
  ```

  **Commit**: YES | Message: `feat(inspection-v2): expose route cover in mission snapshots` | Files: `apps/inspection_v2/services.py`, `apps/inspection_v2/serializers.py`, `apps/inspection_v2/tests.py`

- [x] 4. Run full regression and HTTP manual QA through the live API surface

  **What to do**:
  - Run all verification commands below.
  - Use real HTTP calls against `http://110.42.32.122:8001` if it is running from this worktree; otherwise start local runserver on an unused port and use that base URL.
  - Use account `v2_test_task_monitor_dispatcher` with password `FrontTest@123`.
  - Generate two tiny cover fixtures under `/tmp/route-cover-qa-a.jpg` and `/tmp/route-cover-qa-b.png`.
  - Use multipart `waypoints` as one JSON string form field.
  - Capture every `curl -i` response to `evidence/route-cover/http-*.txt`.
  - Cleanup QA data after HTTP scenarios by deleting only routes whose names start with `ulw-cover-qa-` and deleting their stored cover files through Django shell; record output in `evidence/route-cover/cleanup.txt`.

  **Must NOT do**:
  - Do not leave QA routes or cover files behind.
  - Do not use old `v2_frontend_*` accounts.

  **Parallelization**: Can Parallel: NO | Wave 4 | Blocks: final verification | Blocked By: 1, 2, 3

  **References**:
  - Pattern: `README.md:74` - current v2 test accounts.
  - Pattern: `scripts/local_chain_client.sh:5` - default v2 test account naming.
  - Pattern: `apps/inspection_v2/tests.py:137` - route payload shape.

  **Acceptance Criteria**:
  - [ ] `git diff --check` exits 0.
  - [ ] `.venv/bin/python manage.py check` exits 0.
  - [ ] `.venv/bin/python manage.py makemigrations --check --dry-run` exits 0.
  - [ ] `.venv/bin/python manage.py test apps.inspection_v2.tests apps.api_v2.tests` exits 0.
  - [ ] `scripts/test_v2_regression.sh` exits 0.
  - [ ] HTTP create/update/read/mission/invalid-cover scenarios below pass.
  - [ ] Cleanup command reports zero remaining `ulw-cover-qa-` routes and no QA tmux/browser/process leftovers.

  **QA Scenarios**:
  ```
  Scenario: HTTP multipart route create with cover image
    Tool: HTTP call
    Steps:
      1. TOKEN=$(curl -sS -X POST http://110.42.32.122:8001/api/v2/iam/session/login -H 'Content-Type: application/json' -d '{"username":"v2_test_task_monitor_dispatcher","password":"FrontTest@123"}' | .venv/bin/python -c 'import sys,json; print(json.load(sys.stdin)["data"]["accessToken"])')
      2. printf '\xff\xd8\xff\xd9' > /tmp/route-cover-qa-a.jpg
      3. curl -i -X POST http://110.42.32.122:8001/api/v2/inspection/routes -H "Authorization: Bearer $TOKEN" -F 'name=ulw-cover-qa-create' -F 'defaultAltitude=120.00' -F 'defaultSpeed=8.50' -F 'waypoints=[{"sequence":1,"latitude":"31.23040000","longitude":"121.47370000","altitude":"120.00","speed":"8.50","heading":"90.00","hoverSeconds":3}]' -F 'coverImage=@/tmp/route-cover-qa-a.jpg;type=image/jpeg'
    Expected: HTTP 201; body code=00000; data.coverImageUrl is non-empty
    Evidence: evidence/route-cover/http-create.txt

  Scenario: HTTP route update replaces cover image
    Tool: HTTP call
    Steps:
      1. Use route id from create response.
      2. printf '\x89PNG\r\n\x1a\n' > /tmp/route-cover-qa-b.png
      3. curl -i -X PUT http://110.42.32.122:8001/api/v2/inspection/routes/$ROUTE_ID -H "Authorization: Bearer $TOKEN" -F 'name=ulw-cover-qa-update' -F 'defaultAltitude=121.00' -F 'defaultSpeed=9.00' -F 'waypoints=[{"sequence":1,"latitude":"31.23050000","longitude":"121.47380000","altitude":"121.00","speed":"9.00","heading":"91.00","hoverSeconds":1}]' -F 'coverImage=@/tmp/route-cover-qa-b.png;type=image/png'
    Expected: HTTP 200; body code=00000; data.coverImageUrl changes from create response
    Evidence: evidence/route-cover/http-update.txt

  Scenario: HTTP route detail and list expose coverImageUrl
    Tool: HTTP call
    Steps:
      1. curl -i http://110.42.32.122:8001/api/v2/inspection/routes/$ROUTE_ID -H "Authorization: Bearer $TOKEN"
      2. curl -i 'http://110.42.32.122:8001/api/v2/inspection/routes?keywords=ulw-cover-qa' -H "Authorization: Bearer $TOKEN"
    Expected: both HTTP 200; detail data.coverImageUrl non-empty; list contains route with same coverImageUrl
    Evidence: evidence/route-cover/http-detail.txt and evidence/route-cover/http-list.txt

  Scenario: HTTP invalid cover rejected
    Tool: HTTP call
    Steps:
      1. printf 'not an image' > /tmp/route-cover-qa.txt
      2. curl -i -X POST http://110.42.32.122:8001/api/v2/inspection/routes -H "Authorization: Bearer $TOKEN" -F 'name=ulw-cover-qa-invalid' -F 'waypoints=[{"sequence":1,"latitude":"31.23040000","longitude":"121.47370000","altitude":"120.00"}]' -F 'coverImage=@/tmp/route-cover-qa.txt;type=text/plain'
    Expected: HTTP 400; body code indicates validation failure; error references coverImage
    Evidence: evidence/route-cover/http-invalid.txt
  ```

  **Commit**: NO | Message: N/A | Files: evidence only

## Final Verification Wave
> ALL must approve before delivery.

- [x] F1. Plan Compliance Audit
  - Verify all requested behavior is present: save endpoint upload field, cover persistence, related interfaces.
  - Command: `rg -n "coverImage|coverImageUrl|cover_image|route_cover_upload_to" apps/inspection_v2 apps/api_v2`
  - Evidence: `evidence/route-cover/final-plan-compliance.txt`

- [x] F2. Code Quality Review
  - Run diagnostics/checks on changed files.
  - Commands: `git diff --check`; `.venv/bin/python manage.py check`; `.venv/bin/python manage.py makemigrations --check --dry-run`
  - Evidence: `evidence/route-cover/final-code-quality.txt`

- [x] F3. Real Manual QA
  - Re-run the HTTP scenarios from Task 4 after all fixes.
  - Evidence: `evidence/route-cover/http-*.txt`, `evidence/route-cover/cleanup.txt`

- [x] F4. Scope Fidelity Check
  - Verify no v1 route/media/DJI files changed.
  - Command: `git diff --name-only HEAD -- apps/route apps/mission apps/media_file apps/dji_bff apps/dji_cloud`
  - Expected: no output.
  - Evidence: `evidence/route-cover/final-scope.txt`

## Commit Strategy
- Preferred final commit if implementing in one commit: `feat(inspection-v2): add route cover image upload`
- If committing per task, use the task commit messages above.
- Do not auto-commit unless the user explicitly requests commit after implementation.

## Success Criteria
- Route create/update accepts `coverImage` through multipart form data.
- Existing JSON route create/update remains compatible.
- Route responses expose `coverImageUrl`.
- Mission `routeSnapshot` includes `coverImageUrl` for newly created missions.
- Invalid cover files are rejected with HTTP 400.
- OpenAPI documents multipart route save request bodies and the new response field.
- Tests and HTTP manual QA evidence are captured.
- QA data and temporary files are cleaned up.
