# API v2 前端接入指南

本文面向前端联调人员，说明 `/api/v2/*` 接口的推荐调用顺序、关键字段和常见误区。Swagger 入口是 `/api/v2/docs/`，schema 入口是 `/api/v2/docs/schema/`。

前端只调用 `/api/v2/*`。旧 `/api/v1/*` 已移除；文档里出现的 `/api/v1/manage/*`、`/api/v1/wayline/*`、`/api/v1/media/*` 都是 DJI 上云内部协议路径，不是本系统前端业务 API。

## 通用规则

- 登录后所有业务请求都带 `Authorization: Bearer <accessToken>`。
- HTTP 响应统一是 `{ "code": "...", "msg": "...", "data": ... }`。
- 成功时 `code` 固定为 `00000`；失败时优先展示 `msg`，再按页面场景决定是否保留表单输入。
- 列表接口通常返回 `{ "list": [], "total": 0 }`，前端不要假设所有列表都有分页。
- `resourceId` 不是全局唯一 ID。混合展示资源时用 `${resourceType}:${resourceId}` 作为 key。

## 10 分钟接入流程

1. 确认后端已经创建 v2 平台业务管理员账号。本地联调没有固定内置业务账号；需要重置账号时让后端执行：

   ```bash
   python manage.py bootstrap_v2_system --reset --username <platform_admin_username> --password '<strong_password>' --noinput
   ```

   若需要一组前端联调账号，让后端执行：

   ```bash
   python manage.py bootstrap_v2_system --frontend-test-accounts --frontend-prefix jnu --frontend-password '123456'
   ```

   默认会按当前 v2 角色体系生成 `jnu_super`、`jnu_admin`、`jnu_dispatcher`、`jnu_pilot`、`jnu_handler`；其中 `jnu_pilot` 会同步飞手档案和一条有效 `pilot` 资质。

2. 调用 `POST /api/v2/iam/session/login`，保存 `accessToken` 和 `refreshToken`。
3. 所有业务请求带 `Authorization: Bearer <accessToken>`。
4. 首屏初始化依次调用 `GET /api/v2/iam/me/context`、`GET /api/v2/system/menus/current`、`GET /api/v2/iam/me/profile`。
5. access token 过期时调用 `POST /api/v2/iam/session/refresh`，成功后替换本地 token 并重试原请求。
6. 资源页面按“DJI 连接 -> discover 发现资源 -> bindings 绑定资源 -> drones/docks/gateways/payloads 列表”接。
7. 巡检页面按“上传 KMZ 航线 -> 选择无人机 -> 选择 `dockId` 或 `executorId` -> 创建任务 -> preflight-check -> start -> active-flights/telemetry/live/camera -> complete/cancel/fail/abort -> flight-records/media-files”接。`dockId` 是机场自动执行，会创建 DJI wayline flight task；`executorId` 是 Pilot2 手动执行，不会调用 `/flight-tasks`。

## 接口总览

下面清单来自当前 `/api/v2/docs/schema/`。字段结构、枚举和请求示例以 Swagger 为准；这里用于让前端快速判断“哪个页面该调哪个接口”。

### IAM 与当前用户

| 接口 | 用途 |
| --- | --- |
| `POST /api/v2/iam/session/login` | 登录，换取 `accessToken/refreshToken`。 |
| `POST /api/v2/iam/session/refresh` | access token 过期后续期。 |
| `POST /api/v2/iam/session/logout` | 注销当前会话。 |
| `GET /api/v2/iam/me/context` | 当前账号部门、角色、权限码、数据范围。 |
| `GET /api/v2/iam/me/profile` | 当前账号资料、角色档案、资质摘要。 |
| `GET /api/v2/iam/departments` | 部门树/列表。 |
| `POST /api/v2/iam/departments` | 创建部门。 |
| `PUT /api/v2/iam/departments/{id}` | 更新部门。 |
| `POST /api/v2/iam/departments/{id}/enable` | 启用部门。 |
| `POST /api/v2/iam/departments/{id}/disable` | 禁用部门。 |
| `GET /api/v2/iam/accounts` | 账号列表；常用于账号管理和飞手候选人选择。 |
| `POST /api/v2/iam/accounts` | 创建账号。 |
| `PUT /api/v2/iam/accounts/{id}` | 更新账号。 |
| `DELETE /api/v2/iam/accounts/{id}` | 删除账号。 |
| `POST /api/v2/iam/accounts/{id}/enable` | 启用账号。 |
| `POST /api/v2/iam/accounts/{id}/disable` | 禁用账号。 |
| `POST /api/v2/iam/accounts/{id}/reset-password` | 重置账号密码。 |
| `PUT /api/v2/iam/accounts/{id}/roles` | 替换账号角色。 |
| `GET /api/v2/iam/accounts/{id}/profiles` | 查询账号角色档案。 |
| `POST /api/v2/iam/accounts/{id}/profiles` | 创建账号角色档案。 |
| `GET /api/v2/iam/accounts/{id}/profiles/{profile_type}` | 读取账号角色档案。 |
| `PUT /api/v2/iam/accounts/{id}/profiles/{profile_type}` | 更新账号角色档案。 |
| `DELETE /api/v2/iam/accounts/{id}/profiles/{profile_type}` | 删除账号角色档案。 |
| `GET /api/v2/iam/accounts/{id}/qualifications` | 查询账号资质。 |
| `POST /api/v2/iam/accounts/{id}/qualifications` | 创建账号资质。 |
| `GET /api/v2/iam/accounts/{id}/qualifications/{qualification_id}` | 读取账号资质。 |
| `PUT /api/v2/iam/accounts/{id}/qualifications/{qualification_id}` | 更新账号资质。 |
| `DELETE /api/v2/iam/accounts/{id}/qualifications/{qualification_id}` | 删除账号资质。 |
| `GET /api/v2/iam/profile-types` | 查询账号档案类型。 |
| `POST /api/v2/iam/profile-types` | 创建账号档案类型。 |
| `GET /api/v2/iam/profile-types/{code}` | 读取账号档案类型。 |
| `PUT /api/v2/iam/profile-types/{code}` | 更新账号档案类型。 |
| `DELETE /api/v2/iam/profile-types/{code}` | 删除账号档案类型。 |
| `GET /api/v2/iam/roles` | 查询角色目录。 |
| `POST /api/v2/iam/roles` | 创建角色。 |
| `GET /api/v2/iam/roles/{id}` | 读取角色详情。 |
| `PUT /api/v2/iam/roles/{id}` | 更新角色。 |
| `DELETE /api/v2/iam/roles/{id}` | 删除非内置角色。 |
| `PUT /api/v2/iam/roles/{id}/menus` | 替换角色菜单授权。 |
| `PUT /api/v2/iam/roles/{id}/permissions` | 替换角色高级权限授权。 |
| `PUT /api/v2/iam/roles/{id}/data-scope` | 更新角色数据范围。 |
| `GET /api/v2/iam/permissions` | 查询权限点。 |
| `POST /api/v2/iam/permissions/sync` | 同步后端注册权限点；平台管理动作，普通前端页面通常不调用。 |

