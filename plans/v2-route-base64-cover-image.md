# API v2 Route Base64 Cover Image Plan

## TL;DR
> **Summary**: Extend the existing API v2 route cover image contract so JSON route save requests can send `coverImage` as a base64 image string, persist route covers through S3/MinIO-backed default storage, and return the object-storage-generated `coverImageUrl`.
> **Deliverables**:
> - JSON `coverImage` base64 support on `POST /api/v2/inspection/routes`
> - JSON `coverImage` base64 support on `PUT /api/v2/inspection/routes/{id}`
> - Shared route-cover parsing/validation helper with the existing 5 MiB and jpg/png/webp rules
> - S3/MinIO default-storage configuration checks and deployment guidance
> - `coverImageUrl` URL-shape tests proving route covers use object-storage-generated URLs, not `/media/...` filesystem URLs
> - OpenAPI/schema tests proving JSON `coverImage` is base64 string and multipart `coverImage` remains binary
> - Regression script coverage and HTTP QA evidence
> **Effort**: Short
> **Parallel**: YES - 3 waves
> **Critical Path**: Object-storage contract -> route-cover helper -> serializer/schema integration -> regression/manual QA

## Context
### Original Request
- `航线接口接收前端base64形式的图片 ，你要考虑这个系统接口和实现要怎么改`

### Interview Summary
- No user follow-up is required. Existing code already identifies the route save endpoints and field names.
- The public request field remains `coverImage`; adding a second name would create avoidable frontend/API ambiguity.
- Base64 is a JSON-only input shape. Existing multipart `coverImage` file uploads remain supported and documented.
- The read-side contract remains `coverImageUrl`; it must be the URL returned by the storage backend through `route.cover_image.url`, with no raw storage path and no base64 echo.
- Runtime route-cover storage is S3-compatible object storage. Configure it through `OBJECT_STORAGE_BACKEND=s3` or `OBJECT_STORAGE_BACKEND=minio`, using the existing `STORAGES["default"]` switch rather than a route-cover-specific storage class.

### Metis Review (gaps addressed)
- Accepted grammar is fixed: strict Data URL prefixes `data:image/jpeg;base64,`, `data:image/png;base64,`, `data:image/webp;base64,`, or strict raw standard Base64.
- ASCII whitespace in the payload may be stripped. URL-safe Base64, MIME parameters, `image/jpg`, and automatic missing-padding repair are rejected.
- Raw base64 image type is inferred from decoded magic bytes. Data URL MIME must match decoded magic bytes.
- Validation remains signature/size/MIME based only; no Pillow dependency and no full image decode.
- JSON request body size must be configured high enough for a 5 MiB decoded image, because base64 expands to roughly 6.7 MiB before JSON overhead.
- New focused test modules must be added to `scripts/test_v2_regression.sh`, because the current gate lists exact module labels.
- Existing settings already include `django-storages[s3]` and an S3/MinIO default-storage branch, but the plan must add fail-fast configuration checks, public URL guidance, and object-storage URL tests.
- `FileField.url` delegates to the underlying storage `url()` method; for S3Storage, `AWS_QUERYSTRING_AUTH=true` may return a signed URL with query parameters, so tests must parse the URL and assert the path/key instead of asserting the full string ends with `.png` or `.jpg`.

## Work Objectives
### Core Objective
Route create/update APIs accept frontend-supplied base64 cover images through JSON `coverImage`, persist them through the existing `WaypointRoute.cover_image` field backed by S3/MinIO default storage, and keep every existing route/mission read surface returning the object-storage-generated `coverImageUrl`.

### Deliverables
- Add a small route-cover helper module for base64 parsing, upload-file normalization, and canonical validation constants.
- Replace the serializer write field with a custom route-cover field that accepts existing uploaded files and JSON base64 strings.
- Keep `POST /api/v2/inspection/routes` and `PUT /api/v2/inspection/routes/{id}` create/update storage behavior unchanged after `validated_data["coverImage"]` is normalized.
- Add object-storage configuration checks for `OBJECT_STORAGE_BACKEND=s3|minio` and document the required environment variables.
- Add a focused object-storage URL test module using a fake S3/MinIO-shaped storage class, so tests do not make network calls.
- Document JSON `coverImage` as optional base64/Data URL string and multipart `coverImage` as binary.
- Add focused Django/DRF tests for JSON base64 create, update, invalid input, size rejection, and multipart backward compatibility.
- Add focused schema tests and update the v2 regression script/doc gate as needed.

### Definition of Done (verifiable conditions with commands)
- `DB_ENGINE=sqlite DJANGO_TEST_FAST_PASSWORD_HASHERS=1 .venv/bin/python manage.py test apps.inspection_v2.test_route_cover_base64` passes.
- `DB_ENGINE=sqlite DJANGO_TEST_FAST_PASSWORD_HASHERS=1 .venv/bin/python manage.py test apps.inspection_v2.test_route_cover_object_storage` passes.
- `DB_ENGINE=sqlite DJANGO_TEST_FAST_PASSWORD_HASHERS=1 .venv/bin/python manage.py test apps.api_v2.test_route_cover_base64_schema` passes.
- `scripts/test_v2_regression.sh fast` passes and includes the new focused modules.
- `/api/v2/docs/schema/` generated locally documents JSON route `coverImage` as string and multipart route `coverImage` as binary.
- `OBJECT_STORAGE_BACKEND=minio DB_ENGINE=sqlite .venv/bin/python manage.py check` fails when required object-storage env vars are missing and passes when test-safe required env vars are supplied.
- HTTP QA can login as `v2_test_task_monitor_dispatcher`, create a route with a Data URL cover, update it with a raw base64 cover, and receive a non-empty object-storage `coverImageUrl` from an environment confirmed to use S3/MinIO.
- Invalid base64 and unsupported image type return HTTP 400 with a `coverImage` validation error.

