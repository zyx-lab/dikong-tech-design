# V2 End-to-End Validation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove whether the v2 inspection platform can run the minimum end-to-end path from login to mission creation, DJI execution, status refresh, realtime update, and frontend-readable results.

**Architecture:** Treat automated local regression as the baseline, then validate a deployed compose environment, then run a single minimum business chain against mock DJI first and real DJI only after the mock path is stable. Each task produces evidence that separates "confirmed running" from "not yet confirmed".

**Tech Stack:** Django, Django REST Framework, Channels/WebSocket, Docker Compose, PostgreSQL, Redis, MinIO, v2 DJI worker, DJI mock/real upstream, curl, `scripts/test_v2_regression.sh`, `scripts/local_chain_client.sh`.

---

## Evidence Directory

Use this directory for all command outputs:

```bash
mkdir -p evidence/v2-e2e-2026-06-07
```

Every command below should redirect stdout/stderr into this directory. Do not store cookies, bearer tokens, refresh tokens, passwords, MinIO secrets, DJI tokens, or database credentials in evidence files.

## Task 1: Confirm Local Automated Regression

**Files:**
- Read: `scripts/test_v2_regression.sh`
- Read: `docs/v2-regression-testing.md`
- Evidence: `evidence/v2-e2e-2026-06-07/regression-boundary.txt`

- [ ] **Step 1: Run boundary regression with writable log dir**

```bash
DJANGO_LOG_DIR=/tmp/dikong-test-logs \
scripts/test_v2_regression.sh boundary --keepdb \
  > evidence/v2-e2e-2026-06-07/regression-boundary.txt 2>&1
```

Expected: command exits `0`, includes `Ran 159 tests`, and ends with `OK`.

- [ ] **Step 2: Record deterministic status**

If Step 1 passes, report:

```text
已跑通：本地 v2 自动化回归和 v1/DJI 边界烟测，159 个测试通过。
未证明：部署环境、前端联调、真实 DJI 云任务执行和真实实时状态回传。
```

If Step 1 fails before business tests with `PermissionError` under `logs/app.log`, use `DJANGO_LOG_DIR=/tmp/dikong-test-logs` and rerun. That failure is an environment log-permission issue, not a business regression.

## Task 2: Confirm Compose Services Can Start

**Files:**
- Read: `DEPLOY.md`
- Read: `docker-compose.yml`
- Evidence: `evidence/v2-e2e-2026-06-07/compose-up.txt`
- Evidence: `evidence/v2-e2e-2026-06-07/compose-ps.txt`
- Evidence: `evidence/v2-e2e-2026-06-07/schema-health.txt`
- Evidence: `evidence/v2-e2e-2026-06-07/minio-health.txt`
- Evidence: `evidence/v2-e2e-2026-06-07/worker-once.txt`

- [ ] **Step 1: Start the minimum v2 compose stack**

```bash
OBJECT_STORAGE_ENDPOINT_URL=http://127.0.0.1:9000 \
docker compose up -d --no-build db redis minio minio-init web v2-dji-worker \
  > evidence/v2-e2e-2026-06-07/compose-up.txt 2>&1
```

Expected: command exits `0`.

- [ ] **Step 2: Verify service status**

```bash
docker compose ps > evidence/v2-e2e-2026-06-07/compose-ps.txt 2>&1
```

Expected: `web` and `v2-dji-worker` are `Up`; `db`, `redis`, and `minio` are `Up` or `healthy`.

- [ ] **Step 3: Verify schema endpoint**

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:8000/api/v2/docs/schema/ \
  > evidence/v2-e2e-2026-06-07/schema-health.txt 2>&1
```

Expected: file contains `200`.

- [ ] **Step 4: Verify MinIO health**

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:9000/minio/health/live \
  > evidence/v2-e2e-2026-06-07/minio-health.txt 2>&1
```

Expected: file contains `200`.

- [ ] **Step 5: Verify worker can run one check cycle**

```bash
docker compose exec -T web python manage.py run_v2_dji_worker --once \
  > evidence/v2-e2e-2026-06-07/worker-once.txt 2>&1
```

Expected: command exits `0` and reports the number of checked DJI connections.