### 资源与 DJI 连接

| 接口 | 用途 |
| --- | --- |
| `GET /api/v2/resource/dji-connections` | 查询 DJI 连接列表。 |
| `POST /api/v2/resource/dji-connections` | 创建 DJI 连接。 |
| `GET /api/v2/resource/dji-connections/{id}` | 读取 DJI 连接详情。 |
| `PUT /api/v2/resource/dji-connections/{id}` | 更新 DJI 连接。 |
| `POST /api/v2/resource/dji-connections/{id}/discover` | 登录 DJI 并发现无人机、机场、网关、负载。 |
| `GET /api/v2/resource/dji-connections/mqtt-health` | 查询 MQTT worker 健康状态。 |
| `GET /api/v2/resource/dji-connections/{id}/mqtt-messages/latest` | 查询指定连接的最新 MQTT 透传消息。 |
| `POST /api/v2/resource/bindings` | 把发现资源绑定到部门。 |
| `DELETE /api/v2/resource/bindings/{id}` | 解绑资源。 |
| `GET /api/v2/resource/drones` | 查询当前账号可见无人机。 |
| `GET /api/v2/resource/drones/{id}` | 读取无人机详情。 |
| `GET /api/v2/resource/docks` | 查询当前账号可见机场。 |
| `GET /api/v2/resource/docks/{id}` | 读取机场详情。 |
| `GET /api/v2/resource/gateways` | 查询当前账号可见网关/执行端。 |
| `GET /api/v2/resource/gateways/{id}` | 读取网关/执行端详情。 |
| `GET /api/v2/resource/payloads` | 查询当前账号可见负载。 |
| `GET /api/v2/resource/payloads/{id}` | 读取负载详情。 |
| `GET /api/v2/resource/summary` | 资源总览统计。 |
| `GET /api/v2/resource/share-groups` | 查询资源共享组。 |
| `POST /api/v2/resource/share-groups` | 创建资源共享组。 |
| `PUT /api/v2/resource/share-groups/{id}` | 更新资源共享组。 |
| `POST /api/v2/resource/share-groups/{id}/departments` | 添加共享目标部门。 |
| `DELETE /api/v2/resource/share-groups/{id}/departments/{department_id}` | 移除共享目标部门。 |
| `POST /api/v2/resource/share-groups/{id}/resources` | 添加共享资源。 |
| `PUT /api/v2/resource/share-groups/{id}/resources/{resource_share_id}` | 更新共享资源权限。 |
| `DELETE /api/v2/resource/share-groups/{id}/resources/{resource_share_id}` | 移除共享资源。 |
| `GET /api/v2/resource/audit-logs` | 查询资源审计日志。 |

### 巡检、直播、相机和媒体

| 接口 | 用途 |
| --- | --- |
| `GET /api/v2/inspection/routes` | 查询航线列表。 |
| `POST /api/v2/inspection/routes` | 创建航线并上传 KMZ。 |
| `GET /api/v2/inspection/routes/{id}` | 读取航线详情；下载 KMZ 前用它刷新 DJI 下载 URL。 |
| `PUT /api/v2/inspection/routes/{id}` | 更新航线；替换 KMZ 时用 `multipart/form-data`。 |
| `DELETE /api/v2/inspection/routes/{id}` | 删除未被任务引用的航线。 |
| `GET /api/v2/inspection/missions` | 查询任务列表。 |
| `POST /api/v2/inspection/missions` | 创建任务。 |
| `GET /api/v2/inspection/missions/{id}` | 读取任务详情。 |
| `PUT /api/v2/inspection/missions/{id}` | 更新任务。 |
| `DELETE /api/v2/inspection/missions/{id}` | 删除待执行任务；不取消 DJI job。 |
| `POST /api/v2/inspection/missions/{id}/preflight-check` | 启动前检查；不下发 DJI 任务。 |
| `POST /api/v2/inspection/missions/{id}/start` | 启动任务；Dock 模式创建 DJI flight task，Pilot2 模式只建本地执行记录。 |
| `POST /api/v2/inspection/missions/{id}/cloud-execution/refresh` | 手动刷新 Dock 模式 DJI job 执行状态；Pilot2 模式不可用。 |
| `POST /api/v2/inspection/missions/{id}/complete` | 完成任务并尝试同步媒体、停止直播。 |
| `POST /api/v2/inspection/missions/{id}/cancel` | 取消任务；仅 Dock 模式已有 `djiJobId` 时取消 DJI job。 |
| `POST /api/v2/inspection/missions/{id}/fail` | 标记任务失败。 |
| `POST /api/v2/inspection/missions/{id}/abort` | 安全中止任务。 |
| `GET /api/v2/inspection/active-flights` | 查询活动飞行列表。 |
| `GET /api/v2/inspection/active-flights/{id}` | 读取活动飞行详情。 |
| `POST /api/v2/inspection/telemetry/snapshots` | 上报遥测快照；通常由后端/设备链路使用。 |
| `GET /api/v2/inspection/live/capacity` | 查询 DJI 直播能力，获取 `payloadIndex/videoIndex`。 |
| `POST /api/v2/inspection/live/start` | 启动直播。 |
| `POST /api/v2/inspection/live/stop` | 停止直播。 |
| `POST /api/v2/inspection/live/update` | 更新直播质量等参数。 |
| `POST /api/v2/inspection/live/switch` | 切换直播镜头。 |
| `POST /api/v2/inspection/camera/actions` | 相机拍照、录像、变焦、点选瞄准和云台复位。 |
| `GET /api/v2/inspection/flight-records` | 查询飞行记录。 |
| `GET /api/v2/inspection/flight-records/{id}` | 读取飞行记录详情。 |
| `PUT /api/v2/inspection/flight-records/{id}` | 更新飞行记录备注。 |
| `POST /api/v2/inspection/flight-records/{id}/refresh-media` | 按飞行记录关联任务刷新媒体索引。 |
| `GET /api/v2/inspection/media-files` | 查询云媒体文件；照片补齐 `previewUrl`，视频补齐 `playbackUrl`。 |
| `GET /api/v2/inspection/media-files/{id}` | 读取云媒体文件详情；照片补齐 `previewUrl`，视频补齐 `playbackUrl`。 |
| `POST /api/v2/inspection/media-files/{id}/refresh-url` | 刷新媒体下载、预览或播放 URL。 |

