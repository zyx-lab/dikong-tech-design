# 无人机实现说明

- generated_at: 2026-03-08
- updated_at: 2026-03-09
- entity: drone / drone_assignment

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

### Drone 表 (drones)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| code | CharField(64) | 业务编码，唯一 |
| name | CharField(128) | 无人机名称 |
| model | CharField(128) | 型号 |
| serial_no | CharField(128) | 出厂序列号，唯一 |
| status | CharField(16) | 状态：ENABLED/DISABLED/MAINTENANCE/RETIRED |
| org_id | BigIntegerField | 组织 ID（预留） |
| created_by_tenant_member_id | BigIntegerField | 创建人 TenantMember ID |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

### DroneStatus 枚举
- ENABLED = "ENABLED", "启用"
- DISABLED = "DISABLED", "停用"
- MAINTENANCE = "MAINTENANCE", "维护中"
- RETIRED = "RETIRED", "已退役"

### DroneAssignment 表 (drone_assignments)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| drone | ForeignKey | 关联无人机 |
| tenant_member | ForeignKey | 关联租户成员 |
| status | CharField(16) | 分配状态：ACTIVE/INACTIVE |
| start_at | DateTimeField | 分配开始时间 |
| end_at | DateTimeField | 分配结束时间（可选） |
| created_by_tenant_member_id | BigIntegerField | 创建人 TenantMember ID |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

### DroneAssignmentStatus 枚举
- ACTIVE = "ACTIVE", "生效中"
- INACTIVE = "INACTIVE", "已失效"

### 约束
- 同一 `(drone, tenant_member)` 在 `ACTIVE` 状态下唯一
- `ASSIGNED` 范围统一按 `tenant_member_id` 命中

---

## API 实现

统一响应契约：
- 成功：`code=00000`，`msg=success`
- 失败：统一返回 `code / msg / data`，常见错误码为 `A0401`、`A0403`、`B0001`、`C0101`、`C0201`、`C0404`

### 无人机台账 (/api/v1/drones)

#### 1. GET /api/v1/drones
- 功能：无人机列表查询
- 筛选参数：code, name, model, status, org_id
- Scope：`ASSIGNED` 用户只返回当前租户下分配给本人 `TenantMember` 的无人机
- 权限：drone.view_drone
- 业务码：`00000`, `A0401 / A0403`

#### 2. POST /api/v1/drones
- 功能：创建无人机
- 必填：code, name, model, serial_no
- 自动设置：`created_by_tenant_member_id=当前租户成员`
- 默认状态：status=DISABLED
- 权限：drone.manage_drone
- 业务码：`00000`, `B0001`, `C0101`, `A0401 / A0403`

#### 3. GET /api/v1/drones/{id}
- 功能：无人机详情
- 权限：drone.view_drone
- 业务码：`00000`, `C0404`, `A0401 / A0403`

