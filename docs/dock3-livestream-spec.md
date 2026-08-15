# Dock 3 直播支持规格

## 1. 目标

在现有链路上完整支持 DJI Dock 3 直播 Service：

```text
Frontend -> Django REST -> Java REST -> DJI MQTT services -> Dock 3
```

Django 负责登录态、资源权限、参数校验、上游代理和审计；`feature/DJI-Cloud-API-Demo-main` 中的 Java 服务负责直播地址配置、直播能力缓存和 DJI MQTT 请求。

## 2. 范围

本期实现：

- 保留并收紧开始直播、停止直播、调整清晰度和切换镜头四个现有 Django 接口，并允许这些接口选择无人机或 Dock 视频源。
- 扩展现有直播能力接口，使前端能够查询无人机或 Dock 的 `videoId`。
- 在 Java Cloud SDK 和 sample 服务中补齐 Dock 3 `live_camera_change`。
- 新增 Django Dock 舱内/舱外相机切换接口。
- 补齐 Django 和 Java OpenAPI 文档、Django mock 上游及聚焦测试。

本期不实现：

- 不让 Django 或浏览器直接连接 DJI MQTT。
- 不允许前端指定 `url`；推流目标仍由 Java `LiveStreamProperty` 配置。
- 不增加数据库表、Redis key、worker、直播会话中心或另一份直播能力缓存。
- 不改变播放器、录制、转码和 MediaMTX 方案。
- 不删除 Java 已兼容的 Agora、RTSP 推流类型。

## 3. DJI 协议

五个 Service 使用相同 topic：

```text
down: thing/product/{gateway_sn}/services
up:   thing/product/{gateway_sn}/services_reply
```

| method | data | 说明 |
|---|---|---|
| `live_start_push` | `url_type`、`url`、`video_id`、`video_quality` | 开始推流 |
| `live_stop_push` | `video_id` | 停止推流 |
| `live_set_quality` | `video_id`、`video_quality` | 调整清晰度 |
| `live_lens_change` | `video_id`、`video_type` | 切换广角、变焦、红外等镜头 |
| `live_camera_change` | `video_id`、`camera_position` | 切换 Dock 舱内/舱外 FPV 相机 |

公共枚举：

- `video_quality`：`0=自适应`、`1=流畅`、`2=标清`、`3=高清`、`4=超清`。
- `video_type`：`wide|zoom|ir|normal`。
- `camera_position`：`0=舱内`、`1=舱外`。
- Dock 3 官方 `url_type`：`1=RTMP`、`3=GB28181`、`4=WHIP`。

`video_id` 格式固定为：

```text
{sn}/{camera_index}/{video_index}
```

例如：

```text
1ZNDH1D0010098/165-0-7/normal-0
```

应答以 `data.result == 0` 为成功，非零为 DJI 业务错误。

## 4. 当前覆盖

| Django 接口 | Java REST | DJI method | 状态 |
|---|---|---|---|
| `POST /api/v2/inspection/live/start` | `/api/v1/manage/live/streams/start` | `live_start_push` | 已有，需收紧校验 |
| `POST /api/v2/inspection/live/stop` | `/api/v1/manage/live/streams/stop` | `live_stop_push` | 已有，需收紧校验 |
| `POST /api/v2/inspection/live/update` | `/api/v1/manage/live/streams/update` | `live_set_quality` | 已有，需收紧校验 |
| `POST /api/v2/inspection/live/switch` | `/api/v1/manage/live/streams/switch` | `live_lens_change` | 已有，需收紧校验 |
| `POST /api/v2/inspection/live/camera-change` | `/api/v1/manage/live/streams/camera-change` | `live_camera_change` | 本期新增 |

`live_lens_change` 与 `live_camera_change` 是不同操作。现有 `/live/switch` 继续只切换镜头类型，不兼做 Dock 舱内/舱外相机切换。

Java 已能依据 `videoId` 第一段 SN 对无人机或 Dock 执行 start、stop、update 和 switch；当前限制在 Django，因为接口只接受 `droneId`。本期将这些接口扩展为接受 `droneId` 或 `dockId`，两者必须且只能传一个。

直播能力沿用现有流程：Java 接收 `dock_livestream_ability_update` 并写入现有 Redis live capacity；Django 的 `GET /api/v2/inspection/live/capacity` 调用 Java 查询，不再持久化第二份数据。为了让前端取得 Dock FPV 的 `videoId`，capacity 查询也必须支持 `dockId`。

## 5. Django API

### 5.1 直播能力

