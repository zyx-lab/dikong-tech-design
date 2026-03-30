# 无人机分配逻辑模型

- generated_at: 2026-03-15
- entity: drone_assignment

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

- table: drone_assignments
- 主键: id (BigAutoField)

## 状态机

| 状态字段 | 值 | 含义 |
|---------|-----|------|
| status | ACTIVE | 生效中 |
| status | INACTIVE | 已失效 |

## 关系与约束

- tenant_id -> tenants.id
- drone_id -> drones.id
- tenant_member_id -> tenant_members.id
- created_by_tenant_member_id：记录租户内操作人 TenantMember ID，用于审计
- 唯一约束：同一 `(drone_id, tenant_member_id)` 在 `ACTIVE` 状态下唯一
- 业务硬约束：`drone`、`tenant_member`、`created_by_tenant_member_id` 必须属于当前 `tenant`
- 业务硬约束：仅允许给 `ACTIVE` 租户成员创建分配，且该成员对应账号必须存在在职 `staff_profile`
- 业务硬约束：仅允许给已绑定 `pilot_operator` 角色的成员创建分配
- 授权口径：`OWN / ASSIGNED` 一律以 `TenantMember.id` 为判定主体，不再使用全局 `staff_id`

## 生命周期入口

| 操作 | 路径 | 说明 |
|-----|------|------|
| 创建 | POST /api/v1/drone-assignments | 创建分配 |
| 列表 | GET /api/v1/drone-assignments | 分配列表查询 |
| 详情 | GET /api/v1/drone-assignments/{id} | 分配详情 |
| 取消 | POST /api/v1/drone-assignments/{id}/cancel | 取消分配 |

## 接口语义

### 创建分配 POST /api/v1/drone-assignments
- 功能：创建无人机与租户成员的分配关系
- 约束：`tenant_member` 属于当前租户且为 `ACTIVE`，关联账号在职并持有 `pilot_operator` 角色，不存在同键 `ACTIVE` 记录
- 业务码：`00000`, `B0001`, `C0101`, `A0401 / A0403`

### 取消分配 POST /api/v1/drone-assignments/{id}/cancel
- 状态流转：ACTIVE -> INACTIVE
- 有效状态：ACTIVE
- 无效状态：INACTIVE
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`
