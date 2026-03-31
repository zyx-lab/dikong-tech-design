# 航点逻辑模型

- updated_at: 2026-03-31
- entity: waypoint

## 实体主表

- table: waypoints
- 主键: id (BigAutoField)
- 排序规则: `ordering = ['route_id', 'sequence', 'id']`

## 当前边界

- `Waypoint` 已不再是独立业务资源。
- `waypoints` 表只承担 `Route` 聚合的内部持久化。
- 前端和外部系统只能通过 `Route.waypoints[]` 读写整条航线的航点集。

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
| 创建 route 草稿并写入航点 | POST /api/v1/routes | 可随 route 一起提交完整 `waypoints[]` |
| 更新 route 草稿并替换航点 | PUT / PATCH /api/v1/routes/{id} | 提交 `waypoints[]` 时整条航线全量替换 |
| 删除 route | DELETE /api/v1/routes/{id} | 同步删除该 route 的内部 waypoint 行 |

## 接口语义

### 通过 Route 创建航点集 POST /api/v1/routes

- 功能：创建 route 草稿时写入内部航点行
- 输入：`waypoints[]`
- 约束：`sequence` 必须唯一，且从 `1` 开始校验为正整数
- 写入结果：按 `sequence` 排序后批量写入 `waypoints`

### 通过 Route 更新航点集 PUT / PATCH /api/v1/routes/{id}

- 功能：更新 route 草稿时维护内部航点行
- 语义：
  - 提交 `waypoints[]` 时，视为整条航线完整替换
  - `PATCH` 未提交 `waypoints[]` 时，不修改已有 waypoint 行
- 副作用：route 聚合会统一回写 `route.waypoint_count`

### 通过 Route 删除航点集 DELETE /api/v1/routes/{id}

- 功能：删除 route 时清理内部 waypoint 行
- 约束：若 route 正被 `PENDING / RUNNING` 任务引用，则整条 route 不允许删除
