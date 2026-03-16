# 航点实现说明

- generated_at: 2026-03-09T02:39:31.679337Z
- updated_at: 2026-03-09
- entity: waypoint

## 数据库

PostgreSQL

---

## 文档格式说明

本文档为 **实现描述** 类型文档，记录 API 实现、数据模型、审计动作等。

### 更新本文档的指南（大模型用）

当需要更新此文档时，请遵循以下格式：

```
## N. {模块名}

### N.X API / 功能名称
- 功能：{功能描述}
- 路径：{API路径}
- 方法：{HTTP方法}
- 权限：{所需权限}
- 请求体：{请求格式}
- 响应：{响应格式}
- 业务码：{返回的业务码}
```

---

## 数据模型

### Waypoint 表 (waypoints)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| route | ForeignKey | 所属航线 |
| sequence | PositiveIntegerField | 航点序号 |
| latitude | DecimalField(12,8) | 纬度 |
| longitude | DecimalField(12,8) | 经度 |
| altitude | DecimalField(10,2) | 飞行高度（米） |
| created_at | DateTimeField | 创建时间 |

### 约束
- 同一航线下 sequence 唯一 (waypoints_route_seq_unique)

---

## API 实现 (/api/v1/waypoints)

统一响应契约：
- 成功：`code=00000`，`msg=success`
- 失败：统一返回 `code / msg / data`，常见错误码为 `A0401`、`A0403`、`B0001`、`C0101`、`C0404`

### 1. GET /api/v1/waypoints
- 功能：航点列表查询
- 筛选参数：route_id, sequence
- 排序：按 route_id, sequence, id 升序
- 权限：waypoint.view_waypoint
- 业务码：`00000`, `A0401 / A0403`

### 2. POST /api/v1/waypoints
- 功能：创建航点
- 必填：route, sequence, latitude, longitude, altitude
- 约束：仅允许写入 ACTIVE 航线，同一航线下 sequence 不重复
- 自动同步：创建后更新 route.waypoint_count
- 权限：waypoint.manage_waypoint
- 业务码：`00000`, `B0001`, `C0101`, `A0401 / A0403`

### 3. GET /api/v1/waypoints/{id}
- 功能：航点详情
- 权限：waypoint.view_waypoint
- 业务码：`00000`, `C0404`, `A0401 / A0403`

### 4. PUT / PATCH /api/v1/waypoints/{id}
- 功能：全量或局部更新航点
- 可写字段：sequence, latitude, longitude, altitude
- 约束：
  - 不支持切换所属航线
  - 仅允许更新 ACTIVE 航线下的航点
  - PATCH 请求体必须至少包含一个可写字段
- 权限：waypoint.manage_waypoint
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`

### 5. DELETE /api/v1/waypoints/{id}
- 功能：删除航点
- 自动同步：删除后更新 route.waypoint_count
- 权限：waypoint.manage_waypoint
- 业务码：`00000`, `C0404`, `A0401 / A0403`

---

## 审计动作

- WAYPOINT_CREATE
- WAYPOINT_UPDATE
- WAYPOINT_DELETE

---

## 关键实现文件

- apps/waypoint/models.py
- apps/waypoint/serializers.py
- apps/waypoint/views.py
- apps/waypoint/urls.py
- apps/waypoint/tests.py
- apps/access/management/commands/seed_role_permissions.py
- apps/api_v1/urls.py
