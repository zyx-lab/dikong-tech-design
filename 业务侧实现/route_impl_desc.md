# 航线实现说明

- updated_at: 2026-04-16
- entity: route

## 数据模型

### Route 表 (routes)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| tenant | ForeignKey | 租户 |
| name | CharField(100) | 航线名称 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

`Route` 只保存本地业务主数据；当前不再落本地 XML/KMZ 文件副本。

### TenantRouteIndex 表 (tenant_route_indexes)

| 字段 | 类型 | 说明 |
|-----|------|------|
| tenant | ForeignKey | 租户 |
| route | OneToOneField | 业务航线 |
| dji_wayline_id | CharField(128) | 当前绑定的 DJI 航线 ID；未发布时为空串 |
| download_url | CharField(500) | 当前 DJI 航线下载地址 |
| is_published | BooleanField | 当前 route 是否已绑定最近一次成功上传的 DJI 航线 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

`TenantRouteIndex` 是 `Route` 与 DJI 上游航线状态的最小映射；同一租户下非空 `dji_wayline_id` 唯一。

### Waypoint 存储边界

- `waypoints` 表仍存在，但只作为历史/内部持久化，不构成对外契约。
- `Waypoint` 模型没有公开 API、权限或审计；外部系统无法直接读写 `waypoints[]`。
- route 的正常写链路只接受 `name` 与 `kmz_file`，不会写入 `waypoints` 行。

## API 实现 (/api/v1/routes)

统一响应契约同 `BusinessApiResponseMixin`：`code/msg/data`。

### 1. GET /api/v1/routes

- 功能：查询本租户航线列表，默认按 `-id` 排序。
- 筛选参数：`name`（模糊匹配）。
- 返回：`Route` 读序列化字段（`id`、`name`、`is_published`、`created_at`、`updated_at`）。
- 权限：`route.view_route`。

### 2. POST /api/v1/routes

- 功能：创建 route，并立即上传 KMZ 到 DJI。
- 请求：仅支持 `multipart/form-data`，必须包含 `name` 与 `kmz_file`。
- 过程：
  - 新建本地 `Route`；
  - 调用 `DjiGateway.upload_route` 上传 KMZ；
  - 校验 `download_url` 可下载；
  - 创建或写入 `TenantRouteIndex(dji_wayline_id, download_url, is_published=true)`；
  - 记录 `ROUTE_CREATE` 审计。
- 约束：不支持 `route_type`、`drone_type_id`、`total_distance`、`waypoints[]` 等旧字段；上传文件必须是有效 KMZ/ZIP。
- 权限：`route.manage_route`。

### 3. GET /api/v1/routes/{id}

- 功能：返回单条航线元数据。
- 返回字段与列表相同。
- 权限：`route.view_route`。

### 4. PUT /api/v1/routes/{id}

- 功能：对同一条 route 做部分更新。
- 更新路径：
  - `application/json` + `name`：只更新本地 `Route.name`；
  - `multipart/form-data` / `application/x-www-form-urlencoded` + `kmz_file`：保持当前名称，替换上游 KMZ；
  - 表单同时提交 `name + kmz_file`：名称与上游 KMZ 一起更新；
  - 空请求体：返回当前航线快照并视为 no-op。
- 关键行为：
  - 仅更新名称时，不触达 DJI，不修改 `dji_wayline_id` / `download_url`；
  - 携带 `kmz_file` 时，沿用“先上传并校验新航线，再切换本地索引，提交后 best-effort 删除旧航线”的流程；
  - 不再接受 `route_type`、`waypoints[]` 等旧字段；
  - `PATCH` 仍然不支持。
- 约束：若该 route 被当前租户内一个 `PENDING` / `RUNNING` 且已绑定无人机的 mission 占用，拒绝更新。
- 权限：`route.manage_route`。

### 5. GET /api/v1/routes/{id}/kmz

- 功能：根据当前 `download_url` 代理下载 KMZ。
- 过程：
  - 优先使用已保存 `download_url`；
  - 若为空或上游返回 404，则尝试通过 `dji_wayline_id` 刷新下载地址并重试；
  - 最终返回二进制 KMZ 文件流。
- 权限：`route.view_route`。

### 6. DELETE /api/v1/routes/{id}

- 功能：删除本地 route，并 best-effort 删除当前 DJI 航线。
- 约束：若存在 `Mission` 在 `PENDING` 或 `RUNNING` 且已绑定无人机，拒绝删除（返回 `B0001`）。
- 删除流程：
  - 删除 route 前先清理残留 `Waypoint` 行；
  - 删除本地 `Route`；
  - 提交后 best-effort 调用 DJI 删除当前 `dji_wayline_id`；
  - 记录 `ROUTE_DELETE` 审计。
- 权限：`route.manage_route`。

### 7. 审计动作

- `ROUTE_CREATE`
- `ROUTE_UPDATE`
- `ROUTE_DELETE`

## 关键实现文件

- apps/route/models.py
- apps/route/serializers.py
- apps/route/views.py
- apps/route/urls.py
- apps/dji_bff/models.py
- apps/dji_bff/gateway.py
- apps/route/tests.py
- apps/route/test_live_api.py
