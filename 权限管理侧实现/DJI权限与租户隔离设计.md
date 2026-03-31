# DJI 权限与租户隔离设计

## 1. 文档目的

1. 本文档描述如何把 DJI 后端服务引入当前 Django 系统。
2. 本文档描述引入后的权限边界、租户隔离方式和代码重构落点。
3. 本文档面向 AI 与新接手成员，目标是指导重构，不是解释概念背景。

## 2. 当前系统起点

1. 当前 HTTP 入口挂在 `/api/v1/`，根路由位于 `config/urls.py`。
2. 当前业务 API 总入口位于 `apps/api_v1/urls.py`。
3. 当前 IAM 接口位于 `apps/access/api_v1/urls.py`。
4. 当前业务模块以 `drone`、`route`、`mission`、`media_file`、`drone_assignment` 为主。
5. 当前业务接口统一复用 `BusinessApiResponseMixin`、`TenantScopedBusinessMixin`、`PermissionMapMixin`、`ScopedQuerysetMixin` 和 `ScopedActionPermission`。
6. 当前业务接口已经具备 Bearer 会话鉴权、`X-TENANT-CODE` 租户上下文、scope 过滤和统一响应码。
7. 当前系统已经把 `platform_admin` 排除在租户业务 API 之外。

## 3. 引入 DJI 服务后的总原则

1. DJI 服务通过内部代理层接入，不直接暴露给前端。
2. 前端继续只调用我方 `/api/v1/*` 与 `/api/v1/iam/*`。
3. `tenant` 继续是本系统唯一正式隔离边界。
4. 当前接入按单 DJI `workspace`、单系统托管 DJI `user`、单上游资源池落地。
5. `workspace_id` 和 `dji_user_id` 都是 `DjiGateway` 内部托管的上游执行上下文。
6. 本地 `tenant_member` 不与 DJI `user` 建立一一映射关系。
7. 本地权限判断继续基于 `TenantMemberRole -> RolePermissionGrant -> Permission`。
8. 租户可见性继续由本地 `tenant_id`、本地索引表和本地业务模型共同决定。
9. 浏览器不持有 DJI `x-auth-token`、`mqtt_*`、STS 凭证和上游账号口令。
10. DJI 登录、token 刷新、当前用户探测、工作空间用户探测、共享池同步、回调处理和索引刷新由系统身份执行。
11. 业务模块调用 DJI 时必须先完成本地 tenant 校验和权限校验。
12. 当前阶段不新增对外 `integrations/dji/*` 风格接口。

## 4. 接入目标

1. 当前系统保留业务 API 外壳。
2. 当前系统在服务端新增 DJI 内部代理层。
3. 当前系统把 DJI 设备、航线、任务和媒体能力映射到现有业务模块。
4. 当前系统把 DJI 上游资源转换为 tenant 可见资源。
5. 当前系统把 DJI 上游 `workspace`、`user`、`token` 收敛为服务端内部托管状态。
6. 当前系统把 DJI 交互失败收敛到我方统一响应结构与审计体系。

## 5. 推荐接入结构

### 5.1 新增内部模块

1. 新增内部模块 `apps/dji_bff/`。
2. `apps/dji_bff/gateway.py` 负责登录、token 刷新、当前用户探测、工作空间用户探测、`workspace_id` 托管、`dji_user_id` 托管、HTTP 调用和重试。
3. `apps/dji_bff/models.py` 负责共享索引与映射索引表。
4. `apps/dji_bff/tasks.py` 负责设备、任务、媒体等后台同步任务。
5. `apps/dji_bff/services.py` 负责 DJI 回调后的最小业务收敛。
6. `apps/dji_bff/views.py` 负责内部同步入口与回调入口。
7. `apps/dji_bff` 是内部模块，不承担新的前台业务概念。

### 5.2 建议的数据模型

