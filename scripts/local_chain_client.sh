#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8001}"
USERNAME="${USERNAME:-jnu_super}"
PASSWORD="${PASSWORD:-FrontTest@123}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-30}"
TMP_DIR="${TMP_DIR:-/tmp/dikong_v2_client}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

mkdir -p "$TMP_DIR"

json_access_token() {
  "$PYTHON_BIN" -c 'import json, sys; print((json.load(sys.stdin).get("data") or {}).get("accessToken") or "")'
}

json_code() {
  "$PYTHON_BIN" -c 'import json, sys; print(json.load(sys.stdin).get("code") or "")'
}

json_summary() {
  "$PYTHON_BIN" -c '
import json, sys
payload = {
    "ok": True,
    "timestamp": sys.argv[1],
    "username": sys.argv[2],
    "artifact_dir": sys.argv[3],
}
print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
' "$@"
}

login_token() {
  local login_json token
  login_json="$(curl -sS -X POST "$BASE_URL/api/v2/iam/session/login" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}")"
  token="$(printf '%s' "$login_json" | json_access_token)"
  if [ -z "$token" ]; then
    echo "$login_json" >&2
    return 1
  fi
  printf '%s' "$token"
}

get_endpoint() {
  local token="$1"
  local name="$2"
  local path="$3"
  local response code
  response="$(curl -sS "$BASE_URL$path" -H "Authorization: Bearer $token")"
  printf '%s\n' "$response" > "$TMP_DIR/$name.json"
  code="$(printf '%s' "$response" | json_code)"
  if [ "$code" != "00000" ]; then
    echo "$response" >&2
    return 1
  fi
}

run_cycle() {
  local token timestamp

  token="$(login_token)"

  get_endpoint "$token" "iam_context" "/api/v2/iam/me/context"
  get_endpoint "$token" "iam_profile" "/api/v2/iam/me/profile"
  get_endpoint "$token" "resource_summary" "/api/v2/resource/summary"
  get_endpoint "$token" "resource_drones" "/api/v2/resource/drones"
  get_endpoint "$token" "resource_docks" "/api/v2/resource/docks"
  get_endpoint "$token" "resource_gateways" "/api/v2/resource/gateways"
  get_endpoint "$token" "iam_profile_types" "/api/v2/iam/profile-types"
  get_endpoint "$token" "iam_pilot_candidates" "/api/v2/iam/accounts?roleCode=pilot&profileType=pilot&qualified=true"
  get_endpoint "$token" "inspection_routes" "/api/v2/inspection/routes"
  get_endpoint "$token" "inspection_missions" "/api/v2/inspection/missions"
  get_endpoint "$token" "inspection_active_flights" "/api/v2/inspection/active-flights"

  timestamp="$(date -Iseconds)"
  json_summary "$timestamp" "$USERNAME" "$TMP_DIR"
}

if [ "${1:-}" = "--once" ]; then
  run_cycle
  exit 0
fi

while true; do
  if ! run_cycle; then
    sleep 5
    continue
  fi
  sleep "$INTERVAL_SECONDS"
done
