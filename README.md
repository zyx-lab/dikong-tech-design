# 低空平台权限系统（V3）

## 当前状态（对齐日期：2026-04-03）

本仓库当前是一个 Django + DRF 的正式 API 项目，现状已经收敛为五类 HTTP 入口：

1. Formal IAM Plane（正式权限与身份接口）

- 前缀：`/api/v1/iam/*`
- 文档：`/api/v1/docs/`
- 能力分层：
  - `session/*`：登录、刷新令牌、登出、自助注册
  - `me/*`：当前登录业务账号读取自己的全局资料与可进入租户列表
  - `tenant/*`：当前租户上下文内的成员、角色目录、租户审计
  - `platform/*`：平台租户治理、平台角色目录、平台权限目录、平台审计

2. Business API Plane（业务接口）

- 前缀：`/api/v1/*`
- 文档：`/api/v1/docs/`
- 已实现业务域：
  - 无人机台账（drone）：认领共享设备、读取/编辑本地管理字段、直播控制
  - 无人机分配（drone_assignment）：查询、创建、取消
  - 航线（route）：KMZ 直传与 DJI 航线绑定；`POST /api/v1/routes` 通过 `name + kmz_file` 创建并立即上传 DJI，`PUT /api/v1/routes/{id}` 支持部分更新（仅名称、本地 no-op、或替换 KMZ），`GET /api/v1/routes/{id}/kmz` 代理下载当前 KMZ；`waypoints` 表只保留历史/内部语义
  - 任务（mission）：查询、创建、更新、软删除、推进执行状态；本地维护执行时间窗
  - 飞行记录（flight_record）：只读查询、摘要更新、软删除；记录由任务完成链路自动生成
  - 媒体文件（media_file）：只读查询 + 下载 / 回放 / 回放地址 / 绑定任务；数据由 DJI 同步沉淀，并按 `device_sn + captured_at` 自动回填 mission

3. Internal DJI Bridge（系统内部 DJI 桥接）

- 前缀：`/api/v1/__internal__/dji/*`
- 用途：
  - 受控触发设备 / 媒体同步
  - 接收 DJI 侧上传回调
- 认证：
  - 不走 Bearer Token
  - 通过 `X-DJI-Internal-Token` 做系统内部鉴权

4. Mock DJI Upstream（测试用模拟上游）

- 前缀：`/__mock-dji__/api/v1/*`
- 用途：
  - live tests
  - 本地联调
  - 替代真实 DJI 上游做受控验证

5. Unified OpenAPI Docs（统一文档）

- Swagger UI：`/api/v1/docs/`
- OpenAPI Schema(JSON)：`/api/v1/docs/schema/`
- 用途：统一查看当前仓库正式 IAM + Business API；`/api/v1/docs/schema/` 可直接导入 Apifox

说明：

- 旧 `/internal/auth/*` 已下线，不保留兼容入口。
- 正式 IAM 认证已切换为 Bearer Token，不再使用 Django Session / Basic 作为正式 API 认证方式。
- 邀请流、`me/permissions`、`set-plan`、全局用户目录等旧能力不再属于正式 API。
- `Waypoint` 已降级为 `Route` 聚合内部存储结构，不再公开 `/api/v1/waypoints*`。

## API v2 当前开发入口

现阶段业务开发和联调以 API v2 为准，默认不要从 v1 路径开始业务流：

- Swagger UI：`/api/v2/docs/`
- OpenAPI Schema(JSON)：`/api/v2/docs/schema/`
- 登录：`POST /api/v2/iam/session/login`
- 刷新令牌：`POST /api/v2/iam/session/refresh`
- 登出：`POST /api/v2/iam/session/logout`
- 当前权限上下文：`GET /api/v2/iam/me/context`
- 当前可见菜单：`GET /api/v2/system/menus/current`
- 当前账号资料：`GET /api/v2/iam/me/profile`
- 业务分组：`/api/v2/iam/*`、`/api/v2/system/*`、`/api/v2/resource/*`、`/api/v2/inspection/*`

