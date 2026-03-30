# 低空智能巡检平台 - 航点

## 阅读说明

本表仍然存在，但已经不是独立业务资源。

它当前的定位是：

- `Route` 聚合的内部子表
- 只服务 `Route.waypoints[]` 的持久化
- 不再拥有独立 API、权限、审计和业务状态机

## 1. waypoints（航点内部存储表）

**说明**：存储 route 草稿的内部航点行。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| route_id | bigint | FK, NOT NULL | - | 所属 route ID |
| sequence | int | NOT NULL | - | 航点序号 |
| latitude | decimal(12,8) | NOT NULL | - | 纬度 |
| longitude | decimal(12,8) | NOT NULL | - | 经度 |
| altitude | decimal(10,2) | NOT NULL | - | 飞行高度（米） |
| created_at | timestamp | NOT NULL | now() | 创建时间 |

**约束**：

- 同一 route 下 `sequence` 唯一

**业务规则**：

1. 该表不再直接对外暴露。
2. 所有写入都来自 `Route` 聚合的 `waypoints[]`。
3. 当 route 提交新的 `waypoints[]` 时，内部 waypoint 行按整条航线全量替换。
4. `route.waypoint_count` 由 route 聚合写链路统一维护。

## 2. 与实现对应

1. 模型：`apps/waypoint/models.py`
2. route 聚合写链路：`apps/route/serializers.py`、`apps/route/services.py`、`apps/route/views.py`
