# 航线实现说明

- updated_at: 2026-03-31
- entity: route

## 数据模型

### Route 表 (routes)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| tenant | ForeignKey | 租户 |
| name | CharField(100) | 航线名称 |
| xml_file | FileField | 本地 XML 草稿；对外唯一的航线源数据 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

`xml_file` 是聚合的唯一来源，`POST /api/v1/routes` 与 `PUT /api/v1/routes/{id}` 只能写 `name` 与 `xml_file`，文件必须是可解析的 XML，写请求必须通过 `multipart/form-data` 提交。

### TenantRouteIndex 表 (tenant_route_indexes)

| 字段 | 类型 | 说明 |
|-----|------|------|
| tenant | ForeignKey | 租户 |
| route | OneToOneField | 业务航线 |
| dji_wayline_id | CharField(128) | 当前已发布 DJI 航线 ID；未发布时为空串 |
| is_published | BooleanField | 当前本地草稿是否与上一次成功发布一致 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

`TenantRouteIndex` 是 `Route` 与 DJI 上游航线状态的最小映射；`dji_wayline_id` 受限于同一租户下非空值唯一，`is_published` 通过本地写操作统一置为 `false`，发布成功后设置为 `true`。

### Waypoint 存储边界

- `waypoints` 表在项目中仍存在，但只作为历史/内部持久化，不构成对外契约。
- `Waypoint` 模型没有公开 API、权限或审计；外部系统无法直接读写 `waypoints[]`。
- 当前路线编辑链路只通过 XML 文件，`waypoints` 表不会参与正常的 `POST`/`PUT` 写入，也不会被前端显式触发。

## API 实现 (/api/v1/routes)

统一响应契约同 `BusinessApiResponseMixin`：`code/msg/data`。

### 1. GET /api/v1/routes

- 功能：查询本租户航线列表，默认按 `-id` 排序。
- 筛选参数：`name`（模糊匹配）。
- 返回：`Route` 读序列化字段（`id`、`name`、`is_published`、`created_at`、`updated_at`）。
- 权限：`route.view_route`。

### 2. POST /api/v1/routes

- 功能：创建本地航线草稿。
- 请求：`multipart/form-data`，必须包含 `xml_file`（可解析 XML）与 `name`。
- 写入：新建 `Route`，同时创建 `TenantRouteIndex(dji_wayline_id="", is_published=false)`。
- 约束：不支持 `route_type`、`drone_type_id`、`total_distance`、`waypoints[]` 等旧字段；上传的 XML 会立即被验证。
- 返回：新航线描述，`is_published=false`。
- 权限：`route.manage_route`。

### 3. GET /api/v1/routes/{id}

- 功能：返回单条航线元数据。
- 返回字段与列表相同，不返回 `xml_file`、`waypoints[]`。
- 权限：`route.view_route`。

### 4. PUT /api/v1/routes/{id}

- 功能：替换本地航线草稿。
- 请求：`multipart/form-data`，只能写 `name` 与 `xml_file`（必须提供，且必须是可解析 XML）。
- 关键行为：
  - 替换文件后删除旧 XML 存储。
  - 调用 `_mark_route_unpublished` 将 `TenantRouteIndex.is_published` 置为 `false`。
  - 不再接受 `route_type`、`waypoints[]` 等旧字段。
- 返回：更新后的航线（`is_published` 为 `false`）。
- 权限：`route.manage_route`。

### 5. GET /api/v1/routes/{id}/xml

- 功能：直接返回当前草稿的 XML 内容。
- 如果 XML 文件缺失、路径为空或存储里找不到文件，返回 404。
- 响应：`application/xml` 的流；`filename` 基于存储路径动态确定。
- 权限：`route.view_route`。

### 6. POST /api/v1/routes/{id}/publish

- 功能：从当前 XML 草稿构建 KMZ 并上传 DJI，完成显式发布。
- 请求：不接受请求体，任何 `Content-Length` 均会触发 400。
- 过程：
  - `build_route_kmz_from_xml` 读取并校验本地 XML。
  - 使用 `DjiGateway.upload_route` 上传 KMZ，返回新的 `dji_wayline_id`。
  - 回写 `TenantRouteIndex` 的 `dji_wayline_id` 与 `is_published=true`，并记录审计。
  - 若先前已有已发布 DJI 航线，异步删除旧的上游航线。
- 错误：若 XML 无效或上传失败，会返回标准错误码（例如 `B0001`、`C0201`）。
- 权限：`route.manage_route`。

### 7. DELETE /api/v1/routes/{id}

- 功能：删除本地航线草稿及关联资源。
- 约束：若存在 `Mission` 在 `PENDING` 或 `RUNNING`，拒绝删除（返回 `C0201`）。
- 删除流程：
  - 先尝试删除 DJI 上游航线（若 `TenantRouteIndex.dji_wayline_id` 非空），404 会被忽略。
  - 删除 `Route` 与关联 XML 文件。
  - 相关 `Waypoint` 行在外键级联下被清理（`PROTECT` 除外），但 `waypoints` 表不再承载任何公开契约。
  - 删除后记录 `ROUTE_DELETE` 审计。
- 权限：`route.manage_route`。

### 8. 审计动作

- `ROUTE_CREATE`
- `ROUTE_UPDATE`
- `ROUTE_PUBLISH`
- `ROUTE_DELETE`

## 关键实现文件

- apps/route/models.py
- apps/route/serializers.py
- apps/route/services.py
- apps/route/views.py
- apps/route/urls.py
- apps/dji_bff/models.py
- apps/route/tests.py
- apps/route/test_live_api.py
