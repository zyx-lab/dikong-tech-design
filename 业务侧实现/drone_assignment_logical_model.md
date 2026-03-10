# 无人机分配逻辑模型

- generated_at: 2026-03-08
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

- drone_id -> drones.id
- staff_id -> staff_profiles.id
- 唯一约束：同一 (drone_id, staff_id) 在 ACTIVE 状态下唯一

## 生命周期入口

| 操作 | 路径 | 说明 |
|-----|------|------|
| 创建 | POST /api/v1/drone-assignments | 创建分配 |
| 列表 | GET /api/v1/drone-assignments | 分配列表查询 |
| 详情 | GET /api/v1/drone-assignments/{id} | 分配详情 |
| 取消 | POST /api/v1/drone-assignments/{id}/cancel | 取消分配 |
| 恢复 | POST /api/v1/drone-assignments/{id}/reactivate | 恢复分配 |

## 接口语义

### 创建分配 POST /api/v1/drone-assignments
- 功能：创建无人机与飞手的分配关系
- 约束：drone.status!=RETIRED, staff在职且为pilot_operator, 不存在同键ACTIVE记录
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, PERMISSION_DENIED

### 取消分配 POST /api/v1/drone-assignments/{id}/cancel
- 状态流转：ACTIVE -> INACTIVE
- 有效状态：ACTIVE
- 无效状态：INACTIVE
- 业务码：SUCCESS, RESOURCE_NOT_FOUND, PERMISSION_DENIED

### 恢复分配 POST /api/v1/drone-assignments/{id}/reactivate
- 状态流转：INACTIVE -> ACTIVE
- 有效状态：INACTIVE
- 无效状态：ACTIVE
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, RESOURCE_NOT_FOUND, PERMISSION_DENIED
