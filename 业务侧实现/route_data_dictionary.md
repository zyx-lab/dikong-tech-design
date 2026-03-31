# 低空智能巡检平台 - 航线

## 阅读说明

本数据字典覆盖当前 route 业务域已落地的两张核心表：

- `routes`
- `tenant_route_indexes`

## 1. routes（航线表）

**说明**：存储租户内 route 草稿主记录；对外编辑边界以 route 为聚合根。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| tenant_id | bigint | FK, NOT NULL | - | 所属租户 |
| name | varchar(100) | NOT NULL | - | 航线名称 |
| route_type | smallint | NOT NULL | 0 | 航线类型扩展位 |
| drone_type_id | bigint | - | - | 适用无人机类型 ID（预留） |
| total_distance | numeric(12,2) | - | - | 航线总长度（米） |
| estimated_duration | integer | - | - | 预计飞行时长（秒） |
| waypoint_count | integer | NOT NULL | 0 | 内部航点数量 |
| creator_name | varchar(50) | NOT NULL | '' | 创建人姓名 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**route_type 枚举值**：

| 值 | 含义 |
|----|------|
| 0 | 待扩展 |

**业务规则**：
1. `Route` 不再有独立 `status` 字段。
2. `waypoints` 不再是独立业务资源，只能通过 `Route.waypoints[]` 读写。
3. 提交新的 `waypoints[]` 时，内部 waypoint 行按整条航线全量替换。
4. `waypoint_count` 由 route 聚合写链路统一维护。
5. 删除 route 时，若存在 `PENDING / RUNNING` 任务引用，则拒绝删除；否则物理删除 route 与内部 waypoint 行。

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