`/api/v1/*` 仍作为 legacy 兼容入口保留，但不作为当前 v2 业务开发和联调入口。DJI 上游协议里的 `/api/v1/manage/*`、`/api/v1/wayline/*`、`/api/v1/media/*` 是 DJI 云平台自身的协议路径，不是本系统对外业务 API。

v2 和 v1 是两套平行体系：v2 账号资料使用 `V2AccountProfile`，不使用 v1 的 `StaffProfile`；v2 不提供公开注册入口，账号由平台超管或部门管理员通过 `/api/v2/iam/accounts` 创建维护。账号角色档案和账号资质归属 IAM 域，通过 `/api/v2/iam/accounts/{id}/profiles` 和 `/api/v2/iam/accounts/{id}/qualifications` 维护；巡检任务选择飞手时提交具备 `pilot` 档案和有效 `pilot` 资质的 `pilotAccountProfileId`。

### API v2 联调账号

当前 v2 联调只使用以下 5 个账号，统一密码为 `FrontTest@123`：

| 账号 | 姓名 | 手机号 | 邮箱 | 角色 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `v2_test_platform_super_admin` | v2 平台超级管理员 | `13800010001` | `v2_test_platform_super_admin@example.test` | `platform_super_admin` | 平台调试/管理身份，不属于部门业务角色 |
| `v2_test_department_admin` | v2 部门管理员 | `13800010002` | `v2_test_department_admin@example.test` | `department_admin` | 部门管理员 |
| `v2_test_task_monitor_dispatcher` | v2 任务派发监控员 | `13800010003` | `v2_test_task_monitor_dispatcher@example.test` | `task_monitor_dispatcher` | 任务派发和监控人员 |
| `v2_test_pilot` | v2 飞手 | `13800010004` | `v2_test_pilot@example.test` | `pilot` | 飞手 |
| `v2_test_work_order_handler` | v2 工单处理者 | `13800010005` | `v2_test_work_order_handler@example.test` | `work_order_handler` | 工单处理者 |

`GET /api/v2/iam/roles` 返回全局角色目录，包含 `platform_super_admin` 等内置系统角色。前端业务角色分配时应优先使用接口返回的 `assignableByDepartmentAdmin`、`dataScope`、`isSuperAdmin` 等字段过滤可分配角色；部门管理员只能分配 `assignableByDepartmentAdmin=true` 且数据范围不是 `ALL` 的角色。

## 快速启动

项目运行时默认连接 PostgreSQL：`127.0.0.1:5432`、数据库 `dikong`、用户/密码 `postgres/postgres`。本地启动前先确认 PostgreSQL 可连接，或通过 `DB_HOST`、`DB_PORT`、`DB_NAME`、`DB_USER`、`DB_PASSWORD` 覆盖。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py bootstrap_v2_system --reset --username v2_root --password 'YourStrongPassword' --noinput
python manage.py runserver 0.0.0.0:8001
```

`bootstrap_v2_system` 会初始化 v2 根部门、默认角色、权限点、菜单和指定的第一个平台超管账号；不提供前端 setup API，也不内置固定种子账号。仅调试 legacy v1 入口时再单独执行 v1 的初始化命令。

### 航线封面对象存储

API v2 航线封面通过 Django default storage 保存。部署到 S3/MinIO 时使用以下环境变量：

```bash
export OBJECT_STORAGE_BACKEND=minio
export OBJECT_STORAGE_ACCESS_KEY_ID=...
export OBJECT_STORAGE_SECRET_ACCESS_KEY=...
export OBJECT_STORAGE_BUCKET_NAME=dikong-route-covers
export OBJECT_STORAGE_ENDPOINT_URL=https://minio.example.com
export AWS_S3_ADDRESSING_STYLE=path
export AWS_QUERYSTRING_AUTH=true
export OBJECT_STORAGE_URL_EXPIRE_SECONDS=3600
```

`OBJECT_STORAGE_PUBLIC_DOMAIN=assets.example.com` 只用于公有 bucket 或 CDN 风格域名，值必须是不带 scheme 和 path 的 host-only 形式。私有 MinIO 需要预签名 URL 时不要设置 `OBJECT_STORAGE_PUBLIC_DOMAIN`，并确保 `OBJECT_STORAGE_ENDPOINT_URL` 是前端浏览器可访问的地址。

本仓库的 Docker Compose 本地部署已包含配套 MinIO，默认 bucket 为 `dikong-route-covers`，当前本机默认对外 endpoint 为 `http://192.168.3.99:9000`；如部署机器 IP 不同，用 `OBJECT_STORAGE_ENDPOINT_URL` 覆盖。API v2 保存航线后：

