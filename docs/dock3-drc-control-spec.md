# Dock 3 DRC 本项目托管规格

## 1. 范围

本规格定义 Dock 3 DRC 页面与本项目的接口、会话、安全控制和上游边界。

固定链路：

```text
Frontend -> Django REST/WebSocket -> Django DRC MQTT client -> DJI broker -> Dock 3
                                \-> Java REST connect/enter/exit
```

前端不得直连 DJI MQTT，不得获得 broker、username、password、上游 clientId、publishTopic 或 subscribeTopic。

不新增 Django 数据表，不新增 Java 会话平台或 Java WebSocket，不持久化每条控制帧和 DRC 上行消息。

## 2. 前置条件

- 用户为超级管理员、调度员或飞手。
- 用户对 Dock 和其子无人机具备 `use` 或 `dispatch_task` 权限。
- 订阅标准 MQTT 起飞、FlyTo 和拍照进度时，用户还需具备现有 `monitor` 权限。
- Dock 型号为 Dock 3，Dock 和子无人机在线，并绑定到同一个 DJI connection。
- Java 上云 API 已配置 DRC broker，Dock 当前状态允许进入指令飞行。
- 前端使用本项目 access token 连接 WebSocket。

## 3. Django REST

### 3.1 查询能力

```http
GET /api/v2/inspection/drc/capabilities?dockId=12
```

响应：

```json
{
  "dockId": 12,
  "droneId": 31,
  "supported": true,
  "available": true,
  "blockers": [],
  "control": {
    "protocol": "stick_control",
    "frequency": {"min": 5, "max": 10, "default": 10},
    "channel": {"min": 364, "neutral": 1024, "max": 1684},
    "actions": ["takeoff_to_point", "fly_to_point", "fly_to_point_update", "fly_to_point_stop"]
  },
  "payloads": []
}
```

阻断码至少包括：`DOCK_NOT_SUPPORTED`、`DOCK_OFFLINE`、`CHILD_DRONE_NOT_FOUND`、`DRONE_OFFLINE`。

### 3.2 创建 DRC 会话

```http
POST /api/v2/inspection/drc/connect
Content-Type: application/json

{
  "dockId": 12,
  "expireSec": 3600,
  "osdFrequency": 10,
  "hsiFrequency": 5
}
```

限制：`expireSec=1800..86400`，OSD/HSI 频率为 `1..30`。请求不接受 `clientId` 或任何 MQTT 字段。

Django 按以下顺序执行：

1. 校验角色、Dock/无人机权限、型号和在线状态。
2. 调用 Java `drc/connect` 取得短期 MQTT 凭据。
3. 使用相同 `dockSn/clientId` 调用 Java `drc/enter`。
4. 严格确认 Java 只返回当前 Dock 的 `/drc/down` 和 `/drc/up`。
5. 将凭据和 topic 写入短期 Django Redis cache，TTL 不超过上游凭据有效期。
6. 只向前端返回本项目会话信息。

响应：

```json
{
  "sessionId": "0a4df506-3c23-4b0e-bf69-3edfe64a662b",
  "dockId": 12,
  "droneId": 31,
  "expiresAt": "2026-08-10T12:00:00+08:00",
  "webSocketPath": "/ws/v2/drc/sessions/0a4df506-3c23-4b0e-bf69-3edfe64a662b"
}
```

任一步失败时不得返回半份凭据；已取得 clientId 时必须 best-effort 调用 Java `exit`。

### 3.3 一键起飞与 FlyTo

```http
POST /api/v2/inspection/drc/actions
Content-Type: application/json
```

公共字段为 `dockId` 和 `action`。动作契约：

| action | 字段 |
|---|---|
| `takeoff_to_point` | `targetLatitude=-90..90`、`targetLongitude=-180..180`、`targetHeight=2..1500`、`securityTakeoffHeight=20..1500`、`rthMode=1`、`rthAltitude=2..1500`、`rcLostAction=0..2`、`commanderModeLostAction=0..1`、`commanderFlightMode=0..1`、`commanderFlightHeight=2..3000`、`maxSpeed=1..15`；可选 `flightSafetyAdvanceCheck` |
| `fly_to_point` | `maxSpeed=1..15`，`points` 仅一个目标点；点包含 `latitude=-90..90`、`longitude=-180..180`、`height=2..10000` |
| `fly_to_point_update` | 与 `fly_to_point` 相同，只更新当前 FlyTo 目标 |
| `fly_to_point_stop` | 无额外字段 |

Django 校验操作者和 Dock/子无人机权限、型号及在线状态后，通过 Java 现有 control service 发布到标准 services topic。飞行控制权由 Java 现有起飞、FlyTo 和 DRC enter 流程按需抢夺，不向浏览器开放独立的 authority 接口。