### 系统菜单和日志

| 接口 | 用途 |
| --- | --- |
| `GET /api/v2/system/menus/current` | 当前账号可见菜单树，驱动导航和按钮。 |
| `GET /api/v2/system/menus/tree` | 查询全部菜单树，角色授权页使用。 |
| `GET /api/v2/system/menus` | 查询菜单列表。 |
| `POST /api/v2/system/menus` | 创建菜单。 |
| `PUT /api/v2/system/menus/{id}` | 更新菜单。 |
| `DELETE /api/v2/system/menus/{id}` | 删除菜单。 |
| `GET /api/v2/system/operation-logs` | 查询操作日志。 |
| `GET /api/v2/system/login-logs` | 查询登录日志。 |
| `GET /api/v2/system/file-logs` | 查询文件日志。 |

## 登录与账号上下文

推荐顺序：

1. `POST /api/v2/iam/session/login`
2. `GET /api/v2/iam/me/context`
3. `GET /api/v2/system/menus/current`
4. `GET /api/v2/iam/me/profile`

`login` 返回 `accessToken` 和 `refreshToken`，token 只代表会话，不承载角色、权限、菜单或数据范围。`context` 用于读取当前部门、角色、权限码和数据范围摘要；`menus/current` 返回当前账号可见菜单树和按钮节点；`profile` 用于个人资料、飞手档案摘要和页面展示。

access token 失效时调用 `POST /api/v2/iam/session/refresh`，成功后替换本地 token 并重试原请求。退出登录调用 `POST /api/v2/iam/session/logout` 后清理本地 token。

## 权限与菜单

v2 权限码由后端注册，格式固定为 `<domain>:<resource>:<action>`，例如 `iam:account:read`、`resource:share_group:create`、`inspection:mission:start`。前端不能创建后端不认识的权限码。

角色配置菜单或按钮时，后端会自动授予这些菜单/按钮绑定的权限点。前端保存角色菜单后，只需要重新请求：

1. `GET /api/v2/iam/me/context`
2. `GET /api/v2/system/menus/current`

权限、角色、菜单和数据范围不写入 token，后端每次按数据库中的当前授权裁决。管理员修改角色权限后，已登录用户不需要重新登录；刷新 context 和 menus 后即可看到菜单变化，接口权限也会立即按新配置生效。

菜单显示规则：

- `DIRECTORY`：只要有可见子菜单就显示。
- `MENU`：角色被授予该菜单，并且用户拥有菜单绑定权限中的任意一个。
- `BUTTON`：角色被授予该按钮，并且用户拥有按钮绑定的权限码。

`GET /api/v2/iam/roles` 返回全局角色目录。部门管理员只能给本部门及下级账号分配 `assignableByDepartmentAdmin=true` 且数据范围不是 `ALL` 的角色；`platform_super_admin` 是内置系统角色，只给平台业务管理员使用。

初始化 v2 系统使用管理命令，不提供前端 setup API，也不内置固定种子账号：

```bash
python manage.py bootstrap_v2_system --reset --username <platform_admin_username> --password '<strong_password>' --noinput
```

前端联调账号使用显式开关生成，不随普通初始化自动创建：

```bash
python manage.py bootstrap_v2_system --frontend-test-accounts --frontend-prefix jnu --frontend-password 'FrontTest@123'
```

默认账号：`jnu_super`、`jnu_admin`、`jnu_dispatcher`、`jnu_pilot`、`jnu_handler`。

## 资源发现与绑定

推荐顺序：

1. `POST /api/v2/resource/dji-connections` 创建 DJI 连接。
2. `POST /api/v2/resource/dji-connections/{id}/discover` 登录 DJI 并同步发现资源。
3. `POST /api/v2/resource/bindings` 把需要使用的资源绑定到部门。
4. `GET /api/v2/resource/drones`、`docks`、`gateways`、`payloads` 展示已绑定资源。

`discover` 返回的是发现结果，资源列表返回的是已绑定资源。前端绑定时直接取资源项里的字段：

```json
{
  "resourceType": "drone",
  "resourceId": 1,
  "djiConnectionId": 1
}
```

`djiConnectionId` 是 v2 本地 DJI 连接 ID，不是 DJI workspace ID。`resourceType + resourceId` 才能唯一定位一个 v2 资源。

资源列表和详情都会返回 `djiConnectionId`、`djiConnectionName`。前端展示无人机、机场、网关或负载时，可以直接显示资源所属 DJI 连接；排查 MQTT 或 DJI 上游问题时，也可以用这个 ID 去查 `mqtt-health` 和 `mqtt-messages/latest`。

## MQTT

DJI MQTT 由后端 worker 连接，前端不直接连接 DJI broker。

推荐顺序：

1. `GET /api/v2/resource/dji-connections/mqtt-health` 判断 worker 是否连接、订阅和收包。
2. `GET /api/v2/resource/dji-connections/{id}/mqtt-messages/latest?deviceSn=<SN>` 查询设备最新消息。
3. WebSocket `/ws/v2/dji/mqtt?token=<accessToken>` 用于实时订阅。

常用 `topicKind`：

- `osd`：设备位置、电量、速度、高度等遥测。
- `state`：设备状态。
- `events`：事件和任务进度。
- `services_reply`：服务调用回复。
- `status`：上下线和拓扑状态。

