# 低空智能巡检平台 - 航点

## 阅读说明

本表仍然存在，但已经不是独立业务资源。

它当前的定位是：

- 历史内部子表
- 不再参与当前公开 route 编辑链路
- 不再拥有独立 API、权限、审计和业务状态机

## 1. waypoints（航点内部存储表）

**说明**：存储历史 route 数据遗留的内部航点行。

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
2. 当前 `POST /api/v1/routes` 与 `PUT /api/v1/routes/{id}` 只处理 XML，不再写入该表。
3. 删除 route 时，系统会先清理残留 waypoint 行，再删除 route。

## 2. 与实现对应

1. 模型：`apps/waypoint/models.py`
2. route 删除链路：`apps/route/views.py`