成功响应：

```json
{
  "action": "fly_to_point",
  "status": "SUCCEEDED",
  "dockId": 12,
  "droneId": 31,
  "upstream": {}
}
```

`takeoff_to_point_progress`、`fly_to_point_progress` 和 `camera_photo_take_progress` 属于标准 events topic，复用本项目 `WS /ws/v2/dji/mqtt?token=` 权限过滤后转发，不进入短期 `/drc/up` 会话。

### 3.4 退出 DRC 会话

```http
POST /api/v2/inspection/drc/exit
Content-Type: application/json

{"sessionId":"0a4df506-3c23-4b0e-bf69-3edfe64a662b"}
```

Django 只允许会话创建者退出。成功调用 Java `exit` 后删除 cache，返回：

```json
{"status":"CLOSED"}
```

Java `exit` 必须幂等。上游失败时 Django 返回 `UPSTREAM_ERROR`，保留会话供重试；会话过期或不属于当前用户时返回 `DRC_SESSION_NOT_FOUND`。

正常退出顺序固定为：前端发送 `control.disarm` 并收到 `armed=false`，调用本接口，成功后关闭 WebSocket。WebSocket 意外断开时由 Django 自动执行同一上游清理。

## 4. Django WebSocket

### 4.1 建连

```text
WS /ws/v2/drc/sessions/{sessionId}?token={accessToken}
```

关闭码：

| code | 含义 |
|---|---|
| `4401` | token 缺失或失效 |
| `4403` | 角色或资源权限不足 |
| `4404` | 会话不存在、已过期或不属于当前用户 |
| `4409` | 当前会话已有 WebSocket 控制连接 |
| `1011` | Django 无法连接上游 DRC MQTT |

建连时 Django 在 Redis 中原子占用 session；建连后从 Redis 读取内部凭据，按 `address/enableTls` 建立 TCP、TLS、WS 或 WSS MQTT 连接，并只订阅固定 `/drc/up`。占用 TTL 不超过 session 有效期。

状态消息：

```json
{"type":"session.connecting"}
{"type":"session.connected"}
{"type":"session.disconnected"}
```

MQTT 重连成功后保持 `disarmed`，不得自动恢复非中立控制。

### 4.2 前端消息

进入 armed：

```json
{"type":"control.arm"}
```

退出 armed：

```json
{"type":"control.disarm"}
```

飞行急停：

```json
{"type":"control.emergencyStop"}
```

Django 先发送中立帧，再发布 `drone_emergency_stop`。成功返回：

```json
{"type":"control.state","armed":false,"reason":"EMERGENCY_STOP_LATCHED"}
{"type":"control.emergencyStop.ack","seq":89}
```

急停在当前 WebSocket 内锁定；再次 `control.arm` 返回 `EMERGENCY_STOP_LATCHED`，前端必须退出并创建新会话。

摇杆帧：

```json
{
  "type": "control.frame",
  "clientSeq": 23,
  "sentAt": 1786330800123,
  "roll": 1024,
  "pitch": 1024,
  "throttle": 1024,
  "yaw": 1024
}
```

校验规则：

- 顶层字段必须精确匹配，不接受 raw topic、raw MQTT envelope 或额外字段。
- `clientSeq` 为正整数且在当前 WebSocket 内严格递增。
- `sentAt` 为正整数。
- `roll/pitch/throttle/yaw` 必须是整数 `364..1684`，`1024` 为中立。
- 只有 MQTT 已连接且状态为 armed 时才接受控制帧。
- 前端发送频率必须为 5-10 Hz。

成功发布后返回：

```json
{"type":"control.ack","clientSeq":23,"seq":87}
```

拒绝时返回稳定错误码：

```json
{"type":"error","code":"CONTROL_NOT_ARMED"}
```

错误码包括：`MQTT_NOT_CONNECTED`、`CONTROL_NOT_ARMED`、`EMERGENCY_STOP_LATCHED`、`INVALID_FIELDS`、`CLIENT_SEQ_REPLAY`、`INVALID_SENT_AT` 和四轴范围错误。

### 4.3 内部 MQTT 下行

Django 为每条下行消息分配会话内递增 `seq`。摇杆帧转换为：

```json
{
  "seq": 87,
  "method": "stick_control",
  "data": {"roll":1024,"pitch":1024,"throttle":1024,"yaw":1024}
}
```

Django 每 5 秒发送：

```json
{
  "seq": 88,
  "method": "heart_beat",
  "data": {"timestamp":1786330805000}
}
```

