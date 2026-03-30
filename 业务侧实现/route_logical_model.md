# 航线逻辑模型

- updated_at: 2026-03-30T00:00:00Z
- entity: route

## 实体主表

- table: routes
- 主键: id (BigAutoField)
- 排序规则: `ordering = ['-id']`

## 聚合边界

- `Route` 是公开业务聚合根。
- `waypoints[]` 是 `Route` 的内部编辑结构，不再是独立业务资源。
- `waypoints` 表仍存在，但只承担内部持久化，不再单独暴露 API、权限和状态机。

## 发布状态模型

当前设计不再使用 `Route.status`。

Route 与 DJI 的发布关系由 `TenantRouteIndex` 表达：

| 字段 | 含义 |
|---|---|
| dji_wayline_id | 当前已发布 DJI 航线 ID；未发布时为空串 |
| is_published | 当前本地 route 草稿是否已与最新一次成功发布结果一致 |

语义：

- `is_published = false`
  - 从未发布，或
  - 曾发布，但本地 route 草稿在此后又发生编辑
- `is_published = true`
  - 当前本地草稿与最新一次成功 DJI 发布结果一致

## 关系与约束

- `route.tenant -> access.Tenant`
- `tenant_route_indexes.route -> route.Route`（一对一）
- `waypoints.route_id -> routes.id`
- `waypoint_count` 为系统维护冗余字段，只能由 route 聚合写链路回写
- `waypoints(route_id, sequence)` 唯一

## 生命周期入口

| 操作 | 路径 | 说明 |
|-----|------|------|
| 创建草稿 | POST /api/v1/routes | 新增本地 route 草稿，可带完整 `waypoints[]` |
| 列表 | GET /api/v1/routes | 航线列表查询 |
| 详情 | GET /api/v1/routes/{id} | 读取 route 及完整 `waypoints[]` |
| 更新草稿 | PUT / PATCH /api/v1/routes/{id} | 更新 route 元数据或完整 waypoint 集 |
| 发布 | POST /api/v1/routes/{id}/publish | 将当前草稿显式发布到 DJI |
| 下载 | GET /api/v1/routes/{id}/download | 下载当前已发布航线 |
| 删除 | DELETE /api/v1/routes/{id} | 删除 route |

## 接口语义

### 创建草稿 POST /api/v1/routes

- 功能：创建 route 草稿
- DJI 行为：不上传 DJI
- 写入结果：
  - 创建 `Route`
  - 创建 `TenantRouteIndex(dji_wayline_id="", is_published=false)`
  - 若提交 `waypoints[]`，写入内部 waypoint 行

### 更新草稿 PUT / PATCH /api/v1/routes/{id}

- 功能：更新 route 草稿
- 可写字段：`name`、`route_type`、`drone_type_id`、`total_distance`、`estimated_duration`、`waypoints[]`
- 语义：
  - `PUT` / `PATCH` 提交 `waypoints[]` 时，视为整条航线完整替换
  - `PATCH` 未提交 `waypoints[]` 时，仅更新元数据
  - 任意本地编辑后都将 `is_published` 置回 `false`

### 发布 POST /api/v1/routes/{id}/publish

- 功能：把当前 route 草稿显式发布到 DJI
- 前置条件：至少存在一个 waypoint
- 过程：
  - 从当前内部 waypoint 行生成 KMZ
  - 以系统名 `route-{route.id}-{uuid}` 上传 DJI
  - 发布成功后写回 `dji_wayline_id` 并设置 `is_published=true`
  - 若此前已有旧的 DJI 航线，则新航线上传成功后删除旧航线

### 下载 GET /api/v1/routes/{id}/download

- 功能：下载当前已发布航线
- 前置条件：`is_published=true`
- 未发布行为：返回 `409 / C0201`

### 删除 DELETE /api/v1/routes/{id}

- 功能：删除 route
- 约束：
  - 若存在 `PENDING/RUNNING` 任务引用，则拒绝删除
  - 否则删除本地 route 与内部 waypoint 行
  - 若已存在 `dji_wayline_id`，同步删除 DJI 航线
