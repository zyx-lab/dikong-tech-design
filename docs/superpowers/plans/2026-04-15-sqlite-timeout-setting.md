# SQLite Timeout Setting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `OPTIONS.timeout = 20` to the default SQLite database settings so short lock contention waits instead of failing immediately.

**Architecture:** Keep the change fully local to the SQLite branch in `config/settings.py`. Lock the behavior with one lightweight settings test in an installed app test module, then verify Django still boots cleanly with `manage.py check`.

**Tech Stack:** Django 5.1, Django test runner, SQLite default database settings.

---

## File map and responsibilities

- **Modify:** `apps/access/tests.py`
  - Add one small configuration test that asserts the default SQLite database settings include `OPTIONS["timeout"] == 20`.
- **Modify:** `config/settings.py`
  - Add the SQLite `OPTIONS` block with `timeout: 20` and leave the PostgreSQL branch unchanged.

---

### Task 1: Add a SQLite timeout config test, implement the setting, and verify startup

**Files:**
- Modify: `apps/access/tests.py`
- Modify: `config/settings.py`
- Test: `apps/access/tests.py`

- [ ] **Step 1: Write the failing settings test**

Update `apps/access/tests.py` imports and add this test class above `AccessValidationHelperTests`:

```python
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from rest_framework import serializers


class DatabaseSettingsTests(SimpleTestCase):
    def test_sqlite_default_database_should_wait_20_seconds_for_locks(self):
        default_db = settings.DATABASES["default"]

        self.assertEqual(default_db["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(default_db["NAME"], settings.BASE_DIR / "db.sqlite3")
        self.assertEqual(default_db["OPTIONS"]["timeout"], 20)
```

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```bash
.venv/bin/python manage.py test apps.access.tests.DatabaseSettingsTests -v 2
```

Expected:

- FAIL with `KeyError: 'OPTIONS'` or an assertion failure showing the SQLite timeout is not configured yet

- [ ] **Step 3: Write the minimal settings change**

Update the SQLite branch in `config/settings.py`:

```python
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            "OPTIONS": {
                "timeout": 20,
            },
        }
    }
```

- [ ] **Step 4: Re-run the focused test to verify GREEN**

Run:

```bash
.venv/bin/python manage.py test apps.access.tests.DatabaseSettingsTests -v 2
```

Expected:

- PASS
- The new `DatabaseSettingsTests` case passes without changing any unrelated test output

- [ ] **Step 5: Run a Django config sanity check**

Run:

```bash
.venv/bin/python manage.py check
```

Expected:

- `System check identified no issues`

- [ ] **Step 6: Commit**

```bash
git add apps/access/tests.py config/settings.py
git commit -m "fix: add sqlite busy timeout"
```
