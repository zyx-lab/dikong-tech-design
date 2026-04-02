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
| RELEASED | 已释放（解除认领） |

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
- 唯一约束：`(tenant_id, code)` 全量唯一
- 唯一约束：`(tenant_id, device_sn)` 在 `status != RELEASED` 条件下唯一
- 唯一约束：`device_sn` 在 `status != RELEASED` 条件下全局唯一
- 唯一约束：同一 `(drone_id, tenant_member_id)` 在 `ACTIVE` 状态下唯一
- `OWN / ASSIGNED` 授权统一按 `TenantMember.id` 命中，不再使用全局 `staff_id`

## 生命周期入口

### 无人机 (/api/v1/drones)
| 操作 | 路径 | 说明 |
|-----|------|------|
| 列表 | GET /api/v1/drones | 无人机列表查询 |
| 可认领列表 | GET /api/v1/drones/available | 查询共享设备池中可认领设备 |
| 认领 | POST /api/v1/drones | 认领共享设备 |
| 详情 | GET /api/v1/drones/{id} | 无人机详情 |
| 更新 | PUT / PATCH /api/v1/drones/{id} | 全量或局部更新无人机 |
| 解除认领 | DELETE /api/v1/drones/{id} | 软删除；将状态改为 RELEASED，释放设备占用 |
| 直播能力 | GET /api/v1/drones/{id}/live/capacity | 查询设备直播能力 |
| 启动直播 | POST /api/v1/drones/{id}/live/start | 启动直播 |
| 停止直播 | POST /api/v1/drones/{id}/live/stop | 停止直播 |
| 调整画质 | POST /api/v1/drones/{id}/live/video-quality | 调整直播画质 |
| 切换视频源 | POST /api/v1/drones/{id}/live/video-source | 切换直播视频源 |

### 分配关系 (/api/v1/drone-assignments)
| 操作 | 路径 | 说明 |
|-----|------|------|
| 创建 | POST /api/v1/drone-assignments | 创建分配 |
| 列表 | GET /api/v1/drone-assignments | 分配列表查询 |
| 详情 | GET /api/v1/drone-assignments/{id} | 分配详情 |
| 取消 | POST /api/v1/drone-assignments/{id}/cancel | 取消分配 |

## 接口语义

### 认领设备 POST /api/v1/drones
- 功能：从共享设备池中认领设备到当前租户
- 必填：`code`、`device_sn`
- 可选：`name`、`model`、`org_id`
- 业务码：`00000`, `B0001`, `C0101`, `A0401 / A0403`

### 更新设备 PUT / PATCH /api/v1/drones/{id}
- 功能：修改本地管理字段
- 可写字段：`code`、`name`、`model`、`org_id`
- 不可写字段：`device_sn`、`status`
- 业务码：`00000`, `B0001`, `C0101`, `C0404`, `A0401 / A0403`

### 解除认领 DELETE /api/v1/drones/{id}
- 功能：软删除已认领无人机
- 状态流转：`ENABLED / DISABLED -> RELEASED`
- 衍生动作：将该无人机下所有 `ACTIVE` 分配关系软失效为 `INACTIVE`
- 历史兼容：不物理删除；Mission/FlightRecord 等历史记录仍引用原 `drone_id`
- 业务码：`00000`, `C0404`, `A0401 / A0403`

### 取消分配 POST /api/v1/drone-assignments/{id}/cancel
- 状态流转：ACTIVE -> INACTIVE
- 有效状态：ACTIVE
- 无效状态：INACTIVE
- 额外约束：请求体必须为空
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`
