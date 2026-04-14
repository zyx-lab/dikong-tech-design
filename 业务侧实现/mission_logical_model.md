# 任务逻辑模型

- updated_at: 2026-04-14
- entity: mission

## 实体主表

- table: missions
- 主键: id (BigAutoField)
- 排序规则: `ordering = ['-id']`

## 状态模型

`Mission.status` 表达本地执行状态：

| 值 | 含义 |
|----|------|
| 0 | 待执行 |
| 1 | 执行中 |
| 2 | 执行完成 |

执行时间窗字段：

- `started_at`：任务进入 `执行中` 时写入
- `finished_at`：任务进入 `执行完成` 时写入

当前语义：

- 创建任务时，本地初始化为 `PENDING`
- 只能通过 `POST /api/v1/missions/{id}/advance` 推进状态
- 不存在 `cancel / pause / resume / fail` 等 mission 动作接口
- 当前阶段不做“飞行中”派生态判断

## 关系与约束

- `mission.tenant -> access.Tenant`
- `mission.route -> route.Route`（`SET_NULL`，DB 允许为空；创建接口要求必填）
- `mission.drone -> drone.Drone`（`PROTECT`，DB 允许为空；创建接口要求必填）
- `mission.pilot -> access.TenantMember`
- 创建接口约束：
  - `route`、`drone`、`pilot` 必须属于当前 tenant
  - `pilot` 必须为 `ACTIVE` 成员，且账号存在在职 `staff_profile`
  - `pilot` 必须已绑定 `pilot_operator`
- 更新接口约束：
  - 仅 `待执行` 任务允许更新
  - 同一无人机同一时刻只允许一个 `执行中` mission
- 删除约束：
  - mission 软删除不可恢复

## 生命周期入口

| 操作 | 路径 | 说明 |
|-----|------|------|
| 创建 | POST /api/v1/missions | 创建本地任务 |
| 列表 | GET /api/v1/missions | 任务列表查询 |
| 详情 | GET /api/v1/missions/{id} | 任务详情 |
| 更新 | PUT /api/v1/missions/{id} | 更新待执行任务字段 |
| 推进 | POST /api/v1/missions/{id}/advance | 推进 `待执行 -> 执行中 -> 执行完成` |
| 删除 | DELETE /api/v1/missions/{id} | 软删除任务 |

## 接口语义

### 创建任务 POST /api/v1/missions

- 功能：创建本地 mission
- 写入结果：
  - 创建 `Mission(status=PENDING)`
  - `started_at`、`finished_at` 保持为空
  - 自动回填 `route_name`、`device_sn`、`drone_name`、`pilot_name`
- 当前不再同步 DJI job

### 更新任务 PUT /api/v1/missions/{id}

- 功能：修改待执行任务的本地管理字段
- 可写字段：`name`、`route`、`drone`、`scheduled_at`、`remark`
- 约束：不允许通过更新接口改写 `pilot`、`status`、`started_at`、`finished_at`

### 推进任务 POST /api/v1/missions/{id}/advance

- 功能：推进任务执行状态
- 输入边界：请求体必须为空
- 并发约束：同一无人机若已有其他 `RUNNING` mission，则拒绝推进
- 副作用：
  - 进入 `RUNNING` 时写入 `started_at`
  - 进入 `COMPLETED` 时写入 `finished_at`

### 删除任务 DELETE /api/v1/missions/{id}

- 功能：软删除任务
- 输入边界：请求体必须为空
- 副作用：
  - `missions.is_deleted = true`
  - `missions.deleted_at = now()`

## 与媒体归属的关系

- mission 是当前阶段媒体自动归档的唯一业务锚点。
- DJI 媒体同步按 `device_sn + captured_at` 在同租户内匹配唯一 mission 时间窗。
- 若命中多个 mission 窗口，则保持媒体未绑定。
- 若媒体已被人工绑定 mission，则自动同步不覆盖人工结果。
