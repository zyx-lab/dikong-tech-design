# Low-Risk Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify duplicated control flow in DJI internal views and route upload/update flow, plus remove unnecessary serializer method wrappers, without changing behavior.

**Architecture:** Keep the public API contract, models, and persistence semantics unchanged. Add focused regression tests around the duplicated paths, then extract private helpers inside existing modules so the write surface stays small.

**Tech Stack:** Django, Django REST Framework, drf-spectacular, unittest-based test suite

---

### Task 1: Lock regression coverage for internal sync wrappers and serializer output

**Files:**
- Modify: `apps/dji_bff/tests.py`
- Modify: `apps/media_file/tests.py`
- Test: `apps/dji_bff/tests.py`
- Test: `apps/media_file/tests.py`

- [ ] **Step 1: Write the failing tests**

Add a test in `apps/dji_bff/tests.py` that proves callback endpoints reject non-POST methods with `404`, and a test in `apps/media_file/tests.py` that proves the media read payload still exposes `mission_id`, `device_sn`, `dji_file_id`, `sync_status`, and `last_sync_at` exactly as before.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test apps.dji_bff.tests.DjiBffSyncTests.test_media_callback_should_reject_non_post apps.media_file.tests.MediaFileApiTests.test_list_should_expose_media_read_model_fields`
Expected: FAIL because the new regression tests do not exist yet.

- [ ] **Step 3: Write the minimal test implementation**

Use existing APIClient fixtures and assertions already present in those test modules. Do not introduce new factories.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test apps.dji_bff.tests.DjiBffSyncTests.test_media_callback_should_reject_non_post apps.media_file.tests.MediaFileApiTests.test_list_should_expose_media_read_model_fields`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/dji_bff/tests.py apps/media_file/tests.py
git commit -m "test: lock low-risk refactor coverage"
```

### Task 2: Refactor duplicated internal view wrapper flow

**Files:**
- Modify: `apps/dji_bff/views.py`
- Test: `apps/dji_bff/tests.py`

- [ ] **Step 1: Keep the tests red if behavior drifts**

Re-run the Task 1 callback method test before touching `apps/dji_bff/views.py`.

- [ ] **Step 2: Write minimal implementation**

Extract one private helper in `apps/dji_bff/views.py` that enforces internal POST preconditions and optionally parses JSON before invoking a handler. Keep error payloads and endpoint names unchanged.

- [ ] **Step 3: Run focused tests**

Run: `python manage.py test apps.dji_bff.tests.DjiBffSyncTests`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add apps/dji_bff/views.py apps/dji_bff/tests.py
git commit -m "refactor(dji_bff): remove duplicate internal view wrappers"
```

### Task 3: Refactor route upload/update shared flow

**Files:**
- Modify: `apps/route/views.py`
- Test: `apps/route/tests.py`

- [ ] **Step 1: Write the failing test**

Add one regression test in `apps/route/tests.py` covering the update path when a new wayline replaces an old wayline, asserting the new index values are persisted and the stale upstream wayline is scheduled for deletion. Use the existing mocked gateway patterns.

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.route.tests.RouteApiTests.test_update_should_replace_wayline_and_cleanup_old_upstream_route`
Expected: FAIL because the new regression test does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Extract helper(s) inside `apps/route/views.py` for:
- obtaining `kmz_file`
- uploading to DJI and returning normalized upstream identifiers
- persisting `TenantRouteIndex`
Keep create/update responses and rollback behavior unchanged.

- [ ] **Step 4: Run focused tests**

Run: `python manage.py test apps.route.tests.RouteApiTests`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/route/views.py apps/route/tests.py
git commit -m "refactor(route): share upload and index sync flow"
```

### Task 4: Simplify media read serializer field definitions

**Files:**
- Modify: `apps/media_file/serializers.py`
- Test: `apps/media_file/tests.py`

- [ ] **Step 1: Keep the serializer regression test in place**

Re-run `apps.media_file.tests.MediaFileApiTests.test_list_should_expose_media_read_model_fields` before editing the serializer.

- [ ] **Step 2: Write minimal implementation**

Replace `SerializerMethodField` usages that only forward model or related-object data with direct read-only fields using `source` where appropriate. Preserve null/empty-string behavior.

- [ ] **Step 3: Run focused tests**

Run: `python manage.py test apps.media_file.tests.MediaFileApiTests apps.media_file.test_live_api.LiveMediaFileApiTestCase`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add apps/media_file/serializers.py apps/media_file/tests.py
git commit -m "refactor(media_file): simplify read serializer fields"
```