### Must Have
- `coverImage` in JSON can be omitted, `""`, or `null`; all three are treated as omitted. On create this means no cover; on update this preserves the current cover.
- `coverImage` in JSON accepts Data URL strings with MIME `image/jpeg`, `image/png`, or `image/webp`.
- `coverImage` in JSON accepts raw standard Base64 when decoded bytes have JPEG, PNG, or WebP signatures.
- Decoded size must satisfy `0 < size <= 5 * 1024 * 1024`.
- Existing multipart create/update tests must keep passing.
- Existing mission `routeSnapshot.coverImageUrl` behavior must keep working.
- `coverImageUrl` must come from `route.cover_image.url`; do not manually construct `/media/`, endpoint, bucket, or key strings in serializers/views.
- When object storage returns signed URLs, assertions must parse the URL with `urllib.parse.urlparse` and validate `parsed.path`, host, and query params separately.
- `OBJECT_STORAGE_BACKEND=s3|minio` must configure `STORAGES["default"]["BACKEND"] = "storages.backends.s3.S3Storage"`.
- Required object-storage env vars when backend is `s3` or `minio`: access key, secret key, bucket name, endpoint URL for MinIO, and addressing style. For MinIO, `AWS_S3_ADDRESSING_STYLE` defaults to `path`.
- The object-storage endpoint used in `coverImageUrl` must be browser-accessible. For private buckets that need presigned URLs, leave `AWS_S3_CUSTOM_DOMAIN` unset and make `AWS_S3_ENDPOINT_URL` browser-accessible. Only use `OBJECT_STORAGE_PUBLIC_DOMAIN` / `AWS_S3_CUSTOM_DOMAIN` for a public bucket or CDN-style domain where unsigned constructed URLs are intended.
- Custom-domain URL behavior is documentation/deployment guidance only in this slice. Automated URL acceptance covers signed endpoint URLs from storage `.url()`; it does not require a separate custom-domain URL-generation test.
- Permissions remain unchanged: route save still goes through `require_dispatcher`.
- All public API paths remain under `/api/v2/inspection/*`; do not reintroduce `/api/v2/routes` or any `/api/v1` business flow.

### Must NOT Have (guardrails, scope boundaries)
- No v1 API change.
- No frontend UI work.
- No delete-cover endpoint or clear-cover behavior.
- No route cover thumbnail/resizing/compression.
- No Pillow or `ImageField`.
- No route-cover-specific storage field, model migration, or proxy/download endpoint; use the existing global default Django storage switch.
- No base64 response body.
- No URL-safe base64, missing-padding repair, or MIME-parameter Data URLs.

## Verification Strategy
> ZERO HUMAN INTERVENTION - all verification is agent-executed.
- Test decision: TDD with Django/DRF integration tests.
- QA policy: Every task has agent-executed happy and failure scenarios.
- Evidence: write command outputs under `evidence/route-base64-cover/`.
- HTTP artifacts: write redacted `curl -i` outputs under `evidence/route-base64-cover/http-*.txt`.
- Do not commit bearer tokens, refresh tokens, cookies, or passwords in evidence.

## Execution Strategy
### Parallel Execution Waves
Wave 1: Task 1
Wave 2: Task 2, Task 3
Wave 3: Task 4
Final Wave: F1-F4

### Dependency Matrix (full, all tasks)
| Task | Depends On | Blocks |
| --- | --- | --- |
| 1. Base64 parser and route serializer behavior | None | 2, 3, 4 |
| 2. OpenAPI/schema parity for JSON base64 and multipart binary | 1 | 4 |
| 3. Object-storage configuration, URL contract, body limit, and v2 regression gate coverage | 1 | 4 |
| 4. HTTP QA and live docs parity evidence | 1, 2, 3 | Final verification |

## TODOs