## Task 3: Confirm Read-Only API Chain

**Files:**
- Read: `scripts/local_chain_client.sh`
- Evidence directory: `/tmp/dikong_v2_client`
- Evidence: `evidence/v2-e2e-2026-06-07/local-chain-once.txt`

- [ ] **Step 1: Run the local chain client**

```bash
BASE_URL=http://127.0.0.1:8000 \
USERNAME=biz_root \
PASSWORD=admin123 \
TMP_DIR=/tmp/dikong_v2_client \
scripts/local_chain_client.sh --once \
  > evidence/v2-e2e-2026-06-07/local-chain-once.txt 2>&1
```

Expected: command exits `0` and prints JSON with `"ok":true`.

- [ ] **Step 2: Verify generated API artifacts**

```bash
ls -1 /tmp/dikong_v2_client \
  > evidence/v2-e2e-2026-06-07/local-chain-files.txt 2>&1
```

Expected: includes `iam_context.json`, `resource_summary.json`, `resource_drones.json`, `resource_docks.json`, `inspection_routes.json`, `inspection_missions.json`, and `inspection_active_flights.json`.

Deterministic report after this task:

```text
已跑通：登录和只读查询链路。
未跑通：创建航线、创建任务、启动 DJI 执行、状态回传和前端实时展示。
```

## Task 4: Confirm Minimum Mutation Chain With Mock DJI

**Files:**
- Read: `docs/api-v2-frontend-guide.md`
- Evidence: `evidence/v2-e2e-2026-06-07/mock-mutation-chain.txt`
- Create during test: `/tmp/dikong_v2_client/test-route.kmz`

- [ ] **Step 1: Prepare a small KMZ test file**

```bash
python3 - <<'PY' > evidence/v2-e2e-2026-06-07/create-kmz.txt 2>&1
from pathlib import Path
from zipfile import ZipFile
path = Path("/tmp/dikong_v2_client/test-route.kmz")
path.parent.mkdir(parents=True, exist_ok=True)
with ZipFile(path, "w") as zf:
    zf.writestr("wpmz/template.kml", "<kml><Document><name>test</name></Document></kml>")
print(path)
PY
```

Expected: command exits `0` and prints `/tmp/dikong_v2_client/test-route.kmz`.

- [ ] **Step 2: Login and save token in shell only**

```bash
TOKEN="$(
  curl -sS -X POST http://127.0.0.1:8000/api/v2/iam/session/login \
    -H 'Content-Type: application/json' \
    -d '{"username":"biz_root","password":"admin123"}' \
  | python3 -c 'import json,sys; print((json.load(sys.stdin).get("data") or {}).get("accessToken") or "")'
)"
test -n "$TOKEN"
```

Expected: shell variable `TOKEN` is non-empty. Do not write token to evidence.

- [ ] **Step 3: Create a route with KMZ**

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v2/inspection/routes \
  -H "Authorization: Bearer $TOKEN" \
  -F "name=E2E Test Route" \
  -F "waylineType=0" \
  -F "kmzFile=@/tmp/dikong_v2_client/test-route.kmz;type=application/octet-stream" \
  > evidence/v2-e2e-2026-06-07/create-route.json
```

Expected: JSON has `code` equal to `00000`, returns local route id, and returns `data.djiFile.djiFileId`.

- [ ] **Step 4: Create a mission from the route**

Before running, read `docs/api-v2-frontend-guide.md` and the route creation response to provide the actual `routeId`, visible resource ids, and pilot account profile id. Use the exact request shape documented in the guide.

Expected: JSON has `code` equal to `00000` and returns a mission id in pending state.

- [ ] **Step 5: Run preflight check**

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/v2/inspection/missions/${MISSION_ID}/preflight-check" \
  -H "Authorization: Bearer $TOKEN" \
  > evidence/v2-e2e-2026-06-07/preflight-check.json
```

Expected: JSON has `code` equal to `00000`. If `data.canStart=false`, record `data.blockingReasons` and stop this task; the mutation chain has not reached DJI execution.

- [ ] **Step 6: Start the mission**

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/v2/inspection/missions/${MISSION_ID}/start" \
  -H "Authorization: Bearer $TOKEN" \
  > evidence/v2-e2e-2026-06-07/start-mission.json