| 模型 | 作用 | tenant 属性 |
|------|------|-------------|
| `DjiWorkspaceConfig(workspace_id, dji_user_id, dji_username, dji_user_type, access_token, mqtt_username, mqtt_password, mqtt_addr, expires_at)` | 托管当前上游 workspace 与系统执行 user 会话 | 无 |
| `DjiDeviceIndex(device_sn, last_payload, last_seen_at)` | 保存 DJI 共享设备快照 | 无 |
| `TenantRouteIndex(tenant_id, route_id, dji_wayline_id, is_published)` | 保存本地 route 草稿与 DJI 已发布航线的映射 | 有 |
| `TenantMissionIndex(tenant_id, mission_id, dji_job_id, execution_status, sync_status, last_sync_at, error_msg)` | 保存本地任务与 DJI 任务映射 | 有 |
| `TenantMediaIndex(tenant_id, media_file_id, dji_file_id, device_sn, mission_id, sync_status, last_sync_at, error_msg)` | 保存本地媒体与 DJI 媒体映射 | 有 |

### 5.3 推荐调用链路

1. 前端请求先进入现有 `/api/v1/*` 业务接口。
2. 业务接口先执行 Bearer 鉴权、tenant 解析、permission 命中和 scope 过滤。
3. 业务接口在本地鉴权通过后调用 `apps/dji_bff/services/*`。
4. `apps/dji_bff/services/*` 通过 `DjiGateway` 调用 DJI 后端服务。
5. `DjiGateway` 使用 `DjiWorkspaceConfig` 托管的 `workspace_id` 与 DJI `user` 执行上游调用。
6. 上游返回结果经过本地映射、审计和统一响应封装后再返回前端。

### 5.4 上游身份模型

1. DJI backend 的登录结果同时定义了 `workspace_id` 与 `user_id`。
2. `workspace_id` 表示上游资源作用域。
3. `user_id`、`username`、`user_type` 表示上游执行账号。
4. 系统身份是我方服务端运行时身份。
5. 系统身份在上游实际使用 `DjiWorkspaceConfig` 托管的 DJI `user`。
6. 本地 `tenant_member` 只表示“谁在我方系统发起业务动作”。
7. 上游 DJI `user` 只表示“我方系统以哪个上游账号执行动作”。
8. `user_type` 保持 DJI 上游原生语义，不进入本地正式权限模型。

## 6. 现有代码中的重构落点

### 6.1 全局入口与基础设施

| 文件 | 作用 | 重构动作 |
|------|------|----------|
| `config/urls.py` | 全局 URL 入口 | 保持 `/api/v1/` 不变 |
| `apps/api_v1/urls.py` | 业务 API 总路由 | 当前已挂载 `apps.dji_bff.urls` 内部入口与业务路由 |
| `apps/api_v1/business_response.py` | 标准响应码与统一响应结构 | DJI 相关返回继续走 `code/msg/data` |
| `apps/access/middleware.py` | `X-TENANT-CODE` 解析与 tenant 上下文注入 | DJI 业务请求继续依赖该租户上下文 |
| `apps/api_v1/tenant_scope.py` | tenant 业务入口约束与 `platform_admin` 禁入规则 | DJI 业务请求继续复用 |
| `apps/access/drf_permissions.py` | permission 与 scope 检查 | DJI 业务动作继续复用 |
| `apps/access/services.py` | 账号状态、权限判定、scope 应用、审计辅助 | DJI 业务动作继续复用 |

### 6.2 业务模块入口

| 文件 | 当前角色 | DJI 接入后的角色 |
|------|----------|------------------|
| `apps/drone/urls.py` | `/api/v1/drones` 路由入口 | 保持设备业务入口 |
| `apps/drone/views.py` | 设备读写、状态 action、权限映射 | 改造成设备认领、共享池查询和直播代理入口 |
| `apps/route/urls.py` | `/api/v1/routes` 路由入口 | 保持航线业务入口 |
| `apps/route/views.py` | 航线 CRUD 与权限映射 | 改造成 DJI 航线上传、下载、删除的业务入口 |
| `apps/mission/urls.py` | `/api/v1/missions` 路由入口 | 保持任务业务入口 |
| `apps/mission/views.py` | 任务 CRUD、状态 action、权限映射 | 改造成 DJI 任务创建、取消、状态同步入口 |
| `apps/media_file/urls.py` | `/api/v1/media-files` 路由入口 | 保持媒体业务入口 |
| `apps/media_file/views.py` | 媒体读写与权限映射 | 改造成媒体索引查询和下载入口 |
| `apps/drone_assignment/urls.py` | `/api/v1/drone-assignments` 路由入口 | 保持本地分配业务入口 |
| `apps/drone_assignment/views.py` | 设备分配关系管理 | 增加“仅允许已认领设备参与分配”的校验 |

