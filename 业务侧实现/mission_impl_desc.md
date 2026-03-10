# 任务实现说明

- generated_at: 2026-03-09T09:30:00+08:00
- updated_at: 2026-03-09
- entity: mission

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

### Mission 表 (missions)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| name | CharField(100) | 任务名称 |
| route | ForeignKey | 航线 |
| route_name | CharField(100) | 航线名称（冗余） |
| drone | ForeignKey | 无人机 |
| drone_name | CharField(100) | 无人机名称（冗余） |
| pilot | ForeignKey | 飞手 |
| pilot_name | CharField(50) | 飞手姓名（冗余） |
| scheduled_at | DateTimeField | 计划执行时间（可选） |
| remark | CharField(500) | 任务备注 |
| status | PositiveSmallIntegerField | 任务状态 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

### MissionStatus 枚举
- PENDING = 0, "待执行"
- RUNNING = 1, "执行中"
- PAUSED = 2, "已暂停"
- COMPLETED = 3, "已完成"
- CANCELED = 4, "已取消"
- FAILED = 5, "执行失败"

---

## API 实现 (/api/v1/missions)

### 1. GET /api/v1/missions
- 功能：任务列表查询
- 筛选参数：route_id, drone_id, pilot_id, status
- 权限：mission.view_mission
- 业务码：SUCCESS, PERMISSION_DENIED

### 2. POST /api/v1/missions
- 功能：创建任务
- 必填：name, route, drone, pilot
- 可选：scheduled_at, remark
- 自动填充：route_name, drone_name, pilot_name
- 默认状态：status=PENDING
- 权限：mission.manage_mission
- 业务码：SUCCESS, INVALID_PARAMS, PERMISSION_DENIED

### 3. GET /api/v1/missions/{id}
- 功能：任务详情
- 权限：mission.view_mission
- 业务码：SUCCESS, RESOURCE_NOT_FOUND, PERMISSION_DENIED

### 4. PATCH /api/v1/missions/{id}
- 功能：局部更新任务
- 可写字段：name, route, drone, pilot, scheduled_at, remark
- 约束：status 不可写（状态通过专用动作接口变更）
- 权限：mission.manage_mission
- 业务码：SUCCESS, INVALID_PARAMS, RESOURCE_NOT_FOUND, PERMISSION_DENIED

### 5. POST /api/v1/missions/{id}/start
- 功能：启动任务
- 状态流转：PENDING -> RUNNING
- 约束：请求体必须为空
- 幂等：已 RUNNING 的任务重复 start 返回当前状态
- 禁止状态：PAUSED, COMPLETED, CANCELED, FAILED
- 权限：mission.manage_mission
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, RESOURCE_NOT_FOUND, PERMISSION_DENIED
- 审计：MISSION_START

### 6. POST /api/v1/missions/{id}/pause
- 功能：暂停任务
- 状态流转：RUNNING -> PAUSED
- 约束：请求体必须为空
- 幂等：已 PAUSED 的任务重复 pause 返回当前状态
- 禁止状态：PENDING, COMPLETED, CANCELED, FAILED
- 权限：mission.manage_mission
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, RESOURCE_NOT_FOUND, PERMISSION_DENIED
- 审计：MISSION_PAUSE

### 7. POST /api/v1/missions/{id}/resume
- 功能：恢复任务
- 状态流转：PAUSED -> RUNNING
- 约束：请求体必须为空
- 幂等：已 RUNNING 的任务重复 resume 返回当前状态
- 禁止状态：PENDING, COMPLETED, CANCELED, FAILED
- 权限：mission.manage_mission
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, RESOURCE_NOT_FOUND, PERMISSION_DENIED
- 审计：MISSION_RESUME

### 8. POST /api/v1/missions/{id}/complete
- 功能：完成任务
- 状态流转：RUNNING -> COMPLETED
- 约束：请求体必须为空
- 幂等：已 COMPLETED 的任务重复 complete 返回当前状态
- 禁止状态：PENDING, PAUSED, CANCELED, FAILED
- 权限：mission.manage_mission
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, RESOURCE_NOT_FOUND, PERMISSION_DENIED
- 审计：MISSION_COMPLETE

### 9. POST /api/v1/missions/{id}/fail
- 功能：标记任务失败
- 状态流转：RUNNING -> FAILED
- 约束：请求体必须为空
- 幂等：已 FAILED 的任务重复 fail 返回当前状态
- 禁止状态：PENDING, PAUSED, COMPLETED, CANCELED
- 权限：mission.manage_mission
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, RESOURCE_NOT_FOUND, PERMISSION_DENIED
- 审计：MISSION_FAIL

### 10. POST /api/v1/missions/{id}/cancel
- 功能：取消任务
- 状态流转：PENDING/RUNNING/PAUSED -> CANCELED
- 约束：请求体必须为空
- 幂等：已 CANCELED 的任务重复 cancel 返回当前状态
- 禁止状态：COMPLETED, CANCELED, FAILED
- 权限：mission.manage_mission
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, RESOURCE_NOT_FOUND, PERMISSION_DENIED
- 审计：MISSION_CANCEL

---

## 审计动作

- MISSION_CREATE
- MISSION_UPDATE
- MISSION_START
- MISSION_PAUSE
- MISSION_RESUME
- MISSION_COMPLETE
- MISSION_FAIL
- MISSION_CANCEL

---

## 关键实现文件

- apps/mission/models.py
- apps/mission/serializers.py
- apps/mission/views.py
- apps/mission/urls.py
- apps/mission/tests.py
- apps/access/management/commands/seed_role_permissions.py
- apps/api_v1/urls.py