#### 4. PUT / PATCH /api/v1/drones/{id}
- 功能：全量或局部更新无人机
- 可写字段：code, name, model, serial_no
- 约束：status 不可直接修改，需通过状态动作接口
- 权限：drone.manage_drone
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`

#### 5. DELETE /api/v1/drones/{id}
- 功能：删除无人机
- 约束：
  - DELETE 请求体必须为空
  - 若存在 ACTIVE 分配关系，返回 409 + `C0201`
- 权限：drone.manage_drone
- 业务码：`00000`, `B0001`, `C0201`, `C0404`, `A0401 / A0403`

#### 6. POST /api/v1/drones/{id}/enable
- 功能：启用无人机
- 状态流转：DISABLED/MAINTENANCE -> ENABLED
- 约束：RETIRED 不可逆
- 幂等：已 ENABLED 返回当前状态
- 权限：drone.change_drone_status
- 业务码：`00000`, `C0201`, `C0404`, `A0401 / A0403`

#### 7. POST /api/v1/drones/{id}/disable
- 功能：停用无人机
- 状态流转：ENABLED/MAINTENANCE -> DISABLED
- 幂等：已 DISABLED 返回当前状态
- 权限：drone.change_drone_status
- 业务码：`00000`, `C0201`, `C0404`, `A0401 / A0403`

#### 8. POST /api/v1/drones/{id}/maintenance
- 功能：设置维护中
- 状态流转：ENABLED/DISABLED -> MAINTENANCE
- 幂等：已 MAINTENANCE 返回当前状态
- 权限：drone.change_drone_status
- 业务码：`00000`, `C0201`, `C0404`, `A0401 / A0403`

#### 9. POST /api/v1/drones/{id}/retire
- 功能：退役无人机
- 状态流转：任意 -> RETIRED
- 约束：RETIRED 不可逆，不可恢复为其他状态
- 幂等：已 RETIRED 返回当前状态
- 权限：drone.change_drone_status
- 业务码：`00000`, `C0201`, `C0404`, `A0401 / A0403`

#### 10. GET /api/v1/drones/{id}/assignments/history
- 功能：获取无人机分配历史
- 返回：所有分配记录（无论状态）
- 权限：drone.view_drone
- 业务码：`00000`, `C0404`, `A0401 / A0403`

#### 11. GET /api/v1/drones/{id}/assignments/active
- 功能：获取无人机当前有效分配
- 筛选：仅返回 status=ACTIVE 的记录
- 权限：drone.view_drone
- 业务码：`00000`, `C0404`, `A0401 / A0403`

#### 12. GET /api/v1/drones/{id}/assignments/latest
- 功能：获取无人机最近一条分配记录
- 返回：最新一条分配（可能是 ACTIVE 或 INACTIVE）
- 无分配时返回 has_record: false
- 权限：drone.view_drone
- 业务码：`00000`, `C0404`, `A0401 / A0403`

---

### 分配关系 (/api/v1/drone-assignments)

#### 1. GET /api/v1/drone-assignments
- 功能：分配关系列表查询
- 筛选参数：drone_id, tenant_member_id, status
- 权限：drone_assignment.manage_drone_assignment
- 业务码：`00000`, `A0401 / A0403`

#### 2. POST /api/v1/drone-assignments
- 功能：创建分配关系
- 必填：drone, tenant_member
- 默认：status=ACTIVE, start_at=当前时间
- 约束：同一无人机与同一租户成员不得重复存在 ACTIVE 分配
- 权限：drone_assignment.manage_drone_assignment
- 业务码：`00000`, `B0001`, `C0201`, `A0401 / A0403`

#### 3. GET /api/v1/drone-assignments/{id}
- 功能：分配关系详情
- 权限：drone_assignment.manage_drone_assignment
- 业务码：`00000`, `C0404`, `A0401 / A0403`

#### 4. POST /api/v1/drone-assignments/{id}/cancel
- 功能：取消分配
- 状态流转：ACTIVE -> INACTIVE
- 自动设置：end_at=当前时间
- 幂等：已 INACTIVE 返回当前状态
- 权限：drone_assignment.manage_drone_assignment
- 业务码：`00000`, `C0404`, `A0401 / A0403`
- 审计：DRONE_ASSIGNMENT_CANCEL

#### 5. POST /api/v1/drone-assignments/{id}/reactivate
- 功能：重新激活分配
- 状态流转：INACTIVE -> ACTIVE
- 自动清除：end_at=None
- 约束：若存在同一无人机与飞手的 ACTIVE 分配，返回 409 + `C0201`
- 幂等：已 ACTIVE 返回当前状态
- 权限：drone_assignment.manage_drone_assignment
- 业务码：`00000`, `B0001`, `C0201`, `C0404`, `A0401 / A0403`
- 审计：DRONE_ASSIGNMENT_REACTIVATE

---

## 审计动作

### 无人机
- DRONE_CREATE
- DRONE_UPDATE
- DRONE_STATUS_CHANGE
- DRONE_DELETE

### 分配关系
- DRONE_ASSIGNMENT_CREATE
- DRONE_ASSIGNMENT_CANCEL
- DRONE_ASSIGNMENT_REACTIVATE

---

## 关键实现文件

- apps/drone/models.py
- apps/drone/serializers.py
- apps/drone/views.py
- apps/drone/urls.py
- apps/drone_assignment/models.py
- apps/drone_assignment/serializers.py
- apps/drone_assignment/views.py
- apps/drone_assignment/urls.py
- apps/access/management/commands/seed_role_permissions.py
- apps/api_v1/urls.py