## 内部调用 DJI 上云 API 的接口

前端只调用 `/api/v2/*`，不要直接调用 DJI 上云 API。后端会用 `DjiConnection` 保存的 `baseUrl/username/password/loginFlag` 自动登录、续期并带 DJI token 请求上游。DJI 上游失败时，向前端返回标准 envelope，常见 HTTP 状态是 `502`，前端优先展示 `msg` 并保留当前页面状态。

后端内部会按需调用：

```http
POST /api/v1/manage/login
POST /api/v1/manage/token/refresh
```

这两个登录态接口是网关内部细节，前端不需要也不应该保存 DJI token。前端只保存本系统 `POST /api/v2/iam/session/login` 返回的 `accessToken`。

会触发 DJI 上云 HTTP 调用的 v2 接口如下：

| v2 接口 | 后端 DJI 调用 | 前端关注点 |
| --- | --- | --- |
| `POST /api/v2/resource/dji-connections/{id}/discover` | 登录/续期后调用 DJI 设备列表 | 返回的是发现结果，不等于已绑定资源；继续用 `resourceType/resourceId/djiConnectionId` 调绑定接口 |
| `POST /api/v2/inspection/routes` | 上传 DJI WPML KMZ 到 DJI wayline，并获取下载 URL | 必须传真实 `kmzFile`；后端自动解析航点和 `waylineType`，成功后用本地 `routeId` 创建任务 |
| `GET /api/v2/inspection/routes/{id}` | 仅在 DJI KMZ 下载 URL 缺失或快过期时刷新 URL | 下载 KMZ 前先读详情，避免使用列表里的过期 URL |
| `PUT /api/v2/inspection/routes/{id}` | 只有替换 `kmzFile` 时才重新上传 DJI | 不替换 KMZ 用 JSON；替换 KMZ 用 `multipart/form-data`，航点和 `waylineType` 由后端重新解析 |
| `DELETE /api/v2/inspection/routes/{id}` | 本地删除后 best-effort 删除 DJI wayline 文件 | 成功响应以本地删除为准；DJI 清理失败不阻断 |
| `POST /api/v2/inspection/missions/{id}/preflight-check` | 本地前置检查；条件满足时只读查询 DJI live capacity | 启动任务前先调用；`canStart=false` 时展示 `blockingReasons` |
| `POST /api/v2/inspection/missions/{id}/start` | Dock 模式启动直播并创建 DJI wayline flight task；Pilot2 模式只创建本地执行记录，直播 best-effort | 任务必须待执行；`dockId/executorId` 二选一；航线、无人机和执行资源必须同 DJI 连接 |
| `POST /api/v2/inspection/missions/{id}/cloud-execution/refresh` | Dock 模式查询 DJI jobs 并按本地 `djiJobId` 匹配；Pilot2 模式返回 409 | 只有机场自动执行任务可用；Pilot2 手动任务通过 `complete/cancel/fail` 推进 |
| `POST /api/v2/inspection/missions/{id}/complete` | Dock/Pilot2 先执行一次媒体同步，创建后台媒体同步状态，并停止直播 | 媒体同步失败不阻断完成；媒体没出现时等待 worker 或调用 `refresh-media` |
| `POST /api/v2/inspection/missions/{id}/cancel` | Dock 且已有 `djiJobId` 时取消 DJI job；Pilot2 只更新本地状态并停止直播 | DJI 取消失败会返回错误；停止直播失败不阻断取消 |
| `POST /api/v2/inspection/missions/{id}/fail` | 尝试停止任务直播 | 不主动取消 DJI job；以本地失败状态为准 |
| `POST /api/v2/inspection/missions/{id}/abort` | 尝试停止任务直播 | 安全中止场景；不主动取消 DJI job |
| `GET /api/v2/inspection/live/capacity` | 查询 DJI live capacity | 用 `cameras_list[].index` 取 `payloadIndex`，用视频能力组装 `videoId` |
| `POST /api/v2/inspection/live/start` | 启动 DJI live stream | 响应可能包含 `url/rtmp_url/webrtc_url/play_url/hls_url` |
| `POST /api/v2/inspection/live/stop` | 停止 DJI live stream | 成功后停止播放器并清理直播状态 |
| `POST /api/v2/inspection/live/update` | 更新 DJI live stream | 常用于调整 `videoQuality` |
| `POST /api/v2/inspection/live/switch` | 切换 DJI live stream 镜头 | `videoType` 用 `wide/zoom/ir/normal` |
| `POST /api/v2/inspection/camera/actions` | 抢占 payload authority 后下发 payload commands | 用本地 `droneId/executorId` 和 capacity 中的 `payloadIndex` |
| `POST /api/v2/inspection/flight-records/{id}/refresh-media` | 查询 DJI media files 列表；Dock 按 `djiJobId` 精确归属，Pilot2 按 workspace、无人机和会话时间窗唯一命中归属无 job 媒体 | 显式刷新媒体索引并更新后台同步状态；照片预览 URL 和视频播放 URL 在媒体列表和详情读取时按需补齐 |
| `POST /api/v2/inspection/media-files/{id}/refresh-url` | 按本地媒体文件显式刷新 DJI signed URL | `urlType=download|preview|playback`，用于下载、播放或预览失败重试 |

资源发现对应的 DJI 参考路径：

```http
GET /api/v1/manage/workspaces/{workspace_id}/devices/bound?domain=0
GET /api/v1/manage/workspaces/{workspace_id}/devices/bound?domain=3
GET /api/v1/manage/workspaces/{workspace_id}/devices
```

`domain=0` 用于无人机，`domain=3` 用于机场；网关来自 workspace 设备列表。后端还会从设备 payload 字段里提取负载资源。`discover` 成功后，资源只是进入本地资源池；前端必须再调用 `POST /api/v2/resource/bindings`，资源列表接口才会出现这些资源。

航线和 KMZ 对应的 DJI 参考路径：

```http
POST /api/v1/wayline/workspaces/{workspace_id}/waylines/files/upload
GET /api/v1/wayline/workspaces/{workspace_id}/waylines/{wayline_id}/url
DELETE /api/v1/wayline/workspaces/{workspace_id}/waylines/{wayline_id}
```

