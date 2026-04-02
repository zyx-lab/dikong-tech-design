# 低空平台权限系统（V3）

## 当前状态（对齐日期：2026-03-31）

本仓库当前是一个 Django + DRF 的正式 API 项目，现状已经收敛为四类 HTTP 入口：

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
  - 航线（route）：XML 草稿 CRUD（只写 `name` + `xml_file`，`multipart/form-data` 提交），通过 `GET /api/v1/routes/{id}/xml` 读取源 XML，并通过 `POST /api/v1/routes/{id}/publish` 发布到 DJI；`waypoints` 表只能做内部/历史持久化，前端不再写入 `waypoints[]`
  - 任务（mission）：查询、创建、更新、取消；创建时同步 DJI job
  - 飞行记录（flight_record）：CRUD + 状态流转（完成/异常终止）
  - 媒体文件（media_file）：只读查询 + 下载；数据由 DJI 同步沉淀

3. Internal DJI Bridge（系统内部 DJI 桥接）

- 前缀：`/api/v1/__internal__/dji/*`
- 用途：
  - 受控触发设备 / 任务 / 媒体同步
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

## 快速启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_role_permissions --mode replace
python manage.py createsuperuser
python manage.py create_business_admin_account --username biz_root --password 'YourStrongPassword'
python manage.py runserver 0.0.0.0:8001
```

如需启用 DJI 后台同步，请先配置 DJI 上游环境变量；`DjiGateway` 会在运行时自动登录、自动续期，并把当前 `workspace_id`、`access_token`、`mqtt_*` 等会话字段回写到 `DjiWorkspaceConfig`：

```bash
export DJI_UPSTREAM_BASE_URL=http://8.129.135.140
export DJI_UPSTREAM_USERNAME=adminPC
export DJI_UPSTREAM_PASSWORD=adminPC1234567890
export DJI_UPSTREAM_LOGIN_FLAG=1
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
- 该命令默认循环执行设备、任务、媒体同步
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
  - `apps/route/*` - 航线聚合与 `waypoints[]` 读写
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
- `GET/PUT/PATCH /api/v1/drones/{id}`
- `GET /api/v1/drones/{id}/live/capacity`
- `POST /api/v1/drones/{id}/live/start`
- `POST /api/v1/drones/{id}/live/stop`
- `POST /api/v1/drones/{id}/live/video-quality`
- `POST /api/v1/drones/{id}/live/video-source`

6. Business API - 无人机分配（drone_assignment）

- `GET/POST /api/v1/drone-assignments`
- `GET /api/v1/drone-assignments/{id}`
- `POST /api/v1/drone-assignments/{id}/cancel`

7. Business API - 航线（route）

- `GET /api/v1/routes`
- `POST /api/v1/routes`
- `GET /api/v1/routes/{id}`
- `PUT /api/v1/routes/{id}`
- `DELETE /api/v1/routes/{id}`
- `GET /api/v1/routes/{id}/xml`
- `POST /api/v1/routes/{id}/publish`
- `POST /api/v1/routes` 与 `PUT /api/v1/routes/{id}` 仅接受 `multipart/form-data`，可写字段仅 `name` 和 `xml_file`，旧字段（`route_type`、`waypoints[]` 等）会被拒绝
- `GET /api/v1/routes/{id}/xml` 直接返回源 XML 文件流，`POST /api/v1/routes/{id}/publish` 以此 XML 构建 KMZ 上传 DJI

8. Business API - 任务（mission）

- `GET/POST /api/v1/missions`
- `GET/PUT/PATCH /api/v1/missions/{id}`
- `POST /api/v1/missions/{id}/cancel`

9. Business API - 飞行记录（flight_record）

- `GET/POST /api/v1/flight-records`
- `GET/PUT/PATCH /api/v1/flight-records/{id}`
- `POST /api/v1/flight-records/{id}/complete`
- `POST /api/v1/flight-records/{id}/abort`

10. Business API - 媒体文件（media_file）

- `GET /api/v1/media-files`
- `GET /api/v1/media-files/{id}`
- `GET /api/v1/media-files/{id}/download`

11. Internal DJI Bridge

- `POST /api/v1/__internal__/dji/sync/devices`
- `POST /api/v1/__internal__/dji/sync/missions`
- `POST /api/v1/__internal__/dji/sync/media`
- `POST /api/v1/__internal__/dji/callbacks/wayline-upload`
- `POST /api/v1/__internal__/dji/callbacks/media-upload`
- `POST /api/v1/__internal__/dji/callbacks/media-group-upload`

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