- `coverImageUrl` 返回 MinIO 私有预签名 URL，会过期，前端长期展示前重新读取航线详情即可刷新。
- `djiFile.downloadUrl` 返回 DJI 上云侧 KMZ 绝对下载链接，KMZ 不会复制到本系统 MinIO。

如果从旧的 filesystem 部署切换到 MinIO，执行以下命令修复历史数据：

```bash
python manage.py migrate_route_covers_to_object_storage
python manage.py refresh_route_kmz_download_urls
```

临时需要 SQLite 时显式设置 `DB_ENGINE=sqlite`。Django 测试命令和仓库内 v2 回归脚本会固定使用 SQLite，不依赖本地 PostgreSQL。

## API v2 本地回归测试

开发 API v2 时优先使用仓库内置的 v2 回归脚本，避免默认跑完整测试套件：

```bash
scripts/test_v2_regression.sh
```

默认模式会固定使用 SQLite，并执行 Django check、迁移 dry-run，以及 v2 API、资源、飞手、巡检相关测试。如果改动碰到共享响应、DJI 网关、schema/routing 或 v1/v2 共用模型，再跑边界烟测：

```bash
scripts/test_v2_regression.sh boundary
```

更多模式见 `docs/v2-regression-testing.md`。

如需启用 DJI 后台同步，请先配置 DJI 上游环境变量；`DjiGateway` 会在运行时自动登录、自动续期，并把当前 `workspace_id`、`access_token`、`mqtt_*` 等会话字段回写到 `DjiWorkspaceConfig`：

```bash
export DJI_UPSTREAM_BASE_URL=https://drone-java-api.metop.com.cn
export DJI_UPSTREAM_USERNAME=adminPC1
export DJI_UPSTREAM_PASSWORD=adminPC1234567890
export DJI_UPSTREAM_LOGIN_FLAG=1
export DJANGO_ALLOWED_HOSTS=drone-django-api.metop.com.cn,127.0.0.1,localhost
```

说明：

- `DJI_UPSTREAM_LOGIN_FLAG` 默认值为 `1`，只有上游登录参数不同的环境才需要覆盖
- 不再需要手工预写 `DjiWorkspaceConfig.access_token`
- `DjiWorkspaceConfig` 仍然是当前上游会话的唯一持久化落点，供业务请求和同步调度复用

补充说明：

- Django management command 是 Django 提供的命令行管理入口，用来执行“启动服务、跑迁移、导数据、跑后台任务”这类系统级操作
- 它不是 HTTP API，不通过浏览器或 Swagger 调用，而是在项目根目录下通过 `python manage.py <command>` 执行
- 常见例子：
  - `python manage.py migrate`
  - `python manage.py runserver 0.0.0.0:8001`
  - `python manage.py createsuperuser`

当前项目里的 DJI 同步调度命令就是一个 management command，命令名为 `run_dji_sync_scheduler`：

```bash
python manage.py run_dji_sync_scheduler --interval-seconds 60
```

说明：

- 通用格式：`python manage.py run_dji_sync_scheduler [options]`
- 该命令默认循环执行设备、媒体同步
- `--once` 只跑一轮，适合人工触发或排障
- `--max-cycles N` 适合受控运行和测试
- 循环模式下单轮失败不会退出进程，下一轮会继续重试