`POST /api/v2/inspection/routes` 会先解析 DJI WPML KMZ，生成本地 `waypoints/defaultAltitude/defaultSpeed/waylineType`，再上传 KMZ。DJI 返回 `wayline_id/download_url` 后，后端会再取一次可直接访问的下载地址，最终写入 `data.djiFile`。字段含义：

- `data.id`：本地航线 ID，创建任务时传这个 ID。
- `data.djiFile.djiFileId`：DJI `wayline_id`，前端只展示或排查时使用。
- `data.djiFile.downloadUrl`：DJI 上云侧 KMZ 下载地址，可能过期。
- `data.djiFile.downloadUrlExpiresAt`：后端从签名 URL 推断的过期时间；如果为空，也建议下载前重新读详情。

任务启动对应的 DJI 参考路径。注意：`/flight-tasks` 只用于 Dock 自动模式，Pilot2 手动模式不会调用它：

```http
GET /api/v1/manage/live/capacity
POST /api/v1/manage/live/streams/start
POST /api/v1/wayline/workspaces/{workspace_id}/flight-tasks
GET /api/v1/wayline/workspaces/{workspace_id}/jobs
POST /api/v1/manage/live/streams/stop
```

启动前建议先调用：

```http
POST /api/v2/inspection/missions/{id}/preflight-check
```

preflight 不会启动直播，也不会调用 DJI `flight-tasks` 下发航线任务。它会检查本地任务状态、航线是否已上传 DJI、无人机和执行资源是否绑定且在线、是否属于同一个 DJI 连接、资源是否被其他运行中任务占用；这些本地条件都通过后，才会调用只读的 DJI live capacity。Dock 模式下直播能力失败会阻断启动；Pilot2 手动模式下直播能力失败只进入 `warnings`。

响应重点字段：

- `data.canStart`：是否可以让前端展示或启用“开始任务”按钮。
- `data.blockingReasons[]`：阻断启动的原因；前端按 `message` 展示即可。
- `data.warnings[]`：不阻断启动的提示。Pilot2 手动模式下，直播能力查询失败或没有可用视频源会在这里提示，但不阻断飞手在遥控器执行航线。
- `data.execution.executionMode`：`DOCK_AUTO` 表示机场自动执行，`PILOT2_MANUAL` 表示 Pilot2/遥控器手动执行。
- `data.execution.dockId`：Dock 自动模式的机场资源 ID。
- `data.execution.executorId`：Pilot2 手动模式的执行端/遥控器资源 ID；它不是飞手 ID，也不是无人机 ID。
- `data.execution.selectedLiveVideoId`：preflight 从 live capacity 里选出的直播视频源；Pilot2 模式为空也可以启动，Dock 模式为空会阻断。

如果 `canStart=false`，前端不要调用 `start`，应先让用户按 `blockingReasons` 修正资源、航线、执行端或设备在线状态。即使 `canStart=true`，Dock 模式真实 `start` 仍可能因为设备类型或现场状态返回 DJI 错误；Pilot2 模式不会创建 DJI job。

`POST /api/v2/inspection/missions/{id}/start` 按 `executionMode` 分流：

- `DOCK_AUTO`：先从 capacity 里选择第一个可用视频源启动直播，再创建 DJI wayline flight task。
- `PILOT2_MANUAL`：创建本地 `FlightSession` 和 `MissionCloudExecution`，`djiJobId=""`；直播是 best-effort，失败只写入 `liveStatus=FAILED/liveErrorMessage`，任务仍进入 `RUNNING`。

Dock 自动模式的 DJI 字段映射：

- 本地 `route.cloudFile.djiFileId` -> DJI `file_id`
- 本地 `dock.deviceSn` -> DJI `dock_sn`
- 本地 `mission.name` -> DJI `name`
- 本地 `route.cloudFile.waylineType` -> DJI `wayline_type`，该值由后端从 KMZ `templateType` 推导
- 固定 `task_type=0`，表示立即任务
- 默认 `rth_altitude=30`、`out_of_control_action=0`

如果 Dock 模式创建 DJI 任务失败，后端会尝试停止刚启动的直播，然后把 DJI 错误返回给前端。启动成功后，前端主要看任务详情里的 `executionMode/cloudExecution` 和活动飞行列表；任务进度继续通过 MQTT `events/services_reply/status/osd` 和 v2 worker 写入的数据刷新。

Dock 模式如果没有稳定 MQTT 任务事件，可调用：

```http
POST /api/v2/inspection/missions/{id}/cloud-execution/refresh
```

该接口不接受 DJI job id，后端会用本地任务里的 `cloudExecution.djiJobId` 去 DJI jobs 列表匹配。Pilot2 手动模式没有 DJI job，调用会返回 409，前端应使用 `complete/cancel/fail` 推进本地状态。Dock 模式响应仍是任务详情，前端重点读取：

- `data.status`：任务本地状态。
- `data.cloudExecution.status`：云端执行状态，可能是 `STARTING/RUNNING/COMPLETED/CANCELED/FAILED`。
- `data.cloudExecution.progressPercent`：DJI job 返回的执行进度。
- `data.cloudExecution.lastEventAt`：本次从 DJI jobs 刷新的时间。

当 DJI job 已成功、取消或失败时，后端会复用任务事件闭环，自动更新飞行会话、生成飞行记录、执行一次媒体同步、创建后台媒体同步状态并停止直播。前端收到终态后刷新任务列表、飞行记录列表和媒体列表；普通 GET 查询不触发媒体发现。

任务取消和结束对应的 DJI 参考路径：

```http
DELETE /api/v1/wayline/workspaces/{workspace_id}/jobs?job_id={job_id}
POST /api/v1/manage/live/streams/stop
GET /api/v1/media/workspaces/{workspace_id}/files
```

`cancel` 只有在 Dock 自动模式且本地已有 `djiJobId` 时才会取消 DJI job；Pilot2 模式只更新本地状态并停止直播。`complete/fail/abort` 不主动取消 DJI job，只会尝试停止直播。Dock 模式 `complete` 会尝试查询 DJI media files 列表并按 `djiJobId` 关联照片/视频；Pilot2 模式会查询 DJI media files 列表中的无 job 媒体，并结合已回调落库媒体，按同 workspace、同无人机、拍摄时间落在会话窗口内且唯一命中飞行记录时绑定。媒体同步失败不阻断完成操作；后台 worker 会按退避节奏继续补偿同步到截止时间。

