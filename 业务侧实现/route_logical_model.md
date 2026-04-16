# 航线逻辑模型

- updated_at: 2026-04-16
- entity: route

## 实体主表

- table: routes
- 主键: id (BigAutoField)
- 排序规则: `ordering = ['-id']`

## 聚合边界

- `Route` 是公开业务聚合根，只保存本地业务主数据 `name`。
- `TenantRouteIndex` 是 route 与 DJI 航线之间的唯一发布状态桥接，维护 `dji_wayline_id`、`download_url`、`is_published`。
- `waypoints` 表仍存在，但只保留历史/内部存储语义，不再参与公开读写契约。

## 发布状态模型

当前设计不使用 `Route.status`。

Route 与 DJI 的绑定关系由 `TenantRouteIndex` 表达：

| 字段 | 含义 |
|---|---|
| dji_wayline_id | 当前绑定的 DJI 航线 ID；未发布时为空串 |
| download_url | 当前绑定航线的下载地址 |
| is_published | 当前 route 是否已绑定最近一次成功上传的 DJI 航线 |

语义：

- `is_published = false`
  - route 尚未成功绑定可下载的上游航线，或
  - 本地数据存在但尚未建立有效 DJI 索引
- `is_published = true`
  - 当前 `Route` 已与最新一次成功上传到 DJI 的 KMZ 保持一致

## 关系与约束

- `route.tenant -> access.Tenant`
- `tenant_route_indexes.route -> route.Route`（一对一）
- `tenant_route_indexes(tenant_id, dji_wayline_id)` 在 `dji_wayline_id != ''` 时唯一
- `waypoints.route_id -> routes.id`
- `waypoints(route_id, sequence)` 唯一

## 生命周期入口

| 操作 | 路径 | 说明 |
|-----|------|------|
| 创建 | POST /api/v1/routes | 创建 route，并立即上传 `name + kmz_file` 到 DJI |
| 列表 | GET /api/v1/routes | 航线列表查询 |
| 详情 | GET /api/v1/routes/{id} | 读取 route 元数据 |
| 更新 | PUT /api/v1/routes/{id} | 部分更新 route 名称，或替换上游 KMZ |
| 下载 KMZ | GET /api/v1/routes/{id}/kmz | 通过 `download_url` 代理当前 KMZ |
| 删除 | DELETE /api/v1/routes/{id} | 删除 route，并 best-effort 删除当前 DJI 航线 |

## 接口语义

### 创建 POST /api/v1/routes

- 请求：仅支持 `multipart/form-data`，必须提供 `name` 与 `kmz_file`。
- 结果：
  - 创建本地 `Route`
  - 上传 DJI 并校验下载地址
  - 写入 `TenantRouteIndex(dji_wayline_id, download_url, is_published=true)`
  - 记录 `ROUTE_CREATE` 审计

### 更新 PUT /api/v1/routes/{id}

- 请求语义是“部分更新”，而不是完整替换。
- 支持四种输入：
  - `application/json` 仅提交 `name`
  - 表单仅提交 `kmz_file`
  - 表单同时提交 `name + kmz_file`
  - 空请求体 no-op
- 关键行为：
  - 仅更新 `name` 时，不触达 DJI，不修改 `TenantRouteIndex`
  - 只替换 `kmz_file` 时，保留当前名称
  - 携带 `kmz_file` 的更新会走“上传 -> 校验 -> 本地切换 -> on_commit 清理旧航线”流程
  - 若该 route 被 `PENDING` / `RUNNING` 且已绑定无人机的 mission 占用，拒绝更新
  - `PATCH /api/v1/routes/{id}` 不存在

### 下载 KMZ GET /api/v1/routes/{id}/kmz

- 优先使用已保存 `download_url` 下载
- 若 `download_url` 为空或上游返回 404，则尝试通过 `dji_wayline_id` 刷新下载地址后重试
- 成功返回二进制 KMZ 流

### 删除 DELETE /api/v1/routes/{id}

- 本地先删除 `Route` 及残留 `waypoints`
- 事务提交后 best-effort 删除当前 `dji_wayline_id`
- 若 route 被 `PENDING` / `RUNNING` 且已绑定无人机的 mission 占用，拒绝删除
