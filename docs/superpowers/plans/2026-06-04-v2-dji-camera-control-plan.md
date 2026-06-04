# v2 DJI 上云摄像头与直播控制接口方案

## Summary

- DJI Cloud API 支持这批能力：直播 start/stop/update/switch，直播镜头切 `wide/zoom/ir`，payload 相机模式切换、拍照、开始录像、停止录像、变焦、点选瞄准、云台复位。
- Django 第一版面向业务前端：请求用本地 `droneId/executorId` 走现有资源权限模型，同时在响应和文档里明确映射到 DJI `drone_sn/gateway_sn/payload_index/video_id`。
- 直播继续保留现有 `/api/v2/inspection/live/*`；新增一个统一摄像头动作入口 `/api/v2/inspection/camera/actions`。
- 第一版在线即允许，不要求 active flight session；拍照后的媒体文件不在相机接口里强制同步，继续走现有媒体同步/回调流程。
- 操作同步等待 DJI 上游返回，并保存后端操作记录；前端不做历史列表，POST 响应返回本次操作结果。

## Public API

- 调整现有直播接口：
  - `GET /api/v2/inspection/live/capacity?droneId=1`
  - `POST /api/v2/inspection/live/start`
  - `POST /api/v2/inspection/live/stop`
  - `POST /api/v2/inspection/live/update`
  - `POST /api/v2/inspection/live/switch`
  - 移除 active session 限制，改为检查无人机在线、资源可见/可用权限。
  - `switch` 请求补齐 `videoType`/`video_type`，映射 DJI `video_type`，用于 `wide/zoom/ir` 直播镜头切换。

- 新增摄像头统一动作接口：
  - `POST /api/v2/inspection/camera/actions`
  - 请求字段：

    ```json
    {
      "droneId": 1,
      "executorId": 2,
      "payloadIndex": "88-0-0",
      "action": "camera_photo_take"
    }
    ```

  - `droneId` 映射本地 `DroneResource`，响应带出 `droneSn`，对应 DJI 视频源设备 SN。
  - `executorId` 映射本地 `GatewayResource`，响应带出 `gatewaySn`，对应 DJI MQTT services 的 `{gateway_sn}` / Demo REST 路径 `{sn}`。
  - `payloadIndex` 直接对应 DJI `payload_index`，由前端从 live capacity 的 `cameras_list[].index` 获取。
  - `action` 使用 DJI 方法名，第一版支持：
    - `camera_mode_switch`：需要 `cameraMode`，枚举 `0=拍照, 1=录像, 2=智能低光, 3=全景`
    - `camera_photo_take`：只需要 `payloadIndex`
    - `camera_recording_start`：只需要 `payloadIndex`
    - `camera_recording_stop`：只需要 `payloadIndex`
    - `camera_focal_length_set`：需要 `cameraType` 和 `zoomFactor`；`cameraType=zoom` 允许 `2..200`，`cameraType=ir` 允许 `2..20`
    - `camera_aim`：需要 `cameraType`、`locked`、`x`、`y`；`cameraType` 支持 `zoom/wide/ir`，`x/y` 为 `0..1`
    - `gimbal_reset`：需要 `resetMode`，枚举 `0=回中, 1=朝下, 2=偏航回中, 3=俯仰朝下`

- 成功响应数据：

  ```json
  {
    "operationId": 123,
    "status": "SUCCEEDED",
    "action": "camera_photo_take",
    "droneId": 1,
    "droneSn": "1581F7FVC252A00CJ5TT",
    "executorId": 2,
    "gatewaySn": "9N9CMCJ001131H",
    "payloadIndex": "88-0-0",
    "upstream": {
      "authority": {},
      "command": {}
    }
  }
  ```

  失败时返回现有标准错误包；同时保存 `FAILED` 操作记录和 DJI 上游错误详情。

## Implementation Changes

