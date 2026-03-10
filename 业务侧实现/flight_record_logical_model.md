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

- mission -> mission.Mission
- drone -> drone.Drone
- pilot -> access.StaffProfile
- 唯一约束：flight_no 全局唯一

## 生命周期入口

| 操作 | 路径 | 说明 |
|-----|------|------|
| 创建 | POST /api/v1/flight-records | 新增飞行记录 |
| 列表 | GET /api/v1/flight-records | 飞行记录列表查询 |
| 详情 | GET /api/v1/flight-records/{id} | 飞行记录详情 |
| 更新 | PATCH /api/v1/flight-records/{id} | 局部更新飞行记录 |
| 完成 | POST /api/v1/flight-records/{id}/complete | 完成飞行记录 |
| 异常终止 | POST /api/v1/flight-records/{id}/abort | 异常终止飞行记录 |

## 接口语义

### 更新飞行记录 PATCH /api/v1/flight-records/{id}
- 功能：局部更新飞行记录元数据
- 可写字段：mission, drone, pilot, start_time, end_time, flight_duration, photo_count, video_count, airport_name
- 约束：PATCH 请求体必须至少包含一个可写字段
- 业务码：SUCCESS, INVALID_PARAMS, RESOURCE_NOT_FOUND, PERMISSION_DENIED

### 完成飞行记录 POST /api/v1/flight-records/{id}/complete
- 状态流转：IN_PROGRESS -> COMPLETED
- 有效状态：IN_PROGRESS
- 无效状态：COMPLETED / ABORTED
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, RESOURCE_NOT_FOUND, PERMISSION_DENIED

### 异常终止飞行记录 POST /api/v1/flight-records/{id}/abort
- 状态流转：IN_PROGRESS -> ABORTED
- 有效状态：IN_PROGRESS
- 无效状态：COMPLETED / ABORTED
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, RESOURCE_NOT_FOUND, PERMISSION_DENIED