- [x] 1. Add route-cover base64 parsing and serializer behavior

  **What to do**:
  - Create `apps/inspection_v2/test_route_cover_base64.py` before implementation. Keep it focused and under 250 pure LOC.
  - Test names to add:
    - `test_json_route_create_should_accept_data_url_cover_image`
    - `test_json_route_update_should_accept_raw_base64_and_replace_cover`
    - `test_json_route_cover_should_reject_invalid_base64`
    - `test_json_route_cover_should_reject_mime_signature_mismatch`
    - `test_json_route_cover_should_reject_decoded_payload_over_5_mib`
    - `test_json_route_update_should_preserve_cover_when_cover_is_empty_or_null`
    - `test_multipart_route_cover_should_remain_supported`
  - Use the existing v2 fixture style from `apps/inspection_v2/tests.py`: create a root department, an owner department, a dispatcher account with `FixedRole.TASK_MONITOR_DISPATCHER`, authenticate with `APIClient.force_authenticate`, and use `TemporaryDirectory` plus filesystem `override_settings(STORAGES=...)` only for compatibility tests that intentionally exercise filesystem behavior.
  - Create `apps/inspection_v2/route_cover_images.py` as the single canonical place for route cover constants, base64 parsing, signature detection, and upload validation.
  - Move or re-export these constants from the new helper so `serializers.py` no longer owns duplicate rules:
    - `ROUTE_COVER_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}`
    - `ROUTE_COVER_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}`
    - `ROUTE_COVER_MAX_BYTES = 5 * 1024 * 1024`
    - `ROUTE_COVER_VALIDATION_MESSAGE = "只支持上传 jpg/jpeg/png/webp 图片，且大小不能超过 5MB"`
  - Implement `RouteCoverImageField(serializers.FileField)` in the helper. It must accept normal uploaded files unchanged and convert accepted JSON strings into `SimpleUploadedFile`.
  - For base64 strings, implement this exact grammar:
    - Data URL prefixes accepted only: `data:image/jpeg;base64,`, `data:image/png;base64,`, `data:image/webp;base64,`.
    - Raw base64 has no prefix.
    - Strip only ASCII whitespace from the payload before validation.
    - Decode with strict standard Base64 validation. Do not accept URL-safe alphabet, missing-padding repair, or MIME parameters.
    - Estimate decoded size before decoding and reject payloads that can exceed `ROUTE_COVER_MAX_BYTES`.
    - After decoding, reject empty bytes and bytes over `ROUTE_COVER_MAX_BYTES`.
    - Detect JPEG by `b"\xff\xd8\xff"`, PNG by `b"\x89PNG\r\n\x1a\n"`, WebP by `decoded.startswith(b"RIFF") and decoded[8:12] == b"WEBP"`.
    - For Data URLs, declared MIME must match the detected signature.
    - Generated file names must be `cover.jpg`, `cover.png`, or `cover.webp`; `image/jpeg` maps to `.jpg`.
  - All base64 parsing and cover upload validation failures must raise a DRF validation error under `coverImage` with `ROUTE_COVER_VALIDATION_MESSAGE`.
  - In `RouteWriteSerializer`, replace `serializers.FileField(...)` with `RouteCoverImageField(required=False, allow_empty_file=False, write_only=True)`.
  - In `RouteWriteSerializer.to_internal_value`, treat `coverImage == ""` and `coverImage is None` as omitted before calling `super().to_internal_value(data)`.
  - Keep `validate_coverImage` as the single serializer hook that calls `validate_route_cover_upload(value)` from the helper and returns the value.

  **Must NOT do**:
  - Do not edit v1 route code.
  - Do not add Pillow or use `ImageField`.
  - Do not add a second request field such as `coverImageBase64`.
  - Do not return base64 in any response.
  - Do not add tests to the already oversized `apps/inspection_v2/tests.py` unless a one-line import or compatibility hook is strictly required.

  **Parallelization**: Can Parallel: NO | Wave 1 | Blocks: 2, 3, 4 | Blocked By: none

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `apps/inspection_v2/serializers.py:25` - current route cover validation constants.
  - Pattern: `apps/inspection_v2/serializers.py:78` - `RouteWriteSerializer` field definitions and validation hooks.
  - Pattern: `apps/inspection_v2/serializers.py:87` - current multipart `waypoints` parsing and empty `coverImage` preservation.
  - Pattern: `apps/inspection_v2/views.py:198` - route create view already accepts JSON, multipart, and form parsers.
  - Pattern: `apps/inspection_v2/views.py:232` - create stores `serializer.validated_data.get("coverImage", "")`.
  - Pattern: `apps/inspection_v2/views.py:295` - update changes cover only when `coverImage` is present in `validated_data`.
  - Pattern: `apps/inspection_v2/views.py:175` - old cover deletion behavior to preserve.
  - Pattern: `apps/inspection_v2/models.py:64` - upload path preserves filename extension.
  - Pattern: `apps/inspection_v2/tests.py:55` - existing v2 test fixture structure.
  - Pattern: `apps/inspection_v2/tests.py:267` - existing multipart cover regression tests.

  **Acceptance Criteria** (agent-executable only):
  - [ ] RED: `DB_ENGINE=sqlite DJANGO_TEST_FAST_PASSWORD_HASHERS=1 .venv/bin/python manage.py test apps.inspection_v2.test_route_cover_base64.RouteCoverBase64Tests.test_json_route_create_should_accept_data_url_cover_image` fails before implementation because JSON `coverImage` is still treated as a non-file string. Capture to `evidence/route-base64-cover/red-task-1-create.txt`.
  - [ ] GREEN: `DB_ENGINE=sqlite DJANGO_TEST_FAST_PASSWORD_HASHERS=1 .venv/bin/python manage.py test apps.inspection_v2.test_route_cover_base64` passes after implementation. Capture to `evidence/route-base64-cover/green-task-1.txt`.
  - [ ] Existing multipart regression still passes: `DB_ENGINE=sqlite DJANGO_TEST_FAST_PASSWORD_HASHERS=1 .venv/bin/python manage.py test apps.inspection_v2.tests.InspectionV2ApiTests.test_route_save_should_accept_cover_image_and_return_cover_image_url apps.inspection_v2.tests.InspectionV2ApiTests.test_route_update_should_replace_cover_image_and_preserve_when_omitted`. Capture to `evidence/route-base64-cover/green-task-1-multipart.txt`.
  - [ ] `DB_ENGINE=sqlite DJANGO_TEST_FAST_PASSWORD_HASHERS=1 .venv/bin/python manage.py test apps.inspection_v2.tests.InspectionV2ApiTests.test_mission_route_snapshot_should_include_route_cover_image_url` still passes. Capture to `evidence/route-base64-cover/green-task-1-snapshot.txt`.

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: JSON create accepts a Data URL PNG cover
    Tool: bash
    Steps: Run apps.inspection_v2.test_route_cover_base64.RouteCoverBase64Tests.test_json_route_create_should_accept_data_url_cover_image with `coverImage="data:image/png;base64,iVBORw0KGgo="`.
    Expected: exits 0; response status is 201; response data.coverImageUrl is non-empty; urllib.parse.urlparse(coverImageUrl).path ends with .png.
    Evidence: evidence/route-base64-cover/green-task-1-create.txt

  Scenario: Invalid base64 is rejected
    Tool: bash
    Steps: Run apps.inspection_v2.test_route_cover_base64.RouteCoverBase64Tests.test_json_route_cover_should_reject_invalid_base64 with `coverImage="data:image/png;base64,not-base64"`.
    Expected: exits 0; response status is 400; response error text contains coverImage and `只支持上传 jpg/jpeg/png/webp 图片，且大小不能超过 5MB`.
    Evidence: evidence/route-base64-cover/green-task-1-invalid.txt
  ```

  **Commit**: NO | Message: `feat(inspection-v2): parse base64 route cover images` | Files: `apps/inspection_v2/route_cover_images.py`, `apps/inspection_v2/serializers.py`, `apps/inspection_v2/test_route_cover_base64.py`

- [x] 2. Update OpenAPI schema to match JSON base64 and multipart binary behavior

  **What to do**:
  - Create `apps/api_v2/test_route_cover_base64_schema.py` before schema implementation. Keep it focused and under 250 pure LOC.
  - Add `RouteCoverBase64SchemaTests.test_route_save_schema_should_document_json_base64_and_multipart_binary_for_create_and_update`.
  - The schema test must iterate over:
    - `("post", "/api/v2/inspection/routes")`
    - `("put", "/api/v2/inspection/routes/{id}")`
  - For each operation, assert:
    - request body has `application/json` and `multipart/form-data`.
    - JSON properties include `coverImage` and `waypoints`.
    - JSON `coverImage.type == "string"`.
    - JSON `coverImage.format` is absent or is not `"binary"`.
    - JSON `coverImage.description` contains `base64` and `data:image`.
    - JSON `waypoints.type == "array"`.
    - multipart `coverImage.type == "string"` and `coverImage.format == "binary"`.
    - multipart `waypoints.type == "string"`.
  - In `apps/inspection_v2/route_cover_images.py`, define a schema dict:
    - `{"type": "string", "nullable": True, "description": "Base64 图片字符串，支持 data:image/jpeg;base64,...、data:image/png;base64,...、data:image/webp;base64,... 或原始标准 Base64。", "example": "data:image/png;base64,iVBORw0KGgo="}`
  - Decorate `RouteCoverImageField` with `drf_spectacular.utils.extend_schema_field` using that exact schema dict so JSON schema represents `coverImage` as a string with description and example.
  - Keep route views using `"application/json": RouteWriteSerializer`.
  - Keep `ROUTE_MULTIPART_WRITE_REQUEST` in `apps/inspection_v2/views.py` as the multipart-specific binary contract.

  **Must NOT do**:
  - Do not change multipart `coverImage` from binary to base64.
  - Do not add or document `/api/v2/routes`.
  - Do not grow `apps/api_v2/test_schema_docs_sync.py`, which is already near the file-size ceiling.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 4 | Blocked By: 1

  **References**:
  - Pattern: `apps/inspection_v2/views.py:92` - current manual multipart route write schema.
  - Pattern: `apps/inspection_v2/views.py:219` - create request content map.
  - Pattern: `apps/inspection_v2/views.py:274` - update request content map.
  - Pattern: `apps/api_v2/tests.py:336` - existing schema test that checks both POST and PUT route save request bodies.
  - Pattern: `apps/api_v2/test_schema_docs_sync.py:148` - current docs-sync assertion for route JSON waypoints and multipart cover.

  **Acceptance Criteria**:
  - [ ] RED: `DB_ENGINE=sqlite DJANGO_TEST_FAST_PASSWORD_HASHERS=1 .venv/bin/python manage.py test apps.api_v2.test_route_cover_base64_schema.RouteCoverBase64SchemaTests.test_route_save_schema_should_document_json_base64_and_multipart_binary_for_create_and_update` fails before schema implementation because JSON `coverImage` is not documented as base64 string. Capture to `evidence/route-base64-cover/red-task-2-schema.txt`.
  - [ ] GREEN: `DB_ENGINE=sqlite DJANGO_TEST_FAST_PASSWORD_HASHERS=1 .venv/bin/python manage.py test apps.api_v2.test_route_cover_base64_schema` passes. Capture to `evidence/route-base64-cover/green-task-2-schema.txt`.
  - [ ] `DB_ENGINE=sqlite DJANGO_TEST_FAST_PASSWORD_HASHERS=1 .venv/bin/python manage.py test apps.api_v2.tests.ApiV2SchemaBoundaryTests.test_v2_schema_should_document_route_cover_multipart_request_body` still passes. Capture to `evidence/route-base64-cover/green-task-2-existing-schema.txt`.

  **QA Scenarios**:
  ```
  Scenario: Docs schema describes JSON base64 cover input
    Tool: bash
    Steps: Run apps.api_v2.test_route_cover_base64_schema.RouteCoverBase64SchemaTests.test_route_save_schema_should_document_json_base64_and_multipart_binary_for_create_and_update.
    Expected: exits 0; POST and PUT JSON request bodies show coverImage as string with base64/Data URL description.
    Evidence: evidence/route-base64-cover/green-task-2-schema.txt

  Scenario: Docs schema keeps multipart cover as binary
    Tool: bash
    Steps: Run apps.api_v2.tests.ApiV2SchemaBoundaryTests.test_v2_schema_should_document_route_cover_multipart_request_body.
    Expected: exits 0; POST and PUT multipart request bodies show coverImage as string format binary.
    Evidence: evidence/route-base64-cover/green-task-2-existing-schema.txt
  ```

  **Commit**: NO | Message: `test(api-v2): document base64 route cover schema` | Files: `apps/api_v2/test_route_cover_base64_schema.py`, `apps/inspection_v2/route_cover_images.py`

- [x] 3. Configure object storage, lock `coverImageUrl` URL source, configure JSON body limit, and include new tests in the v2 regression gate

  **What to do**:
  - Create `apps/inspection_v2/test_route_cover_object_storage.py` before implementation. Keep it focused and under 250 pure LOC.
  - Test names to add:
    - `test_route_cover_image_url_should_use_storage_generated_object_url`
    - `test_route_cover_object_url_should_propagate_to_create_detail_list_and_mission_snapshot`
    - `test_route_cover_object_url_should_keep_signed_query_parameters_when_querystring_auth_enabled`
    - `test_multipart_route_cover_should_use_same_object_storage_url_contract`
  - In that test module, define a local fake storage class, for example `FakeSignedMinioStorage`, that subclasses `django.core.files.storage.Storage` and never performs network I/O:
    - `_save(name, content)` records saved object names in a class-level list and returns `name`.
    - `exists(name)` returns `False` so UUID/key collision handling is deterministic.
    - `delete(name)` records deleted object names in a class-level list.
    - `url(name)` returns `https://minio-public.example.test/dikong-route-covers/{filepath_to_uri(name)}?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=fake-signature`.
  - Use `override_settings(STORAGES={"default": {"BACKEND": "apps.inspection_v2.test_route_cover_object_storage.FakeSignedMinioStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})` for object-storage URL tests.
  - In object-storage URL assertions, always parse URLs with `urllib.parse.urlparse`:
    - `parsed.scheme == "https"`.
    - `parsed.netloc == "minio-public.example.test"`.
    - `parsed.path` contains `/dikong-route-covers/inspection/routes/covers/`.
    - `parsed.path` ends with `.jpg`, `.png`, or `.webp` as appropriate.
    - `parsed.query` contains `X-Amz-Signature=fake-signature` for signed fake storage.
    - Full URL must not start with `/media/`.
  - Add a focused unit-style assertion that `route_cover_image_url(route)` returns exactly the value produced by the storage backend `url(name)` method; do not allow serializers/views to construct object URLs manually.
  - Add object-storage propagation coverage for:
    - create response `data.coverImageUrl`
    - detail response `data.coverImageUrl`
    - list item `coverImageUrl`
    - mission `routeSnapshot.coverImageUrl`
  - Keep `WaypointRoute.cover_image` unchanged. It must keep using the global default Django storage through `FileField`; do not add field-specific storage.
  - In `config/settings.py`, keep the existing `OBJECT_STORAGE_BACKEND=s3|minio` switch, and add these env-backed settings in the S3/MinIO branch:
    - `AWS_S3_CUSTOM_DOMAIN = os.getenv("AWS_S3_CUSTOM_DOMAIN", os.getenv("OBJECT_STORAGE_PUBLIC_DOMAIN", "")).strip() or None`
    - `AWS_QUERYSTRING_EXPIRE = int(os.getenv("AWS_QUERYSTRING_EXPIRE", os.getenv("OBJECT_STORAGE_URL_EXPIRE_SECONDS", "3600")))`
    - `AWS_S3_ADDRESSING_STYLE = os.getenv("AWS_S3_ADDRESSING_STYLE", "path" if OBJECT_STORAGE_BACKEND == "minio" else "auto")`
  - Add fail-fast Django system checks in `apps/api_v2/checks.py` and load them from `apps/api_v2/apps.py.ready()`:
    - Implement `check_object_storage_settings(app_configs=None, **kwargs)` so tests can call the check function directly before relying on `manage.py check`.
    - Use exact stable check IDs:
      - `api_v2.E001`: `OBJECT_STORAGE_BACKEND` is not one of `filesystem`, `s3`, `minio`.
      - `api_v2.E002`: object storage backend is `s3` or `minio` but access key is missing.
      - `api_v2.E003`: object storage backend is `s3` or `minio` but secret key is missing.
      - `api_v2.E004`: object storage backend is `s3` or `minio` but bucket name is missing.
      - `api_v2.E005`: object storage backend is `minio` but endpoint URL is missing.
      - `api_v2.E006`: `OBJECT_STORAGE_PUBLIC_DOMAIN` / `AWS_S3_CUSTOM_DOMAIN` is set with a scheme or path instead of a host-only value.
    - If backend is `minio`, require non-empty `AWS_S3_ENDPOINT_URL`; document that this endpoint must be browser-accessible unless `AWS_S3_CUSTOM_DOMAIN` points to a public bucket/CDN domain.
  - Create `apps/api_v2/test_object_storage_settings.py` before implementation. Add tests:
    - `test_object_storage_check_should_report_exact_ids_for_invalid_backend`
    - `test_object_storage_check_should_report_exact_ids_for_missing_minio_required_values`
    - `test_object_storage_check_should_report_exact_id_for_invalid_public_domain`
    - `test_object_storage_check_should_pass_with_minio_required_env`
    - `test_object_storage_settings_should_configure_s3_storage_backend`
  - The first RED for settings/checks must run the direct check-function tests, not only `manage.py check`, so failures are pinned to exact `api_v2.E0xx` IDs before implementation.
  - In `config/settings.py`, add an environment-overridable Django body limit after the media settings:
    - `DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE", str(10 * 1024 * 1024)))`
  - Do not change `FILE_UPLOAD_MAX_MEMORY_SIZE`; multipart files already have a separate API-level 5 MiB validation rule.
  - Update `README.md` with a short "Route cover object storage" subsection under "快速启动" or the API v2 area. Include these exact env examples:
    - `OBJECT_STORAGE_BACKEND=minio`
    - `OBJECT_STORAGE_ACCESS_KEY_ID=...`
    - `OBJECT_STORAGE_SECRET_ACCESS_KEY=...`
    - `OBJECT_STORAGE_BUCKET_NAME=dikong-route-covers`
    - `OBJECT_STORAGE_ENDPOINT_URL=https://minio.example.com`
    - `OBJECT_STORAGE_PUBLIC_DOMAIN=assets.example.com` only for a public bucket/CDN-style host; do not include a scheme or path. For private MinIO presigned URLs, leave this unset and make `OBJECT_STORAGE_ENDPOINT_URL` browser-accessible.
    - `AWS_S3_ADDRESSING_STYLE=path`
    - `AWS_QUERYSTRING_AUTH=true`
    - `OBJECT_STORAGE_URL_EXPIRE_SECONDS=3600`
  - Add these labels to both `fast` and `boundary` modes in `scripts/test_v2_regression.sh`:
    - `apps.api_v2.test_route_cover_base64_schema`
    - `apps.api_v2.test_object_storage_settings`
    - `apps.inspection_v2.test_route_cover_base64`
    - `apps.inspection_v2.test_route_cover_object_storage`
  - If `docs/v2-regression-testing.md` is already being maintained in this branch, update the listed default v2 gate modules to include the same four labels. Preserve all prior docs-sync text.
  - Capture `manage.py check`, migration dry-run, and the full fast regression command output.
  - Keep existing object-storage delete behavior: `_delete_replaced_route_cover` delete failures remain non-fatal and logged. Do not change this policy in this slice.
  - Keep existing transaction/orphan-object policy: do not add cross-storage transaction compensation in this slice. If DB save fails after storage write, orphan cleanup remains outside this plan.

  **Must NOT do**:
  - Do not use this task to change database settings or PostgreSQL behavior.
  - Do not remove existing regression labels.
  - Do not add a route-cover proxy/download endpoint.
  - Do not create a route-cover-specific storage class in production.
  - Do not make tests depend on real S3/MinIO network calls.
  - Do not edit unrelated `.omo` ledger or evidence files except this plan's evidence directory.

  **Parallelization**: Can Parallel: YES | Wave 2 | Blocks: 4 | Blocked By: 1

  **References**:
  - Pattern: `config/settings.py:166` - media settings location for nearby upload/body settings.
  - Pattern: `config/settings.py:172` - existing `OBJECT_STORAGE_BACKEND` switch.
  - Pattern: `config/settings.py:178` - existing S3/MinIO branch.
  - Pattern: `config/settings.py:187` - existing `storages.backends.s3.S3Storage` default storage backend.
  - Pattern: `requirements.txt:6` - `django-storages[s3]` dependency already present.
  - Pattern: `apps/inspection_v2/models.py:76` - `WaypointRoute.cover_image` uses default storage through `FileField`.
  - Pattern: `apps/inspection_v2/models.py:97` - `route_cover_image_url(route)` must continue using `route.cover_image.url`.
  - Pattern: `apps/inspection_v2/serializers.py:74` - `coverImageUrl` delegates to `route_cover_image_url(instance)`.
  - Pattern: `apps/inspection_v2/services.py:290` - mission snapshot includes `coverImageUrl`.
  - Pattern: `apps/api_v2/apps.py:1` - add `ready()` import for Django system checks.
  - Pattern: `scripts/test_v2_regression.sh:58` - exact test labels for fast mode.
  - Pattern: `scripts/test_v2_regression.sh:68` - exact test labels for boundary mode.
  - Pattern: `docs/v2-regression-testing.md:7` - default v2 gate documentation.
  - Constraint: `README.md:104` - README instructs v2 development to use `scripts/test_v2_regression.sh`.
  - External: Django `FileField.url` documentation - `FileField.url` calls the underlying storage `url()` method.
  - External: django-storages S3Storage source/docs - `S3Storage.url()` returns presigned URLs when `AWS_QUERYSTRING_AUTH=True` and no custom domain is configured; `AWS_QUERYSTRING_AUTH=False` strips signing params.

  **Acceptance Criteria**:
  - [ ] RED: `DB_ENGINE=sqlite DJANGO_TEST_FAST_PASSWORD_HASHERS=1 .venv/bin/python manage.py test apps.inspection_v2.test_route_cover_object_storage.RouteCoverObjectStorageTests.test_route_cover_image_url_should_use_storage_generated_object_url` fails before implementation because no focused object-storage URL contract exists. Capture to `evidence/route-base64-cover/red-task-3-object-url.txt`.
  - [ ] GREEN: `DB_ENGINE=sqlite DJANGO_TEST_FAST_PASSWORD_HASHERS=1 .venv/bin/python manage.py test apps.inspection_v2.test_route_cover_object_storage` passes. Capture to `evidence/route-base64-cover/green-task-3-object-url.txt`.
  - [ ] RED: `DB_ENGINE=sqlite DJANGO_TEST_FAST_PASSWORD_HASHERS=1 .venv/bin/python manage.py test apps.api_v2.test_object_storage_settings.ObjectStorageSettingsTests.test_object_storage_check_should_report_exact_ids_for_missing_minio_required_values apps.api_v2.test_object_storage_settings.ObjectStorageSettingsTests.test_object_storage_check_should_report_exact_ids_for_invalid_backend apps.api_v2.test_object_storage_settings.ObjectStorageSettingsTests.test_object_storage_check_should_report_exact_id_for_invalid_public_domain` fails before implementation because the direct check function or exact IDs do not exist yet. Capture to `evidence/route-base64-cover/red-task-3-storage-check-tests.txt`.
  - [ ] RED: `OBJECT_STORAGE_BACKEND=minio DB_ENGINE=sqlite .venv/bin/python manage.py check` fails with the planned `api_v2.E0xx` object-storage check IDs when required env vars are missing. Capture to `evidence/route-base64-cover/red-task-3-storage-check.txt`.
  - [ ] GREEN: `OBJECT_STORAGE_BACKEND=minio OBJECT_STORAGE_ACCESS_KEY_ID=minioadmin OBJECT_STORAGE_SECRET_ACCESS_KEY=minioadmin123 OBJECT_STORAGE_BUCKET_NAME=dikong-route-covers OBJECT_STORAGE_ENDPOINT_URL=https://minio.example.test AWS_S3_ADDRESSING_STYLE=path DB_ENGINE=sqlite .venv/bin/python manage.py check` exits 0. Capture to `evidence/route-base64-cover/green-task-3-storage-check.txt`.
  - [ ] `DB_ENGINE=sqlite DJANGO_TEST_FAST_PASSWORD_HASHERS=1 .venv/bin/python manage.py test apps.api_v2.test_object_storage_settings` passes. Capture to `evidence/route-base64-cover/green-task-3-storage-settings-tests.txt`.
  - [ ] `DB_ENGINE=sqlite DJANGO_TEST_FAST_PASSWORD_HASHERS=1 .venv/bin/python manage.py shell -c 'from django.conf import settings; assert settings.DATA_UPLOAD_MAX_MEMORY_SIZE >= 10 * 1024 * 1024'` exits 0. Capture to `evidence/route-base64-cover/green-task-3-body-limit.txt`.
  - [ ] `scripts/test_v2_regression.sh fast` runs the four new modules and exits 0. Capture to `evidence/route-base64-cover/green-task-3-regression.txt`.
  - [ ] `DB_ENGINE=sqlite .venv/bin/python manage.py makemigrations --check --dry-run` reports no model changes. Capture to `evidence/route-base64-cover/green-task-3-migrations.txt`.

  **QA Scenarios**:
  ```
  Scenario: Route cover URL is generated by object storage, not filesystem media
    Tool: bash
    Steps: Run apps.inspection_v2.test_route_cover_object_storage.RouteCoverObjectStorageTests.test_route_cover_image_url_should_use_storage_generated_object_url.
    Expected: exits 0; coverImageUrl host is minio-public.example.test, path contains /dikong-route-covers/inspection/routes/covers/, query contains X-Amz-Signature, and the URL does not start with /media/.
    Evidence: evidence/route-base64-cover/green-task-3-object-url.txt

  Scenario: Object-storage settings fail fast when MinIO config is incomplete
    Tool: bash
    Steps: Run OBJECT_STORAGE_BACKEND=minio DB_ENGINE=sqlite .venv/bin/python manage.py check.
    Expected: exits non-zero; output includes the planned api_v2.E0xx object-storage check IDs.
    Evidence: evidence/route-base64-cover/red-task-3-storage-check.txt

  Scenario: JSON request body limit can carry a 5 MiB decoded cover
    Tool: bash
    Steps: Run the settings assertion command for DATA_UPLOAD_MAX_MEMORY_SIZE.
    Expected: exits 0; setting is at least 10485760 bytes.
    Evidence: evidence/route-base64-cover/green-task-3-body-limit.txt

  Scenario: Default v2 regression gate covers new base64 tests
    Tool: bash
    Steps: Run scripts/test_v2_regression.sh fast.
    Expected: exits 0; command output includes apps.api_v2.test_route_cover_base64_schema, apps.api_v2.test_object_storage_settings, apps.inspection_v2.test_route_cover_base64, and apps.inspection_v2.test_route_cover_object_storage.
    Evidence: evidence/route-base64-cover/green-task-3-regression.txt
  ```

  **Commit**: NO | Message: `test(v2): lock route cover object storage contract` | Files: `config/settings.py`, `apps/api_v2/apps.py`, `apps/api_v2/checks.py`, `apps/api_v2/test_object_storage_settings.py`, `apps/inspection_v2/test_route_cover_object_storage.py`, `scripts/test_v2_regression.sh`, `README.md`, `docs/v2-regression-testing.md` if updated

