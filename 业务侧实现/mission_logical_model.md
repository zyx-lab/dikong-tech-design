# 任务逻辑模型

- updated_at: 2026-03-31
- entity: mission

## 实体主表

- table: missions
- 主键: id (BigAutoField)
- 排序规则: `ordering = ['-id']`

## 状态模型

`Mission.status` 仍保留本地摘要状态：

| 值 | 含义 |
|----|------|
| 0 | 待执行 |
| 1 | 执行中 |
| 2 | 已暂停 |
| 3 | 已完成 |
| 4 | 已取消 |
| 5 | 执行失败 |

当前语义：

- 创建任务时，本地初始化为 `PENDING`
- `cancel` 动作会直接把本地状态写为 `CANCELED`
- 后台同步任务会根据 DJI job 状态刷新本地 `Mission.status`
- 不再存在本地 `start / pause / resume / complete / fail` 动作接口

## 关系与约束

- `mission.tenant -> access.Tenant`
- `mission.route -> route.Route`（`SET_NULL`，DB 允许为空；创建接口仍要求必填）
- `mission.drone -> drone.Drone`
- `mission.pilot -> access.TenantMember`
- `tenant_mission_indexes.mission -> mission.Mission`（一对一）
- 创建接口约束：
  - `route`、`drone`、`pilot` 必须属于当前 tenant
  - `pilot` 必须为 `ACTIVE` 成员，且账号存在在职 `staff_profile`
  - `pilot` 必须已绑定 `pilot_operator`
  - `route` 必须已发布到 DJI

## 生命周期入口

| 操作 | 路径 | 说明 |
|-----|------|------|
| 创建 | POST /api/v1/missions | 创建本地任务并同步 DJI job |
| 列表 | GET /api/v1/missions | 任务列表查询 |
| 详情 | GET /api/v1/missions/{id} | 任务详情 |
| 更新 | PUT / PATCH /api/v1/missions/{id} | 仅更新本地管理字段 |
| 取消 | POST /api/v1/missions/{id}/cancel | 取消 DJI job 并回写本地状态 |

## 接口语义

### 创建任务 POST /api/v1/missions

- 功能：创建 mission，并立刻在 DJI 创建 job
- 写入结果：
  - 创建 `Mission(status=PENDING)`
  - 写回 `missions.dji_job_id`
  - 创建 `TenantMissionIndex`
- 扩展输入：可选 `dock_sn`，仅用于透传 DJI，不落本地主表

### 更新任务 PUT / PATCH /api/v1/missions/{id}

- 功能：修改 mission 本地管理字段
- 可写字段：`name`、`scheduled_at`、`remark`
- 约束：不允许通过更新接口改写 `route`、`drone`、`pilot`、`status`、`dji_job_id`

### 取消任务 POST /api/v1/missions/{id}/cancel

- 功能：取消 DJI job
- 前置条件：`mission.dji_job_id` 非空
- 输入边界：请求体必须为空
- 幂等语义：若 DJI 已返回 `404`，本地仍按取消成功收敛
- 副作用：
  - `missions.status = CANCELED`
  - 若存在 `tenant_mission_indexes`，同步更新 `execution_status`、`sync_status`、`last_sync_at`、`error_msg`
