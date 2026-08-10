# Dock 3 DRC 前端直连 MQTT 规格

## 1. 范围

本规格覆盖 DRC 页面所需的连接、连续控制、相机、云台、红外和扬声器操作。前端取得短期凭据后直接连接 DJI DRC MQTT；Django 和 Java 不转发单条控制指令。

不包含：探照灯未冻结私有协议、服务端 DRC WebSocket、服务端 operation 状态机、多实例 DRC owner 租约。

## 2. 前置条件

- 用户为超级管理员、调度员或飞手。
- 用户对 Dock 和其子无人机具备 `use` 或 `dispatch_task` 权限。
- Dock 型号为 Dock 3，Dock 与子无人机在线，且属于同一 DJI connection。
- 浏览器运行环境支持 MQTT over WebSocket；上云 API 返回的 `address` 必须是浏览器可访问地址。

## 3. Django REST

### 3.1 能力

```http
GET /api/v2/inspection/drc/capabilities?dockId=12
```

响应包含：

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

### 3.2 连接或刷新凭据

```http
POST /api/v2/inspection/drc/connect
Content-Type: application/json

{
  "dockId": 12,
  "clientId": null,
  "expireSec": 3600,
  "osdFrequency": 10,
  "hsiFrequency": 5
}
```

首次连接不传 `clientId`；刷新凭据时传当前 `clientId`。响应：

```json
{
  "dockId": 12,
  "droneId": 31,
  "mqtt": {
    "address": "wss://broker.example/mqtt",
    "username": "temporary-user",
    "password": "temporary-password",
    "clientId": "temporary-client-id",
    "expireTime": 1800000000,
    "enableTls": true
  },
  "publishTopic": "thing/product/DOCK_SN/drc/down",
  "subscribeTopic": "thing/product/DOCK_SN/drc/up"
}
```

连接顺序固定为 Java `connect` 后 `enter`。任一步失败，Django 返回上游错误，不返回半份凭据。

### 3.3 退出

```http
POST /api/v2/inspection/drc/exit
Content-Type: application/json

{"dockId": 12, "clientId": "temporary-client-id"}
```

成功响应 `{"status":"CLOSED"}`。退出必须允许重复调用。

## 4. Java REST

### 4.1 connect

`POST /api/v1/control/workspaces/{workspaceId}/drc/connect`

请求字段：`dockSn` 必填，`clientId` 可选，`expireSec=1800..86400`。

行为：

- 校验 Dock 在线且属于路径 workspace。
- 生成或刷新短期 MQTT 凭据。
- Redis ACL 只能包含：发布 `{dockSn}/drc/down`、订阅 `{dockSn}/drc/up`。
- 禁止空 topic `ALL` 权限。

### 4.2 enter

`POST /api/v1/control/workspaces/{workspaceId}/drc/enter`

必须使用请求的 `expireSec`、`deviceInfo.osdFrequency`、`deviceInfo.hsiFrequency`，成功后返回 `pub/sub` topic。

### 4.3 exit

`POST /api/v1/control/workspaces/{workspaceId}/drc/exit`

退出 DRC 并删除 owner/ACL；Dock 已退出时仍返回成功。

## 5. MQTT 规则

- 前端只发布响应中的 `publishTopic`，只订阅 `subscribeTopic`。
- 同一连接只有一个 publisher。
- `seq` 为正整数并在连接内单调递增；同一逻辑消息重试复用相同 `seq`。
- heartbeat 按 DJI DRC 协议持续发送。
- `stick_control` 频率为 5-10 Hz，四轴范围 `364..1684`，中立值 `1024`。
- 页面未 armed、失焦、松杆、MQTT 断开或退出前，发送中立帧并停止控制循环。
- 重连后不得自动 armed。

## 6. 方法目录

相机与云台：

`camera_mode_switch`、`camera_photo_take`、`camera_photo_stop`、`camera_recording_start`、`camera_recording_stop`、`camera_screen_drag`、`camera_aim`、`camera_focal_length_set`、`gimbal_reset`、`camera_look_at`、`camera_screen_split`、`photo_storage_set`、`video_storage_set`、`camera_exposure_mode_set`、`camera_exposure_set`、`camera_focus_mode_set`、`camera_focus_value_set`、`camera_point_focus_action`、`ir_metering_mode_set`、`ir_metering_point_set`、`ir_metering_area_set`。

扬声器：

`speaker_play_volume_set`、`speaker_play_mode_set`、`speaker_play_stop`、`speaker_replay`、`speaker_tts_play_start`。

公共页面不使用 feature 私有 `drc_speaker_*`；`drc_light_*` 在官方字段和真机证据冻结前保持 disabled。

## 7. 前端状态

页面状态至少包括：`DISCONNECTED`、`CONNECTING`、`CONNECTED`、`ARMED`、`DEGRADED`。

- `DISCONNECTED`：控制不可用，可发起连接。
- `CONNECTED`：可操作一次性载荷指令，不发送非中立摇杆。
- `ARMED`：按 5-10 Hz 发布 `stick_control`。
- `DEGRADED`：停止非中立控制，显示链路异常，只允许退出。

## 8. 安全

- MQTT password 只存在于当前页面内存，不写 localStorage/sessionStorage/IndexedDB。
- Django/Java 请求日志、响应日志、审计和异常上报不得记录 password。
- 前端不得接受服务端以外来源提供的 broker/topic。
- 页面关闭使用 best-effort 中立帧和 exit；不能依赖 unload 请求一定送达。
- 凭据到期前刷新失败必须进入 `DEGRADED` 并停止控制。

## 9. 最小验收

自动检查：

- 无权限用户无法查询能力、连接或退出。
- Django `connect` 严格校验字段，并按 `connect -> enter` 顺序调用上云 API。
- Java 不生成空 topic `ALL` ACL，且使用请求频率和有效期。
- Django 日志和审计不包含 MQTT password。

真机检查：

- 5 Hz 与 10 Hz 四轴方向和中立值正确。
- 松杆、切后台、断网、MQTT 断开和页面关闭后飞行器停止响应。
- 拍照、录像、云台、红外和扬声器方法逐项验证回执。
- `stick_control` 停帧后的设备 fail-stop 行为有记录；未通过前不得发布飞控功能。
