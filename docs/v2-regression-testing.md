# API v2 Regression Testing

Use `scripts/test_v2_regression.sh` as the local gate while developing API v2. It keeps the default loop focused on the v2 boundary instead of running the full project suite.

The script explicitly exports `DB_ENGINE=sqlite`, so v2 regression tests do not require a local PostgreSQL instance even though normal runtime defaults to PostgreSQL.

## Default v2 Gate

```bash
scripts/test_v2_regression.sh
```

This runs:

- `manage.py check`
- `manage.py makemigrations --check --dry-run`
- `apps.api_v2.tests`
- `apps.resource_v2.tests`
- `apps.workforce_v2.tests`
- `apps.inspection_v2.tests`

Use this for normal changes under `apps/api_v2`, `apps/iam_v2`, `apps/resource_v2`, `apps/workforce_v2`, `apps/inspection_v2`, and the v2 resource-permission design docs.

## Boundary Smoke Gate

```bash
scripts/test_v2_regression.sh boundary
```

This runs the default v2 gate plus a small legacy boundary smoke suite:

- business API response envelope smoke tests
- DJI v2 platform model tests
- mock DJI upstream contract tests
- selected internal DJI sync endpoint contract tests

Use this when v2 work touches shared response handling, DJI gateway behavior, schema/routing boundaries, or models reused by v1 and v2.

## Wider Legacy Gate

```bash
scripts/test_v2_regression.sh legacy
```

This keeps the previous broader regression command available without making it the default v2 development loop:

- `apps.api_v1.tests`
- `apps.dji_bff.tests`
- `apps.dji_bff.test_v2_platform_models`
- `apps.dji_mock.tests`

Run this before handoff if the change edits shared v1/DJI code directly. A full `manage.py test` remains a release-level check, not the default v2 iteration command.

## Useful Options

Pass Django test options after the mode:

```bash
scripts/test_v2_regression.sh boundary --keepdb
scripts/test_v2_regression.sh fast --verbosity 2
```

Skip checks when repeating the exact same code state:

```bash
RUN_CHECKS=0 scripts/test_v2_regression.sh fast --keepdb
```

The script enables `DJANGO_TEST_FAST_PASSWORD_HASHERS=1` by default so local regression tests use Django's fast MD5 test hasher instead of production password hashing. This only changes test process settings. Disable it when specifically testing password hasher configuration:

```bash
DJANGO_TEST_FAST_PASSWORD_HASHERS=0 scripts/test_v2_regression.sh fast
```

Override Python if needed:

```bash
PYTHON_BIN=.venv/bin/python scripts/test_v2_regression.sh
```
