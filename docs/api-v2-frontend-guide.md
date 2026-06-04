# API v2 前端接入指南

本文面向前端联调人员，说明 `/api/v2/*` 接口的推荐调用顺序、关键字段和常见误区。Swagger 入口是 `/api/v2/docs/`，schema 入口是 `/api/v2/docs/schema/`。

## 通用规则

- 登录后所有业务请求都带 `Authorization: Bearer <accessToken>`。
- HTTP 响应统一是 `{ "code": "...", "msg": "...", "data": ... }`。
- 成功时 `code` 固定为 `00000`；失败时优先展示 `msg`，再按页面场景决定是否保留表单输入。
- 列表接口通常返回 `{ "list": [], "total": 0 }`，前端不要假设所有列表都有分页。
- `resourceId` 不是全局唯一 ID。混合展示资源时用 `${resourceType}:${resourceId}` 作为 key。

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

`GET /api/v2/iam/roles` 返回全局角色目录。部门管理员只能给本部门及下级账号分配 `assignableByDepartmentAdmin=true` 且数据范围不是 `ALL` 的角色；`platform_super_admin` 是内置系统角色，不应作为普通业务角色分配。

初始化 v2 系统使用管理命令，不提供前端 setup API，也不内置固定种子账号：

```bash
python manage.py bootstrap_v2_system --reset --username <super_username> --password '<strong_password>' --noinput
```

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

## 航线与任务

推荐顺序：

1. 先完成 DJI 资源发现和绑定。
2. 通过 `GET/POST /api/v2/iam/accounts/{id}/profiles` 维护账号角色档案；飞手档案使用 `profileType=pilot`。
3. 通过 `/api/v2/iam/accounts/{id}/qualifications` 维护账号资质；飞手证书资质同样使用 `profileType=pilot`。
4. `POST /api/v2/inspection/routes` 创建航线并上传 KMZ。
5. 通过 `GET /api/v2/iam/accounts?roleCode=pilot&profileType=pilot&qualified=true` 选择候选飞手账号，并在 `POST /api/v2/inspection/missions` 中提交 `pilotAccountProfileId`。
6. `POST /api/v2/inspection/missions/{id}/start` 启动任务。
7. `GET /api/v2/inspection/active-flights` 和 `GET /api/v2/inspection/telemetry/snapshots` 展示飞行过程。
8. `POST /api/v2/inspection/missions/{id}/complete`、`cancel`、`fail` 或 `abort` 结束任务。

飞手不再是独立资源；它是账号拥有 `pilot` 角色、有效 `pilot` 档案和有效 `pilot` 资质后的业务能力。旧 `/api/v2/workforce/pilots` 和 `/api/v2/inspection/pilot-profiles` 不再作为 v2 接口使用。

创建航线使用 `multipart/form-data`，必须上传 `kmzFile`。更新航线时，如果不替换 KMZ，可以用 JSON 只更新名称、状态、备注、封面等基础信息；如果替换 KMZ，继续用 `multipart/form-data`。

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

相机动作统一使用：

```http
POST /api/v2/inspection/camera/actions
```

基础请求体：

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

第一版支持的动作：

- `camera_mode_switch`：额外传 `cameraMode`，取值 `0=拍照`、`1=录像`、`2=智能低光`、`3=全景`。
- `camera_photo_take`：拍照，只需要基础字段。
- `camera_recording_start`：开始录像，只需要基础字段。
- `camera_recording_stop`：停止录像，只需要基础字段。
- `camera_focal_length_set`：额外传 `cameraType` 和 `zoomFactor`；`cameraType=zoom` 时 `zoomFactor` 为 `2..200`，`cameraType=ir` 时为 `2..20`。
- `camera_aim`：额外传 `cameraType`、`locked`、`x`、`y`；`cameraType` 支持 `wide/zoom/ir`，`x/y` 为 `0..1`。
- `gimbal_reset`：额外传 `resetMode`，取值 `0=回中`、`1=朝下`、`2=偏航回中`、`3=俯仰朝下`。

接口会先抢 DJI payload authority，再发送 payload command，并保存本次后端操作记录。拍照和录像生成的媒体文件不从这个接口返回，继续通过 DJI 媒体同步、回调或飞行记录媒体刷新流程进入系统。

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
