# 航点逻辑模型

- updated_at: 2026-03-31
- entity: waypoint

## 实体主表

- table: waypoints
- 主键: id (BigAutoField)
- 排序规则: `ordering = ['route_id', 'sequence', 'id']`

## 当前边界

- `Waypoint` 已不再是独立业务资源。
- `waypoints` 表只承担历史内部持久化。
- 当前前端和外部系统不会通过公开 API 读写 `waypoints` 行。

## 状态机

- N/A（航点无独立状态机）

## 关系与约束

- `waypoints.route_id -> routes.id`
- `route` 外键删除策略：`PROTECT`
- `waypoints(route_id, sequence)` 唯一
- Django 侧通过 `route.waypoint_rows` 访问内部航点行

## 生命周期入口

| 操作 | 路径 | 说明 |
|-----|------|------|
| 创建 route 草稿 | POST /api/v1/routes | 当前只上传 XML，不写 `waypoints` 行 |
| 更新 route 草稿 | PUT /api/v1/routes/{id} | 当前只替换 XML，不写 `waypoints` 行 |
| 删除 route | DELETE /api/v1/routes/{id} | 先清理残留 `waypoints` 行，再删除 route |

## 接口语义

### 公开 route 写接口

- `POST /api/v1/routes` 与 `PUT /api/v1/routes/{id}` 当前只维护 `Route.xml_file`。
- 不接受公开 `waypoints[]` 输入。
- 当前正常业务链路不会新增或替换 `waypoints` 行。

### 通过 Route 删除航点集 DELETE /api/v1/routes/{id}

- 功能：删除 route 时清理残留内部 waypoint 行
- 约束：若 route 正被 `PENDING` / `RUNNING` / `PAUSED` 任务引用，则整条 route 不允许删除