- [x] 4. Execute local MinIO-backed HTTP QA and docs parity checks

  **What to do**:
  - Use the v2 dispatcher account from `README.md`: username `v2_test_task_monitor_dispatcher`, password `FrontTest@123`.
  - Use only v2 paths:
    - `POST /api/v2/iam/session/login`
    - `POST /api/v2/inspection/routes`
    - `PUT /api/v2/inspection/routes/{id}`
    - `GET /api/v2/inspection/routes/{id}`
    - `GET /api/v2/docs/schema/`
  - Create HTTP evidence files under `evidence/route-base64-cover/`.
  - Primary HTTP QA must run against a local API process started with explicit MinIO env vars. Do not use the public live host as the primary object-storage proof.
  - Start a local MinIO-compatible endpoint for QA. Preferred command:
    - `docker rm -f ulw-route-cover-minio || true`
    - `docker run -d --name ulw-route-cover-minio -p 19000:9000 -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin123 quay.io/minio/minio server /data`
  - Create the QA bucket with boto3 through a one-off Python command using:
    - `AWS_ACCESS_KEY_ID=minioadmin`
    - `AWS_SECRET_ACCESS_KEY=minioadmin123`
    - endpoint `http://127.0.0.1:19000`
    - bucket `dikong-route-covers`
  - Seed the local SQLite DB with the documented v2 dispatcher account before starting the server if it does not already exist:
    - Create root department `总部`.
    - Create child department `资源队`.
    - Create or update user `v2_test_task_monitor_dispatcher` with password `FrontTest@123` and active status.
    - Create `V2AccountProfile` for that user in `资源队`.
    - Create `V2AccountRoleAssignment` with `FixedRole.TASK_MONITOR_DISPATCHER`.
  - Start the local API server in tmux with the same explicit object-storage env:
    - `tmux new-session -d -s ulw-route-cover-api 'OBJECT_STORAGE_BACKEND=minio OBJECT_STORAGE_ACCESS_KEY_ID=minioadmin OBJECT_STORAGE_SECRET_ACCESS_KEY=minioadmin123 OBJECT_STORAGE_BUCKET_NAME=dikong-route-covers OBJECT_STORAGE_ENDPOINT_URL=http://127.0.0.1:19000 AWS_S3_ADDRESSING_STYLE=path AWS_QUERYSTRING_AUTH=true OBJECT_STORAGE_URL_EXPIRE_SECONDS=3600 DB_ENGINE=sqlite .venv/bin/python manage.py runserver 127.0.0.1:18001'`
  - Before route HTTP QA, capture storage preflight with the exact same env:
    - `OBJECT_STORAGE_BACKEND=minio OBJECT_STORAGE_ACCESS_KEY_ID=minioadmin OBJECT_STORAGE_SECRET_ACCESS_KEY=minioadmin123 OBJECT_STORAGE_BUCKET_NAME=dikong-route-covers OBJECT_STORAGE_ENDPOINT_URL=http://127.0.0.1:19000 AWS_S3_ADDRESSING_STYLE=path AWS_QUERYSTRING_AUTH=true OBJECT_STORAGE_URL_EXPIRE_SECONDS=3600 DB_ENGINE=sqlite .venv/bin/python manage.py check`
    - Capture output to `evidence/route-base64-cover/http-storage-preflight.txt`.
  - If Docker/MinIO is unavailable after two attempts, object-storage HTTP QA is blocked. Record the exact failure in `evidence/route-base64-cover/http-storage-preflight.txt` and do not claim Task 4 complete.
  - Live-host QA against `http://110.42.32.122:8001` is optional and may only be claimed after the live server is redeployed with this code and a concrete non-secret preflight from the same deployment host proves `OBJECT_STORAGE_BACKEND=s3|minio`. If host access is unavailable, record live object-storage QA as `deployment-blocked`; local MinIO-backed HTTP QA remains mandatory.
  - Use a unique route name such as `ulw-base64-cover-$(date +%s)` so repeated QA does not collide with the per-department route name uniqueness constraint.
  - Create with Data URL PNG `data:image/png;base64,iVBORw0KGgo=`.
  - Update with raw JPEG base64 `/9j/2Q==`.
  - Invalid QA must send `data:image/png;base64,not-base64` and assert HTTP 400.
  - Use this concrete HTTP data shape:
    - Login body: `{"username":"v2_test_task_monitor_dispatcher","password":"FrontTest@123"}`
    - Create route body: `{"name":"ulw-base64-cover-<unix-seconds>","defaultAltitude":"120.00","defaultSpeed":"8.50","coverImage":"data:image/png;base64,iVBORw0KGgo=","waypoints":[{"sequence":1,"latitude":"31.23040000","longitude":"121.47370000","altitude":"120.00","speed":"8.50","heading":"90.00","hoverSeconds":3}]}`
    - Update route body: same fields, new name `ulw-base64-cover-updated-<unix-seconds>`, `coverImage":"/9j/2Q=="`
    - Invalid route body: same fields, new name `ulw-base64-cover-invalid-<unix-seconds>`, `coverImage":"data:image/png;base64,not-base64"`
  - Run local schema parity first. Run live schema parity against `http://110.42.32.122:8001/api/v2/docs/schema/` only after the live server has been redeployed or restarted with this code. If not redeployed, record `deployment-blocked` instead of claiming no drift.
  - For every successful route response, validate `coverImageUrl` by parsing it:
    - `parsed.scheme` is `http` or `https`.
    - `parsed.netloc` matches the configured S3/MinIO endpoint host or public custom domain.
    - `parsed.path` contains `/inspection/routes/covers/`.
    - `parsed.path` ends with `.png` for create and `.jpg` for update.
    - If `AWS_QUERYSTRING_AUTH=true`, `parsed.query` is non-empty.
    - The URL does not start with `/media/`.

  **Must NOT do**:
  - Do not store access or refresh tokens in committed evidence. Redact with `python -c` or `sed` before keeping artifacts.
  - Do not test through `/api/v1`.
  - Do not claim live docs parity if the live server still runs old code.
  - Do not claim object-storage HTTP QA if the target environment is filesystem-backed or if the returned URL starts with `/media/`.
  - Do not leave the `ulw-route-cover-api` tmux session or `ulw-route-cover-minio` container running after QA.

  **Parallelization**: Can Parallel: NO | Wave 3 | Blocks: Final verification | Blocked By: 1, 2, 3

  **References**:
  - Credential source: `README.md:73` - current v2 integration accounts and password.
  - Login contract: `README.md:66` - v2 login endpoint.
  - Route paths: `apps/inspection_v2/urls.py:6` - v2 inspection route URL registrations.
  - Schema docs: `docs/v2-regression-testing.md:40` - docs/code sync procedure and live deployment caveat.
  - Create/update behavior: `apps/inspection_v2/views.py:216` and `apps/inspection_v2/views.py:271`.
  - Storage settings: `config/settings.py:172` - `OBJECT_STORAGE_BACKEND` switch.
  - URL helper: `apps/inspection_v2/models.py:97` - `route_cover_image_url(route)` returns storage-generated `route.cover_image.url`.

  **Acceptance Criteria**:
  - [ ] Local MinIO container starts, bucket `dikong-route-covers` exists, and the local API server is reachable on `http://127.0.0.1:18001`. Capture setup output to `evidence/route-base64-cover/http-local-minio-setup.txt`.
  - [ ] Object-storage preflight confirms the local HTTP environment uses `OBJECT_STORAGE_BACKEND=minio` and passes `manage.py check`. Capture to `evidence/route-base64-cover/http-storage-preflight.txt`.
  - [ ] HTTP login against `http://127.0.0.1:18001/api/v2/iam/session/login` succeeds and returns `data.accessToken`. Capture redacted login response to `evidence/route-base64-cover/http-login.txt`.
  - [ ] HTTP JSON create with Data URL PNG against `http://127.0.0.1:18001/api/v2/inspection/routes` returns 201 and object-storage `coverImageUrl`; parsed URL host is `127.0.0.1:19000`, parsed path contains `/dikong-route-covers/inspection/routes/covers/`, parsed path ends with `.png`, query is non-empty, and URL is not `/media/...`. Capture to `evidence/route-base64-cover/http-create-data-url.txt`.
  - [ ] HTTP JSON update with raw JPEG base64 against `http://127.0.0.1:18001/api/v2/inspection/routes/{id}` returns 200 and a changed object-storage `coverImageUrl`; parsed URL host is `127.0.0.1:19000`, parsed path contains `/dikong-route-covers/inspection/routes/covers/`, parsed path ends with `.jpg`, query is non-empty, and URL is not `/media/...`. Capture to `evidence/route-base64-cover/http-update-raw-base64.txt`.
  - [ ] HTTP invalid base64 returns 400 and error references `coverImage`. Capture to `evidence/route-base64-cover/http-invalid-base64.txt`.
  - [ ] `curl -sS -i http://110.42.32.122:8001/api/v2/docs/schema/` is captured if live was deployed; otherwise record `deployment-blocked` in `evidence/route-base64-cover/live-docs-parity.txt`.
  - [ ] Cleanup receipt confirms `tmux has-session -t ulw-route-cover-api` fails and `docker ps -a --filter name=ulw-route-cover-minio` no longer lists a running QA container. Capture to `evidence/route-base64-cover/http-cleanup.txt`.

  **QA Scenarios**:
  ```
  Scenario: Live/local HTTP route create and update accept JSON base64 covers
    Tool: bash + curl
    Steps: Start local MinIO on 127.0.0.1:19000, create bucket dikong-route-covers, start local API in tmux on 127.0.0.1:18001 with OBJECT_STORAGE_BACKEND=minio, login as v2_test_task_monitor_dispatcher, POST a route JSON payload with Data URL PNG coverImage, then PUT the same route with raw JPEG base64 coverImage.
    Expected: login HTTP 200; create HTTP 201; update HTTP 200; both responses have data.coverImageUrl; parsed URL host is 127.0.0.1:19000; parsed URL path contains /dikong-route-covers/inspection/routes/covers/ and ends with the expected extension; signed URL query is non-empty; URL does not start with /media/; update URL differs from create URL.
    Evidence: evidence/route-base64-cover/http-create-data-url.txt and evidence/route-base64-cover/http-update-raw-base64.txt

  Scenario: HTTP invalid base64 returns validation failure
    Tool: bash + curl
    Steps: POST a route JSON payload to local API `http://127.0.0.1:18001/api/v2/inspection/routes` with coverImage `data:image/png;base64,not-base64`.
    Expected: HTTP 400; standard response envelope contains a coverImage validation error.
    Evidence: evidence/route-base64-cover/http-invalid-base64.txt
  ```

  **Commit**: NO | Message: `test(inspection-v2): verify base64 cover http flow` | Files: `evidence/route-base64-cover/*` only if evidence is intentionally kept

## Final Verification Wave (MANDATORY - after ALL implementation tasks)
> ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.
- [x] F1. Plan Compliance Audit
  - Confirm every TODO acceptance criterion has corresponding evidence under `evidence/route-base64-cover/`.
  - Confirm `apps/inspection_v2/test_route_cover_base64.py`, `apps/inspection_v2/test_route_cover_object_storage.py`, `apps/api_v2/test_route_cover_base64_schema.py`, and `apps/api_v2/test_object_storage_settings.py` are registered in `scripts/test_v2_regression.sh`.
  - Confirm `README.md` documents S3/MinIO route-cover env vars and does not include secret values.
  - Confirm no plan task was skipped or replaced with an undocumented approach.
- [x] F2. Code Quality Review
  - Run `DB_ENGINE=sqlite .venv/bin/python manage.py check`.
  - Run `OBJECT_STORAGE_BACKEND=minio OBJECT_STORAGE_ACCESS_KEY_ID=minioadmin OBJECT_STORAGE_SECRET_ACCESS_KEY=minioadmin123 OBJECT_STORAGE_BUCKET_NAME=dikong-route-covers OBJECT_STORAGE_ENDPOINT_URL=https://minio.example.test AWS_S3_ADDRESSING_STYLE=path DB_ENGINE=sqlite .venv/bin/python manage.py check`.
  - Run `DB_ENGINE=sqlite .venv/bin/python manage.py makemigrations --check --dry-run`.
  - Run `scripts/test_v2_regression.sh fast`.
  - Review `apps/inspection_v2/route_cover_images.py` for single-responsibility parsing/validation and no duplicated route-cover constants in `serializers.py`.
  - Review `apps/api_v2/checks.py` for deterministic object-storage checks with no network calls.
- [x] F3. Real Manual QA
  - Verify Task 4 HTTP evidence exists for storage preflight, login, create, update, invalid base64, and docs/schema.
  - Confirm create/update HTTP responses return object-storage URLs: host matches S3/MinIO endpoint/custom domain, path contains `/inspection/routes/covers/`, signed URL query is present when `AWS_QUERYSTRING_AUTH=true`, and URL does not start with `/media/`.
  - Confirm evidence is redacted: no bearer token, refresh token, cookie, or password remains in committed artifacts.
- [x] F4. Scope Fidelity Check
  - Run `git diff -- apps/inspection_v2 apps/api_v2 config scripts docs | rg -n "/api/v1|/api/v2/routes|coverImageBase64|ImageField|Pillow|PIL"`. Expected: no matches from this change.
  - Run `git diff --name-only` and confirm changed files are limited to this plan's source, test, settings, regression, docs, and evidence files plus any existing dirty files that were already part of prior work.

## Commit Strategy
- One commit is acceptable after all tasks pass: `feat(inspection-v2): accept base64 route covers on object storage`.
- Do not commit unrelated dirty files unless they are required by this plan. Current known unrelated dirty files from prior work include `.omo/boulder.json`, `.omo/start-work/ledger.jsonl`, `docs/v2-regression-testing.md`, `scripts/test_v2_regression.sh`, `apps/api_v2/test_schema_docs_sync.py`, `evidence/v2-docs-sync/`, `plans/v2-docs-code-sync.md`, and `scripts/compare_v2_docs_schema.py`; if this plan edits `scripts/test_v2_regression.sh` or `docs/v2-regression-testing.md`, preserve prior content and only add the base64-cover lines.

## Success Criteria
- Frontend can send route cover images as JSON `coverImage` base64 strings.
- Existing multipart route cover upload remains compatible.
- Route and mission responses continue exposing only `coverImageUrl`.
- `coverImageUrl` is generated by S3/MinIO-backed object storage through `route.cover_image.url`; it is not a filesystem `/media/` URL in object-storage deployments.
- Missing S3/MinIO configuration fails fast through Django checks when `OBJECT_STORAGE_BACKEND=s3|minio`.
- `/api/v2/docs/` and `/api/v2/docs/schema/` describe the implemented v2 route-save contract.
- Regression and manual QA evidence prove both success and failure paths.
