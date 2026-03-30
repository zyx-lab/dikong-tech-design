# 航点实现说明

- updated_at: 2026-03-30
- entity: waypoint

## 当前边界

`Waypoint` 已不再是独立业务资源。

当前实现中：

- 没有公开 API `/api/v1/waypoints*`
- 没有独立权限 `waypoint.view_waypoint` / `waypoint.manage_waypoint`
- 没有独立审计动作
- 只保留 `waypoints` 表，作为 `Route` 聚合的内部持久化结构

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

## 写入路径

`waypoints` 的新增、替换和删除全部通过 `Route` 聚合写入：

- `POST /api/v1/routes`
- `PUT / PATCH /api/v1/routes/{id}`

写入语义：

- 提交 `Route.waypoints[]` 时，按整条航线全量替换内部 waypoint 行
- 不再支持单航点独立 CRUD
- `route.waypoint_count` 由 route 聚合写链路统一回写

## 关键实现文件

- apps/waypoint/models.py
- apps/route/serializers.py
- apps/route/services.py
- apps/route/views.py
