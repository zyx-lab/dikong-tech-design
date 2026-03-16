# 飞行记录实现说明

- generated_at: 2026-03-08T09:24:39.176004Z
- updated_at: 2026-03-15
- entity: flight_record

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

### FlightRecord 表 (flight_records)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| tenant | ForeignKey | 租户 |
| flight_no | CharField(50) | 架次编号（租户内唯一） |
| mission | ForeignKey | 所属任务（可选） |
| mission_name | CharField(100) | 任务名称（冗余） |
| route_name | CharField(100) | 航线名称（冗余） |
| airport_name | CharField(100) | 执行机场名称 |
| drone | ForeignKey | 执行无人机（可选） |
| drone_name | CharField(100) | 无人机名称（冗余） |
| pilot | ForeignKey | 执行飞手成员（TenantMember，可选） |
| pilot_name | CharField(50) | 飞手姓名（冗余） |
| start_time | DateTimeField | 开始时间（可选） |
| end_time | DateTimeField | 结束时间（可选） |
| flight_duration | PositiveIntegerField | 飞行时长（秒，可选） |
| photo_count | PositiveIntegerField | 拍摄照片数量，默认0 |
| video_count | PositiveIntegerField | 录制视频数量，默认0 |
| status | PositiveSmallIntegerField | 状态：0=飞行中, 1=已完成, 2=异常终止 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

### FlightRecordStatus 枚举
- IN_PROGRESS = 0, "飞行中"
- COMPLETED = 1, "已完成"
- ABORTED = 2, "异常终止"

---

## API 实现 (/api/v1/flight-records)

### 1. GET /api/v1/flight-records
- 功能：飞行记录列表查询
- 筛选参数：mission_id, drone_id, pilot_id, status, flight_no
- 说明：`pilot_id` 按 `TenantMember.id` 过滤
- 权限：flight_record.view_flight_record
- 业务码：SUCCESS, PERMISSION_DENIED

### 2. POST /api/v1/flight-records
- 功能：创建飞行记录
- 必填：flight_no
- 可选：mission, drone, pilot, start_time, end_time, flight_duration, photo_count, video_count, airport_name
- 约束：`flight_no` 只要求租户内唯一；`pilot` 若提交，必须是当前租户下的 `ACTIVE TenantMember`
- 权限：flight_record.manage_flight_record
- 业务码：SUCCESS, INVALID_PARAMS, PERMISSION_DENIED

### 3. GET /api/v1/flight-records/{id}
- 功能：飞行记录详情
- 权限：flight_record.view_flight_record
- 业务码：SUCCESS, RESOURCE_NOT_FOUND, PERMISSION_DENIED

### 4. PATCH /api/v1/flight-records/{id}
- 功能：局部更新飞行记录
- 可写字段：mission, drone, pilot, start_time, end_time, flight_duration, photo_count, video_count, airport_name
- 约束：PATCH 请求体必须至少包含一个可写字段；`pilot` 字段语义同创建接口，提交值为 `TenantMember.id`
- 权限：flight_record.manage_flight_record
- 业务码：SUCCESS, INVALID_PARAMS, RESOURCE_NOT_FOUND, PERMISSION_DENIED

### 5. POST /api/v1/flight-records/{id}/complete
- 功能：完成飞行记录
- 状态流转：IN_PROGRESS -> COMPLETED
- 约束：请求体必须为空
- 幂等：已完成记录重复 complete 返回当前状态
- 权限：flight_record.manage_flight_record
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, RESOURCE_NOT_FOUND, PERMISSION_DENIED
- 审计：FLIGHT_RECORD_COMPLETE

### 6. POST /api/v1/flight-records/{id}/abort
- 功能：异常终止飞行记录
- 状态流转：IN_PROGRESS -> ABORTED
- 约束：请求体必须为空
- 幂等：已异常终止记录重复 abort 返回当前状态
- 权限：flight_record.manage_flight_record
- 业务码：SUCCESS, INVALID_PARAMS, STATE_CONFLICT, RESOURCE_NOT_FOUND, PERMISSION_DENIED
- 审计：FLIGHT_RECORD_ABORT

---

## 审计动作

- FLIGHT_RECORD_CREATE
- FLIGHT_RECORD_UPDATE
- FLIGHT_RECORD_COMPLETE
- FLIGHT_RECORD_ABORT

---

## 关键实现文件

- apps/flight_record/models.py
- apps/flight_record/serializers.py
- apps/flight_record/views.py
- apps/flight_record/urls.py
- apps/flight_record/tests.py
- apps/access/management/commands/seed_role_permissions.py
- apps/api_v1/urls.py
