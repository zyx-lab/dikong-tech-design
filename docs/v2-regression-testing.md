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
- `apps.api_v2.test_schema_docs_sync`
- `apps.api_v2.test_route_cover_base64_schema`
- `apps.api_v2.test_object_storage_settings`
- `apps.iam_v2.test_profile_api`
- `apps.resource_v2.tests`
- `apps.workforce_v2.tests`
- `apps.inspection_v2.tests`
- `apps.inspection_v2.test_route_cover_base64`
- `apps.inspection_v2.test_route_cover_object_storage`

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

## API v2 Docs Sync

The default v2 gate is the deterministic local check for docs/code synchronization. It verifies the v2-only schema boundary, route/method parity, no tenant/v1 leakage, and representative field-level contracts for IAM, resource, workforce, and inspection.

Use the live parity command only as an explicit network check:

```bash
DB_ENGINE=sqlite .venv/bin/python scripts/compare_v2_docs_schema.py \
  --live-url http://110.42.32.122:8001/api/v2/docs/schema/ \
  --local-django \
  --write-local evidence/v2-docs-sync/local-schema.json \
  --output-json evidence/v2-docs-sync/schema-parity-result.json
```

The diff output groups drift into `info_diff`, `paths_only_live`, `paths_only_local`, `method_diffs`, `operation_diffs`, and `component_diffs`. A no-drift run exits `0`; detected drift exits `1`; malformed input, missing files, or network/read failures exit `2`.

For HTTP evidence, use `curl -sS -i` so headers are captured without progress output corrupting JSON bodies:

```bash
curl -sS -i http://110.42.32.122:8001/api/v2/docs/ > evidence/v2-docs-sync/http-docs-page.txt
curl -sS -i http://110.42.32.122:8001/api/v2/docs/schema/ > evidence/v2-docs-sync/http-docs-schema.txt
```

No credentials are required for the schema parity command. Do not commit cookies, bearer tokens, refresh tokens, passwords, or DB credentials in docs-sync evidence. If `curl -i` captures `Set-Cookie`, redact the value before keeping the artifact.

If a local change alters the generated v2 schema, live no-drift is not a valid final claim until the live server at `110.42.32.122:8001` is redeployed or restarted with the changed code. Otherwise record the live parity state as deployment-blocked.

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