常见用法：

```bash
# 只执行一轮同步
python manage.py run_dji_sync_scheduler --once

# 每 60 秒执行一轮
python manage.py run_dji_sync_scheduler --interval-seconds 60

# 不等待间隔，连续执行 2 轮后退出
python manage.py run_dji_sync_scheduler --interval-seconds 0 --max-cycles 2
```

## 代码与能力映射

- 路由入口：`config/urls.py`
- 正式 IAM：
  - 路由：`apps/access/api_v1/urls.py`
  - 视图：`apps/access/api_v1/views/*`
  - 认证：`apps/access/api_v1/authentication.py`
  - 上下文与运行态：`apps/access/api_v1/context.py`
  - 服务：`apps/access/api_v1/services/*`
  - 核心数据模型：`apps/access/models.py`
- Business API 根入口：`apps/api_v1/*`
- 业务域：
  - `apps/drone/*` - 无人机台账
  - `apps/drone_assignment/*` - 无人机分配
  - `apps/route/*` - 航线台账、KMZ 上传替换与下载代理
  - `apps/waypoint/*` - route 内部航点存储模型
  - `apps/mission/*` - 任务
  - `apps/flight_record/*` - 飞行记录
  - `apps/media_file/*` - 媒体文件
  - `apps/dji_bff/*` - DJI 网关、同步任务、内部回调入口
  - `apps/dji_mock/*` - 测试用 mock DJI upstream

### 已实现接口清单（与当前代码一致）

1. Formal IAM - Session

- `POST /api/v1/iam/session/login`
- `POST /api/v1/iam/session/refresh`
- `POST /api/v1/iam/session/logout`
- `POST /api/v1/iam/session/register`
- `POST /api/v1/iam/session/register-by-phone`

2. Formal IAM - Me

- `GET /api/v1/iam/me/profile`
- `GET /api/v1/iam/me/tenants`

3. Formal IAM - Tenant

- `GET /api/v1/iam/tenant/me`
- `GET/POST /api/v1/iam/tenant/members`
- `GET/PATCH /api/v1/iam/tenant/members/{memberId}`
- `PUT /api/v1/iam/tenant/members/{memberId}/roles`
- `POST /api/v1/iam/tenant/members/{memberId}/enable`
- `POST /api/v1/iam/tenant/members/{memberId}/disable`
- `GET /api/v1/iam/tenant/roles`
- `GET /api/v1/iam/tenant/audit-logs`

4. Formal IAM - Platform

- `GET /api/v1/iam/platform/permissions`
- `GET /api/v1/iam/platform/roles`
- `GET /api/v1/iam/platform/roles/{roleId}`
- `GET /api/v1/iam/platform/audit-logs`
- `GET/POST /api/v1/iam/platform/tenants`
- `GET /api/v1/iam/platform/tenants/{tenantId}`
- `POST /api/v1/iam/platform/tenants/{tenantId}/enable`
- `POST /api/v1/iam/platform/tenants/{tenantId}/disable`
- `POST /api/v1/iam/platform/tenants/{tenantId}/initialize-admin`

5. Business API - 无人机（drone）

- `GET /api/v1/drones/available`
- `GET/POST /api/v1/drones`
- `GET/PUT/DELETE /api/v1/drones/{id}`
- `GET /api/v1/drones/{id}/live/capacity`
- `POST /api/v1/drones/{id}/live/start`
- `POST /api/v1/drones/{id}/live/stop`
- `POST /api/v1/drones/{id}/live/update`
- `POST /api/v1/drones/{id}/live/switch`

6. Business API - 无人机分配（drone_assignment）

- `GET/POST /api/v1/drone-assignments`
- `GET /api/v1/drone-assignments/{id}`
- `POST /api/v1/drone-assignments/{id}/cancel`

7. Business API - 航线（route）

