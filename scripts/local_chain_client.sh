#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8001}"
TENANT_CODE="${TENANT_CODE:-frontend_lab}"
USERNAME="${USERNAME:-fe_frontend_lab_tenant_admin}"
PASSWORD="${PASSWORD:-FrontTest@123}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-30}"
TMP_DIR="${TMP_DIR:-/tmp/dikong_client}"
CLIENT_ENABLE_ROUTE_UPLOAD="${CLIENT_ENABLE_ROUTE_UPLOAD:-0}"

mkdir -p "$TMP_DIR"

ensure_kmz() {
  if [ -f "$TMP_DIR/mock_route.kmz" ]; then
    return
  fi
  printf '%s\n' '<kml><Document><name>mock</name></Document></kml>' > "$TMP_DIR/doc.kml"
  (cd "$TMP_DIR" && zip -q -j mock_route.kmz doc.kml)
}

login_token() {
  local login_json token
  login_json="$(curl -sS -X POST "$BASE_URL/api/v1/iam/session/login" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}")"
  token="$(echo "$login_json" | jq -r '.data.accessToken // empty')"
  if [ -z "$token" ]; then
    echo "$login_json" >&2
    return 1
  fi
  printf '%s' "$token"
}

run_cycle() {
  local token pilot_id drones_json drone_id device_sn claim_code claim_resp capacity_json
  local video_id start_payload route_name route_resp route_id mission_name mission_payload
  local mission_resp mission_id media_json media_id timestamp route_list_json created_route_id
  local mission_list_json active_mission_id

  token="$(login_token)"

  pilot_id="$(curl -sS "$BASE_URL/api/v1/iam/tenant/members" \
    -H "Authorization: Bearer $token" \
    -H "X-Tenant-Code: $TENANT_CODE" | jq -r '.data.list[] | select(.roleCodes[]?=="pilot_operator") | .memberId' | head -n1)"
  if [ -z "$pilot_id" ]; then
    echo '{"ok":false,"stage":"pilot_lookup","message":"pilot member not found"}'
    return 1
  fi

  drones_json="$(curl -sS "$BASE_URL/api/v1/drones" \
    -H "Authorization: Bearer $token" \
    -H "X-Tenant-Code: $TENANT_CODE")"
  drone_id="$(echo "$drones_json" | jq -r '.data.list[0].id // empty')"
  device_sn="$(echo "$drones_json" | jq -r '.data.list[0].device_sn // empty')"

  if [ -z "$drone_id" ]; then
    device_sn="$(curl -sS "$BASE_URL/api/v1/drones/available" \
      -H "Authorization: Bearer $token" \
      -H "X-Tenant-Code: $TENANT_CODE" | jq -r '.data.list[0].device_sn // empty')"
    if [ -z "$device_sn" ]; then
      echo '{"ok":false,"stage":"drone_claim","message":"no available device"}'
      return 1
    fi
    claim_code="AUTO-$(date +%H%M%S)"
    claim_resp="$(curl -sS -X POST "$BASE_URL/api/v1/drones" \
      -H "Authorization: Bearer $token" \
      -H "X-Tenant-Code: $TENANT_CODE" \
      -H 'Content-Type: application/json' \
      -d "{\"code\":\"$claim_code\",\"device_sn\":\"$device_sn\"}")"
    drone_id="$(echo "$claim_resp" | jq -r '.data.id // empty')"
  fi

  if [ -z "$drone_id" ]; then
    echo '{"ok":false,"stage":"drone_lookup","message":"drone id not found"}'
    return 1
  fi

  capacity_json="$(curl -sS "$BASE_URL/api/v1/drones/$drone_id/live/capacity" \
    -H "Authorization: Bearer $token" \
    -H "X-Tenant-Code: $TENANT_CODE")"
  video_id="$(echo "$capacity_json" | jq -r '.data.cameras_list[0] as $c | ($c.videos_list[0].id // empty) as $v | if $v=="" then "" else (.data.sn + "/" + $c.index + "/" + $v) end')"
  if [ -n "$video_id" ]; then
    start_payload="$(jq -nc --arg video_id "$video_id" '{url_type:1,video_quality:2,video_id:$video_id}')"

    curl -sS -X POST "$BASE_URL/api/v1/drones/$drone_id/live/start" \
      -H "Authorization: Bearer $token" \
      -H "X-Tenant-Code: $TENANT_CODE" \
      -H 'Content-Type: application/json' \
      -d "$start_payload" > "$TMP_DIR/live_start.json"

    curl -sS -X POST "$BASE_URL/api/v1/drones/$drone_id/live/stop" \
      -H "Authorization: Bearer $token" \
      -H "X-Tenant-Code: $TENANT_CODE" \
      -H 'Content-Type: application/json' \
      -d '{}' > "$TMP_DIR/live_stop.json"
  else
    echo '{"ok":true,"stage":"live_skip","message":"no live capacity, skip live start/stop"}'
  fi

  route_list_json="$(curl -sS "$BASE_URL/api/v1/routes" \
    -H "Authorization: Bearer $token" \
    -H "X-Tenant-Code: $TENANT_CODE")"
  route_id="$(echo "$route_list_json" | jq -r '.data.list[0].id // empty')"

  if [ "$CLIENT_ENABLE_ROUTE_UPLOAD" = "1" ]; then
    route_name="AutoRoute-$(date +%H%M%S)"
    route_resp="$(curl -sS -X POST "$BASE_URL/api/v1/routes" \
      -H "Authorization: Bearer $token" \
      -H "X-Tenant-Code: $TENANT_CODE" \
      -F "name=$route_name" \
      -F "kmz_file=@$TMP_DIR/mock_route.kmz;type=application/vnd.google-earth.kmz")"
    created_route_id="$(echo "$route_resp" | jq -r '.data.id // empty')"
    if [ -n "$created_route_id" ]; then
      route_id="$created_route_id"
      curl -sS -X PUT "$BASE_URL/api/v1/routes/$route_id" \
        -H "Authorization: Bearer $token" \
        -H "X-Tenant-Code: $TENANT_CODE" \
        -H 'Content-Type: application/json' \
        -d "{\"name\":\"$route_name-updated\"}" > "$TMP_DIR/route_update.json"
    else
      echo "$route_resp"
      echo '{"ok":true,"stage":"route_create_skip","message":"route upload failed, fallback to existing route"}'
    fi
  fi

  if [ -z "$route_id" ]; then
    echo '{"ok":false,"stage":"route_lookup","message":"no route available"}'
    return 1
  fi

  mission_list_json="$(curl -sS "$BASE_URL/api/v1/missions" \
    -H "Authorization: Bearer $token" \
    -H "X-Tenant-Code: $TENANT_CODE")"
  active_mission_id="$(echo "$mission_list_json" | jq -r --argjson drone_id "$drone_id" '.data.list[]? | select(.drone == $drone_id and (.status == 0 or .status == 1)) | .id' | head -n1)"
  if [ -n "$active_mission_id" ]; then
    mission_id="$active_mission_id"
  else
    mission_name="AutoMission-$(date +%H%M%S)"
    mission_payload="$(jq -nc \
      --arg name "$mission_name" \
      --argjson route "$route_id" \
      --argjson drone "$drone_id" \
      --argjson pilot "$pilot_id" \
      '{name:$name,route:$route,drone:$drone,pilot:$pilot}')"
    mission_resp="$(curl -sS -X POST "$BASE_URL/api/v1/missions" \
      -H "Authorization: Bearer $token" \
      -H "X-Tenant-Code: $TENANT_CODE" \
      -H 'Content-Type: application/json' \
      -d "$mission_payload")"
    mission_id="$(echo "$mission_resp" | jq -r '.data.id // empty')"
    if [ -z "$mission_id" ]; then
      echo "$mission_resp"
      echo '{"ok":false,"stage":"mission_create","message":"mission create failed"}'
      return 1
    fi
  fi

  curl -sS -X POST "$BASE_URL/api/v1/missions/$mission_id/advance" \
    -H "Authorization: Bearer $token" \
    -H "X-Tenant-Code: $TENANT_CODE" \
    -H 'Content-Type: application/json' \
    -d '{}' > "$TMP_DIR/mission_advance_1.json"

  curl -sS -X POST "$BASE_URL/api/v1/missions/$mission_id/advance" \
    -H "Authorization: Bearer $token" \
    -H "X-Tenant-Code: $TENANT_CODE" \
    -H 'Content-Type: application/json' \
    -d '{}' > "$TMP_DIR/mission_advance_2.json"

  media_json="$(curl -sS "$BASE_URL/api/v1/media-files" \
    -H "Authorization: Bearer $token" \
    -H "X-Tenant-Code: $TENANT_CODE")"
  media_id="$(echo "$media_json" | jq -r '.data.list[]? | select((.media_type // 0) == 2) | .id' | head -n1)"
  if [ -n "$media_id" ]; then
    curl -sS "$BASE_URL/api/v1/media-files/$media_id/playback-url" \
      -H "Authorization: Bearer $token" \
      -H "X-Tenant-Code: $TENANT_CODE" > "$TMP_DIR/media_playback_url.json" || true
  fi

  timestamp="$(date -Iseconds)"
  jq -nc \
    --arg ts "$timestamp" \
    --arg drone_id "$drone_id" \
    --arg route_id "$route_id" \
    --arg mission_id "$mission_id" \
    --arg media_id "$media_id" \
    '{ok:true,timestamp:$ts,drone_id:$drone_id,route_id:$route_id,mission_id:$mission_id,media_id:$media_id}'
}

ensure_kmz

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