```http
GET /api/v2/inspection/live/capacity?droneId=1
GET /api/v2/inspection/live/capacity?dockId=12
```

- `droneId` 与 `dockId` 必须且只能传一个，且必须为正整数。
- 查询无人机时使用 `ResourceType.DRONE`；查询 Dock 时使用 `ResourceType.DOCK`。
- 沿用现有 monitor 权限和在线状态校验。
- Django 将所选资源的 `device_sn` 传给现有 Java capacity 查询，并只返回 SN 匹配的能力项。
- 响应结构不变。前端从 `cameras_list[].index` 和 `videos_list[].index` 组装 `{deviceSn}/{cameraIndex}/{videoIndex}`。

### 5.2 现有控制接口契约

外部请求继续使用 camelCase，Django 调用 Java 时转换为 snake_case。

| 接口 | 允许的业务字段 | 必填规则 |
|---|---|
| `POST /api/v2/inspection/live/start` | `droneId`、`dockId`、`videoId`、`urlType`、`videoQuality` | 资源 ID 二选一；其余三个必填 |
| `POST /api/v2/inspection/live/stop` | `droneId`、`dockId`、`videoId` | 资源 ID 二选一；`videoId` 必填 |
| `POST /api/v2/inspection/live/update` | `droneId`、`dockId`、`videoId`、`videoQuality` | 资源 ID 二选一；其余两个必填 |
| `POST /api/v2/inspection/live/switch` | `droneId`、`dockId`、`videoId`、`videoType` | 资源 ID 二选一；其余两个必填 |

约束：

- `droneId`、`dockId` 如传入必须为正整数，并且必须且只能传一个。
- `videoId` 为非空字符串，必须正好包含三个非空 `/` 分段；第一段 SN 必须等于所选本地资源的 `device_sn`。
- `urlType` 只允许 `0..4`。为保持现有功能兼容，Django 暂不拒绝 Java 已支持的 `0=Agora`、`2=RTSP`；Dock 3 调用推荐使用 `1|3|4`。
- `videoQuality` 只允许 `0..4`。
- `videoType` 只允许 `wide|zoom|ir|normal`。Java 当前 `LensChangeVideoTypeEnum` 缺少官方支持的 `normal`，本期同步补齐。
- 每个接口拒绝表格之外的字段。

例如 start 序列化后传给 Java 的 body：

```json
{
  "device_sn": "DRONE_SN",
  "video_id": "DRONE_SN/88-0-0/normal-0",
  "url_type": 1,
  "video_quality": 1
}
```

stop、update、switch 同样只包含各自操作需要的字段，不发送其他直播参数。

实现时用每个操作一个小 serializer 表达必填和拒绝无关字段，不在一个共享 serializer 中加入按 view context 分支。资源二选一校验可放在一个仅包含三个公共字段的基类中。

### 5.3 新增 Dock 相机切换

```http
POST /api/v2/inspection/live/camera-change
Content-Type: application/json

{
  "dockId": 12,
  "videoId": "1ZNDH1D0010098/165-0-7/normal-0",
  "cameraPosition": 0
}
```

请求字段：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `dockId` | integer | 是 | 正整数，本地 `DockResource.id` |
| `videoId` | string | 是 | 三段 DJI video ID，第一段必须等于 Dock `device_sn` |
| `cameraPosition` | integer | 是 | 只允许 `0|1` |

成功响应沿用项目标准 envelope，`data` 透传 Java 成功数据，通常为空对象：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {}
}
```

Django 调用：

```http
POST /api/v1/manage/live/streams/camera-change