```

Expected: JSON has `code` equal to `00000`, mission enters running/executing state, and `data.cloudExecution.djiJobId` is present.

- [ ] **Step 7: Refresh cloud execution**

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/v2/inspection/missions/${MISSION_ID}/cloud-execution/refresh" \
  -H "Authorization: Bearer $TOKEN" \
  > evidence/v2-e2e-2026-06-07/refresh-execution.json
```

Expected: JSON has `code` equal to `00000`, and `data.cloudExecution.status` is populated.

Deterministic report after this task:

```text
已跑通：mock DJI 环境下的航线创建、任务创建、preflight、任务启动和执行刷新。
未跑通：真实 DJI 云、真实设备状态、前端页面操作和真实 WebSocket 展示。
```

## Task 5: Confirm Realtime WebSocket Path

**Files:**
- Read: `apps/resource_v2/test_mqtt_ws.py`
- Evidence: `evidence/v2-e2e-2026-06-07/websocket-realtime.txt`

- [ ] **Step 1: Run the automated WebSocket test**

```bash
DJANGO_LOG_DIR=/tmp/dikong-test-logs \
.venv/bin/python manage.py test apps.resource_v2.test_mqtt_ws --keepdb \
  > evidence/v2-e2e-2026-06-07/websocket-realtime.txt 2>&1
```

Expected: command exits `0`, and the test verifies authentication, subscription acknowledgement, and realtime-only message streaming.

- [ ] **Step 2: Record limitation**

Report this exactly if Step 1 passes:

```text
已跑通：自动化测试中的 WebSocket 鉴权、订阅和实时消息推送语义。
未跑通：浏览器前端页面上的真实 WebSocket 展示，以及真实 MQTT broker 持续消息流。
```

## Task 6: Real DJI and Frontend Joint Validation

**Files:**
- Read: `docs/api-v2-frontend-guide.md`
- Read: `DEPLOY.md`
- Evidence: `evidence/v2-e2e-2026-06-07/real-dji-frontend-checklist.md`

- [ ] **Step 1: Record required real-environment inputs**

Create `evidence/v2-e2e-2026-06-07/real-dji-frontend-checklist.md` with:

```markdown
# Real DJI + Frontend Validation Checklist

- [ ] Real DJI workspace is configured.
- [ ] At least one drone/dock/gateway is online.
- [ ] DJI MQTT broker credentials are valid.
- [ ] v2-dji-worker is running and subscribed.
- [ ] Frontend can login with a v2 business account.
- [ ] Frontend can select resources visible to that account.
- [ ] Frontend can create route and mission from UI.
- [ ] Frontend can start mission from UI.
- [ ] Frontend can show cloud execution status.
- [ ] Frontend can show realtime status updates.
- [ ] Failure states are visible to frontend users.
```

Expected: all items are explicitly marked pass/fail during joint validation.

- [ ] **Step 2: Produce final deterministic status**

Use only these categories:

```text
已跑通：只能列有 evidence 文件或测试输出证明的项目。
未跑通：已经尝试但失败的项目，必须写失败原因。
未验证：没有执行过的真实环境或前端链路。
下一步：只列阻塞端到端闭环的事项。
```

Do not write "基本完成", "大体打通", "理论上可用", or "应该没问题" unless there is a passing evidence file for the exact claim.

---

## Current Known Evidence As Of 2026-06-07

**Confirmed running:**

- `DJANGO_LOG_DIR=/tmp/dikong-test-logs scripts/test_v2_regression.sh fast --keepdb` passed locally: 140 tests, `OK`.
- `DJANGO_LOG_DIR=/tmp/dikong-test-logs scripts/test_v2_regression.sh boundary --keepdb` passed locally: 159 tests, `OK`.

**Confirmed not running in this session:**

- Docker compose services are not currently running; `docker compose ps` returned no active services.

**Not yet proven:**

- Compose deployment health.
- Read-only API chain against running deployment.
- Mutation chain from route creation to mission start and execution refresh against mock DJI.
- Real DJI cloud task execution.
- Browser frontend operation.
- Browser-visible realtime WebSocket updates backed by real MQTT broker messages.
