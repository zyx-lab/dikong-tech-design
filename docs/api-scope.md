# Spring Boot 重构 API 功能范围

本文件记录重构后需要保留、删除、调整和新增的接口功能。范围已于 2026-10-26 确认。Spring Boot 替换 Django 业务 API，继续调用现有 DJI 上云 Java 服务。除明确列出的变更外，保留接口的路径、HTTP 方法、业务字段、状态与错误语义；响应继续使用 `code/msg/data`，列表使用 `list/total`，分页参数使用 `pageNum/pageSize`。

下表中的 REST 路径均以 `/api/v2` 为前缀。`{id}` 等花括号表示路径参数。保留现有 OpenAPI JSON 和 Swagger UI 入口 `/api/v2/docs/schema/`、`/api/v2/docs/`。`/__mock-dji__/` 是开发用上游模拟入口，不属于产品 API。

## 保留的 REST 接口

### 账号与固定角色

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/iam/session/login`、`/iam/session/refresh`、`/iam/session/logout` | 登录、续期、退出 |
| GET | `/iam/me/context`、`/iam/me/profile` | 当前用户上下文与资料 |
| GET、POST | `/iam/accounts` | 账号列表与创建 |
| PUT | `/iam/accounts/{id}` | 更新账号基本资料，不接受密码字段 |
| POST | `/iam/accounts/{id}/enable`、`/iam/accounts/{id}/disable`、`/iam/accounts/{id}/reset-password` | 启用、禁用、管理员重置密码 |
| PUT | `/iam/accounts/{id}/roles` | 替换账号的唯一角色 |
| GET | `/iam/roles`、`/iam/roles/{id}` | 固定角色目录与详情 |

### DJI 连接、设备与固定摄像头

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET、POST | `/resource/dji-connections` | 连接列表与创建 |
| GET、PUT | `/resource/dji-connections/{id}` | 连接详情与更新 |
| POST | `/resource/dji-connections/{id}/discover` | 发现待接入设备 |
| GET | `/resource/dji-connections/mqtt-health` | MQTT 连接健康状态 |
| GET | `/resource/dji-connections/{id}/mqtt-messages/latest` | 最新 MQTT 消息 |
| GET | `/resource/dji-connections/{id}/hms-alerts` | HMS 告警历史 |
| GET | `/resource/drones`、`/resource/drones/{id}` | 已接入无人机列表与详情，包含在线状态和最新遥测 |
| GET | `/resource/docks`、`/resource/docks/{id}` | 已接入机场列表与详情 |
| GET | `/resource/gateways`、`/resource/gateways/{id}` | 已接入网关列表与详情 |
| GET | `/resource/payloads`、`/resource/payloads/{id}` | 已接入负载列表与详情 |
| GET、POST | `/resource/cameras` | 已接入固定摄像头列表、摄像头登记 |
| GET | `/resource/cameras/{id}`、`/resource/cameras/{id}/playback` | 摄像头详情、播放配置 |
| POST | `/resource/cameras/{id}/whep` | WHEP 信令交换 |
| POST | `/resource/bindings` | 将已发现或已登记资源接入平台 |
| DELETE | `/resource/bindings/{id}` | 解除资源接入 |
| GET | `/resource/summary`、`/resource/audit-logs` | 资源总览、资源审计查询 |

### 巡检、直播、相机与媒体

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET、POST | `/inspection/routes` | 航线列表、创建和 KMZ 上传 |
| GET、PUT、DELETE | `/inspection/routes/{id}` | 航线详情、更新和删除；保留封面及 KMZ 替换 |
| GET、POST | `/inspection/missions` | 任务列表与创建；保留 Dock 自动和 Pilot2 手动模式 |
| GET、PUT、DELETE | `/inspection/missions/{id}` | 任务详情、更新和删除 |
| POST | `/inspection/missions/{id}/preflight-check`、`/inspection/missions/{id}/cloud-execution/refresh` | 启动前检查、云端执行状态刷新 |
| POST | `/inspection/missions/{id}/start`、`/inspection/missions/{id}/complete`、`/inspection/missions/{id}/cancel`、`/inspection/missions/{id}/fail`、`/inspection/missions/{id}/abort` | 启动、完成、取消、失败、安全中止 |
| GET | `/inspection/active-flights`、`/inspection/active-flights/{id}` | 活动飞行列表与详情 |
| POST | `/inspection/telemetry/snapshots` | 按飞行会话上报遥测快照 |
| GET | `/inspection/live/capacity` | DJI 直播能力 |
| POST | `/inspection/live/start`、`/inspection/live/stop`、`/inspection/live/update`、`/inspection/live/switch`、`/inspection/live/camera-change` | 直播启停、画质、镜头与 Dock FPV 切换 |
| POST | `/inspection/camera/actions` | 拍照、录像、变焦等相机动作 |
| GET | `/inspection/drc/capabilities` | DRC 能力 |
| POST | `/inspection/drc/connect`、`/inspection/drc/actions`、`/inspection/drc/dock-actions`、`/inspection/drc/exit` | DRC 会话、飞行与机场动作 |
| GET | `/inspection/flight-records`、`/inspection/flight-records/{id}` | 飞行记录列表与详情 |
| PUT | `/inspection/flight-records/{id}` | 修改记录备注和异常原因 |
| POST | `/inspection/flight-records/{id}/refresh-media` | 手动刷新媒体索引；后台自动同步也保留 |
| GET | `/inspection/media-files`、`/inspection/media-files/{id}` | 云媒体列表与详情 |
| POST | `/inspection/media-files/{id}/refresh-url` | 刷新下载、预览或播放 URL |

### 系统只读接口

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/system/menus/current`、`/system/menus/tree`、`/system/menus` | 当前角色菜单、完整树与列表；菜单定义固定 |
| GET | `/system/operation-logs`、`/system/login-logs`、`/system/file-logs` | 操作、登录、文件日志查询；仅 root 可用 |