直播接口对应的 DJI 参考路径：

```http
GET /api/v1/manage/live/capacity
POST /api/v1/manage/live/streams/start
POST /api/v1/manage/live/streams/stop
POST /api/v1/manage/live/streams/update
POST /api/v1/manage/live/streams/switch
```

前端传本地 `droneId`，后端映射成 DJI `device_sn`。`videoId` 使用 DJI 格式 `{droneSn}/{payloadIndex}/{videoIndex}`，其中 `payloadIndex` 和 `videoIndex` 都来自 capacity。直播响应基本透传 DJI 结果，播放器地址优先按实际返回字段选择，例如 `play_url`、`webrtc_url`、`hls_url`、`rtmp_url` 或 `url`。

相机与云台对应的 DJI 参考路径：

```http
POST /api/v1/control/devices/{gatewaySn}/authority/payload
POST /api/v1/control/devices/{gatewaySn}/payload/commands
```

后端会先抢 payload authority，再发送 payload commands。前端传 camelCase，后端转 DJI snake_case：

- `payloadIndex` -> `payload_index`
- `cameraMode` -> `camera_mode`
- `cameraType` -> `camera_type`
- `zoomFactor` -> `zoom_factor`
- `resetMode` -> `reset_mode`

媒体刷新对应的 DJI 参考路径：

```http
GET /api/v1/media/workspaces/{workspace_id}/files
GET /api/v1/media/workspaces/{workspace_id}/files/{file_id}/url
GET /api/v1/media/workspaces/{workspace_id}/files/{file_id}/preview-url
GET /api/v1/media/workspaces/{workspace_id}/files/{file_id}/playback-url
```

`POST /api/v2/inspection/flight-records/{id}/refresh-media` 对 Dock 模式会用飞行记录关联任务的 `djiJobId` 精确归属媒体，并写入本地 `CloudMediaFile`；对 Pilot2 模式会查询 DJI media files 列表中的无 job 媒体，并结合已回调落库媒体，按本地会话窗口唯一命中绑定，同时更新该飞行记录的后台同步状态。如果要让新拍摄的照片/视频出现，可以等待 DJI 回调/worker 写入，或主动调用 `refresh-media`。`GET /api/v2/inspection/flight-records`、`GET /api/v2/inspection/flight-records/{id}` 和 `GET /api/v2/inspection/media-files` 都不触发 DJI media files 列表同步；媒体列表只查询本地已同步媒体。`GET /api/v2/inspection/media-files` 会为本次返回的照片媒体同步补齐空白或临近过期的 `previewUrl`，并为视频媒体同步补齐空白或临近过期的 `playbackUrl`。照片预览刷新失败或视频播放地址刷新失败时，列表接口会返回错误而不是成功返回空白 URL。`GET /api/v2/inspection/media-files/{id}` 会在照片媒体的 `previewUrl` 为空或签名临近过期时 best-effort 刷新预览 URL，刷新失败仍返回媒体详情；视频媒体的 `playbackUrl` 为空或临近过期时会刷新播放 URL，刷新失败返回错误。

如果照片媒体已经在本地列表里，前端可以直接使用列表或详情返回的 `previewUrl` 展示预览；视频媒体使用 `playbackUrl` 播放。`thumbnailUrl` 只透传 DJI media files 列表或回调里的缩略图字段，DJI 没给时为空，后端不会生成视频封面。下载、播放地址过期，或照片预览仍不可用需要手动重试时，调用：

```http
POST /api/v2/inspection/media-files/{id}/refresh-url
```

请求体：

```json
{
  "urlType": "download"
}
```

`urlType` 可选 `download`、`preview`、`playback`，省略时默认 `download`。前端只传本地媒体文件 ID，不传任意 DJI file id。成功后按 `urlType` 读取返回的 `downloadUrl`、`previewUrl` 或 `playbackUrl`，替换当前媒体项即可。

## 航线与任务

推荐顺序：

1. 先完成 DJI 资源发现和绑定。
2. 通过 `GET/POST /api/v2/iam/accounts/{id}/profiles` 维护账号角色档案；飞手档案使用 `profileType=pilot`。
3. 通过 `/api/v2/iam/accounts/{id}/qualifications` 维护账号资质；飞手证书资质同样使用 `profileType=pilot`。
4. `POST /api/v2/inspection/routes` 创建航线并上传 KMZ。
5. 通过 `GET /api/v2/resource/drones` 选择本地无人机资源，取 `id` 作为 `droneId`。
6. 选择执行资源：机场自动执行从 `GET /api/v2/resource/docks` 取 `id` 作为 `dockId`；Pilot2 手动执行从 `GET /api/v2/resource/gateways` 取 `id` 作为 `executorId`。二者必须且只能提交一个。
7. 通过 `GET /api/v2/iam/accounts?roleCode=pilot&profileType=pilot&qualified=true` 选择候选飞手账号，并在 `POST /api/v2/inspection/missions` 中提交 `pilotAccountProfileId`。
8. `POST /api/v2/inspection/missions/{id}/preflight-check` 做启动前检查。
9. `POST /api/v2/inspection/missions/{id}/start` 启动任务。
10. `GET /api/v2/inspection/active-flights` 展示飞行过程；`POST /api/v2/inspection/telemetry/snapshots` 是遥测上报接口。Dock 自动模式需要手动拉 DJI job 状态时调用 `cloud-execution/refresh`，Pilot2 手动模式不调用该接口。
11. `POST /api/v2/inspection/missions/{id}/complete`、`cancel`、`fail` 或 `abort` 结束任务。

创建 Pilot2 手动执行任务的最小请求体示例：

```json
{
  "name": "巡检任务",
  "routeId": 7,
  "droneId": 1,
  "executorId": 1,
  "pilotAccountProfileId": 3,
  "scheduledAt": null,
  "remark": ""
}
```

创建 Dock 自动执行任务时，把 `executorId` 换成 `dockId`：

