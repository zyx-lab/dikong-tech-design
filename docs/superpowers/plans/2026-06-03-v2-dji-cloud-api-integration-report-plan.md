# v2 接入 DJI 上云 API 机制技术报告计划

日期：2026-06-03

适用项目：`dikong-tech-design`

目标：形成一份面向研发、联调、部署和验收的技术报告，用于说明 v2 平台如何接入 DJI 上云 API 的资源发现、航线上传、任务启动、直播、MQTT OSD/GPS 遥测、任务进度回流机制。

范围边界：

- 只规划 v2 平台接入方案，不修改代码。
- 不在报告中写入真实 DJI 密码、access token、MQTT 密码、STS secret、app license。
- 不把 DJI 上游 API 直接暴露给前端；前端只访问 `/api/v2/*`。
- GPS/遥测接入以 DJI MQTT OSD 为主，不依赖 DJI Java 后端当前异常的 `device-statuses` REST 接口。

## 1. 报告定位

报告要回答四个问题：

1. v2 平台已有能力在哪里。
2. 外部业务请求如何进入 v2。
3. v2 如何调用 DJI 上云 API。
4. DJI 异步 MQTT 回流如何写回 v2，并被前端读取。

报告不应只写接口列表，要按真实业务时序描述调用链：

```text
外部请求
  -> v2 API
  -> v2 view/service/gateway
  -> DJI 上云 HTTP API 或 MQTT broker
  -> v2 本地模型/快照
  -> v2 对外响应或前端轮询结果
```

## 2. 代码依据清单

报告需要引用以下代码模块作为依据：

| 模块 | 作用 |
| --- | --- |
| `apps/resource_v2/models.py` | v2 DJI 连接、无人机/机场/网关/负载资源、资源绑定 |
| `apps/resource_v2/gateway.py` | `DjiConnectionGateway`，把 v2 `DjiConnection` 适配到 DJI 上云 gateway |
| `apps/resource_v2/views.py` | DJI 连接创建、资源发现、资源查询、资源绑定入口 |
| `apps/inspection_v2/models.py` | 巡检任务、飞行会话、遥测快照、云端执行、直播字段 |
| `apps/inspection_v2/views.py` | 航线、任务、活动飞行、遥测、直播 v2 API |
| `apps/inspection_v2/services.py` | 任务启动、任务结束、遥测写入、云端事件回流处理 |
| `apps/inspection_v2/management/commands/run_v2_dji_worker.py` | v2 DJI MQTT worker |
| `apps/dji_cloud/gateway.py` | 通用 DJI 上云 HTTP gateway，负责登录、续期、请求、错误处理 |
| `apps/inspection_v2/tests.py` | 已有 OSD 更新遥测、worker 分发、任务启动/直播等行为证据 |
| `docs/v2-regression-testing.md` | v2 回归测试入口 |

## 3. 总体架构章节

报告应先给出整体架构：

```text
前端 / 外部业务系统
  -> /api/v2/resource/*
  -> /api/v2/inspection/*
  -> Django v2 权限与部门上下文
  -> DjiConnectionGateway
  -> DJI 上云 HTTP API

DJI 设备 / DJI 上云 MQTT broker
  -> run_v2_dji_worker
  -> apply_osd_telemetry / apply_device_status_event / apply_cloud_execution_event
  -> v2 本地 DB
  -> /api/v2/inspection/active-flights
```

同步链路：

- 创建 DJI 连接。
- 发现 DJI 资源。
- 上传 KMZ。
- 启动任务。
- 查询/操作直播。
- 取消任务。

异步链路：

- OSD/GPS 遥测。
- 设备在线状态。
- 航线任务进度和终态。
- 任务结束后的飞行记录和媒体同步。

## 4. 通用 DJI 鉴权链路

报告要单独说明所有 DJI HTTP 请求之前的鉴权机制。

调用链：

```text
外部请求 / v2 worker
  -> DjiConnectionGateway(connection)
  -> DjiGateway._ensure_authenticated()
  -> 若缺少 token/workspace 或 token 过期:
       POST DJI /api/v1/manage/login
       或 POST DJI /api/v1/manage/token/refresh
  -> 保存到 DjiConnection:
       workspace_id
       access_token
       mqtt_addr
       mqtt_username
       mqtt_password
       expires_at
  -> 后续请求 Header:
       x-auth-token: {access_token}
```