## 7. 业务模块改造规则

### 7.1 Drone 模块

1. `Drone` 从纯本地台账调整为“tenant 已认领设备”。
2. 当前代码已经统一使用 `Drone.device_sn`，不再保留 `serial_no` 业务字段。
3. `POST /api/v1/drones` 的创建语义调整为认领语义。
4. 设备共享池由 `DjiDeviceIndex` 提供。
5. 新增 `GET /api/v1/drones/available` 作为共享池查询入口。
6. `device_sn` 是 DJI 设备稳定标识。
7. 同一 `device_sn` 在任一时刻只归属于一个 tenant。
8. 设备认领只写本地 `Drone` 表，不把本地 tenant 绑定关系写成前端概念外溢。
9. `GET /api/v1/drones/{id}/live/capacity` 先调用 DJI `GET /api/v1/manage/live/capacity`，再按当前 Drone 的 `device_sn` 过滤，只返回当前 `{id}` 对应设备的能力对象。
10. `POST /api/v1/drones/{id}/live/start`、`stop`、`video-quality`、`video-source` 分别调用 DJI `manage/live/streams/start`、`stop`、`update`、`switch`。
11. `video_id` 只保留实测格式：`{drone_sn}/{camera.index}/{video.index}`；`live/start` 由后端基于 capacity 结果中的 `camera.index` 和 `video.index` 组装。
12. `POST /api/v1/drones/{id}/live/video-source` 直接接收 `video_id` 和 `videoType`。
13. 直播 `data` 原样透传 DJI `LiveDTO` 或上游原始成功响应，不做字段裁剪和重命名。
14. `GET /api/v1/drones/{id}/live/capacity` 使用 `drone.view_drone` 权限。
15. `POST /api/v1/drones/{id}/live/start|stop|video-quality|video-source` 使用 `drone.manage_drone` 权限。
16. 当前实现已经删除 `enable`、`disable`、`maintenance`、`retire` 四个本地状态 action。
17. `Drone.status` 枚举只保留 `ENABLED` / `DISABLED`，不再保留 `MAINTENANCE` / `RETIRED` 两个旧台账状态。
18. `Drone.status` 当前只表达设备在线摘要：同步命中上游设备时记为 `ENABLED`，本轮未命中时记为 `DISABLED`。
19. 删除 `MAINTENANCE` / `RETIRED` 后，相关代码、校验和测试一起删除，不保留兼容逻辑。

### 7.2 Route 模块

1. `Route` 是公开业务聚合根；当前唯一编辑输入是 `xml_file`，`waypoints` 仅保留为历史内部表。
2. `TenantRouteIndex` 负责记录 `route_id -> dji_wayline_id` 以及 `is_published`。
3. `POST /api/v1/routes` 当前只创建本地 route 草稿，不立即上传 DJI。
4. `PUT /api/v1/routes/{id}` 用完整 XML 替换本地草稿，并把 `is_published` 置回 `false`。
5. `GET /api/v1/routes/{id}/xml` 返回当前草稿的原始 XML 文件流。
6. `POST /api/v1/routes/{id}/publish` 才会调用 DJI wayline 上传链路。
7. 当前没有公开 `GET /api/v1/routes/{id}/download` 接口。
8. `DELETE /api/v1/routes/{id}` 在本地校验后调用 DJI 删除接口，并清理本地映射、XML 文件与残留 waypoint 行。
9. 航线读写权限继续复用 `route.view_route` 与 `route.manage_route`。

### 7.3 Mission 模块

