# 飞行记录逻辑模型

- generated_at: 2026-03-08T09:24:39.176004Z
- entity: flight_record

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

- table: flight_records
- 主键: id (BigAutoField)

## 状态机

| 状态字段 | 值 | 含义 |
|---------|-----|------|
| status | 0 | 飞行中 |
| status | 1 | 已完成 |
| status | 2 | 异常终止 |

## 关系与约束

- tenant -> access.Tenant
- mission -> mission.Mission
- drone -> drone.Drone
- pilot -> access.TenantMember
- 唯一约束：同一 `(tenant_id, flight_no)` 唯一
- 绑定约束：`mission`、`drone`、`pilot` 若存在，必须属于当前 `tenant`
- 绑定约束：`pilot` 必须是当前租户下的 `ACTIVE TenantMember`，其账号需存在在职 `staff_profile`，且成员已绑定 `pilot_operator`
- 一致性约束：若同时绑定 `mission` 与 `drone / pilot`，则必须与 `mission` 上的绑定关系一致
- 飞行记录由 `mission` 完成后自动生成，不提供独立创建接口

## 生命周期入口

| 操作 | 路径 | 说明 |
|-----|------|------|
| 生成 | mission 完成后自动生成 | 无独立 HTTP 接口 |
| 列表 | GET /api/v1/flight-records | 飞行记录列表查询 |
| 详情 | GET /api/v1/flight-records/{id} | 飞行记录详情 |
| 更新 | PUT /api/v1/flight-records/{id} | 更新飞行记录摘要 |
| 删除 | DELETE /api/v1/flight-records/{id} | 软删除飞行记录历史快照 |

## 接口语义

### 更新飞行记录 PUT /api/v1/flight-records/{id}
- 功能：更新飞行记录摘要字段
- 可写字段：mission_name, route_name, airport_name, drone_name, pilot_name, flight_duration, photo_count
- 约束：`status`、`mission`、`drone`、`pilot`、`start_time`、`end_time`、`video_count` 不对外开放写入
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`

### 删除飞行记录 DELETE /api/v1/flight-records/{id}
- 状态流转：软删除，不改变 `status`
- 有效状态：未删除记录
- 无效状态：已删除记录
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`