报告中不要把 v2 DJI 上游配置写成全局环境变量。当前 v2 正式链路通过 `POST /api/v2/resource/dji-connections` 写入 `DjiConnection.base_url/username/password/login_flag`，后续 HTTP 请求和 `v2-dji-worker` 都从数据库中的对应连接读取配置。

```json
{
  "name": "DJI 连接名称",
  "baseUrl": "<dji-api-base-url>",
  "username": "<username>",
  "password": "<password>",
  "loginFlag": 1
}
```

不要写真实密码。

## 5. 业务调用链路章节

### 5.1 创建 DJI 连接

目的：把一个 DJI 上云平台账号挂到 v2 部门。

链路：

```text
POST /api/v2/resource/dji-connections
  -> DjiConnectionListCreateView.post()
  -> DjiConnectionWriteSerializer
  -> DjiConnection.objects.create()
  -> 返回 DjiConnectionReadSerializer
```

DJI 上游请求：无。

报告应说明：这个动作只保存配置，不代表已连通 DJI。真正登录发生在 discover、任务、直播或 worker 调用 `DjiConnectionGateway` 时。

### 5.2 发现 DJI 资源

目的：从 DJI 上云平台拉取无人机、机场、网关、负载，并写入 v2 资源表。

链路：

```text
POST /api/v2/resource/dji-connections/{connectionId}/discover
  -> DjiConnectionDiscoverView.post()
  -> DjiConnectionGateway(connection).discover()
  -> DjiConnectionGateway.list_resources(ResourceType.DRONE)
  -> DjiConnectionGateway.list_resources(ResourceType.DOCK)
  -> DjiConnectionGateway.list_gateways()
  -> upsert_resource_from_payload()
  -> 返回 drones/docks/gateways/payloads
```

DJI 上游请求：

```text
POST /api/v1/manage/login
GET /api/v1/manage/workspaces/{workspace_id}/devices/bound?domain=0
GET /api/v1/manage/workspaces/{workspace_id}/devices/bound?domain=3
GET /api/v1/manage/workspaces/{workspace_id}/devices
```

本地写入：

```text
v2_drone_resources
v2_dock_resources
v2_gateway_resources
v2_payload_resources
```

报告应说明：发现资源后还要通过 `POST /api/v2/resource/bindings` 绑定到部门，否则 v2 业务权限链路不可用。

### 5.3 资源绑定

目的：建立资源与部门、DJI 连接的业务归属关系。

链路：

```text
POST /api/v2/resource/bindings
  -> BindingListCreateView.post()
  -> 校验 resourceType/resourceId/djiConnectionId
  -> ResourceBinding.objects.create()
  -> ResourceBindingHistory.objects.create()
  -> 返回 BindingReadSerializer
```

DJI 上游请求：无。

报告应说明：资源绑定是 v2 权限、任务派发、监控权限的关键，不是 DJI 设备绑定/解绑。

### 5.4 上传巡检航线 KMZ

目的：把 v2 航线 KMZ 上传到 DJI 上云，拿到 `dji_file_id`/`dji_wayline_id`。

链路：

```text
POST /api/v2/inspection/routes/{routeId}/kmz
  -> RouteKmzView.post()
  -> get_editable_route_or_404()
  -> DjiConnectionGateway(connection).upload_route()
  -> WaypointRouteCloudFile.objects.update_or_create()
  -> 返回 RouteCloudFileReadSerializer
```

DJI 上游请求：

```text
POST /api/v1/manage/login
POST /api/v1/wayline/workspaces/{workspace_id}/waylines/files/upload
```

上游请求形态：

```text
multipart/form-data
  name = v2-route-{route_id}-{uuid}
  file = KMZ 文件
```

本地写入：

```text
v2_waypoint_route_cloud_files
  dji_connection
  workspace_id
  dji_file_id
  wayline_type
  download_url
  raw_response
```

### 5.5 创建 v2 巡检任务

目的：创建本地任务，但不立即触达 DJI。

链路：

```text
POST /api/v2/inspection/missions
  -> MissionListCreateView.post()
  -> _validated_mission_inputs()
  -> InspectionMission.objects.create()
  -> create_assignments()
  -> 返回 MissionReadSerializer
```

DJI 上游请求：无。

