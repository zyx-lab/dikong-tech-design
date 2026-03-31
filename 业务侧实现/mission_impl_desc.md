# 任务实现说明

- updated_at: 2026-03-31
- entity: mission

## 数据模型

### Mission 表 (missions)

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | BigAutoField | 主键 |
| tenant | ForeignKey | 租户 |
| name | CharField(100) | 任务名称 |
| route | ForeignKey | 航线，可为空（route 删除后置空） |
| route_name | CharField(100) | 航线名称冗余 |
| drone | ForeignKey | 无人机 |
| drone_name | CharField(100) | 无人机名称冗余 |
| pilot | ForeignKey | 飞手成员（TenantMember） |
| pilot_name | CharField(50) | 飞手姓名冗余 |
| scheduled_at | DateTimeField | 计划执行时间（可选） |
| remark | CharField(500) | 任务备注 |
| status | PositiveSmallIntegerField | 本地任务状态摘要 |
| dji_job_id | CharField(128) | DJI job ID |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

### TenantMissionIndex 表 (tenant_mission_indexes)

| 字段 | 类型 | 说明 |
|-----|------|------|
| tenant | ForeignKey | 租户 |
| mission | OneToOneField | 业务任务 |
| dji_job_id | CharField(128) | DJI 任务 ID |
| execution_status | CharField(64) | 最近一次同步到的 DJI 执行状态原文 |
| sync_status | CharField(32) | 同步状态：`PENDING / SYNCED / ERROR` |
| last_sync_at | DateTimeField | 最近同步时间 |
| error_msg | CharField(255) | 同步错误信息 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

### MissionStatus 枚举

- PENDING = 0, "待执行"
- RUNNING = 1, "执行中"
- PAUSED = 2, "已暂停"
- COMPLETED = 3, "已完成"
- CANCELED = 4, "已取消"
- FAILED = 5, "执行失败"

## API 实现 (/api/v1/missions)

统一响应契约：

- 成功：`code=00000`，`msg=success`
- 失败：统一返回 `code / msg / data`，常见错误码为 `A0401`、`A0403`、`B0001`、`C0404`

### 1. GET /api/v1/missions

- 功能：任务列表查询
- 筛选参数：`route_id`、`drone_id`、`pilot_id`、`status`
- 返回要点：包含 `dji_job_id`、`sync_status`、`execution_status`、`last_sync_at`
- Scope：若命中 `mission.view_mission = ASSIGNED`，仅返回 `pilot_id = 当前 TenantMember.id` 的任务
- 权限：`mission.view_mission`

### 2. POST /api/v1/missions

- 功能：创建本地任务并同步创建 DJI job
- 必填：`name`、`route`、`drone`、`pilot`、`dock_sn`
- 可选：`scheduled_at`、`remark`
- 前置条件：
  - `route`、`drone`、`pilot` 必须属于当前租户
  - `pilot` 必须是当前租户下 `ACTIVE` 成员，且账号存在在职 `staff_profile`
  - `pilot` 必须已绑定 `pilot_operator`
  - `route` 必须已经发布到 DJI（`is_published=true` 且 `dji_wayline_id` 非空）
- 关键行为：
  - 先创建本地 `Mission(status=PENDING)`
  - 调用 `DjiGateway.create_mission(...)`
  - 上游请求固定补齐 DJI `flight-tasks` 的最小必填参数：`waylineType=0`、`taskType=0`、`rthAltitude=30`、`outOfControlAction=0`
  - 把返回的 `dji_job_id` 写回 `missions`
  - 创建 `TenantMissionIndex(sync_status=SYNCED, execution_status=str(MissionStatus.PENDING))`
- 权限：`mission.manage_mission`

### 3. GET /api/v1/missions/{id}

- 功能：任务详情
- 返回要点：包含 `dji_job_id`、`sync_status`、`execution_status`、`last_sync_at`
- Scope：若命中 `mission.view_mission = ASSIGNED`，则只能读取分配给当前飞手的任务
- 权限：`mission.view_mission`

### 4. PUT / PATCH /api/v1/missions/{id}

- 功能：更新本地任务管理字段
- 可写字段：`name`、`scheduled_at`、`remark`
- 不可写字段：`route`、`drone`、`pilot`、`status`、`dji_job_id`
- 说明：只更新本地字段，不调用 DJI
- 权限：`mission.manage_mission`

### 5. POST /api/v1/missions/{id}/cancel

- 功能：取消任务
- 请求体：必须为空
- 前置条件：`mission.dji_job_id` 非空
- 关键行为：
  - 调用 `DjiGateway.cancel_mission(mission.dji_job_id)`
  - 若 DJI 返回 `404`，按幂等取消处理
  - 本地把 `mission.status` 置为 `CANCELED`
  - 若存在 `TenantMissionIndex`，同步写 `execution_status=str(MissionStatus.CANCELED)`、`sync_status=SYNCED`、`last_sync_at`
- 权限：`mission.manage_mission`

## 审计动作

- MISSION_CREATE
- MISSION_UPDATE
- MISSION_CANCEL

## 关键实现文件

- apps/mission/models.py
- apps/mission/serializers.py
- apps/mission/views.py
- apps/mission/urls.py
- apps/dji_bff/models.py
- apps/dji_bff/tasks.py
- apps/mission/tests.py