1. `Mission` 继续作为本地任务业务实体。
2. `TenantMissionIndex` 负责记录 `mission_id -> dji_job_id` 映射。
3. `POST /api/v1/missions` 在本地创建任务后调用 DJI `flight-tasks` 接口。
4. 本地任务需要保存 `dji_job_id` 供取消任务使用。
5. `POST /api/v1/missions/{id}/cancel` 使用当前 tenant 对应的 `dji_job_id` 调用 DJI `jobs` 删除接口。
6. 任务状态由 DJI `jobs` 列表同步回本地，不再依赖本地手工状态推进。
7. 任务读写权限继续复用 `mission.view_mission` 与 `mission.manage_mission`。

### 7.4 MediaFile 模块

1. `MediaFile` 调整为以同步结果为主的读模型。
2. `TenantMediaIndex` 负责记录 `media_id -> dji_file_id` 映射与 tenant 归属。
3. 媒体列表通过本地索引表提供 tenant 可见结果。
4. 媒体下载通过 `dji_file_id` 获取上游下载地址。
5. 媒体上传回调、秒传校验、文件组上传回调和 STS 处理属于系统身份流程。
6. 当前租户侧主要暴露媒体读取和下载能力。
7. 媒体读权限继续复用 `media_file.view_media_file`。

### 7.5 DroneAssignment 模块

1. `drone_assignment` 模块继续保持本地业务属性。
2. `drone_assignment` 不直接调用 DJI 后端服务。
3. `drone_assignment` 只接受当前 tenant 已认领的 `Drone` 记录。
4. `drone_assignment` 继续复用 `drone_assignment.manage_drone_assignment`。

## 8. DJI 上游概念、接口与本地映射

### 8.1 上游概念与本地定位

| 上游概念 | 本地定位 | 说明 |
|----------|----------|------|
| DJI `workspace` | 上游资源作用域 | 不等于本地 tenant |
| DJI `user` | 上游执行身份 | 不等于本地 tenant_member |
| system identity | 本地服务端运行时身份 | 负责托管并使用 DJI `user` |
| `tenant_member` | 本地业务动作发起者 | 先完成本地鉴权，再触发上游代理调用 |

### 8.2 DJI 接口与本地模块映射

| DJI 接口类别 | 主要接口 | 本地使用模块 | 本地发起身份 | 上游执行身份 |
|-------------|----------|--------------|--------------|--------------|
| 上游认证与身份探测 | `POST /api/v1/manage/login`、`POST /api/v1/manage/token/refresh`、`GET /api/v1/manage/users/current`、`GET /api/v1/manage/workspaces/{workspace_id}/users` | `apps/dji_bff/gateway.py` | system | system-owned DJI user |
| 设备列表/设备详情 | `GET /api/v1/manage/workspaces/{workspace_id}/devices`、`GET /api/v1/manage/workspaces/{workspace_id}/devices/{device_sn}` | `dji_bff` 同步任务、`drone` 模块 | system / tenant_member | system-owned DJI user |
| 设备绑定能力 | `POST /api/v1/manage/devices/{device_sn}/binding`、`DELETE /api/v1/manage/devices/{device_sn}/unbinding` | `dji_bff` 内部管理 | system | system-owned DJI user |
| 直播能力 | `GET /api/v1/manage/live/capacity`、`POST /api/v1/manage/live/streams/start`、`POST /api/v1/manage/live/streams/stop`、`POST /api/v1/manage/live/streams/update`、`POST /api/v1/manage/live/streams/switch` | `drone` 模块 | tenant_member | system-owned DJI user |
| 航线 | `GET /api/v1/wayline/workspaces/{workspace_id}/waylines`、`GET /api/v1/wayline/workspaces/{workspace_id}/waylines/{wayline_id}/url`、`GET /api/v1/wayline/workspaces/{workspace_id}/waylines/duplicate-names`、`POST /api/v1/wayline/workspaces/{workspace_id}/waylines/files/upload`、`DELETE /api/v1/wayline/workspaces/{workspace_id}/waylines/{wayline_id}` | `route` 模块 | tenant_member / system | system-owned DJI user |
| 任务 | `POST /api/v1/wayline/workspaces/{workspace_id}/flight-tasks`、`GET /api/v1/wayline/workspaces/{workspace_id}/jobs`、`DELETE /api/v1/wayline/workspaces/{workspace_id}/jobs`、`PUT /api/v1/wayline/workspaces/{workspace_id}/jobs/{job_id}` | `mission` 模块 | tenant_member / system | system-owned DJI user |
| 媒体 | `GET /api/v1/media/workspaces/{workspace_id}/files`、`GET /api/v1/media/workspaces/{workspace_id}/files/{file_id}/url`、`POST /api/v1/media/workspaces/{workspace_id}/fast-upload`、`POST /api/v1/media/workspaces/{workspace_id}/upload-callback`、`POST /api/v1/media/workspaces/{workspace_id}/group-upload-callback`、`POST /api/v1/storage/workspaces/{workspace_id}/sts` | `media_file` 模块、`dji_bff` 回调与同步 | tenant_member / system | system-owned DJI user |