```json
{
  "name": "机场自动巡检任务",
  "routeId": 7,
  "droneId": 1,
  "dockId": 2,
  "pilotAccountProfileId": 3,
  "scheduledAt": null,
  "remark": ""
}
```

`executorId` 是本系统本地 Pilot2 执行端/遥控器资源 ID，对应后端 `GatewayResource.id`，也就是 `GET /api/v2/resource/gateways` 返回列表项里的 `id`。它不是飞手 ID、无人机 ID、机场 ID、DJI workspace ID，也不是 DJI 原始设备 SN。代码里任务表字段 `InspectionMission.executor` 指向 `GatewayResource`；资源类型枚举里 `gateway` 的含义是“执行端/网关”。

Pilot2 手动执行的实际动作发生在遥控器上：后端只要求任务航线已同步到同一 DJI workspace，飞手在 Pilot2 里选择航线并执行；平台通过 `start/complete/cancel/fail` 推进本地任务闭环。

Dock 自动执行时，后端会读取机场资源的 `deviceSn`，并把它作为 DJI 上游 `/flight-tasks` 请求里的 `dock_sn` 使用。当前实现要求：

- `dockId` 或 `executorId` 对应的执行资源必须已绑定且当前账号可用。
- 航线 `routeSnapshot.djiFile.djiConnectionId`、`droneId` 对应无人机、所选 `dockId/executorId` 必须属于同一个 `djiConnectionId`。
- 所选执行资源必须在线。

如果任务同时提交 `dockId` 和 `executorId`，或二者都不提交，创建/更新接口会返回 400。

飞手不再是独立资源；它是账号拥有 `pilot` 角色、有效 `pilot` 档案和有效 `pilot` 资质后的业务能力。旧 `/api/v2/workforce/pilots` 和 `/api/v2/inspection/pilot-profiles` 不再作为 v2 接口使用。

创建航线使用 `multipart/form-data`，必须上传 DJI WPML `kmzFile`，并传 `name/djiConnectionId`。前端不要传 `waypoints/waylineType/defaultAltitude/defaultSpeed`，这些字段由后端解析 KMZ 后返回。更新航线时，如果不替换 KMZ，可以用 JSON 只更新名称、状态、备注、封面等基础信息；如果替换 KMZ，继续用 `multipart/form-data`，传 `djiConnectionId/kmzFile`。删除航线调用 `DELETE /api/v2/inspection/routes/{id}`，请求体为空；已被任何任务引用的航线不能删除，未引用航线删除成功后返回 `id` 和 `deleted=true`，前端从列表移除即可。

航线保存成功后会返回两个可访问 URL：

- `coverImageUrl`：航线封面 URL。封面由后端保存到系统配套 MinIO，接口返回私有 bucket 的预签名 URL。该 URL 会过期，前端长期展示前应重新读取航线详情获取新 URL。
- `djiFile.downloadUrl`：KMZ 下载 URL。KMZ 文件在 DJI 上云侧，不会复制到本系统 MinIO；后端返回 DJI 上云可直接下载的绝对链接。

`GET /api/v2/inspection/routes/{id}` 会按过期时间刷新 `djiFile.downloadUrl` 并返回当前可用链接。`GET /api/v2/inspection/routes` 列表接口只返回缓存值，不主动批量刷新过期链接；如果用户要下载 KMZ，推荐先读取详情。

## 直播

推荐顺序：

1. `GET /api/v2/inspection/live/capacity` 查看 DJI 当前可直播设备和镜头能力。
2. `POST /api/v2/inspection/live/start` 启动直播。
3. `POST /api/v2/inspection/live/update` 调整质量等参数。
4. `POST /api/v2/inspection/live/switch` 切换视频源。
5. `POST /api/v2/inspection/live/stop` 停止直播。

直播接口会调用 DJI 上游。失败时优先展示 `msg`，并保留当前页面状态，便于用户重试。

直播接口使用本地 `droneId`，后端会映射为 DJI `drone_sn`。`videoId` 使用 DJI 格式 `{drone_sn}/{payload_index}/{video_type}-0`；`payload_index` 可从 capacity 返回的 `cameras_list[].index` 获取。切换广角、变焦或红外镜头时调用 `POST /api/v2/inspection/live/switch`，请求里传 `videoType=wide|zoom|ir`，后端会转成 DJI 上游需要的 `video_type`。

## 相机与云台控制

相机、录像、变焦、点选瞄准和云台复位统一使用一个入口：

```http
POST /api/v2/inspection/camera/actions
```

推荐调用顺序：

1. 完成登录，业务请求带 `Authorization: Bearer <accessToken>`。
2. 完成 DJI 连接、资源发现和资源绑定。
3. 通过 `GET /api/v2/resource/drones` 选择本地无人机资源，取 `id` 作为 `droneId`。
4. 通过 `GET /api/v2/resource/gateways` 选择本地执行端/网关资源，取 `id` 作为 `executorId`。
5. 调用 `GET /api/v2/inspection/live/capacity?droneId=<droneId>`，从 `data.cameras_list[].index` 取 `payloadIndex`，例如 `88-0-0`。
6. 调用 `POST /api/v2/inspection/camera/actions` 下发具体动作。

基础请求体如下。对外请求字段固定使用 camelCase；snake_case 只用于后端调用 DJI 上游协议。

```json
{
  "droneId": 1,
  "executorId": 2,
  "payloadIndex": "88-0-0",
  "action": "camera_photo_take"
}
```

字段映射：

- `droneId`：本地无人机资源 ID，响应里会带出 `droneSn`，对应 DJI 视频源设备 SN。
- `executorId`：本地执行端/网关资源 ID，响应里会带出 `gatewaySn`，对应 DJI `/api/v1/control/devices/{sn}/...` 路径里的 `{sn}`。
- `payloadIndex`：DJI `payload_index`，从 `GET /api/v2/inspection/live/capacity?droneId=...` 的 `cameras_list[].index` 获取。
- `action`：DJI payload command 方法名。

后端会替前端调用 DJI 上游，前端不要直接调 DJI：

1. 抢占 payload authority（payload 控制权）：`POST /api/v1/control/devices/{gatewaySn}/authority/payload`，body 是 `{ "payload_index": "<payloadIndex>" }`。
2. 下发 payload commands：`POST /api/v1/control/devices/{gatewaySn}/payload/commands`，body 是 `{ "cmd": "<action>", "data": { ... } }`。