报告应说明：DJI 航线任务创建发生在 `POST /api/v2/inspection/missions/{id}/start`。

### 5.6 启动巡检任务

目的：启动 v2 任务、启动 DJI 直播、创建 DJI 航线任务、创建本地飞行会话。

链路：

```text
POST /api/v2/inspection/missions/{missionId}/start
  -> MissionStartView.post()
  -> get_visible_mission_or_404()
  -> start_mission()
  -> DjiConnectionGateway.get_live_capacity()
  -> DjiConnectionGateway.start_live()
  -> DjiConnectionGateway.create_mission()
  -> InspectionMission.status = RUNNING
  -> FlightSession.objects.create()
  -> MissionCloudExecution.objects.update_or_create()
  -> 返回 MissionReadSerializer
```

DJI 上游请求：

```text
GET /api/v1/manage/live/capacity
POST /api/v1/manage/live/streams/start
POST /api/v1/wayline/workspaces/{workspace_id}/flight-tasks
```

直播启动请求体：

```json
{
  "device_sn": "{drone_sn}",
  "video_id": "{drone_sn}/{camera_index}/{video_index}",
  "url_type": 1,
  "video_quality": 1
}
```

航线任务创建请求体：

```json
{
  "name": "{mission_name}",
  "file_id": "{dji_file_id}",
  "dock_sn": "{executor_sn}",
  "wayline_type": 0,
  "task_type": 0,
  "rth_altitude": 30,
  "out_of_control_action": 0
}
```

失败补偿：

```text
如果 create_mission 失败:
  -> POST /api/v1/manage/live/streams/stop
```

报告应强调启动前置条件：

- mission 必须是 `PENDING`。
- route 必须已上传 DJI KMZ。
- drone 和 executor 必须属于同一个 `DjiConnection`。
- route cloud file 必须属于同一个 `DjiConnection`。
- drone 和 executor 必须在线。
- 资源不能被其他运行中 mission 占用。
- 设备必须有直播能力。

### 5.7 查询活动飞行与 GPS

目的：外部前端读取最新 GPS/遥测。

链路：

```text
GET /api/v2/inspection/active-flights
GET /api/v2/inspection/active-flights/{sessionId}
  -> ActiveFlightListView.get() / ActiveFlightDetailView.get()
  -> visible_sessions_queryset()
  -> ActiveFlightReadSerializer
  -> telemetry = FlightTelemetrySnapshot
  -> 返回 latitude/longitude/altitude/speed/heading/batteryPercent/reportedAt
```

DJI 上游请求：无。

数据来源是异步 MQTT OSD worker 写入，不是 HTTP 实时查询 DJI。

### 5.8 MQTT OSD/GPS 回流

目的：把 DJI 设备 OSD 写入 v2 遥测快照。

启动命令：

```bash
python manage.py run_v2_dji_worker
```

链路：

```text
run_v2_dji_worker
  -> 查 DjiConnection.status = ACTIVE
  -> DjiConnectionGateway(connection).get_workspace_config()
  -> 取 mqtt_addr/mqtt_username/mqtt_password
  -> 连接 DJI MQTT broker
  -> 订阅 thing/product/+/osd
  -> 收到 thing/product/{drone_sn}/osd
  -> V2DjiWorker.handle_message()
  -> apply_osd_telemetry(device_sn, payload)
  -> apply_device_status_event(device_sn, online=True)
  -> 查 RUNNING FlightSession(drone__device_sn=device_sn)
  -> update_telemetry_snapshot()
  -> 写 FlightTelemetrySnapshot
```

OSD 载荷关注字段：

```json
{
  "timestamp": 1780000000000,
  "data": {
    "latitude": 31.2304,
    "longitude": 121.4737,
    "height": 120.5,
    "horizontal_speed": 8.2,
    "attitude_head": 91.0,
    "battery": {
      "capacity_percent": 87
    }
  }
}
```

写入字段映射：

| DJI OSD 字段 | v2 字段 |
| --- | --- |
| `data.latitude` | `FlightTelemetrySnapshot.latitude` |
| `data.longitude` | `FlightTelemetrySnapshot.longitude` |
| `data.altitude` 或 `data.height` | `FlightTelemetrySnapshot.altitude` |
| `data.speed` 或 `data.horizontal_speed` | `FlightTelemetrySnapshot.speed` |
| `data.heading` 或 `data.attitude_head` | `FlightTelemetrySnapshot.heading` |
| `data.battery.capacity_percent` | `FlightTelemetrySnapshot.battery_percent` |
| `payload.timestamp` | `FlightTelemetrySnapshot.reported_at` |

