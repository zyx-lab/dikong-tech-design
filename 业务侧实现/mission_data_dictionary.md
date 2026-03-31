# 低空智能巡检平台 - 任务

## 阅读说明

本数据字典覆盖当前 mission 业务域已落地的两张核心表：

- `missions`
- `tenant_mission_indexes`

## 1. missions（任务表）

**说明**：存储 tenant 内任务主记录，以及与 DJI job 关联所需的最小本地字段。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| tenant_id | bigint | FK, NOT NULL | - | 租户 ID |
| name | varchar(100) | NOT NULL | - | 任务名称 |
| route_id | bigint | FK | - | 航线 ID；route 删除后可为空 |
| route_name | varchar(100) | NOT NULL | '' | 航线名称（冗余） |
| drone_id | bigint | FK, NOT NULL | - | 无人机 ID |
| drone_name | varchar(100) | NOT NULL | '' | 无人机名称（冗余） |
| pilot_id | bigint | FK, NOT NULL | - | 飞手成员 ID（TenantMember） |
| pilot_name | varchar(50) | NOT NULL | '' | 飞手姓名（冗余） |
| scheduled_at | timestamp | - | - | 计划执行时间 |
| remark | varchar(500) | NOT NULL | '' | 任务备注 |
| status | smallint | NOT NULL | 0 | 本地任务状态摘要 |
| dji_job_id | varchar(128) | NOT NULL | '' | DJI job ID |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**status 状态值**：

| 值 | 含义 |
|----|------|
| 0 | 待执行 |
| 1 | 执行中 |
| 2 | 已暂停 |
| 3 | 已完成 |
| 4 | 已取消 |
| 5 | 执行失败 |

**业务规则**：
1. 创建接口要求 `route`、`drone`、`pilot` 必须属于当前租户。
2. 创建接口要求 `route` 已发布到 DJI。
3. 创建接口要求 `pilot` 为 `ACTIVE` 成员，账号存在在职 `staff_profile`，且成员已绑定 `pilot_operator`。
4. `status` 不再由本地 `start / pause / resume / complete / fail` action 推进；主要由 DJI 同步结果回写。

## 2. tenant_mission_indexes（任务同步索引表）

**说明**：存储 mission 与 DJI job 的一对一映射，以及最近一次同步摘要。

| 字段名 | 类型 | 约束 | 默认值 | 说明 |
| ------ | ---- | ---- | ------ | ---- |
| id | bigserial | PK | 自增 | 主键 |
| tenant_id | bigint | FK, NOT NULL | - | 所属租户 |
| mission_id | bigint | FK, NOT NULL, UNIQUE | - | 对应任务 |
| dji_job_id | varchar(128) | NOT NULL | - | DJI 任务 ID |
| execution_status | varchar(64) | NOT NULL | '' | 最近一次同步到的 DJI 执行状态原文 |
| sync_status | varchar(32) | NOT NULL | PENDING | 同步状态 |
| last_sync_at | timestamp | - | - | 最近同步时间 |
| error_msg | varchar(255) | NOT NULL | '' | 同步错误信息 |
| created_at | timestamp | NOT NULL | now() | 创建时间 |
| updated_at | timestamp | NOT NULL | now() | 更新时间 |

**sync_status 枚举值**：

| 值 | 含义 |
|----|------|
| PENDING | 同步中 |
| SYNCED | 同步成功 |
| ERROR | 同步失败 |

**业务规则**：
1. 创建 mission 并成功调用 DJI 后，自动创建一条 `tenant_mission_indexes`。
2. 后台同步任务会刷新 `execution_status`、`sync_status`、`last_sync_at`、`error_msg`。
3. 若上游 job 不存在，则标记 `sync_status=ERROR`，但不自动删除本地 mission。

## 3. 与实现对应

1. 模型：`apps/mission/models.py`
2. DJI 索引：`apps/dji_bff/models.py`
3. 同步任务：`apps/dji_bff/tasks.py`
4. 接口：`apps/mission/views.py`