急停发布：

```json
{"seq":89,"method":"drone_emergency_stop","data":{}}
```

以上消息只能发布到内部 session 的固定 `/drc/down`，QoS 为 1。

### 4.4 上行转发

订阅到 `/drc/up` 的合法 JSON 原样包在本项目事件中，真实 topic 不下发：

```json
{
  "type": "drc.message",
  "message": {
    "method": "osd_info_push",
    "seq": 88,
    "data": {}
  }
}
```

覆盖 `heart_beat`、`hsi_info_push`、`delay_info_push`、`osd_info_push` 及设备返回的控制结果。非 UTF-8 或非 JSON MQTT payload 丢弃。

### 4.5 Fail-stop

- armed 后 500 ms 未收到控制帧：发布一次中立帧，解除 armed，返回 `CONTROL_TIMEOUT`。
- `control.disarm`：先发布中立帧，再解除 armed。
- `control.emergencyStop`：先发布中立帧和急停，再锁定当前会话，后续不再发布摇杆帧。
- MQTT 断开：立即解除 armed；重连后必须由前端重新 arm。
- WebSocket 断开：未急停时 best-effort 发布中立帧，停止 heartbeat，断开 MQTT，调用 Java `exit`，删除 Redis session 和占用。
- 页面隐藏、窗口失焦和松杆由前端立即发送 `control.disarm`；不能只依赖 WebSocket 最终断开。

## 5. Java REST

### 5.1 connect

`POST /api/v1/control/workspaces/{workspaceId}/drc/connect`

请求字段：`dockSn` 必填，`clientId` 可选，`expireSec=1800..86400`。

- Dock 必须在线且属于路径 workspace。
- 浏览器不调用该接口，只有 Django 调用。
- Redis MQTT ACL 只能包含：PUB `thing/product/{dockSn}/drc/down`、SUB `thing/product/{dockSn}/drc/up`。
- 禁止空 topic `ALL` 权限。

### 5.2 enter

`POST /api/v1/control/workspaces/{workspaceId}/drc/enter`

必须使用请求中的 `expireSec`、`deviceInfo.osdFrequency` 和 `deviceInfo.hsiFrequency`。进入成功后只返回当前 Dock 的一对 DRC topic。

### 5.3 exit

`POST /api/v1/control/workspaces/{workspaceId}/drc/exit`

退出 DRC 并删除 owner/ACL；Dock 已退出时仍返回成功。

### 5.4 起飞与 FlyTo

Django 复用 Java 现有设备 control service：

- `POST /api/v1/control/devices/{dockSn}/jobs/takeoff-to-point`
- `POST /api/v1/control/devices/{dockSn}/jobs/fly-to-point`
- `PUT /api/v1/control/devices/{dockSn}/jobs/fly-to-point`
- `DELETE /api/v1/control/devices/{dockSn}/jobs/fly-to-point`

其中 `PUT` 只补充到现有 `IControlService`，直接调用 SDK 已有 `flyToPointUpdate`；不新增任务表、状态机或会话。

### 5.5 控制权、事件与废弃方法

- `flight_authority_grab` 由 Java 在起飞、FlyTo 和 DRC enter 内部执行。
- `payload_authority_grab` 由 Django `/camera/actions` 在每次载荷动作前调用 Java 执行。
- `obstacle_avoidance_notify`、`takeoff_to_point_progress`、`fly_to_point_progress`、`camera_photo_take_progress` 和 `joystick_invalid_notify` 复用现有标准 MQTT 事件入库及 `/ws/v2/dji/mqtt` 转发。
- `heart_beat`、`hsi_info_push`、`delay_info_push` 和 `osd_info_push` 由当前 DRC `/drc/up` WebSocket 转发。
- 官方已废弃的 `drc_status_notify` 和 `drone_control` 不新增调用入口；飞控使用 `stick_control`。

## 6. 相机、云台、红外和扬声器

官方 Dock 3 页面中的相机、云台和红外方法发布到 `thing/product/{gatewaySn}/services`，不属于短期 DRC `/drc/down`。前端必须调用本项目 REST，由 Django 再调用 Java 标准 payload command。

当前可直接复用：

```text
POST /api/v2/inspection/camera/actions
```

后端已实现方法：

