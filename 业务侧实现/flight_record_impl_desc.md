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

统一响应契约：
- 成功：`code=00000`，`msg=success`
- 失败：统一返回 `code / msg / data`，常见错误码为 `A0401`、`A0403`、`B0001`、`C0101`、`C0201`、`C0404`

飞行记录由 `mission` 完成后自动生成，不提供 POST 创建、complete 或 abort 接口。

### 1. GET /api/v1/flight-records
- 功能：飞行记录列表查询
- 筛选参数：mission_id, drone_id, pilot_id, status, flight_no
- 说明：`pilot_id` 按 `TenantMember.id` 过滤
- 说明：若调用方角色命中 `flight_record.* = ASSIGNED`，则仅返回 `pilot_id = 当前 TenantMember.id` 的记录
- 权限：flight_record.view_flight_record
- 业务码：`00000`, `A0401 / A0403`

### 2. GET /api/v1/flight-records/{id}
- 功能：飞行记录详情
- 说明：若调用方角色命中 `flight_record.view_flight_record = ASSIGNED`，则只能读取当前飞手自己的记录
- 权限：flight_record.view_flight_record
- 业务码：`00000`, `C0404`, `A0401 / A0403`

### 3. PUT /api/v1/flight-records/{id}
- 功能：更新飞行记录摘要字段
- 可写字段：mission_name, route_name, airport_name, drone_name, pilot_name, flight_duration, photo_count
- 约束：`video_count`、`status`、`mission`、`drone`、`pilot`、`start_time`、`end_time` 不对外开放写入
- 约束：若调用方角色命中 `flight_record.manage_flight_record = ASSIGNED`，则不能把记录改写到其他飞手名下
- 权限：flight_record.manage_flight_record
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`

### 4. DELETE /api/v1/flight-records/{id}
- 功能：软删除飞行记录
- 约束：请求体必须为空
- 行为：将记录标记为已删除，不再出现在列表中
- 权限：flight_record.manage_flight_record
- 业务码：`00000`, `B0001`, `C0404`, `A0401 / A0403`
- 审计：FLIGHT_RECORD_DELETE

---

## 审计动作

- FLIGHT_RECORD_UPDATE
- FLIGHT_RECORD_DELETE

---

## 关键实现文件

- apps/flight_record/models.py
- apps/flight_record/serializers.py
- apps/flight_record/views.py
- apps/flight_record/urls.py
- apps/flight_record/tests.py
- apps/access/management/commands/seed_role_permissions.py
- apps/api_v1/urls.py
