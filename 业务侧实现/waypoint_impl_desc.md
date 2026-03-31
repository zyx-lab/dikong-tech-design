# 航点实现说明

- updated_at: 2026-03-31
- entity: waypoint

## 当前边界

`Waypoint` 已不再是独立业务资源。

当前实现中：

- 没有公开 API `/api/v1/waypoints*`
- 没有独立权限 `waypoint.view_waypoint` / `waypoint.manage_waypoint`
- 没有独立审计动作
- 只保留 `waypoints` 表，作为历史内部持久化结构

## 数据模型

### Waypoint 表 (waypoints)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| route | ForeignKey | 所属 route |
| sequence | PositiveIntegerField | 航点序号 |
| latitude | DecimalField(12,8) | 纬度 |
| longitude | DecimalField(12,8) | 经度 |
| altitude | DecimalField(10,2) | 飞行高度（米） |
| created_at | DateTimeField | 创建时间 |

### 约束

- 同一 route 下 `sequence` 唯一 (`waypoints_route_seq_unique`)
- Django 侧通过 `route.waypoint_rows` 访问
- `route` 外键删除策略仍是 `PROTECT`，但 route 删除 API 会先主动清理残留 waypoint 行

## 当前运行语义

- 当前 `POST /api/v1/routes` 与 `PUT /api/v1/routes/{id}` 只写 `xml_file`，不再写入 `waypoints` 行。
- 不再支持单航点独立 CRUD。
- 删除 `Route` 时，系统会先清理残留 waypoint 行，避免历史数据阻塞 route 删除。

## 关键实现文件

- apps/waypoint/models.py
- apps/route/views.py
