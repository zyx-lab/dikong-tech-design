# 低空智能巡检平台 - 航线

## 阅读说明

本数据字典覆盖当前 route 业务域已落地的两张核心表：

- `routes`
- `tenant_route_indexes`

## 1. routes（航线表）

**说明**：租户内 route 草稿主记录，`xml_file` 是航线唯一的源文件。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| tenant_id | bigint | FK, NOT NULL | - | 所属租户 |
| name | varchar(100) | NOT NULL | - | 航线名称 |
| xml_file | varchar(100) | NOT NULL | '' | 本地 XML 草稿路径（`FileField`） |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**业务规则**：
1. `POST /api/v1/routes` 与 `PUT /api/v1/routes/{id}` 只接受 `multipart/form-data`，可写字段仅 `name` 与 `xml_file`；旧字段（`route_type`、`waypoints[]`、`drone_type_id` 等）被 reject。
2. 上传 XML 必须可解析，否则返回 `B0001`。
3. 所有本地编辑后都会把关联 `TenantRouteIndex.is_published` 置为 `false`。
4. `waypoints` 表只做内部/历史存储，不再构成公开业务契约。
5. 删除 `Route` 时，若存在 `Mission` 处于 `PENDING` 或 `RUNNING`，删除操作会被拒绝（`B0001`）。
6. 删除 `Route` 时，会先清理残留 `waypoints` 行，再删除 route 主记录与 XML 文件。

## 2. tenant_route_indexes（航线发布索引表）

**说明**：存储 route 与 DJI 航线的最小发布映射。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| tenant_id | bigint | FK, NOT NULL | - | 所属租户 |
| route_id | bigint | FK, NOT NULL, UNIQUE | - | 对应 route |
| dji_wayline_id | varchar(128) | NOT NULL | '' | 当前已发布 DJI 航线 ID；未发布时为空串 |
| is_published | boolean | NOT NULL | false | 当前本地草稿是否已与最近一次成功发布结果一致 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**约束与规则**：
1. `route_id` 一对一绑定 `routes.id`。
2. `(tenant_id, dji_wayline_id)` 仅在 `dji_wayline_id` 非空时唯一。
3. 创建 route 草稿时自动创建一条 `tenant_route_indexes`，初始值为 `dji_wayline_id=''`、`is_published=false`。
4. 任意本地编辑 route 后，都要把 `is_published` 置回 `false`。
5. `POST /api/v1/routes/{id}/publish` 成功后回写新的 `dji_wayline_id`，并把 `is_published` 置为 `true`。

## 3. 与实现对应

1. 模型：`apps/route/models.py`
2. DJI 映射：`apps/dji_bff/models.py`
3. 航点聚合写链路：`apps/route/serializers.py`、`apps/route/services.py`、`apps/route/views.py`

## 4. 业务响应契约（Business API）

说明：业务 API 响应体统一包含 `code`、`msg`、`data` 三个字段。

| 字段 | 类型 | 说明 |
| ------ | ------ | ------ |
| code | string | 业务码。成功固定为 `00000` |
| msg | string | 响应消息。成功通常为 `success` |
| data | object / array / null | 业务数据；失败时为错误上下文 |

| code | 典型 HTTP | 语义 |
| ------ | ------ | ------ |
| 00000 | 200 / 201 | 业务处理成功 |
| A0401 | 401 | 未登录或登录已失效 |
| A0403 | 403 | 无操作权限 |
| B0001 | 400 | 请求参数校验失败 |
| C0201 | 409 | 当前状态不允许操作 |
| C0404 | 404 | 目标资源不存在 |
| E0001 | 500 | 系统异常 |