- 相机/云台：`camera_frame_zoom`、`camera_mode_switch`、`camera_photo_take`、`camera_photo_stop`、`camera_recording_start`、`camera_recording_stop`、`camera_screen_drag`、`camera_aim`、`camera_focal_length_set`、`gimbal_reset`、`camera_look_at`、`camera_screen_split`、`photo_storage_set`、`video_storage_set`。
- 曝光/对焦：`camera_exposure_mode_set`、`camera_exposure_set`、`camera_focus_mode_set`、`camera_focus_value_set`、`camera_point_focus_action`。
- 红外：`ir_metering_mode_set`、`ir_metering_point_set`、`ir_metering_area_set`。
- 扬声器：协议、payload index 和真机支持确认后，通过受控 REST/services 路径开放；不得把私有 `drc_speaker_*` 当成官方公共方法。

所有请求公共字段为 `droneId`、`executorId`、`payloadIndex` 和 `action`；`payloadIndex` 格式为 `type-subtype-index`。动作字段由 Django 转为 DJI snake_case：

| action | 动作字段与约束 |
|---|---|
| `camera_frame_zoom` | `cameraType=wide/zoom/ir`，`locked`，`x/y/width/height=0..1` |
| `camera_mode_switch` | `cameraMode=0..3` |
| `camera_photo_take` / `camera_photo_stop` | 无额外字段 |
| `camera_recording_start` / `camera_recording_stop` | 无额外字段 |
| `camera_screen_drag` | `locked`，`pitchSpeed`，`yawSpeed` |
| `camera_aim` | `cameraType=wide/zoom/ir`，`locked`，`x/y=0..1` |
| `camera_focal_length_set` | `cameraType=zoom/ir`；zoom 为 `2..200`，ir 为 `2..20` |
| `gimbal_reset` | `resetMode=0..3` |
| `camera_look_at` | `locked`，`latitude=-90..90`，`longitude=-180..180`，`height=2..10000` |
| `camera_screen_split` | `enable` |
| `photo_storage_set` | 非空 `photoStorageSettings`，成员为 `current/vision/ir` |
| `video_storage_set` | 非空 `videoStorageSettings`，成员为 `current/wide/zoom/ir` |
| `camera_exposure_mode_set` | `cameraType=wide/zoom`，`exposureMode=1..4` |
| `camera_exposure_set` | `cameraType=wide/zoom`，`exposureValue=1..31/255` |
| `camera_focus_mode_set` | `cameraType=wide/zoom`，`focusMode=0..2` |
| `camera_focus_value_set` | `cameraType=wide/zoom`，整数 `focusValue`；有效范围取设备物模型值 |
| `camera_point_focus_action` | `cameraType=wide/zoom`，`x/y=0..1` |
| `ir_metering_mode_set` | `mode=0..2` |
| `ir_metering_point_set` | `x/y=0..1` |
| `ir_metering_area_set` | `x/y/width/height=0..1` |

动作缺少必填字段、携带其他动作字段、枚举或范围不合法时，Django 必须在调用 Java 前返回 400。Java 复用 `PayloadCommandsEnum/DronePayloadParam` 和 SDK request 校验，不创建另一套 DRC session 或 operation 平台。

## 7. 安全与日志

- MQTT password、username、address、clientId 和 topic 只存在于 Django Redis 短期 cache 与当前 MQTT client 内存。
- REST、WebSocket、审计、异常和请求日志不得记录上述字段。
- 前端不能上传 broker、topic、MQTT envelope 或任意 method。
- sessionId 不替代用户鉴权；每次 REST/WS 使用都校验 owner、角色和资源权限。
- cache 到期后凭据自然失效；Java ACL TTL 不得长于请求有效期。

## 8. 最小验收

自动检查：

- connect 响应不包含任何 MQTT 凭据、topic 或上游 clientId，cache 内存在对应短期配置。
- 无权限用户不能查询能力、创建会话或使用其他用户会话。
- exit 只使用 sessionId，并使用 cache 内 dockSn/clientId 调 Java。
- 一键起飞、FlyTo 开始/更新/停止只通过 `/drc/actions` 调用现有 Java services 接口。
- 四轴边界、布尔伪整数和 clientSeq 重放被拒绝。
- 同一 session 的第二条 WebSocket 被 `4409` 拒绝，断开后 Redis 占用被删除。
- 急停按中立帧、`drone_emergency_stop` 的顺序发布，并锁定后续 arm。
- Java ACL 只有当前 Dock 的 DRC down/up，且 enter 使用请求频率和有效期。

真机检查：

- WebSocket 只连接本项目，浏览器网络和日志中没有 DJI MQTT secrets。
- 5 Hz 与 10 Hz 四轴方向、中立值和序列行为正确。
- 松杆、切后台、断网、MQTT 断开和页面关闭后停止非中立控制。
- heartbeat、OSD、HSI 和 delay 上行可经本项目 WebSocket 收到。
- 22 个相机、云台和红外方法通过现有 REST 逐项验证，并确认发布到 services topic。
