# 航线逻辑模型

- updated_at: 2026-03-30T00:00:00Z
- entity: route

## 实体主表

- table: routes
- 主键: id (BigAutoField)
- 排序规则: `ordering = ['-id']`

## 聚合边界

- `Route` 是公开业务聚合根，唯一可写字段是 `name` 与 `xml_file`，XML 文件是聚合的“单一真实源”。
- `waypoints` 表仍存在，但只做历史/内部持久化，不再单独暴露 API、权限或 `waypoints[]` 编辑契约。
- `TenantRouteIndex` 负责记录 `is_published` 与 `dji_wayline_id`，任何本地编辑都会把 `is_published` 置为 `false`。

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
| 创建草稿 | POST /api/v1/routes | 新增本地 route 草稿，仅写 `name` + `xml_file`（multipart/form-data） |
| 列表 | GET /api/v1/routes | 航线列表查询 |
| 详情 | GET /api/v1/routes/{id} | 读取 route 元数据（不含 XML） |
| 更新草稿 | PUT /api/v1/routes/{id} | 替换 XML 草稿（multipart/form-data，仅 `name` + `xml_file`） |
| XML | GET /api/v1/routes/{id}/xml | 获取当前草稿的源 XML 文件 |
| 发布 | POST /api/v1/routes/{id}/publish | 用 XML 构建 KMZ 并上传 DJI；更新 `TenantRouteIndex` |
| 删除 | DELETE /api/v1/routes/{id} | 删除 route（会同步删除 XML 与 DJI 航线） |

## 接口语义

### 创建草稿 POST /api/v1/routes

- 功能：创建 route 草稿，写入 `name` 与 XML。
- 请求：`multipart/form-data`，必须提供 `xml_file`，会校验可解析性，拒绝 `waypoints[]` 与旧字段。
- 写入结果：
  - 创建 `Route` 并关联 XML 草稿；
  - 创建 `TenantRouteIndex(dji_wayline_id="", is_published=false)`；
  - 记录 `ROUTE_CREATE` 审计。

### 更新草稿 PUT / PATCH /api/v1/routes/{id}

### 更新草稿 PUT /api/v1/routes/{id}

- 功能：全量替换本地航线草稿。
- 请求：`multipart/form-data`，只能写 `name` 与 `xml_file`（必须提供，且可解析）；不接受 `waypoints[]`、`route_type` 等旧字段。
- 语义：
  - 替换 XML 后删除旧文件。
  - 删除旧文件时会清理存储并把 `is_published` 置为 `false`。
  - 返回更新后的航线元数据。

### 获取 XML GET /api/v1/routes/{id}/xml

- 功能：读取当前草稿的 XML 内容流，`Content-Type: application/xml`。
- 若本地文件缺失或存储中找不到，返回 404。
- 权限：`route.view_route`。

### 发布 POST /api/v1/routes/{id}/publish

### 发布 POST /api/v1/routes/{id}/publish

- 功能：用当前 XML 构建 KMZ 并上传 DJI。
- 请求：不接受请求体；若检测到 `Content-Length`，返回 400。
- 过程：
  - `build_route_kmz_from_xml` 校验并打包 XML。
  - `DjiGateway.upload_route` 上传，记录新的 `dji_wayline_id`。
  - 回写 `TenantRouteIndex`（`dji_wayline_id` + `is_published=true`），并写审计。
  - 若原先发布过 DJI 航线，上传成功后异步删除旧航线。

### 删除 DELETE /api/v1/routes/{id}

- 功能：删除本地航线与 XML 草稿。
- 约束：若存在 `Mission.status` 为 `PENDING`/`RUNNING` 的引用，拒绝删除。
- 过程：
  - 删除 `TenantRouteIndex` 关联的 DJI 航线（404 忽略）。
  - 删除 `Route` 与 XML 文件。
  - 记录 `ROUTE_DELETE` 审计并返回 `{id, deleted:true}`。

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