- `GET /api/v1/routes`
- `POST /api/v1/routes`
- `GET /api/v1/routes/{id}`
- `PUT /api/v1/routes/{id}`
- `GET /api/v1/routes/{id}/kmz`
- `DELETE /api/v1/routes/{id}`
- `POST /api/v1/routes` 仅接受 `multipart/form-data`，必须提交 `name` 与 `kmz_file`
- `PUT /api/v1/routes/{id}` 支持部分更新：
  - `application/json` 可只提交 `name`
  - `multipart/form-data` / `application/x-www-form-urlencoded` 可提交 `kmz_file`，也可同时提交 `name + kmz_file`
  - 空请求体返回当前航线快照并视为 no-op
- `PATCH /api/v1/routes/{id}` 不支持
- `GET /api/v1/routes/{id}/kmz` 通过已保存的 `download_url` 代理当前 KMZ 下载
- `POST /api/v1/routes` 与 `PUT /api/v1/routes/{id}` 都会拒绝旧字段（`route_type`、`waypoints[]` 等）

8. Business API - 任务（mission）

- `GET/POST /api/v1/missions`
- `GET/PUT/DELETE /api/v1/missions/{id}`
- `POST /api/v1/missions/{id}/advance`

9. Business API - 飞行记录（flight_record）

- `GET /api/v1/flight-records`
- `GET/PUT/DELETE /api/v1/flight-records/{id}`

10. Business API - 媒体文件（media_file）

- `GET /api/v1/media-files`
- `GET /api/v1/media-files/{id}`
- `GET /api/v1/media-files/{id}/download`
- `GET /api/v1/media-files/{id}/playback`
- `GET /api/v1/media-files/{id}/playback-url`
- `POST /api/v1/media-files/bind-mission`

11. Internal DJI Bridge

- `POST /api/v1/__internal__/dji/sync/devices`
- `POST /api/v1/__internal__/dji/sync/media`
- `POST /api/v1/__internal__/dji/callbacks/media-upload`
- `POST /api/v1/__internal__/dji/callbacks/media-group-upload`

## 关键实现约束（与当前代码一致）

- `DELETE /api/v1/drones/{id}` 为软删除（释放认领）：将 `Drone.status` 置为 `RELEASED`，并把该设备下 `ACTIVE` 分配批量置为 `INACTIVE`。
- `Drone.status` 仅表达业务认领态（`CLAIMED` / `RELEASED`）；DJI 在线态通过独立字段 `dji_online` 表达，由设备同步任务维护。
- `GET /api/v1/drones/available` 仅返回“未被非 `RELEASED` 设备记录占用”的上游设备索引；`ASSIGNED` 范围访问会返回空列表。
- `POST /api/v1/missions` 必须同时绑定 `route`、`drone`、`pilot`，创建后初始状态为 `待执行`。
- `POST /api/v1/missions/{id}/advance` 按 `待执行 -> 执行中 -> 执行完成` 推进，并分别写入 `started_at`、`finished_at`。
- 同一无人机同一时刻只允许一个 mission 进入 `执行中`。
- 任务只有在 `待执行` 时允许修改绑定字段；进入 `执行中 / 执行完成` 后不允许再改绑定关系。
- DJI 媒体同步当前不依赖 `flight_record` 或 `jobId`；系统按 `device_sn + captured_at` 命中唯一 mission 时间窗时自动回填 `mission_id`，若已人工绑定则保留人工结果。
- `POST /api/v1/routes` 会直接上传 DJI，成功后持久化 `dji_wayline_id`、`download_url` 与 `is_published=true`。
- `PUT /api/v1/routes/{id}` 仅更新 `name` 时不会触达 DJI；携带 `kmz_file` 时会先上传并校验新 KMZ，再切换本地索引并在提交后 best-effort 清理旧航线。
- `DELETE /api/v1/routes/{id}` 在当前租户存在状态为 `PENDING/RUNNING` 的关联任务时会被拒绝。
- `PUT /api/v1/flight-records/{id}` 只允许更新摘要字段（`mission_name`、`route_name`、`airport_name`、`drone_name`、`pilot_name`、`flight_duration`、`photo_count`），`status` 不对外开放写入，也没有 `complete/abort` 动作接口。

