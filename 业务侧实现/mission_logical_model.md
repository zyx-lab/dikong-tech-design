# 任务逻辑模型

- updated_at: 2026-03-09T09:30:00+08:00
- entity: mission

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

- table: missions
- 主键: id (BigAutoField)
- 排序规则: `ordering = ['-id']`

## 状态机

| 状态字段 | 值 | 含义 |
|---------|-----|------|
| status | 0 | 待执行 |
| status | 1 | 执行中 |
| status | 2 | 已暂停 |
| status | 3 | 已完成 |
| status | 4 | 已取消 |
| status | 5 | 执行失败 |

## 关系与约束

- route -> route.Route（FK, PROTECT）
- drone -> drone.Drone（FK, PROTECT）
- pilot -> access.StaffProfile（FK, PROTECT）
- 创建约束：route.status=ACTIVE, drone.status=ENABLED, pilot在职且为pilot_operator

## 生命周期入口

| 操作 | 路径 | 说明 |
|-----|------|------|
| 创建 | POST /api/v1/missions | 新增任务 |
| 列表 | GET /api/v1/missions | 任务列表查询 |
| 详情 | GET /api/v1/missions/{id} | 任务详情 |
| 更新 | PATCH /api/v1/missions/{id} | 局部更新任务 |
| 启动 | POST /api/v1/missions/{id}/start | 启动任务 |
| 暂停 | POST /api/v1/missions/{id}/pause | 暂停任务 |
| 恢复 | POST /api/v1/missions/{id}/resume | 恢复任务 |
| 完成 | POST /api/v1/missions/{id}/complete | 完成任务 |
| 失败 | POST /api/v1/missions/{id}/fail | 标记失败 |
| 取消 | POST /api/v1/missions/{id}/cancel | 取消任务 |

## 接口语义

### 启动任务 POST /api/v1/missions/{id}/start
- 状态流转：PENDING -> RUNNING
- 有效状态：PENDING
- 无效状态：PAUSED / COMPLETED / CANCELED / FAILED
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, RESOURCE_NOT_FOUND, PERMISSION_DENIED

### 暂停任务 POST /api/v1/missions/{id}/pause
- 状态流转：RUNNING -> PAUSED
- 有效状态：RUNNING
- 无效状态：PENDING / COMPLETED / CANCELED / FAILED
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, RESOURCE_NOT_FOUND, PERMISSION_DENIED

### 恢复任务 POST /api/v1/missions/{id}/resume
- 状态流转：PAUSED -> RUNNING
- 有效状态：PAUSED
- 无效状态：PENDING / COMPLETED / CANCELED / FAILED
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, RESOURCE_NOT_FOUND, PERMISSION_DENIED

### 完成任务 POST /api/v1/missions/{id}/complete
- 状态流转：RUNNING -> COMPLETED
- 有效状态：RUNNING
- 无效状态：PENDING / PAUSED / CANCELED / FAILED
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, RESOURCE_NOT_FOUND, PERMISSION_DENIED

### 标记失败 POST /api/v1/missions/{id}/fail
- 状态流转：RUNNING -> FAILED
- 有效状态：RUNNING
- 无效状态：PENDING / PAUSED / COMPLETED / CANCELED
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, RESOURCE_NOT_FOUND, PERMISSION_DENIED

### 取消任务 POST /api/v1/missions/{id}/cancel
- 状态流转：PENDING/RUNNING/PAUSED -> CANCELED
- 有效状态：PENDING / RUNNING / PAUSED
- 无效状态：COMPLETED / CANCELED / FAILED
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, RESOURCE_NOT_FOUND, PERMISSION_DENIED