## 9. 权限与租户隔离规则

1. DJI 接入不新增 `dji_integration.*` 权限模块。
2. 设备共享池查询和设备详情复用 `drone.view_drone`。
3. 设备认领和直播写操作复用 `drone.manage_drone`。
4. 航线读写继续复用 `route.view_route` 与 `route.manage_route`。
5. 任务读写继续复用 `mission.view_mission` 与 `mission.manage_mission`。
6. 媒体读继续复用 `media_file.view_media_file`。
7. 审计日志查询继续复用 `access.view_auth_audit_logs`。
8. `platform_admin` 不访问租户业务 DJI 能力。
9. 本地授权主键始终是 `tenant_id`，不是 `workspace_id` 或 `dji_user_id`。
10. `device_sn` 只用于定位上游资源，不单独承担授权语义。
11. 本地 `tenant_member` 不直接对应某个 DJI `user`。
12. 所有租户业务动作都由系统托管的 DJI `user` 代理执行。
13. `user_type` 保持 DJI 上游原生分类，不映射为本地角色或本地 scope。
14. `GET /manage/users/current` 与 `GET /workspaces/{workspace_id}/users` 只用于上游身份确认和运行诊断，不作为本地权限来源。
15. 不带 `workspace_id` 的 DJI 接口必须先通过本地资源反查 tenant 归属。
16. 回调写入业务前必须先建立 `device_sn`、`dji_job_id` 或 `dji_file_id` 到 tenant 的映射关系。

## 10. 回调与同步规则

1. 航线上传回调由系统身份处理。
2. 媒体上传回调和分组上传回调由系统身份处理。
3. 共享设备池同步由系统身份定时执行。
4. 任务状态同步由系统身份定时执行。
5. 媒体索引同步由系统身份定时执行。
6. 回调与同步结果先落本地索引表，再影响 tenant 可见结果。
7. 回调与同步操作写系统审计日志。
8. 回调与同步流程不依赖本地 `tenant_member -> DJI user` 映射。

### 10.0 当前内部入口

1. 当前已落地的系统内部同步入口为：
   - `POST /api/v1/__internal__/dji/sync/devices`
   - `POST /api/v1/__internal__/dji/sync/missions`
   - `POST /api/v1/__internal__/dji/sync/media`
2. 当前已落地的系统内部回调入口为：
   - `POST /api/v1/__internal__/dji/callbacks/wayline-upload`
   - `POST /api/v1/__internal__/dji/callbacks/media-upload`
   - `POST /api/v1/__internal__/dji/callbacks/media-group-upload`
3. 这些入口都要求请求头 `X-DJI-Internal-Token` 命中 `settings.DJI_INTERNAL_API_TOKEN`。

### 10.0.1 这些内部接口与 `run_dji_sync_scheduler` 的关系

1. `run_dji_sync_scheduler` 是当前系统运行闭环里的主调度入口。
2. 该 management command 不会通过 HTTP 再去调用 `/api/v1/__internal__/dji/sync/*`。
3. 当前实现里，management command 和 `sync/*` 内部接口是两层不同入口，但共用同一组底层同步函数：
   - `sync_device_indexes()`
   - `sync_mission_indexes()`
   - `sync_media_indexes()`