- Gateway 层：
  - 在 `DjiGateway` 增加 `grab_payload_authority(gateway_sn, payload_index)`，调用 `/api/v1/control/devices/{gateway_sn}/authority/payload`。
  - 增加 `send_payload_command(gateway_sn, action, data)`，调用 `/api/v1/control/devices/{gateway_sn}/payload/commands`。
  - 摄像头动作统一先抢 payload authority，再下发 command；上游失败按现有 `DjiGatewayError` 机制透传。

- v2 inspection 层：
  - 新增 `CameraActionSerializer`，使用前端 camelCase 字段，必要时兼容 snake_case 别名，但文档主推 `payloadIndex/cameraType/zoomFactor/cameraMode/resetMode`。
  - 新增 `CameraActionView`，做：权限校验、在线校验、同 DJI connection 校验、参数转 DJI snake_case、调用 gateway、记录操作、返回结果。
  - live 视图改造为在线即允许：capacity 可见即可查；start/stop/update/switch 属于操作，要求调度/飞手并具备资源使用权限。
  - live switch serializer 增加 `videoType/video_type`，传给上游 `video_type`。

- 数据记录：
  - 新增 `CameraOperation` 表，保存 `action/status/drone/executor/payload_index/dji_connection/actor/started_at/completed_at/upstream_request/upstream_response/error_message`。
  - 不新增前端查询接口；记录只用于后端排障和审计补充。
  - 继续调用现有 `log_v2_action`，动作名使用 `camera_control_action`，`after_data` 记录 operationId/action/status。

- Mock 与文档：
  - `apps.dji_mock` 增加 payload authority 和 payload commands mock endpoint，支持成功和可控失败。
  - `docs/api-v2-frontend-guide.md` 增加摄像头控制章节，说明 `payloadIndex` 从 live capacity 获取，`executorId -> gatewaySn`，`action -> DJI method`。
  - OpenAPI schema 增加新接口、请求字段和 action 枚举；更新 live switch 字段说明。

## Test Plan

- API happy path：
  - `camera_photo_take`：校验 authority 与 command 调用顺序、operation 记录为 `SUCCEEDED`、响应包含 `droneSn/gatewaySn/payloadIndex`。
  - `camera_recording_start/stop`、`camera_mode_switch`、`camera_focal_length_set`、`camera_aim`、`gimbal_reset` 分别覆盖必填字段和上游 payload。
  - live switch 使用 `videoType=wide` 或 `video_type=wide` 均正确传给上游。

- Validation：
  - 缺 `droneId/executorId/payloadIndex/action` 返回 400。
  - 各 action 缺少对应参数返回 400。
  - `zoomFactor` 越界、`cameraType` 非法、`x/y` 越界、`resetMode` 非法返回 400。
  - 不支持的 action 返回 400。

- Permission and state：
  - 普通 monitor 用户不能 POST camera actions。
  - 调度/飞手具备 use/dispatch_task 权限时可以调用。
  - 无人机离线、执行端离线、无人机和执行端不属于同一个 DJI connection 时返回 409。
  - 不再要求 active flight session，覆盖“在线但无执行中任务”的成功路径。

- Upstream failure：
  - payload authority 失败：operation 记 `FAILED`，接口返回 502 标准错误。
  - payload command 失败或上游 Demo 返回 `The device is offline.`：operation 记 `FAILED`，错误透传。
  - live switch 上游 504/超时按现有上游错误格式返回，不吞掉细节。

- Schema/docs：
  - v2 schema 包含 `/api/v2/inspection/camera/actions`。
  - 所有新增/改造 operation 都满足现有 schema docs sync 测试里“前端用法/下一步”说明要求。

## Assumptions

- “抓拍”指 DJI 相机拍照，即 `camera_photo_take`，不是从直播流截图。
- “调整广角”第一版指直播镜头切换，继续走 `/api/v2/inspection/live/switch` 的 `videoType=wide`。
- 第一版不自动切相机模式；需要模式切换时前端显式调用 `camera_mode_switch`。
- 第一版不自动同步拍照/录像生成的媒体文件，沿用现有 DJI 媒体同步/回调流程。
- DJI Demo 的 payload command 当前实测可能被上游判定 offline；Django 方案不在本地伪造成功，只记录并透传上游真实错误。
