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
    "channel": {"min": 364, "neutral": 1024, "max": 1684}
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
5. 将凭据和 topic 写入短期 Django cache，TTL 不超过上游凭据有效期。
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

### 3.3 退出 DRC 会话

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
| `1011` | Django 无法连接上游 DRC MQTT |

建连后 Django 从 cache 读取内部凭据，按 `address/enableTls` 建立 TCP、TLS、WS 或 WSS MQTT 连接，并只订阅固定 `/drc/up`。

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

错误码包括：`MQTT_NOT_CONNECTED`、`CONTROL_NOT_ARMED`、`INVALID_FIELDS`、`CLIENT_SEQ_REPLAY`、`INVALID_SENT_AT` 和四轴范围错误。

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

两类消息只能发布到内部 session 的固定 `/drc/down`，QoS 为 1。

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
- MQTT 断开：立即解除 armed；重连后必须由前端重新 arm。
- WebSocket 断开：best-effort 发布中立帧，停止 heartbeat，断开 MQTT，调用 Java `exit`，删除 cache。
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

## 6. 相机、云台、红外和扬声器

官方 Dock 3 页面中的相机、云台和红外方法发布到 `thing/product/{gatewaySn}/services`，不属于短期 DRC `/drc/down`。前端必须调用本项目 REST，由 Django 再调用 Java 标准 payload command。

当前可直接复用：

```text
POST /api/v2/inspection/camera/actions
```

已实现方法：`camera_mode_switch`、`camera_photo_take`、`camera_recording_start`、`camera_recording_stop`、`camera_focal_length_set`、`camera_aim`、`gimbal_reset`。

整个页面仍需补齐的方法：

- 相机/云台：`camera_photo_stop`、`camera_screen_drag`、`camera_look_at`、`camera_screen_split`、`photo_storage_set`、`video_storage_set`。
- 曝光/对焦：`camera_exposure_mode_set`、`camera_exposure_set`、`camera_focus_mode_set`、`camera_focus_value_set`、`camera_point_focus_action`。
- 红外：`ir_metering_mode_set`、`ir_metering_point_set`、`ir_metering_area_set`。
- 扬声器：协议、payload index 和真机支持确认后，通过受控 REST/services 路径开放；不得把私有 `drc_speaker_*` 当成官方公共方法。

补齐时扩展现有 Django `CameraActionSerializer` 和 Java `PayloadCommandsEnum/DronePayloadParam`，不再创建另一套 DRC session 或 operation 平台。

## 7. 安全与日志

- MQTT password、username、address、clientId 和 topic 只存在于 Django 短期 cache 与当前 MQTT client 内存。
- REST、WebSocket、审计、异常和请求日志不得记录上述字段。
- 前端不能上传 broker、topic、MQTT envelope 或任意 method。
- sessionId 不替代用户鉴权；每次 REST/WS 使用都校验 owner、角色和资源权限。
- cache 到期后凭据自然失效；Java ACL TTL 不得长于请求有效期。

## 8. 最小验收

自动检查：

- connect 响应不包含任何 MQTT 凭据、topic 或上游 clientId，cache 内存在对应短期配置。
- 无权限用户不能查询能力、创建会话或使用其他用户会话。
- exit 只使用 sessionId，并使用 cache 内 dockSn/clientId 调 Java。
- 四轴边界、布尔伪整数和 clientSeq 重放被拒绝。
- Java ACL 只有当前 Dock 的 DRC down/up，且 enter 使用请求频率和有效期。

真机检查：

- WebSocket 只连接本项目，浏览器网络和日志中没有 DJI MQTT secrets。
- 5 Hz 与 10 Hz 四轴方向、中立值和序列行为正确。
- 松杆、切后台、断网、MQTT 断开和页面关闭后停止非中立控制。
- heartbeat、OSD、HSI 和 delay 上行可经本项目 WebSocket 收到。
- 已实现的 7 个相机/云台方法通过现有 REST 逐项验证；其余方法在补齐契约后再开放。