{
  "device_sn": "1ZNDH1D0010098",
  "video_id": "1ZNDH1D0010098/165-0-7/normal-0",
  "camera_position": 0
}
```

Java 当前 DTO 会忽略未知的 `device_sn`；该字段继续用于 Django gateway 的统一设备调用形式，实际 MQTT `data` 中不得出现它。

### 5.4 权限、资源与错误

五个控制接口执行顺序固定为：

1. 使用 `StrictSerializer` 校验字段和枚举。
2. 调用 `_require_control_operator`，只允许平台超管、调度员或飞手。
3. 使用 `_resource_binding_for_control` 校验资源处于当前账号可用范围，且有效权限包含 `use` 或 `dispatch_task`。
4. 读取本地资源并校验在线。
5. 校验 `videoId` 第一段 SN 与本地资源 `device_sn` 一致。
6. 调用绑定所属的 DJI connection。
7. 成功后写现有 `V2AuditLog`。

现有四个接口根据传入 ID 使用 `ResourceType.DRONE` 或 `ResourceType.DOCK`；`camera-change` 固定使用 `ResourceType.DOCK`，不使用 `ResourceType.GATEWAY`。项目虽然为同一机场保存 dock/gateway 姐妹资源，但业务 API 的 `dockId` 必须对应 `DockResource.id`。

失败语义沿用现有标准异常和 `_upstream_error_response`：

- 请求字段非法：`400`。
- 无权访问或控制：`403`。
- 本地资源不存在：`404`。
- 设备离线、SN 不匹配：`409`。
- Java/DJI 上游失败：沿用现有 DJI gateway 错误映射。

`camera-change` 成功审计字段：

```text
action: camera_change_live_stream
target_type: live_stream
target_id: dockId
resource_type: dock
resource_object_id: dockId
```

## 6. Django 改动位置

只修改现有直播链路涉及的文件：

| 文件 | 改动 |
|---|---|
| `apps/inspection_v2/serializers.py` | 为 capacity 增加资源二选一校验；用四个小 serializer 落实现有操作契约；新增 `LiveCameraChangeSerializer` |
| `apps/inspection_v2/views.py` | 按 `droneId|dockId` 解析资源；增加通用 `videoId` SN 校验；新增 `LiveCameraChangeView`；camelCase 转换增加 `camera_position` |
| `apps/inspection_v2/urls.py` | 注册 `live/camera-change` |
| `apps/dji_cloud/gateway.py` | 增加 `change_live_camera`，调用 Java `/streams/camera-change` |
| `apps/dji_mock/urls.py`、`views.py`、`state.py` | 增加 camera-change mock，供本地集成验证 |
| `apps/inspection_v2/tests.py` | 增加操作契约、权限、SN 和代理映射测试 |
| `apps/api_v2/docs_metadata.py`、相关 schema 测试和 `docs/api-v2-frontend-guide.md` | 登记新接口及前端调用说明 |

不增加新的 Django service、model 或 migration。

## 7. Java 上云改动

### 7.1 Cloud SDK

新增 `cloud-sdk/.../livestream/LiveCameraChangeRequest.java`：

```java
public class LiveCameraChangeRequest extends BaseModel {
    @NotNull
    private VideoId videoId;

    @NotNull
    @Min(0)
    @Max(1)
    private Integer cameraPosition;
}
```

必须提供无参构造、getter、链式 setter 和 `toString()`，与现有 livestream request 风格一致。SDK 的 snake_case ObjectMapper 将其序列化为：

```json
{
  "video_id": "1ZNDH1D0010098/165-0-7/normal-0",
  "camera_position": 0
}
```

修改：

- `cloud-sdk/.../livestream/LiveStreamMethodEnum.java` 增加 `LIVE_CAMERA_CHANGE("live_camera_change")`。
- `cloud-sdk/.../livestream/LensChangeVideoTypeEnum.java` 增加 `NORMAL("normal")`，与 Dock 3 官方枚举一致。
- `cloud-sdk/.../livestream/api/AbstractLivestreamService.java` 增加 `liveCameraChange(GatewayManager, LiveCameraChangeRequest)`，复用现有 `ServicesPublish` 和 `DEFAULT_TIMEOUT`。

`LiveCameraChangeRequest` 继承 `BaseModel` 后会被现有 `CloudSDKHandler` 自动执行 Bean Validation，无需再写校验框架。

### 7.2 Sample 服务

修改 `LiveTypeDTO`，新增：

```java
private Integer cameraPosition;
```

Sample 的全局 ObjectMapper 已使用 `SNAKE_CASE`，因此不需要额外 `@JsonProperty`。

修改 `ILiveStreamService`，增加：

```java
HttpResultResponse liveCameraChange(LiveTypeDTO liveParam);
```

修改 `LiveStreamServiceImpl`，实现固定流程：

1. 调用现有 `checkBeforeLive(liveParam.getVideoId())`。
2. 用返回的网关 SN 取得 `SDKManager.getDeviceSDK(...)`。
3. 构造 `LiveCameraChangeRequest(videoId, cameraPosition)` 并调用 `abstractLivestreamService.liveCameraChange(...)`。
4. DJI result 非成功时返回 `HttpResultResponse.error(result)`；成功时返回 `HttpResultResponse.success()`。

修改 `LiveStreamController`，增加：

```java
@PostMapping("/streams/camera-change")
public HttpResultResponse liveCameraChange(@RequestBody LiveTypeDTO liveParam) {
    return liveStreamService.liveCameraChange(liveParam);
}
```

`checkBeforeLive` 已能识别 `VideoId` 第一段为 Dock SN 的场景并注册对应 Gateway，不新增设备解析逻辑。

修改 `OpenApiLivestreamDocumentationSupport`：

- 登记 `/api/v1/manage/live/streams/camera-change`。
- 在 `LiveTypeDTO` schema 增加 `camera_position=0|1`。
- 增加请求示例并说明它与 `/streams/switch` 的区别。

## 8. 数据流

以切换舱外相机为例：

```text
Frontend
  -> POST Django /live/camera-change
  -> Django 校验角色、Dock binding、在线状态和 videoId SN
  -> POST Java /manage/live/streams/camera-change
  -> Java checkBeforeLive(videoId)
  -> MQTT thing/product/{dock_sn}/services
       method=live_camera_change
       data.video_id=...
       data.camera_position=1
  <- MQTT thing/product/{dock_sn}/services_reply
       data.result=0
  <- Java success
  <- Django 写审计并返回 success
