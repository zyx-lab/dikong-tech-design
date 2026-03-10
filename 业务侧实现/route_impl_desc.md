# 航线实现说明

- generated_at: 2026-03-08T07:52:00Z
- updated_at: 2026-03-10
- entity: route

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

### Route 表 (routes)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| name | CharField(100) | 航线名称 |
| route_type | PositiveSmallIntegerField | 航线类型扩展位，默认0=待扩展 |
| drone_type_id | BigIntegerField | 适用无人机类型 ID（可选） |
| total_distance | DecimalField(12,2) | 航线总长度（米，可选） |
| estimated_duration | PositiveIntegerField | 预计飞行时长（秒，可选） |
| waypoint_count | PositiveIntegerField | 航点数量（可选） |
| creator_name | CharField(50) | 创建人姓名 |
| status | PositiveSmallIntegerField | 状态：0=禁用, 1=正常 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

### RouteStatus 枚举
- DISABLED = 0, "禁用"
- ACTIVE = 1, "正常"

### RouteType 枚举
- PENDING_EXTENSION = 0, "待扩展"

---

## API 实现 (/api/v1/routes)

### 1. GET /api/v1/routes
- 功能：航线列表查询
- 筛选参数：status, route_type, name（模糊匹配）
- 权限：route.view_route
- 业务码：SUCCESS, PERMISSION_DENIED

### 2. POST /api/v1/routes
- 功能：创建航线
- 必填：name
- 可选：route_type, drone_type_id, total_distance, estimated_duration
- 自动设置：status=ACTIVE, creator_name=当前用户姓名
- 权限：route.manage_route
- 业务码：SUCCESS, INVALID_PARAMS, PERMISSION_DENIED

### 3. GET /api/v1/routes/{id}
- 功能：航线详情
- 权限：route.view_route
- 业务码：SUCCESS, RESOURCE_NOT_FOUND, PERMISSION_DENIED

### 4. PATCH /api/v1/routes/{id}
- 功能：局部更新航线
- 可写字段：name, route_type, drone_type_id, total_distance, estimated_duration
- 约束：PATCH 请求体必须至少包含一个可写字段，status 和 creator_name 不可写
- 权限：route.manage_route
- 业务码：SUCCESS, INVALID_PARAMS, RESOURCE_NOT_FOUND, PERMISSION_DENIED

### 5. DELETE /api/v1/routes/{id}
- 功能：删除航线
- 约束：DELETE 请求体必须为空
- 业务规则：
  - 若航线已被任务引用，则不物理删除，改为置为 DISABLED，返回 deleted=true, delete_mode=disabled
  - 若航线未被任务引用，则物理删除航线及其下属航点，返回 deleted=true, delete_mode=hard
- 权限：route.manage_route
- 业务码：SUCCESS, INVALID_PARAMS, RESOURCE_NOT_FOUND, PERMISSION_DENIED

### 6. POST /api/v1/routes/{id}/enable
- 功能：启用航线
- 状态流转：DISABLED -> ACTIVE
- 约束：请求体必须为空
- 幂等：已处于 ACTIVE 的航线重复 enable 返回当前状态
- 权限：route.manage_route
- 业务码：SUCCESS, INVALID_PARAMS, RESOURCE_NOT_FOUND, PERMISSION_DENIED
- 审计：ROUTE_ENABLE

### 7. POST /api/v1/routes/{id}/disable
- 功能：禁用航线
- 路径：/api/v1/routes/{id}/disable
- 方法：POST
- 状态流转：ACTIVE -> DISABLED
- 请求体：必须为空；提交 body 返回 INVALID_PARAMS
- 响应：返回最新 route 快照；若当前已是 DISABLED，则按幂等成功返回当前状态
- 约束：仅处理 route.status 自身流转，不承担航点删除、任务解绑或批量停用编排
- 幂等：已处于 DISABLED 的航线重复 disable 返回当前状态
- 权限：route.manage_route
- 业务码：SUCCESS, INVALID_PARAMS, RESOURCE_NOT_FOUND, PERMISSION_DENIED
- 审计：ROUTE_DISABLE

---

## 审计动作

- ROUTE_CREATE
- ROUTE_UPDATE
- ROUTE_DELETE
- ROUTE_ENABLE
- ROUTE_DISABLE

---

## 关键实现文件

- apps/route/models.py
- apps/route/serializers.py
- apps/route/views.py
- apps/route/urls.py
- apps/route/tests.py
- apps/access/management/commands/seed_role_permissions.py
- apps/api_v1/urls.py