4. 也就是说：
   - `python manage.py run_dji_sync_scheduler` 是 CLI 调度入口
   - `POST /api/v1/__internal__/dji/sync/*` 是 HTTP 触发入口
   - 两者最终执行的是同一套同步逻辑，写同样的表、字段和审计日志
5. 因此，`sync/*` 不是 management command 的“前置步骤”或“下游回调”；它只是同一套同步能力的另一种内部触发方式。
6. 当前推荐关系是：
   - 正常部署和持续运行：优先用 `run_dji_sync_scheduler`
   - 人工排障、受控触发、系统内网编排、接口联调：可用 `POST /api/v1/__internal__/dji/sync/*`
7. `callbacks/*` 又是另一类入口，它们不负责主动拉取同步，也不会被 management command 主动调用。
8. `callbacks/*` 的职责是接收上游或内部转发来的异步事件，然后把事件结果收敛回本地索引：
   - `wayline-upload`：补充确认 route 发布结果
   - `media-upload`：补充确认单文件媒体上传结果
   - `media-group-upload`：记录文件组上传回调摘要
9. 所以当前关系可以概括为：
   - management command = 主调度器
   - `sync/*` = 同步任务的 HTTP 包装入口
   - `callbacks/*` = 异步事件接收入口

### 10.1 同步调度入口（Django management command）

1. 当前最小闭环方案不引入 Celery、beat 或额外调度基础设施。
2. 后台同步通过 Django management command 启动，命令名为 `run_dji_sync_scheduler`。
3. Django management command 的含义是：由 Django 提供的命令行管理入口，用于执行迁移、启动服务、导数和后台任务等系统级操作。
4. 运行方式统一是 `python manage.py <command>`；它是 CLI 命令，不是 HTTP API，不通过浏览器、Swagger 或前端调用。
5. 当前命令负责按系统身份循环执行设备索引同步、任务索引同步和媒体索引同步。
6. 常见用法：
   - `python manage.py run_dji_sync_scheduler --once`
   - `python manage.py run_dji_sync_scheduler --interval-seconds 60`
   - `python manage.py run_dji_sync_scheduler --interval-seconds 0 --max-cycles 2`
7. 参数语义：
   - `--once`：只执行一轮，适合人工触发、排障或验证配置
   - `--interval-seconds`：两轮之间的等待秒数
   - `--max-cycles`：最多执行多少轮，适合测试和受控运行
8. 部署时可由 systemd、supervisor、容器入口脚本或 k8s job/sidecar 拉起该命令；当前阶段不为此额外引入新框架。

### 10.2 `run_dji_sync_scheduler` 副作用清单

1. 该命令不是只读诊断命令；每一轮同步都会真实写本地数据库。
2. 当前一轮同步顺序固定为：设备索引同步 -> 任务索引同步 -> 媒体索引同步。
3. 当前实现不会自动清理本地旧记录；策略是新增、更新、标记 `ERROR` 或忽略。

| 同步环节 | 写入表 | 操作 | 明确写入字段 |
|------|------|------|------|
| 设备索引同步 | `dji_device_indexes` | `update_or_create`，按 `device_sn` 幂等更新 | `device_sn`（首次创建）、`last_payload`、`last_seen_at`、`firmware_version`、`firmware_status`、`created_at`（首次创建）、`updated_at` |
| 设备索引同步 | `drones` | 批量更新已认领设备状态 | `status`、`updated_at`；规则是“本轮在 DJI 共享池中出现的已认领设备置为 `ENABLED`，未出现的已认领设备置为 `DISABLED`” |
| 任务索引同步 | `tenant_mission_indexes` | 逐条更新现有索引；不新建索引 | 正常命中上游任务时写 `execution_status`、`sync_status=SYNCED`、`error_msg=\"\"`、`last_sync_at`、`updated_at`；上游任务缺失时写 `sync_status=ERROR`、`error_msg=\"上游任务不存在\"`、`last_sync_at`、`updated_at` |
| 任务索引同步 | `missions` | 按 DJI 状态映射更新本地任务状态 | `status`、`updated_at` |
| 媒体索引同步 | `media_files` | 若本地不存在则创建，存在则更新 | `tenant_id`、`flight_record_id`、`media_type`、`file_name`、`file_url`、`thumbnail_url`、`file_size`、`latitude`、`longitude`、`captured_at`、`is_deleted=false`、`deleted_at=null`、`created_at`（首次创建） |
| 媒体索引同步 | `tenant_media_indexes` | 若本地不存在则创建，存在则更新 | `tenant_id`、`media_file_id`、`dji_file_id`、`device_sn`、`mission_id`、`sync_status=SYNCED`、`last_sync_at`、`error_msg=\"\"`、`created_at`（首次创建）、`updated_at` |
| 每类同步结束 | `auth_audit_logs` | 新增审计日志 | `tenant=null`、`actor_user=null`、`action`、`target_type`、`target_id=\"\"`、`after_data`、`created_at` |

