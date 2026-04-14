# 任务实现说明

- updated_at: 2026-04-14
- entity: mission

## 数据模型

### Mission 表 (missions)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| tenant | ForeignKey | 租户 |
| name | CharField(100) | 任务名称 |
| route | ForeignKey | 航线；DB 可为空，创建接口要求必填 |
| route_name | CharField(100) | 航线名称冗余 |
| drone | ForeignKey | 无人机；DB 可为空，创建接口要求必填 |
| device_sn | CharField(128) | 无人机 SN 冗余 |
| drone_name | CharField(100) | 无人机名称冗余 |
| pilot | ForeignKey | 飞手成员（TenantMember） |
| pilot_name | CharField(50) | 飞手姓名冗余 |
| scheduled_at | DateTimeField | 计划执行时间（可选） |
| started_at | DateTimeField | 开始执行时间（可选） |
| finished_at | DateTimeField | 执行完成时间（可选） |
| remark | CharField(500) | 任务备注 |
| status | PositiveSmallIntegerField | 任务执行状态 |
| is_deleted | BooleanField | 是否软删除 |
| deleted_at | DateTimeField | 删除时间（可选） |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

### MissionStatus 枚举

- PENDING = 0, "待执行"
- RUNNING = 1, "执行中"
- COMPLETED = 2, "执行完成"

### 关键约束

1. `Mission.clean()` 会校验 `route` / `drone` / `pilot` 的租户归属。
2. `pilot` 必须是当前租户下 `ACTIVE` 的飞手成员，并满足在职与 `pilot_operator` 角色约束。
3. `PENDING` 不允许写入 `started_at` / `finished_at`。
4. `RUNNING` 必须有 `started_at`，且不允许写入 `finished_at`。
5. `COMPLETED` 必须同时具备 `started_at` / `finished_at`，且 `finished_at >= started_at`。
6. mission 软删除后不可恢复。

## API 实现 (/api/v1/missions)

统一响应契约：

- 成功：`code=00000`，`msg=success`
- 失败：统一返回 `code / msg / data`，常见错误码为 `A0401`、`A0403`、`B0001`、`C0201`、`C0404`

### 1. GET /api/v1/missions

- 功能：任务列表查询
- 筛选参数：`route_id`、`drone_id`、`pilot_id`、`status`
- 返回要点：包含 `started_at`、`finished_at`、`device_sn`
- Scope：若命中 `mission.view_mission = ASSIGNED`，仅返回 `pilot_id = 当前 TenantMember.id` 的任务
- 权限：`mission.view_mission`

### 2. POST /api/v1/missions

- 功能：创建本地任务
- 必填：`name`、`route`、`drone`、`pilot`
- 可选：`scheduled_at`、`remark`
- 前置条件：
  - `route`、`drone`、`pilot` 必须属于当前租户
  - `pilot` 必须是当前租户下 `ACTIVE` 成员，且账号存在在职 `staff_profile`
  - `pilot` 必须已绑定 `pilot_operator`
- 关键行为：
  - 创建本地 `Mission(status=PENDING)`
  - 自动冗余写入 `route_name`、`device_sn`、`drone_name`、`pilot_name`
  - 不调用 DJI，不生成 `dji_job_id`
- 权限：`mission.manage_mission`

### 3. GET /api/v1/missions/{id}

- 功能：任务详情
- 返回要点：包含 `started_at`、`finished_at`、`device_sn`
- Scope：若命中 `mission.view_mission = ASSIGNED`，则只能读取分配给当前飞手的任务
- 权限：`mission.view_mission`

### 4. PUT /api/v1/missions/{id}

- 功能：更新待执行任务字段
- 可写字段：`name`、`route`、`drone`、`scheduled_at`、`remark`
- 约束：
  - 仅 `待执行` 任务允许更新
  - 接口对 `PUT` 采用“部分更新”语义，请求体只需提交要改的字段
  - 不允许改写 `pilot`、`status`、`started_at`、`finished_at`
- 关键行为：更新 `route` / `drone` 时会同步刷新 `route_name`、`device_sn`、`drone_name`
- 权限：`mission.manage_mission`

### 5. POST /api/v1/missions/{id}/advance

- 功能：推进任务状态
- 请求体：必须为空
- 状态流转：`待执行 -> 执行中 -> 执行完成`
- 关键行为：
  - `待执行 -> 执行中`：写入 `started_at`
  - `执行中 -> 执行完成`：写入 `finished_at`
  - 若同一无人机已有其他 `执行中` 任务，则返回 `409 / C0201`
  - `执行完成` 任务不能继续推进
- 权限：`mission.manage_mission`

### 6. DELETE /api/v1/missions/{id}

- 功能：软删除任务
- 请求体：必须为空
- 关键行为：
  - 写入 `is_deleted=true`
  - 写入 `deleted_at`
  - 删除后从业务查询中隐藏
- 权限：`mission.manage_mission`

## 审计动作

- MISSION_CREATE
- MISSION_UPDATE
- MISSION_ADVANCE
- MISSION_DELETE

## 关键实现文件

- apps/mission/models.py
- apps/mission/serializers.py
- apps/mission/views.py
- apps/mission/urls.py
- apps/mission/tests.py
- apps/mission/test_live_api.py
