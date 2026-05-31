# Sync Log Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split DJI sync logs from request logs, keep request-chain logs in `app.log*`, and let admin view request, sync, and error scopes separately without exposing per-file selection.

**Architecture:** Add a sync-run context so gateway JSON logs emitted during DJI sync carry a `sync_run_id`. Route sync-scoped JSON records into `sync.log*` / `sync.error.log*` with logging filters, while non-sync traffic continues to use `app.log*` / `error.log*`. Update the admin log viewer to select by scope (`request`, `sync`, `error`) and to group sync rows by `sync_run_id` when building chain cards.

**Tech Stack:** Django logging configuration, contextvars, Django admin template/view code, unittest-based Django tests.

---

### Task 1: Add sync log context and file routing

**Files:**
- Modify: `apps/access/request_logging.py`
- Modify: `config/logging_config.py`
- Modify: `apps/dji_bff/tasks.py`
- Modify: `apps/dji_bff/management/commands/run_dji_sync_scheduler.py`
- Test: `apps/dji_bff/test_logging.py`

- [ ] **Step 1: Write the failing tests**

Add one test that enters a sync log context, calls `DjiGateway.get_current_user()`, and asserts the JSON lands in `sync.log` instead of `app.log`.
Add one test that calls `DjiGateway` from normal request-less code and asserts logs still land in `app.log` / `error.log`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python manage.py test apps.dji_bff.test_logging --verbosity 2`
Expected: fail because `sync.log` handlers and `sync_run_id` context are not implemented yet.

- [ ] **Step 3: Write the minimal implementation**

Add a `current_sync_run_id` contextvar and a sync log context manager in `apps/access/request_logging.py`. Extend `log_json()` to include `sync_run_id` in the JSON record and on the log record.

Add logging filters and handlers in `config/logging_config.py` so:
- non-sync records go to `app.log` and `error.log`
- sync records go to `sync.log` and `sync.error.log`

Wrap `sync_device_indexes()` and `sync_media_indexes()` in the sync log context, and pass a shared `sync_run_id` from `run_dji_sync_scheduler.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python manage.py test apps.dji_bff.test_logging --verbosity 2`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add apps/access/request_logging.py config/logging_config.py apps/dji_bff/tasks.py apps/dji_bff/management/commands/run_dji_sync_scheduler.py apps/dji_bff/test_logging.py
git commit -m "feat: split sync logs from request logs"
```

### Task 2: Update admin log scopes and chain grouping

**Files:**
- Modify: `apps/access/admin.py`
- Modify: `apps/access/templates/admin/system_logs.html`
- Test: `apps/access/test_admin_system.py`

- [ ] **Step 1: Write the failing tests**

Add tests that:
- `scope=request` shows request logs from `app.log*`
- `scope=sync` shows sync logs from `sync.log*` / `sync.error.log*`
- `scope=error` shows error logs from `error.log*`
- the page no longer exposes file-selection UI
- sync rows group by `sync_run_id` instead of `request_id`

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python manage.py test apps.access.test_admin_system --verbosity 2`
Expected: fail because scope switching and sync grouping are not implemented yet.

- [ ] **Step 3: Write the minimal implementation**

Teach the admin log parser to read a `scope` parameter, resolve the right log families for each scope, and build chain cards from `sync_run_id` when the sync scope is active.

Replace the file selector UI with a scope selector and remove any display of per-file selection details.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python manage.py test apps.access.test_admin_system --verbosity 2`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add apps/access/admin.py apps/access/templates/admin/system_logs.html apps/access/test_admin_system.py
git commit -m "feat: add scoped admin log viewer"
```

### Task 3: Full verification

**Files:**
- All files changed above

- [ ] **Step 1: Run the full targeted test set**

Run:
`./.venv/bin/python manage.py test apps.access.test_admin_system apps.access.test_logging apps.dji_bff.test_logging --verbosity 2`

- [ ] **Step 2: Inspect the output**

Confirm:
- request logs still build chains
- sync logs are isolated to sync files
- error logs remain available in admin

- [ ] **Step 3: Commit if anything changed during verification**

Use the last feature commit message style if verification required a final fix.