补充规则：
1. `media_files` 当前没有 `updated_at` 字段，因此媒体记录更新时不会额外记录更新时间列。
2. `auth_audit_logs` 每轮会至少新增三条系统审计日志，分别对应 `DJI_DEVICE_SYNC`、`DJI_MISSION_SYNC`、`DJI_MEDIA_SYNC`。
3. 媒体同步会读取 `flight_records` 来尝试回填 `flight_record_id`，但不会修改 `flight_records` 本身。
4. 当前命令会读取 `dji_workspace_configs` 中的 `workspace_id`、`access_token` 等配置，但不会回写该表。
5. 当前命令不会修改 `routes`、`tenant_route_indexes`、`drone_assignments`、`waypoints`。

## 11. 响应与错误处理规则

1. 我方继续使用 `code / msg / data` 统一响应结构。
2. 我方标准成功码保持 `00000`。
3. 我方标准认证失败码保持 `A0401`。
4. 我方标准鉴权失败码保持 `A0403`。
5. 我方标准参数错误码保持 `B0001`。
6. 我方标准资源不存在码保持 `C0404`。
7. 我方标准内部或依赖异常码保持 `E0001`。
8. DJI 原始业务码只在服务端处理、记录和审计。
9. 上游失败不会要求前端直接登录 DJI。

## 12. 推荐重构顺序

1. 先新增 `apps/dji_bff/`，实现 `DjiGateway` 与 `DjiWorkspaceConfig`。
2. 再补齐 `DjiWorkspaceConfig` 的 `dji_user_id`、`dji_username`、`dji_user_type`、`access_token`、`mqtt_*` 托管字段。
3. 再新增 `DjiDeviceIndex`、`TenantRouteIndex`、`TenantMissionIndex`、`TenantMediaIndex` 及其迁移。
4. 再实现设备、任务、媒体的后台同步任务。
5. 再实现航线回调、媒体回调等系统入口。
6. 再重构 `drone` 模块，把创建语义调整为认领语义，并接入直播代理。
7. 再重构 `route` 模块，把本地 CRUD 接到 DJI 航线能力上。
8. 再重构 `mission` 模块，把任务创建和取消接到 DJI job 能力上。
9. 再重构 `media_file` 模块，把媒体改成索引驱动的读模型。
10. 再补充 `drone_assignment` 的“已认领设备”校验。
11. 最后统一补测试、OpenAPI 和审计验证。

## 13. 重构验收标准

1. 前端不需要知道 DJI 登录方式、`workspace_id`、`dji_user_id` 和 `x-auth-token`。
2. 业务接口路径继续维持在 `/api/v1/*` 之下。
3. `tenant` 继续是本地唯一正式隔离边界。
4. `platform_admin` 继续无法进入租户业务 DJI 接口。
5. 每个 DJI 业务动作都能映射到现有权限码。
6. 每个 tenant 只能看到自己认领或映射出的设备、航线、任务和媒体。
7. 每个上游回调都能在本地解析到 tenant 或被安全忽略。
8. 业务响应继续符合当前 `code/msg/data` 合同。
9. 文档和实现都能明确指出当前由哪个 DJI `user` 执行上游调用。
10. 审计日志继续覆盖认领、直播、航线写操作、任务写操作和系统同步动作。
