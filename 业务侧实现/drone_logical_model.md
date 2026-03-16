# 无人机逻辑模型

- generated_at: 2026-03-15
- entity: drone

## 数据库

PostgreSQL

---

## 文档格式说明

本文档为 **逻辑模型** 类型文档，记录实体关系、状态机、生命周期、接口语义等。

### 更新本文档的指南（大模型用）

当需要更新此文档时，请遵循以下格式：

```
## 实体主表
- table: {表名}
- 主键: {主键定义}

## 状态机
- {状态字段}: {状态值列表}

## 关系与约束
- {外键关系}
- {业务约束}

## 生命周期入口
- {HTTP方法} {路径}: {功能描述}

## 接口语义
### {API名称}
- 功能：{功能描述}
- 路径：{API路径}
- 方法：{HTTP方法}
- 状态流转：{状态变化}
- 有效状态：{允许执行该操作的状态}
- 无效状态：{禁止执行该操作的状态列表}
- 业务码：{返回的业务码}
```

---

## 实体主表

| 表名 | 说明 |
|------|------|
| drones | 无人机台账表 |
| drone_assignments | 无人机分配表 |

### drones（无人机台账表）
- 主键: id (BigAutoField)
- 状态字段: status

### drone_assignments（无人机分配表）
- 主键: id (BigAutoField)
- 状态字段: status

## 状态机

### drones.status
| 值 | 含义 |
|----|------|
| ENABLED | 启用 |
| DISABLED | 停用 |
| MAINTENANCE | 维护中 |
| RETIRED | 已退役 |

### drone_assignments.status
| 值 | 含义 |
|----|------|
| ACTIVE | 生效中 |
| INACTIVE | 已失效 |

## 关系与约束

- drones.tenant_id -> tenants.id
- drones.created_by_tenant_member_id：记录租户内创建人，用于 `OWN` 范围判定与审计
- drone_assignments.drone_id -> drones.id
- drone_assignments.tenant_id -> tenants.id
- drone_assignments.tenant_member_id -> tenant_members.id
- 唯一约束：同一 `(drone_id, tenant_member_id)` 在 `ACTIVE` 状态下唯一
- 无人机业务标识改为租户内唯一：`(tenant_id, code)`、`(tenant_id, serial_no)`
- `OWN / ASSIGNED` 授权统一按 `TenantMember.id` 命中，不再使用全局 `staff_id`

## 生命周期入口

### 无人机 (/api/v1/drones)
| 操作 | 路径 | 说明 |
|-----|------|------|
| 创建 | POST /api/v1/drones | 新增无人机 |
| 列表 | GET /api/v1/drones | 无人机列表查询 |
| 详情 | GET /api/v1/drones/{id} | 无人机详情 |
| 更新 | PUT / PATCH /api/v1/drones/{id} | 全量或局部更新无人机 |
| 删除 | DELETE /api/v1/drones/{id} | 删除无人机 |
| 启用 | POST /api/v1/drones/{id}/enable | 启用无人机 |
| 停用 | POST /api/v1/drones/{id}/disable | 停用无人机 |
| 维护 | POST /api/v1/drones/{id}/maintenance | 设置维护中 |
| 退役 | POST /api/v1/drones/{id}/retire | 退役无人机 |

### 分配关系 (/api/v1/drone-assignments)
| 操作 | 路径 | 说明 |
|-----|------|------|
| 创建 | POST /api/v1/drone-assignments | 创建分配 |
| 列表 | GET /api/v1/drone-assignments | 分配列表查询 |
| 详情 | GET /api/v1/drone-assignments/{id} | 分配详情 |
| 取消 | POST /api/v1/drone-assignments/{id}/cancel | 取消分配 |
| 恢复 | POST /api/v1/drone-assignments/{id}/reactivate | 恢复分配 |

## 接口语义

### 启用无人机 POST /api/v1/drones/{id}/enable
- 状态流转：DISABLED/MAINTENANCE -> ENABLED
- 有效状态：DISABLED / MAINTENANCE
- 无效状态：RETIRED
- 业务码：`00000`, `C0201`, `C0404`, `A0401 / A0403`

### 退役无人机 POST /api/v1/drones/{id}/retire
- 状态流转：任意 -> RETIRED
- 有效状态：ENABLED / DISABLED / MAINTENANCE
- 无效状态：（RETIRED 不可逆）
- 业务码：`00000`, `C0201`, `C0404`, `A0401 / A0403`

### 取消分配 POST /api/v1/drone-assignments/{id}/cancel
- 状态流转：ACTIVE -> INACTIVE
- 有效状态：ACTIVE
- 无效状态：INACTIVE
- 业务码：`00000`, `C0404`, `A0401 / A0403`

### 恢复分配 POST /api/v1/drone-assignments/{id}/reactivate
- 状态流转：INACTIVE -> ACTIVE
- 有效状态：INACTIVE
- 无效状态：ACTIVE
- 业务码：`00000`, `B0001`, `C0201`, `C0404`, `A0401 / A0403`