调用前置条件：

- 当前账号必须是平台超管、调度员或飞手。
- `droneId` 和 `executorId` 必须是当前账号可见且可用的已绑定资源。
- 无人机和执行端都必须在线。
- 无人机和执行端必须属于同一个 DJI 连接，否则后端无法用同一个上游连接完成控制。

支持的动作和参数：

| action | 使用场景 | 额外字段 |
| --- | --- | --- |
| `camera_photo_take` | 拍照 | 无 |
| `camera_recording_start` | 开始录像 | 无 |
| `camera_recording_stop` | 停止录像 | 无 |
| `camera_mode_switch` | 切换相机模式 | `cameraMode`：`0=拍照`、`1=录像`、`2=智能低光`、`3=全景` |
| `camera_focal_length_set` | 设置变焦倍率 | `cameraType=zoom|ir`；`cameraType=zoom` 时 `zoomFactor` 为 `2..200`，`cameraType=ir` 时为 `2..20` |
| `camera_aim` | 点选瞄准/云台指向画面位置 | `cameraType=wide|zoom|ir`、`locked`、`x`、`y`；`x/y` 是直播画面归一化坐标，范围 `0..1` |
| `gimbal_reset` | 云台复位 | `resetMode`：`0=回中`、`1=朝下`、`2=偏航回中`、`3=俯仰朝下` |

`camera_aim.locked=true` 表示锁定云台，云台和无人机一起转动；`false` 表示只转动云台。`x/y` 建议从播放器点击位置换算：`x = clickX / videoWidth`，`y = clickY / videoHeight`，并限制在 `0..1`。

常用请求示例：

```json
{
  "droneId": 1,
  "executorId": 2,
  "payloadIndex": "88-0-0",
  "action": "camera_photo_take"
}
```

```json
{
  "droneId": 1,
  "executorId": 2,
  "payloadIndex": "88-0-0",
  "action": "camera_mode_switch",
  "cameraMode": 1
}
```

```json
{
  "droneId": 1,
  "executorId": 2,
  "payloadIndex": "88-0-0",
  "action": "camera_recording_start"
}
```

```json
{
  "droneId": 1,
  "executorId": 2,
  "payloadIndex": "88-0-0",
  "action": "camera_recording_stop"
}
```

```json
{
  "droneId": 1,
  "executorId": 2,
  "payloadIndex": "88-0-0",
  "action": "camera_focal_length_set",
  "cameraType": "zoom",
  "zoomFactor": 12
}
```

```json
{
  "droneId": 1,
  "executorId": 2,
  "payloadIndex": "88-0-0",
  "action": "camera_aim",
  "cameraType": "zoom",
  "locked": false,
  "x": 0.5,
  "y": 0.4
}
```

```json
{
  "droneId": 1,
  "executorId": 2,
  "payloadIndex": "88-0-0",
  "action": "gimbal_reset",
  "resetMode": 0
}
```

成功响应示例：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {
    "operationId": 12,
    "status": "SUCCEEDED",
    "action": "camera_photo_take",
    "droneId": 1,
    "droneSn": "1581F7FVC252A00CJ5TT",
    "executorId": 2,
    "gatewaySn": "RC-GATEWAY-001",
    "payloadIndex": "88-0-0",
    "upstream": {
      "authority": {},
      "command": {}
    }
  }
}
```

响应处理：

- `data.status=SUCCEEDED` 表示后端两步 DJI 调用都已成功。
- `data.upstream.authority` 是抢占 payload 控制权的上游结果。
- `data.upstream.command` 是 payload command 的上游结果。
- `camera_photo_take` 和录像动作不会直接返回照片或视频文件；媒体文件继续通过 DJI 媒体同步、回调或飞行记录媒体刷新流程进入系统。

常见错误：

- `400`：字段缺失、`payloadIndex` 格式不是 `数字-数字-数字`，或 action 的额外字段不合法。
- `403`：当前账号没有控制权限。
- `404`：`droneId` 或 `executorId` 不存在，或对当前账号不可见。
- `409`：无人机/执行端不在线，或二者不属于同一个 DJI 连接。
- `502`：DJI 上游失败，例如设备离线或 payload command 被上游拒绝；前端优先展示 `msg`，并保留当前按钮/表单状态供用户重试。

## 共享与权限

前端菜单正式命名为“资源共享组”，不要使用“用户组管理”。资源共享组用于把本部门已绑定资源授权给其他部门。推荐顺序：

1. `POST /api/v2/resource/share-groups` 创建共享组。
2. `POST /api/v2/resource/share-groups/{id}/departments` 添加目标部门。
3. `POST /api/v2/resource/share-groups/{id}/resources` 添加资源和权限。

资源列表返回的 `effectivePermissions` 是当前账号对该资源的实际可用操作集合。前端按钮显示应以这个字段为准。

## 常见联调问题

- `discover` 能看到资源，但资源列表为空：资源还没有通过 `POST /api/v2/resource/bindings` 绑定。
- drone 和 gateway 的 `id` 都是 1：它们来自不同资源表，前端必须同时使用 `resourceType`。
- `mqtt-health` 有连接但 latest 为空：当前设备可能没有上报对应 topic，或筛选的 `deviceSn/topicKind` 不匹配。
- 航线创建失败：先检查 `kmzFile` 是否真实上传、`djiConnectionId` 是否可用、DJI 上游是否返回错误。
- 航线 URL 不能访问：`coverImageUrl` 应该是后端部署机 MinIO 的预签名 URL，例如当前本机默认 `http://192.168.3.99:9000/...`；`djiFile.downloadUrl` 应该是 DJI 上云返回的绝对下载链接。如果看到 `/media/...` 或 `/api/v1/wayline/...`，说明后端部署或历史数据刷新未完成。
- 任务启动失败：确认任务引用的航线已上传 DJI，资源和飞手都可用，没有运行中的占用。
- 看得到页面但接口 403：确认角色是否通过菜单/按钮保存带出了对应权限点，或在高级权限区补充了接口需要的权限码；保存后重新请求 `/api/v2/iam/me/context` 和 `/api/v2/system/menus/current`。