### 5.9 设备在线状态回流

目的：同步资源在线状态。

链路：

```text
DJI MQTT sys/product/{device_sn}/status
  -> V2DjiWorker.handle_message()
  -> apply_device_status_event(device_sn, payload)
  -> 更新 DroneResource / DockResource / GatewayResource:
       online_status
       last_seen_at
       last_payload
```

外部读取：

```text
GET /api/v2/resource/drones
GET /api/v2/resource/docks
GET /api/v2/resource/gateways
```

### 5.10 DJI 任务进度与终态回流

目的：把 DJI 航线任务执行状态同步回 v2。

链路：

```text
DJI MQTT thing/product/{sn}/events
DJI MQTT thing/product/{sn}/services_reply
  -> V2DjiWorker.handle_message()
  -> 从 payload 解析 job_id/flight_id
  -> 从 payload 解析 status/result/state
  -> apply_cloud_execution_event(dji_job_id, status, payload)
```

本地更新：

```text
MissionCloudExecution.status
MissionCloudExecution.progress_percent
MissionCloudExecution.raw_last_event
InspectionMission.status
FlightSession.status
InspectionFlightRecord
```

任务结束时：

```text
COMPLETED / CANCELED / FAILED
  -> 写任务终态
  -> 写飞行会话终态
  -> 创建/更新飞行记录
  -> sync_media_for_record()
  -> _stop_live_for_execution()
  -> POST /api/v1/manage/live/streams/stop
```

### 5.11 手动直播操作

目的：活动飞行期间手动查询、启动、停止、更新、切换直播。

链路：

```text
GET /api/v2/inspection/live/capacity?droneId={id}
  -> LiveCapacityView.get()
  -> _require_active_session_for_drone()
  -> visible_resource_for_live()
  -> DjiConnectionGateway.get_live_capacity()
  -> GET /api/v1/manage/live/capacity
  -> 返回 DJI capacity item
```

```text
POST /api/v2/inspection/live/start
POST /api/v2/inspection/live/stop
POST /api/v2/inspection/live/update
POST /api/v2/inspection/live/switch
  -> LiveActionView.post()
  -> _require_active_session_for_drone()
  -> visible_resource_for_live()
  -> DjiConnectionGateway.start_live/stop_live/update_live/switch_live()
  -> POST /api/v1/manage/live/streams/{action}
  -> 返回 DJI 响应
```

### 5.12 取消/中止任务

目的：取消或中止 v2 运行中任务，并尽量同步停止 DJI 任务和直播。

链路：

```text
POST /api/v2/inspection/missions/{id}/cancel
POST /api/v2/inspection/missions/{id}/abort
  -> MissionCancelView / MissionAbortView
  -> close_mission() / safety_abort_mission()
  -> DjiConnectionGateway.cancel_mission(dji_job_id)
  -> DjiConnectionGateway.stop_live(drone_sn, video_id)
  -> 返回 MissionReadSerializer
```

DJI 上游请求：

```text
DELETE /api/v1/wayline/workspaces/{workspace_id}/jobs?job_id={dji_job_id}
POST /api/v1/manage/live/streams/stop
```

## 6. 部署计划章节

报告应明确 v2 接入不是只跑 Web 服务，至少需要两个常驻进程：

```bash
# Web/API 进程
gunicorn config.wsgi:application -b 0.0.0.0:8000 --workers 4

# DJI MQTT worker 进程
python manage.py run_v2_dji_worker
```

容器化部署建议：

```text
web:
  负责 /api/v2/* HTTP API

v2-dji-worker:
  使用同一份镜像和数据库连接配置
  command: python manage.py run_v2_dji_worker
  连接同一个数据库
  查询 ACTIVE 的 DjiConnection
  访问 DJI MQTT broker
```

最低环境变量：

```bash
DJANGO_SETTINGS_MODULE=config.settings
DB_ENGINE=postgres
DB_HOST=<db-host>
DB_NAME=<db-name>
DB_USER=<db-user>
DB_PASSWORD=<db-password>
DJANGO_ALLOWED_HOSTS=<hosts>
```