### WebSocket 与内部回调

| 类型 | 路径 | 功能 |
| --- | --- | --- |
| WS | `/ws/v2/dji/mqtt` | 实时 DJI MQTT 事件和资源状态推送 |
| WS | `/ws/v2/cameras/{id}/results` | 固定摄像头识别结果 |
| WS | `/ws/v2/drc/sessions/{sessionId}` | DRC 实时摇杆及上行消息 |
| POST | `/api/internal/dji/callbacks/media-upload` | 单文件媒体上传回调 |

## 删除的接口

| 路径或方法 | 原功能 | 原因 |
| --- | --- | --- |
| `/iam/departments/**` | 部门增改查及启停 | 不再建模部门 |
| `/iam/profile-types/**`、`/iam/accounts/{id}/profiles/**`、`/iam/accounts/{id}/qualifications/**` | 档案类型、角色档案、资质 | 固定单角色且移除资质 |
| `DELETE /iam/accounts/{id}` | 物理删除账号 | 使用 `/iam/accounts/{id}/disable` |
| `POST /iam/roles`、`PUT/DELETE /iam/roles/{id}`、`PUT /iam/roles/{id}/menus`、`PUT /iam/roles/{id}/permissions`、`PUT /iam/roles/{id}/data-scope` | 运行时角色与授权配置 | 固定角色及后端授权规则 |
| `GET /iam/permissions`、`POST /iam/permissions/sync` | 动态权限目录 | 不维护权限点模型 |
| `POST /system/menus`、`PUT/DELETE /system/menus/{id}` | 运行时菜单编辑 | 固定菜单 |
| `/resource/share-groups/**` | 部门间资源共享 | 无部门边界 |
| `POST /api/internal/dji/callbacks/media-group-upload` | 媒体组回调空实现 | 当前仅返回已忽略，媒体自动同步与单文件回调继续保留 |

## 现有接口的契约调整

1. **组织与角色**：删除所有请求和响应中的部门归属、部门树、数据范围和已废弃的动态授权字段。四个角色编码为 `platform_super_admin`、`business_admin`、`task_monitor_dispatcher`、`pilot`；其中 `department_admin` 改为 `business_admin`。`roleCodes` 保留数组形式，但必须恰好包含一个角色。角色列表与详情中的每个角色仅保留 `id/code/name/status`。
2. **当前用户**：`GET /iam/me/context` 仅返回 `user` 和单元素 `roles`；`GET /iam/me/profile` 保留姓名、电话、邮箱和单个角色，不返回部门、角色档案或资质。登录响应保留原 Token 字段与单角色账号摘要。
3. **资源接入**：`discover` 返回待接入 DJI 设备；登记后的固定摄像头也须由管理员通过原绑定接口手动接入平台。常规资源列表仅包含已接入资源。已接入资源按角色全局可见，`effectivePermissions` 字段继续返回，但由固定角色计算。绑定请求的 `resourceType/resourceId` 和 DJI 资源的 `djiConnectionId` 保留，部门字段删除。
4. **解除接入**：运行中任务、DRC 会话或已知正在推流时返回 `409`；历史任务引用不阻止解除。固定摄像头的观看者不阻止解除；解绑后拒绝新的播放请求，已有媒体连接由上游结束。资源和历史记录不物理删除。
5. **账号安全**：唯一平台管理员为 `root`；业务管理员只可创建或管理飞手与任务监控调度员，业务管理员彼此平级。禁用或重置密码立即撤销全部会话。账号基本资料更新不接受密码；管理员重置密码使用既有专用接口。
6. **审计**：所有写操作与高风险操作都记录结果和追踪标识，普通查询不写业务审计。操作、登录、文件和资源审计查询接口保留且仅 root 可用；登录和文件日志需要实际记录，不再返回固定空列表。

## 新增接口

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| POST | `/api/v2/iam/me/change-password` | 所有角色提供旧密码和新密码修改本人密码；成功后撤销该用户全部会话并要求重新登录 |