## `/api/v1/*` 响应契约

正式 `/api/v1/*` 接口统一返回：

```json
{
  "code": "00000",
  "msg": "success",
  "data": {}
}
```

- 列表接口统一返回 `data.list` 和 `data.total`
- 详情、创建、更新、动作接口统一返回 `data=资源快照或动作结果`
- 常用错误码：
  - `A0401`：未登录或登录已失效
  - `A0403`：无操作权限
  - `B0001`：参数校验失败
  - `C0101`：资源已存在或重复提交
  - `C0102`：账号字段冲突
  - `C0103`：成员关系重复
  - `C0201`：当前状态不允许操作
  - `C0202`：当前数据已被引用，无法删除
  - `C0203`：会导致租户失去最后一个有效租户管理员
  - `C0404`：目标资源不存在
  - `E0001`：系统异常

## 鉴权与授权边界

### 认证方式

| 方式                | 说明                                                                      |
| ------------------- | ------------------------------------------------------------------------- |
| Bearer Token        | 正式 `/api/v1/*` 接口统一使用。通过 `POST /api/v1/iam/session/login` 获取 |
| Django Admin 登录态 | 仅用于 `/admin/`，不属于正式 API 认证域                                   |

### 正式 IAM 运行态

| 运行态              | 说明                                                                                |
| ------------------- | ----------------------------------------------------------------------------------- |
| `unassigned`        | 已注册、可登录、可访问 `me/*`，但尚无任何租户成员关系                               |
| `tenant_member`     | 至少拥有一条租户成员关系；可访问 `me/*`，并在合法 `X-TENANT-CODE` 下访问 `tenant/*` |
| `platform_operator` | 平台工作态账号；只访问 `session/*` 与 `platform/*`                                  |

补充规则：

- `tenant_member` 与 `platform_operator` 互斥。
- `superuser` 是技术 root，不属于正式 IAM 业务账号集合，不能通过 `session/login` 建立正式认证会话。
- `platform_admin` 通过 `User.is_platform_admin` 承载，不进入 `TenantMember -> TenantMemberRole` 链。

### 授权链

普通租户工作态授权链：

```text
TenantMember(ACTIVE)
  -> TenantMemberRole(GRANTED)
  -> RolePermissionGrant
  -> Permission
```

平台工作态授权链：

```text
User(is_platform_admin=true)
  -> Role(code=platform_admin)
  -> RolePermissionGrant
  -> Permission
```

### 数据范围（Scope）

| Scope      | 含义                   |
| ---------- | ---------------------- |
| `ALL`      | 当前作用域内全部数据   |
| `OWN`      | 当前成员自己的资源     |
| `ASSIGNED` | 当前成员被分配到的资源 |

说明：

- 目前 `pilot_operator` 在无人机、任务、飞行记录、媒体文件等能力上使用 `ASSIGNED`。
- `OWN / ASSIGNED` 的主体统一按当前租户内的 `TenantMember.id` 判定，不再使用全局 staff id。

## 保留文档

开发者日常只需要看这几份长期文档：

- [README.md](README.md)：项目总览、启动方式、API 入口、当前约束
- [CLAUDE.md](CLAUDE.md)：开发约定、常用命令、架构速查
- [DEPLOY.md](DEPLOY.md)：容器化部署与运维入口
- [docs/django-logging-guide.md](docs/django-logging-guide.md)：日志排查
- [docs/host-django-postgres-migration.md](docs/host-django-postgres-migration.md)：宿主机迁移到 Docker PostgreSQL
- [项目总体概览/DJI适配接入边界设计.md](项目总体概览/DJI适配接入边界设计.md)：DJI 接入边界
- [权限管理侧实现/DJI权限与租户隔离设计.md](权限管理侧实现/DJI权限与租户隔离设计.md)：权限与租户隔离边界

其余研究记录、阶段计划、一次性复现文档已经删除，避免文档入口分散。