```

任一层失败立即返回，不写成功审计；不做自动重试或状态补偿。

## 9. 测试

### 9.1 Django 聚焦测试

至少覆盖：

1. start 缺少 `videoQuality` 时返回 `400`，不调用 Java。
2. stop、update、switch 分别缺少各自必填字段时返回 `400`。
3. capacity 和现有四个控制接口对 `droneId|dockId` 未传、同时传入和非法值返回 `400`。
4. capacity 使用 `dockId` 时按 Dock SN 查询并返回对应能力。
5. start 使用 `dockId` 时能启动 Dock 视频源；stop、update、switch 的 Dock 路径保持相同资源映射。
6. `urlType`、`videoQuality`、`videoType` 越界或非法时返回 `400`，`videoType=normal` 可通过并正确代理。
7. 四个已有接口拒绝不属于自身契约的字段。
8. 现有四个接口的 `videoId` SN 与所选无人机或 Dock SN 不一致时返回 `409`，不调用 Java。
9. camera-change 成功时把 `dockId` 映射为 Dock SN，并向 Java 发送 `video_id` 和 `camera_position`。
10. camera-change 对非法 `cameraPosition`、离线 Dock、无使用权限和 SN 不匹配分别拒绝，且不调用 Java。
11. camera-change 成功后生成指向 Dock 资源的审计记录。
12. Django mock camera-change 可成功响应，便于端到端本地验证。
13. API v2 schema、路由清单和前端指南包含 capacity 的 `dockId` 和新控制接口。

已有任务自动直播调用固定传入 `video_id`、`url_type=1`、`video_quality=1`，不受此次必填收紧影响。

### 9.2 Java 聚焦测试

至少覆盖：

1. `LiveCameraChangeRequest` 经过 SDK ObjectMapper 后字段为 `video_id` 和 `camera_position`。
2. `cameraPosition=-1` 或 `2` 不能通过 `BaseModel.valid()`。
3. `liveCameraChange` 发布 method `live_camera_change`，并使用由 `checkBeforeLive` 解析出的网关。
4. DJI `result=0` 返回 success，非零 result 返回对应错误。
5. `LensChangeVideoTypeEnum.find("normal")` 能正确解析。

不新增测试框架，使用现有 Django TestCase 和 Maven/Spring Boot Test 依赖。

## 10. 验收标准

以下条件全部满足才视为完成：

1. `GET /api/v2/inspection/live/capacity` 能按 `droneId` 或 `dockId` 返回目标设备的直播能力。
2. start、stop、update、switch 能按 `droneId` 或 `dockId` 操作对应视频源，必填字段和枚举得到严格校验，现有无人机合法请求不回归。
3. `POST /api/v2/inspection/live/camera-change` 能控制已绑定、在线且有权限的 Dock 切换舱内/舱外相机。
4. 实际 MQTT 请求 topic 为 `thing/product/{dock_sn}/services`，method 为 `live_camera_change`，data 只包含正确的 `video_id` 和 `camera_position`。
5. DJI `result=0` 返回成功，非零 result 能通过现有错误链路返回前端。
6. 非法输入、越权、离线和 SN 不匹配均在调用上游前被 Django 拒绝。
7. 成功操作进入现有审计日志；不产生新数据库表或持久化状态。
8. Django 聚焦测试、API schema 同步测试及 Java Maven 测试通过。
9. 最后使用 Dock 3 真机分别验证五个 DJI method；若无真机环境，自动化测试通过不等同于真机验收完成。
