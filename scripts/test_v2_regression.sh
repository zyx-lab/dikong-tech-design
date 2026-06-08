#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'EOF'
Usage: scripts/test_v2_regression.sh [fast|boundary] [django-test-options...]

Modes:
  fast      Run the default v2 development gate.
  boundary  Run fast plus the preserved DJI upstream mock smoke suite.

Environment:
  PYTHON_BIN=path/to/python  Override Python. Defaults to .venv/bin/python.
  RUN_CHECKS=0               Skip manage.py check and migration dry-run.
  DJANGO_TEST_FAST_PASSWORD_HASHERS=0
                             Disable the local test-only fast password hasher.

Examples:
  scripts/test_v2_regression.sh
  scripts/test_v2_regression.sh boundary --keepdb
  RUN_CHECKS=0 scripts/test_v2_regression.sh fast --verbosity 2
EOF
}

if [[ $# -gt 0 && ( "$1" == "help" || "$1" == "-h" || "$1" == "--help" ) ]]; then
  usage
  exit 0
fi

mode="fast"
if [[ $# -gt 0 && "$1" != -* ]]; then
  mode="$1"
  shift
fi

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

export DB_ENGINE=sqlite
export DJANGO_TEST_FAST_PASSWORD_HASHERS="${DJANGO_TEST_FAST_PASSWORD_HASHERS:-1}"

run() {
  printf '\n==> %s\n' "$*"
  "$@"
}

if [[ "${RUN_CHECKS:-1}" != "0" ]]; then
  run "$PYTHON_BIN" manage.py check
  run "$PYTHON_BIN" manage.py makemigrations --check --dry-run
fi

case "$mode" in
  fast)
    labels=(
      apps.api_v2.tests
      apps.api_v2.test_schema_docs_sync
      apps.api_v2.test_route_cover_base64_schema
      apps.api_v2.test_object_storage_settings
      apps.iam_v2.test_account_role_profiles_api
      apps.iam_v2.test_profile_api
      apps.resource_v2.tests
      apps.inspection_v2.test_pilot_profiles_api
      apps.inspection_v2.tests
      apps.inspection_v2.test_route_cover_base64
      apps.inspection_v2.test_route_cover_object_storage
    )
    ;;
  boundary)
    labels=(
      apps.api_v2.tests
      apps.api_v2.test_schema_docs_sync
      apps.api_v2.test_route_cover_base64_schema
      apps.api_v2.test_object_storage_settings
      apps.iam_v2.test_account_role_profiles_api
      apps.iam_v2.test_profile_api
      apps.resource_v2.tests
      apps.inspection_v2.test_pilot_profiles_api
      apps.inspection_v2.tests
      apps.inspection_v2.test_route_cover_base64
      apps.inspection_v2.test_route_cover_object_storage
      apps.dji_mock.tests.DjiMockServerTests
    )
    ;;
  *)
    printf 'Unknown mode: %s\n\n' "$mode" >&2
    usage >&2
    exit 2
    ;;
esac

run "$PYTHON_BIN" manage.py test "${labels[@]}" "$@"
