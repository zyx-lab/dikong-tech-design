# 航线实现说明

- updated_at: 2026-03-30
- entity: route

## 数据模型

### Route 表 (routes)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| tenant | ForeignKey | 租户 |
| name | CharField(100) | 航线名称 |
| route_type | PositiveSmallIntegerField | 航线类型扩展位，默认 0=待扩展 |
| drone_type_id | BigIntegerField | 适用无人机类型 ID（可选） |
| total_distance | DecimalField(12,2) | 航线总长度（米，可选） |
| estimated_duration | PositiveIntegerField | 预计飞行时长（秒，可选） |
| waypoint_count | PositiveIntegerField | 航点数量（系统维护冗余） |
| creator_name | CharField(50) | 创建人姓名 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

### TenantRouteIndex 表 (tenant_route_indexes)

| 字段 | 类型 | 说明 |
|-----|------|------|
| tenant | ForeignKey | 租户 |
| route | OneToOneField | 业务航线 |
| dji_wayline_id | CharField(128) | 当前已发布 DJI 航线 ID；未发布时为空串 |
| is_published | BooleanField | 当前本地草稿是否已成功发布到 DJI |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

### RouteType 枚举

- PENDING_EXTENSION = 0, "待扩展"

### Waypoint 存储边界

- `waypoints` 表在实现上仍然存在，但已经降级为 `Route` 聚合内部存储。
- 不再存在独立的业务资源 `/api/v1/waypoints*`。
- 前端和外部系统只能通过 `Route` 的 `waypoints[]` 结构读写航点集。

## API 实现 (/api/v1/routes)

统一响应契约：

- 成功：`code=00000`，`msg=success`
- 失败：统一返回 `code / msg / data`，常见错误码为 `A0401`、`A0403`、`B0001`、`C0201`、`C0404`

### 1. GET /api/v1/routes

- 功能：航线列表查询
- 筛选参数：`name`、`route_type`
- 权限：`route.view_route`
- 返回要点：列表项包含 `is_published`，不再暴露独立 waypoint 资源

### 2. POST /api/v1/routes

- 功能：创建本地航线草稿
- 必填：`name`
- 可选：`route_type`、`drone_type_id`、`total_distance`、`estimated_duration`、`waypoints[]`
- 关键行为：
  - 只落本地库，不触发 DJI 上传
  - 自动创建 `TenantRouteIndex(dji_wayline_id="", is_published=false)`
  - 若提交 `waypoints[]`，按整条航线全量写入内部 waypoint 行
- 权限：`route.manage_route`

### 3. GET /api/v1/routes/{id}

- 功能：航线详情
- 返回要点：
  - 包含完整可编辑 `waypoints[]`
  - 包含 `is_published`
- 权限：`route.view_route`

### 4. PUT / PATCH /api/v1/routes/{id}

- 功能：更新本地航线草稿
- 可写字段：`name`、`route_type`、`drone_type_id`、`total_distance`、`estimated_duration`、`waypoints[]`
- 关键行为：
  - 若提交 `waypoints[]`，视为整条航线的完整替换
  - 若 PATCH 未提交 `waypoints[]`，保留原有内部 waypoint 行
  - 任意本地编辑都会把 `is_published` 置回 `false`
- 权限：`route.manage_route`

### 5. POST /api/v1/routes/{id}/publish

- 功能：显式发布当前 route 草稿到 DJI
- 请求体：必须为空
- 前置条件：至少存在一个 waypoint
- 关键行为：
  - 从当前内部 waypoint 行生成整条 KMZ
  - 以上游系统名 `route-{route.id}-{uuid}` 上传 DJI
  - 上传成功后写回新的 `dji_wayline_id` 并设置 `is_published=true`
  - 若此前已有已发布 DJI 航线，则在新航线上传成功后删除旧航线
- 权限：`route.manage_route`

### 6. GET /api/v1/routes/{id}/download

- 功能：下载当前已发布航线文件
- 前置条件：`is_published=true` 且 `dji_wayline_id` 非空
- 未发布行为：返回 `409 / C0201`
- 权限：`route.view_route`

### 7. DELETE /api/v1/routes/{id}

- 功能：删除航线
- 请求体：必须为空
- 关键行为：
  - 若存在 `PENDING/RUNNING` 任务引用，拒绝删除
  - 否则删除本地 route 与内部 waypoint 行
  - 若存在已发布 `dji_wayline_id`，同步删除 DJI 航线
- 权限：`route.manage_route`

## 审计动作

- ROUTE_CREATE
- ROUTE_UPDATE
- ROUTE_PUBLISH
- ROUTE_DELETE

## 关键实现文件

- apps/route/models.py
- apps/route/serializers.py
- apps/route/services.py
- apps/route/views.py
- apps/route/urls.py
- apps/dji_bff/models.py
- apps/route/tests.py
- apps/route/test_live_api.py
