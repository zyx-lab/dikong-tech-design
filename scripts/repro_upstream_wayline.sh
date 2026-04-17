#!/usr/bin/env bash
set -uo pipefail

BASE_URL="http://8.129.135.140"
USERNAME="adminPC"
PASSWORD="adminPC1234567890"
LOGIN_FLAG="1"

KMZ_PATH="${KMZ_PATH:-$(pwd)/routes/test.kmz}"
RUNS="${RUNS:-1}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-1}"
LOG_FILE="${LOG_FILE:-}"

if [[ ! -f "${KMZ_PATH}" ]]; then
  echo "kmz file not found: ${KMZ_PATH}" >&2
  exit 1
fi

if [[ ! "${RUNS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "RUNS must be a positive integer: ${RUNS}" >&2
  exit 1
fi

if [[ ! "${INTERVAL_SECONDS}" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "INTERVAL_SECONDS must be a non-negative number: ${INTERVAL_SECONDS}" >&2
  exit 1
fi

if [[ -n "${LOG_FILE}" ]]; then
  mkdir -p "$(dirname "${LOG_FILE}")"
  : > "${LOG_FILE}"
fi

write_line() {
  local line="$1"
  printf '%s\n' "${line}"
  if [[ -n "${LOG_FILE}" ]]; then
    printf '%s\n' "${line}" >> "${LOG_FILE}"
  fi
}

write_blank() {
  write_line ""
}

json_get() {
  local file_path="$1"
  local key_path="$2"
  python3 - "$file_path" "$key_path" <<'PY'
import json
import sys

file_path = sys.argv[1]
key_path = sys.argv[2]

with open(file_path, "r", encoding="utf-8") as fh:
    data = json.load(fh)

value = data
for part in key_path.split("."):
    if isinstance(value, list):
        value = value[int(part)]
    else:
        value = value[part]

if value is None:
    raise SystemExit(1)

if isinstance(value, (dict, list)):
    print(json.dumps(value, ensure_ascii=False))
else:
    print(value)
PY
}

http_status() {
  local header_path="$1"
  awk 'toupper($1) ~ /^HTTP\// { code = $2 } END { if (code != "") print code; else exit 1 }' "${header_path}"
}

header_value() {
  local header_path="$1"
  local header_name="$2"
  python3 - "$header_path" "$header_name" <<'PY'
import sys

header_path = sys.argv[1]
header_name = sys.argv[2].lower()

value = ""
with open(header_path, "r", encoding="utf-8", errors="replace") as fh:
    for raw_line in fh:
        line = raw_line.rstrip("\r\n")
        if ":" not in line:
            continue
        name, content = line.split(":", 1)
        if name.strip().lower() == header_name:
            value = content.strip()

if value:
    print(value)
else:
    raise SystemExit(1)
PY
}

body_snippet() {
  local body_path="$1"
  if [[ ! -f "${body_path}" ]]; then
    return 0
  fi
  python3 - "$body_path" <<'PY'
import sys

body_path = sys.argv[1]
with open(body_path, "r", encoding="utf-8", errors="replace") as fh:
    content = fh.read().replace("\r", " ").replace("\n", " ")
print(content[:400])
PY
}

object_key_from_url() {
  local url="$1"
  python3 - "$url" <<'PY'
import sys
from urllib.parse import unquote, urlsplit

path = urlsplit(sys.argv[1]).path.strip("/")
parts = path.split("/", 1)
if len(parts) == 2:
    print(unquote(parts[1]))
elif parts:
    print(unquote(parts[0]))
PY
}

emit_run_result() {
  local run_no="$1"
  local started_at="$2"
  local login_ok="$3"
  local workspace_ok="$4"
  local upload_ok="$5"
  local resolve_ok="$6"
  local chain_completed="$7"
  local workspace_id="$8"
  local wayline_id="$9"
  local resolved_url="${10}"
  local object_key="${11}"
  local final_status="${12}"
  local object_exists="${13}"
  local error_stage="${14}"
  local error_detail="${15}"
  local final_body="${16}"

  if (( RUNS > 1 )); then
    printf 'run_no=%s\n' "${run_no}"
    printf 'started_at=%s\n' "${started_at}"
  fi

  printf 'workspace_id=%s\n' "${workspace_id}"
  printf 'wayline_id=%s\n' "${wayline_id}"
  printf 'resolved_url=%s\n' "${resolved_url}"
  printf 'object_key=%s\n' "${object_key}"
  printf 'login_ok=%s\n' "${login_ok}"
  printf 'workspace_ok=%s\n' "${workspace_ok}"
  printf 'upload_ok=%s\n' "${upload_ok}"
  printf 'resolve_ok=%s\n' "${resolve_ok}"
  printf 'chain_completed=%s\n' "${chain_completed}"
  printf 'final_status=%s\n' "${final_status}"
  printf 'object_exists=%s\n' "${object_exists}"
  printf 'error_stage=%s\n' "${error_stage}"

  if [[ -n "${error_detail}" ]]; then
    printf 'error_detail=%s\n' "${error_detail}"
  fi
  if [[ -n "${final_body}" ]]; then
    printf 'final_body=%s\n' "${final_body}"
  fi

  if (( RUNS > 1 )); then
    printf '\n'
  fi
}

run_once() {
  local run_no="$1"
  local started_at
  started_at="$(date '+%Y-%m-%dT%H:%M:%S%z')"

  local login_ok="false"
  local workspace_ok="false"
  local upload_ok="false"
  local resolve_ok="false"
  local chain_completed="false"
  local workspace_id=""
  local wayline_id=""
  local resolved_url=""
  local object_key=""
  local final_status=""
  local object_exists="unknown"
  local error_stage="none"
  local error_detail=""
  local final_body=""
  local route_name="repro-upstream-wayline-$(date +%s)-${run_no}"
  local run_exit_code=0

  local tmp_dir
  tmp_dir="$(mktemp -d)"

  local login_body="${tmp_dir}/login.json"
  local login_stderr="${tmp_dir}/login.stderr"
  if ! curl -sS \
    -X POST \
    -H "Content-Type: application/json" \
    --data "{\"username\":\"${USERNAME}\",\"password\":\"${PASSWORD}\",\"flag\":${LOGIN_FLAG}}" \
    -o "${login_body}" \
    "${BASE_URL}/api/v1/manage/login" \
    2>"${login_stderr}"; then
    error_stage="login_request"
    error_detail="$(body_snippet "${login_stderr}")"
    run_exit_code=1
  fi

  local access_token=""
  if (( run_exit_code == 0 )); then
    if ! access_token="$(json_get "${login_body}" "data.access_token")"; then
      error_stage="login_parse"
      error_detail="$(body_snippet "${login_body}")"
      run_exit_code=1
    else
      login_ok="true"
    fi
  fi

  local workspace_body="${tmp_dir}/workspace.json"
  local workspace_stderr="${tmp_dir}/workspace.stderr"
  if (( run_exit_code == 0 )); then
    if ! curl -sS \
      -H "x-auth-token: ${access_token}" \
      -o "${workspace_body}" \
      "${BASE_URL}/api/v1/manage/workspaces/current" \
      2>"${workspace_stderr}"; then
      error_stage="workspace_request"
      error_detail="$(body_snippet "${workspace_stderr}")"
      run_exit_code=1
    fi
  fi

  if (( run_exit_code == 0 )); then
    if ! workspace_id="$(json_get "${workspace_body}" "data.workspace_id")"; then
      error_stage="workspace_parse"
      error_detail="$(body_snippet "${workspace_body}")"
      run_exit_code=1
    else
      workspace_ok="true"
    fi
  fi

  local upload_body="${tmp_dir}/upload.json"
  local upload_stderr="${tmp_dir}/upload.stderr"
  if (( run_exit_code == 0 )); then
    if ! curl -sS \
      -X POST \
      -H "x-auth-token: ${access_token}" \
      -F "name=${route_name}" \
      -F "file=@${KMZ_PATH};type=application/vnd.google-earth.kmz" \
      -o "${upload_body}" \
      "${BASE_URL}/api/v1/wayline/workspaces/${workspace_id}/waylines/files/upload" \
      2>"${upload_stderr}"; then
      error_stage="upload_request"
      error_detail="$(body_snippet "${upload_stderr}")"
      run_exit_code=1
    fi
  fi

  if (( run_exit_code == 0 )); then
    if ! wayline_id="$(json_get "${upload_body}" "data.wayline_id")"; then
      error_stage="upload_parse"
      error_detail="$(body_snippet "${upload_body}")"
      run_exit_code=1
    else
      upload_ok="true"
    fi
  fi

  local resolve_headers="${tmp_dir}/resolve.headers"
  local resolve_body="${tmp_dir}/resolve.body"
  local resolve_stderr="${tmp_dir}/resolve.stderr"
  if (( run_exit_code == 0 )); then
    if ! curl -sS \
      -H "x-auth-token: ${access_token}" \
      -D "${resolve_headers}" \
      -o "${resolve_body}" \
      "${BASE_URL}/api/v1/wayline/workspaces/${workspace_id}/waylines/${wayline_id}/url" \
      2>"${resolve_stderr}"; then
      error_stage="resolve_request"
      error_detail="$(body_snippet "${resolve_stderr}")"
      run_exit_code=1
    fi
  fi

  if (( run_exit_code == 0 )); then
    if ! resolved_url="$(header_value "${resolve_headers}" "Location")"; then
      error_stage="resolve_parse"
      error_detail="$(body_snippet "${resolve_body}")"
      run_exit_code=1
    else
      object_key="$(object_key_from_url "${resolved_url}")"
      resolve_ok="true"
    fi
  fi

  local final_headers="${tmp_dir}/final.headers"
  local final_body_path="${tmp_dir}/final.body"
  local final_stderr="${tmp_dir}/final.stderr"
  if (( run_exit_code == 0 )); then
    if ! curl -sS \
      -D "${final_headers}" \
      -o "${final_body_path}" \
      "${resolved_url}" \
      2>"${final_stderr}"; then
      error_stage="final_request"
      error_detail="$(body_snippet "${final_stderr}")"
      run_exit_code=1
    fi
  fi

  if (( run_exit_code == 0 )); then
    if ! final_status="$(http_status "${final_headers}")"; then
      error_stage="final_status_parse"
      run_exit_code=1
    else
      chain_completed="true"
      case "${final_status}" in
        200)
          object_exists="true"
          ;;
        404)
          object_exists="false"
          error_stage="object_404"
          final_body="$(body_snippet "${final_body_path}")"
          ;;
        *)
          object_exists="false"
          error_stage="final_status_${final_status}"
          final_body="$(body_snippet "${final_body_path}")"
          run_exit_code=1
          ;;
      esac
    fi
  fi

  emit_run_result \
    "${run_no}" \
    "${started_at}" \
    "${login_ok}" \
    "${workspace_ok}" \
    "${upload_ok}" \
    "${resolve_ok}" \
    "${chain_completed}" \
    "${workspace_id}" \
    "${wayline_id}" \
    "${resolved_url}" \
    "${object_key}" \
    "${final_status}" \
    "${object_exists}" \
    "${error_stage}" \
    "${error_detail}" \
    "${final_body}"

  rm -rf "${tmp_dir}"
  return "${run_exit_code}"
}

success_runs=0
object_404_runs=0
other_fail_runs=0
chain_completed_runs=0
declare -A object_keys_seen=()
declare -A error_stage_counts=()

if (( RUNS > 1 )); then
  write_line "runs=${RUNS}"
  write_line "interval_seconds=${INTERVAL_SECONDS}"
  write_line "kmz_path=${KMZ_PATH}"
  if [[ -n "${LOG_FILE}" ]]; then
    write_line "log_file=${LOG_FILE}"
  fi
  write_blank
fi

for run_no in $(seq 1 "${RUNS}"); do
  if run_output="$(run_once "${run_no}")"; then
    :
  else
    :
  fi

  while IFS= read -r line; do
    if [[ "${line}" == object_key=* ]]; then
      object_key_value="${line#object_key=}"
      if [[ -n "${object_key_value}" ]]; then
        object_keys_seen["${object_key_value}"]=1
      fi
    fi
    if [[ "${line}" == chain_completed=true ]]; then
      chain_completed_runs=$((chain_completed_runs + 1))
    fi
    if [[ "${line}" == final_status=200 ]]; then
      success_runs=$((success_runs + 1))
    fi
    if [[ "${line}" == final_status=404 ]]; then
      object_404_runs=$((object_404_runs + 1))
    fi
    if [[ "${line}" == error_stage=* ]]; then
      error_stage_value="${line#error_stage=}"
      error_stage_counts["${error_stage_value}"]=$(( ${error_stage_counts["${error_stage_value}"]:-0} + 1 ))
    fi
  done <<< "${run_output}"

  write_line "${run_output}"

  if ! grep -q '^chain_completed=true$' <<< "${run_output}"; then
    other_fail_runs=$((other_fail_runs + 1))
  elif ! grep -Eq '^final_status=(200|404)$' <<< "${run_output}"; then
    other_fail_runs=$((other_fail_runs + 1))
  fi

  if (( run_no < RUNS )); then
    sleep "${INTERVAL_SECONDS}"
  fi
done

if (( RUNS > 1 )); then
  object_keys_csv=""
  if (( ${#object_keys_seen[@]} > 0 )); then
    while IFS= read -r item; do
      [[ -z "${item}" ]] && continue
      if [[ -n "${object_keys_csv}" ]]; then
        object_keys_csv+=","
      fi
      object_keys_csv+="${item}"
    done < <(printf '%s\n' "${!object_keys_seen[@]}" | sort)
  fi

  error_stages_csv=""
  if (( ${#error_stage_counts[@]} > 0 )); then
    while IFS= read -r item; do
      [[ -z "${item}" ]] && continue
      if [[ -n "${error_stages_csv}" ]]; then
        error_stages_csv+=","
      fi
      error_stages_csv+="${item}"
    done < <(
      for key in "${!error_stage_counts[@]}"; do
        printf '%s:%s\n' "${key}" "${error_stage_counts[${key}]}"
      done | sort
    )
  fi

  write_line "summary_total_runs=${RUNS}"
  write_line "summary_chain_completed_runs=${chain_completed_runs}"
  write_line "summary_success_runs=${success_runs}"
  write_line "summary_object_404_runs=${object_404_runs}"
  write_line "summary_other_fail_runs=${other_fail_runs}"
  write_line "summary_distinct_object_keys=${#object_keys_seen[@]}"
  if [[ -n "${object_keys_csv}" ]]; then
    write_line "summary_object_keys=${object_keys_csv}"
  fi
  if [[ -n "${error_stages_csv}" ]]; then
    write_line "summary_error_stages=${error_stages_csv}"
  fi
fi

if (( other_fail_runs > 0 )); then
  exit 1
fi

exit 0