DJI 上游 API 地址和账号不属于 Docker 启动最低环境变量。v2 没有全局 DJI 上游地址、账号或密码环境变量，以 `DjiConnection` 数据库配置为准。

## 7. 验收计划章节

报告应给出可复现验收流程。

### 7.1 本地/测试环境验收

命令：

```bash
scripts/test_v2_regression.sh boundary
python manage.py run_v2_dji_worker --once
```

期望：

- v2 schema 和回归测试通过。
- worker 能识别 active `DjiConnection` 数量。
- `paho-mqtt` 依赖可用。

### 7.2 资源发现验收

操作：

```text
POST /api/v2/resource/dji-connections
POST /api/v2/resource/dji-connections/{id}/discover
GET /api/v2/resource/drones
GET /api/v2/resource/gateways
```

期望：

- `DjiConnection.workspace_id` 有值。
- `DjiConnection.mqtt_addr/mqtt_username/mqtt_password` 已回写。
- 无人机、网关资源已同步。
- 资源绑定后 v2 用户能按权限看到资源。

### 7.3 航线和任务启动验收

操作：

```text
POST /api/v2/inspection/routes
POST /api/v2/inspection/routes/{id}/kmz
POST /api/v2/inspection/missions
POST /api/v2/inspection/missions/{id}/start
```

期望：

- 航线上传成功并写入 `WaypointRouteCloudFile`。
- mission 进入 `RUNNING`。
- 创建 `FlightSession`。
- 创建 `MissionCloudExecution`。
- `liveStatus=RUNNING`，`liveVideoId` 有值。
- `djiJobId` 有值。

### 7.4 GPS/遥测验收

操作：

```text
python manage.py run_v2_dji_worker
等待 DJI OSD 上报
GET /api/v2/inspection/active-flights
```

期望：

- `active-flights.list[].telemetry.latitude` 有值。
- `active-flights.list[].telemetry.longitude` 有值。
- `reportedAt` 随 OSD 更新。
- 电量、高度、速度、航向按 OSD 字段映射。

### 7.5 任务进度回流验收

操作：

```text
保持 run_v2_dji_worker 运行
等待 DJI events/services_reply
GET /api/v2/inspection/missions/{id}
GET /api/v2/inspection/flight-records
```

期望：

- `MissionCloudExecution.progressPercent` 更新。
- 任务结束时 mission 和 session 进入终态。
- 生成飞行记录。
- 直播被停止或进入停止状态。

## 8. 风险和改进项章节

报告应明确当前方案风险：

| 风险 | 说明 | 建议 |
| --- | --- | --- |
| Web 进程运行但 worker 未运行 | GPS/任务进度不会回流 | 独立部署 `run_v2_dji_worker` 并监控 |
| 当前 telemetry 只挂在 running FlightSession | 非任务态无人机没有位置接口 | 后续新增资源级 telemetry snapshot |
| `DroneResource.device_sn` 当前全局唯一 | 多 DJI 平台相同 SN 可能串数据 | 后续改为 `dji_connection + device_sn` 匹配 |
| OSD 依赖 DJI MQTT 持续上报 | 设备静默时 GPS 不更新 | 给前端展示 `reportedAt` 和 stale 状态 |
| DJI `device-statuses` 不可靠 | 当前上游返回 `illegal argument`，且非实时 GPS 设计 | 不作为 v2 GPS 数据源 |
| `events/services_reply` 状态解析偏宽松 | 不同 DJI payload 结构可能误判 | 后续按 method 精准解析 |

## 9. 报告最终交付物

技术报告最终应包含：

1. 架构图或时序图。
2. v2 模块与代码入口表。
3. 每个业务动作的外部 API、v2 代码路径、DJI 上游路径、返回结构。
4. MQTT OSD/GPS 回流链路。
5. 部署方式，特别是 worker 常驻进程。
6. 联调步骤。
7. 验收清单。
8. 风险与后续改造建议。

## 10. 建议的报告标题

```text
低空平台 v2 接入 DJI 上云 API 机制技术报告
```

建议报告文件：

```text
docs/superpowers/specs/2026-06-03-v2-dji-cloud-api-integration.md
```

本文件是报告写作计划；最终技术报告可基于本计划展开。