## 文档导航

### 总体设计

- 总体概念图：[overall_er_diagram.md](项目总体概览/概念设计/overall_er_diagram.md)
- 总体逻辑模型：[overall_logical_model.md](项目总体概览/逻辑设计/overall_logical_model.md)
- 总体数据字典：[overall_data_dictionary.md](项目总体概览/逻辑设计/overall_data_dictionary.md)
- 总体 DBML：[overall_schema.dbml](项目总体概览/逻辑设计/overall_schema.dbml)
- DJI 适配边界：[DJI适配接入边界设计.md](项目总体概览/DJI适配接入边界设计.md)
- DJI 权限与租户隔离：[DJI权限与租户隔离设计.md](权限管理侧实现/DJI权限与租户隔离设计.md)
- 业务接口扩展指南：[业务接口扩展指南.md](项目总体概览/业务接口扩展指南.md)

### 业务侧实现（业务侧实现/）

| 业务域     | 数据字典                                                                              | 实现描述                                                                  | 逻辑模型                                                                          | 图表                                                                    |
| ---------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | --------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| 无人机     | [drone_data_dictionary.md](业务侧实现/drone_data_dictionary.md)                       | [drone_impl_desc.md](业务侧实现/drone_impl_desc.md)                       | [drone_logical_model.md](业务侧实现/drone_logical_model.md)                       | [drone_schema.dbml](业务侧实现/drone_schema.dbml)                       |
| 无人机分配 | [drone_assignment_data_dictionary.md](业务侧实现/drone_assignment_data_dictionary.md) | [drone_assignment_impl_desc.md](业务侧实现/drone_assignment_impl_desc.md) | [drone_assignment_logical_model.md](业务侧实现/drone_assignment_logical_model.md) | [drone_assignment_schema.dbml](业务侧实现/drone_assignment_schema.dbml) |
| 航线       | [route_data_dictionary.md](业务侧实现/route_data_dictionary.md)                       | [route_impl_desc.md](业务侧实现/route_impl_desc.md)                       | [route_logical_model.md](业务侧实现/route_logical_model.md)                       | [route_schema.dbml](业务侧实现/route_schema.dbml)                       |
| 航点       | [waypoint_data_dictionary.md](业务侧实现/waypoint_data_dictionary.md)                 | [waypoint_impl_desc.md](业务侧实现/waypoint_impl_desc.md)                 | [waypoint_logical_model.md](业务侧实现/waypoint_logical_model.md)                 | [waypoint_schema.dbml](业务侧实现/waypoint_schema.dbml)                 |
| 任务       | [mission_data_dictionary.md](业务侧实现/mission_data_dictionary.md)                   | [mission_impl_desc.md](业务侧实现/mission_impl_desc.md)                   | [mission_logical_model.md](业务侧实现/mission_logical_model.md)                   | [mission_schema.dbml](业务侧实现/mission_schema.dbml)                   |
| 飞行记录   | [flight_record_data_dictionary.md](业务侧实现/flight_record_data_dictionary.md)       | [flight_record_impl_desc.md](业务侧实现/flight_record_impl_desc.md)       | [flight_record_logical_model.md](业务侧实现/flight_record_logical_model.md)       | [flight_record_schema.dbml](业务侧实现/flight_record_schema.dbml)       |
| 媒体文件   | [media_file_data_dictionary.md](业务侧实现/media_file_data_dictionary.md)             | [media_file_impl_desc.md](业务侧实现/media_file_impl_desc.md)             | [media_file_logical_model.md](业务侧实现/media_file_logical_model.md)             | [media_file_schema.dbml](业务侧实现/media_file_schema.dbml)             |

说明：

- `Waypoint` 四件套当前只描述 route 聚合内部存储，不代表存在公开 waypoint 业务 API。

### 权限管理侧实现（权限管理侧实现/）

- 权限设计基线：[权限设计.md](权限管理侧实现/权限设计.md)
- 角色权限矩阵：[角色权限矩阵设计.md](权限管理侧实现/角色权限矩阵设计.md)
- 维护学习参考：[维护与学习参考.md](权限管理侧实现/维护与学习参考.md)
- Admin 与表关系：[Admin菜单与数据库表关系说明.md](权限管理侧实现/Admin菜单与数据库表关系说明.md)
- 权限逻辑模型：[authz_logical_model.md](权限管理侧实现/authz_logical_model.md)
- 权限 DBML：[authz_schema.dbml](权限管理侧实现/authz_schema.dbml)
